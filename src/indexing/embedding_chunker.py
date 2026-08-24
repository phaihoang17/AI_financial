"""Deterministic M2B chunk construction with lossless cell fragmentation."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple

from src.indexing.embedding_schemas import (
    EMBEDDING_CHUNK_SCHEMA_VERSION,
    EMBEDDING_HARD_MAX_TOKENS,
    EMBEDDING_TARGET_TOKENS,
    CellFragment,
    EmbeddingChunk,
)
from src.indexing.schemas import CellRole, NormalizedCell, NormalizedTable, RetrievalRepresentation
from src.supervisor.schemas import EvidenceSource
from src.understanding.schemas import SchemaValidationError


BGE_M3_MODEL_ID = "BAAI/bge-m3"
BGE_M3_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
CELL_FRAGMENTATION_ALGORITHM_VERSION = "m2b-cell-fragmentation-v1"
TABLE_CHUNKING_ALGORITHM_VERSION = "m2b-table-chunking-v2"


def _sha256_parts(parts: Iterable[Any]) -> str:
    digest = sha256()
    for part in parts:
        encoded = str(part).encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _chunking_fingerprint() -> str:
    payload = {
        "schema_version": EMBEDDING_CHUNK_SCHEMA_VERSION,
        "model_id": BGE_M3_MODEL_ID,
        "model_revision": BGE_M3_REVISION,
        "target_tokens": EMBEDDING_TARGET_TOKENS,
        "hard_max_tokens": EMBEDDING_HARD_MAX_TOKENS,
        "table_chunking_algorithm_version": TABLE_CHUNKING_ALGORITHM_VERSION,
        "cell_fragmentation_algorithm_version": CELL_FRAGMENTATION_ALGORITHM_VERSION,
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


CHUNKING_CONFIG_FINGERPRINT = _chunking_fingerprint()


class TokenizerLike(Protocol):
    def __call__(self, text: str, **kwargs: Any) -> Mapping[str, Any]: ...


class EmbeddingChunkingError(SchemaValidationError):
    """A deterministic, surfaced failure while constructing M2B chunks."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def load_bge_m3_tokenizer(cache_dir: Optional[str | Path] = None) -> Any:
    """Load the exact tokenizer required by the M2B chunk contract."""

    try:
        from transformers import AutoTokenizer
    except ImportError as error:  # pragma: no cover - exercised in deployment environments
        raise EmbeddingChunkingError(
            "TOKENIZER_DEPENDENCY_MISSING",
            "transformers is required to load the pinned BGE-M3 tokenizer",
        ) from error
    kwargs: Dict[str, Any] = {"revision": BGE_M3_REVISION, "use_fast": True}
    if cache_dir is not None:
        kwargs["cache_dir"] = str(cache_dir)
    try:
        return AutoTokenizer.from_pretrained(BGE_M3_MODEL_ID, **kwargs)
    except Exception as error:  # pragma: no cover - network/cache dependent
        raise EmbeddingChunkingError(
            "TOKENIZER_LOAD_FAILED",
            f"unable to load {BGE_M3_MODEL_ID}@{BGE_M3_REVISION}: {error}",
        ) from error


def _token_ids(tokenizer: TokenizerLike, text: str, *, add_special_tokens: bool) -> List[int]:
    encoded = tokenizer(
        text,
        add_special_tokens=add_special_tokens,
        truncation=False,
        return_attention_mask=False,
    )
    ids = encoded.get("input_ids")
    if isinstance(ids, list) and ids and isinstance(ids[0], list):
        ids = ids[0]
    if not isinstance(ids, list):
        raise EmbeddingChunkingError("TOKENIZER_OUTPUT_INVALID", "tokenizer did not return input_ids")
    return list(ids)


def count_tokens(tokenizer: TokenizerLike, text: str, *, add_special_tokens: bool = True) -> int:
    return len(_token_ids(tokenizer, text, add_special_tokens=add_special_tokens))


def _compact_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class _CellItem:
    cell: NormalizedCell
    text: str
    fragment: Optional[CellFragment] = None

    @property
    def source_cell_id(self) -> str:
        return self.cell.source_cell_id


def _cell_payload(item: _CellItem) -> Dict[str, Any]:
    return {
        "row_path": [entry.label for entry in item.cell.row_path],
        "column_path": [entry.label for entry in item.cell.column_path],
        "text": item.text,
    }


def _table_payload(base: Mapping[str, Any], items: Sequence[_CellItem]) -> str:
    return _compact_json({
        "ticker": base["ticker"],
        "company_name": base["company_name"],
        "report_year": base["report_year"],
        "statement_scope": base["statement_scope"],
        "period_labels": base["period_labels"],
        "cells": [_cell_payload(item) for item in items],
    })


def _content_token_count(tokenizer: TokenizerLike, content: str) -> int:
    count = count_tokens(tokenizer, content, add_special_tokens=True)
    if count > EMBEDDING_HARD_MAX_TOKENS:
        raise EmbeddingChunkingError(
            "EMBEDDING_CONTENT_OVER_HARD_LIMIT",
            f"content has {count} tokens, above {EMBEDDING_HARD_MAX_TOKENS}",
        )
    return count


def _fit_count_or_safe_bound(tokenizer: TokenizerLike, content: str) -> int:
    """Return an exact count only near the limit; UTF-8 bytes are a safe upper bound."""

    safe_bound = len(content.encode("utf-8")) + 2  # two defensive special-token slots
    if safe_bound <= EMBEDDING_TARGET_TOKENS:
        return safe_bound
    return count_tokens(tokenizer, content, add_special_tokens=True)


def _context_cells(
    primary: Sequence[_CellItem], all_cells: Sequence[_CellItem]
) -> List[_CellItem]:
    if not primary:
        return []
    primary_ids = {item.source_cell_id for item in primary}
    min_row = min(item.cell.anchor_row for item in primary)
    max_row = max(item.cell.anchor_row for item in primary)
    min_col = min(item.cell.anchor_column for item in primary)
    max_col = max(item.cell.anchor_column for item in primary)
    result: List[_CellItem] = []
    for item in all_cells:
        if item.source_cell_id in primary_ids:
            continue
        role = item.cell.role
        row_overlap = item.cell.anchor_row <= max_row < item.cell.anchor_row + item.cell.rowspan or (
            min_row <= item.cell.anchor_row + item.cell.rowspan - 1
            and item.cell.anchor_row <= max_row
        )
        col_overlap = item.cell.anchor_column <= max_col < item.cell.anchor_column + item.cell.colspan or (
            min_col <= item.cell.anchor_column + item.cell.colspan - 1
            and item.cell.anchor_column <= max_col
        )
        if role in {CellRole.COLUMN_HEADER, CellRole.CORNER} and col_overlap:
            result.append(item)
        elif role is CellRole.ROW_HEADER and row_overlap:
            result.append(item)
    return result


def _render_items(
    base: Mapping[str, Any],
    primary: Sequence[_CellItem],
    all_cells: Sequence[_CellItem],
    tokenizer: TokenizerLike,
) -> Tuple[str, List[_CellItem], int]:
    context = _context_cells(primary, all_cells)
    context_ids = {item.source_cell_id for item in context}
    ordered = list(primary) + [item for item in context if item.source_cell_id not in context_ids.intersection({p.source_cell_id for p in primary})]
    ordered.sort(key=lambda item: (item.cell.anchor_row, item.cell.anchor_column, item.source_cell_id))
    content = _table_payload(base, ordered)
    count = _fit_count_or_safe_bound(tokenizer, content)
    if count <= EMBEDDING_TARGET_TOKENS:
        return content, context, count
    # Header context is optional repetition. Keep provenance in paths and use
    # the primary cells alone when the repeated structural context would exceed
    # the embedding budget.
    content = _table_payload(base, list(primary))
    count = _fit_count_or_safe_bound(tokenizer, content)
    return content, [], count


def _candidate_with_text(
    base: Mapping[str, Any], cell: NormalizedCell, text: str
) -> str:
    return _table_payload(base, [_CellItem(cell=cell, text=text)])


def _fragment_text(
    tokenizer: TokenizerLike,
    base: Mapping[str, Any],
    cell: NormalizedCell,
) -> List[str]:
    text = cell.normalized_text
    if not text:
        return [text]
    fragments: List[str] = []
    start = 0
    while start < len(text):
        low = start + 1
        high = len(text)
        best = None
        while low <= high:
            middle = (low + high) // 2
            candidate = _candidate_with_text(base, cell, text[start:middle])
            count = count_tokens(tokenizer, candidate, add_special_tokens=True)
            if count <= EMBEDDING_TARGET_TOKENS:
                best = middle
                low = middle + 1
            else:
                high = middle - 1
        if best is None:
            raise EmbeddingChunkingError(
                "CELL_FRAGMENTATION_NO_PROGRESS",
                f"cell {cell.source_cell_id} cannot fit even one character",
            )
        fragment = text[start:best]
        fragments.append(fragment)
        start = best
    if "".join(fragments) != text:
        raise EmbeddingChunkingError(
            "CELL_FRAGMENTATION_LOSS",
            f"fragment concatenation did not reproduce cell {cell.source_cell_id}",
        )
    return fragments


def _prepare_cell_items(
    tokenizer: TokenizerLike,
    base: Mapping[str, Any],
    cells: Sequence[NormalizedCell],
) -> List[_CellItem]:
    result: List[_CellItem] = []
    for cell in cells:
        candidate = _candidate_with_text(base, cell, cell.normalized_text)
        if _fit_count_or_safe_bound(tokenizer, candidate) <= EMBEDDING_TARGET_TOKENS:
            result.append(_CellItem(cell=cell, text=cell.normalized_text))
            continue
        fragments = _fragment_text(tokenizer, base, cell)
        count = len(fragments)
        for index, text in enumerate(fragments):
            result.append(
                _CellItem(
                    cell=cell,
                    text=text,
                    fragment=CellFragment(
                        source_cell_id=cell.source_cell_id,
                        fragment_index=index,
                        fragment_count=count,
                        text=text,
                        content_token_count=count_tokens(tokenizer, text, add_special_tokens=False),
                    ),
                )
            )
    return result


def _chunk_id(
    representation_id: str,
    chunk_index: int,
    primary_ids: Sequence[str],
    context_ids: Sequence[str],
    fragment: Optional[CellFragment],
) -> str:
    parts: List[Any] = [
        EMBEDDING_CHUNK_SCHEMA_VERSION,
        representation_id,
        CHUNKING_CONFIG_FINGERPRINT,
        chunk_index,
        "primary",
        *primary_ids,
        "context",
        *context_ids,
    ]
    if fragment is not None:
        parts.extend([
            "fragment",
            fragment.source_cell_id,
            fragment.fragment_index,
            CELL_FRAGMENTATION_ALGORITHM_VERSION,
            CHUNKING_CONFIG_FINGERPRINT,
        ])
    return _sha256_parts(parts)


def _make_table_chunks(
    representation: RetrievalRepresentation,
    table: NormalizedTable,
    tokenizer: TokenizerLike,
) -> List[EmbeddingChunk]:
    if representation.source_type is not EvidenceSource.TABLE:
        raise EmbeddingChunkingError("REPRESENTATION_TYPE_MISMATCH", "expected a TABLE representation")
    if representation.source_ref != table.normalized_table_id:
        raise EmbeddingChunkingError("TABLE_PROVENANCE_MISMATCH", "representation and table IDs differ")
    try:
        base = json.loads(representation.content)
    except (TypeError, ValueError) as error:
        raise EmbeddingChunkingError("REPRESENTATION_CONTENT_INVALID", "table content is not JSON") from error
    if not isinstance(base, dict) or list(base) != [
        "ticker", "company_name", "report_year", "statement_scope", "period_labels", "cells"
    ]:
        raise EmbeddingChunkingError("REPRESENTATION_CONTENT_INVALID", "table content has the wrong shape")
    cells = sorted(table.cells, key=lambda cell: (cell.anchor_row, cell.anchor_column, cell.source_cell_id))
    if len(base["cells"]) != len(cells):
        raise EmbeddingChunkingError("TABLE_CELL_COUNT_MISMATCH", "representation and normalized table cells differ")
    for payload_cell, cell in zip(base["cells"], cells):
        expected_row = [entry.label for entry in cell.row_path]
        expected_col = [entry.label for entry in cell.column_path]
        if payload_cell["row_path"] != expected_row or payload_cell["column_path"] != expected_col or payload_cell["text"] != cell.normalized_text:
            raise EmbeddingChunkingError("TABLE_CELL_CONTENT_MISMATCH", f"cell {cell.source_cell_id} differs from representation")
    items = _prepare_cell_items(tokenizer, base, cells)
    context_cells = [_CellItem(cell=cell, text=cell.normalized_text) for cell in cells]
    chunks_data: List[Tuple[List[_CellItem], List[_CellItem], str, int]] = []
    rows: Dict[int, List[_CellItem]] = {}
    for item in items:
        rows.setdefault(item.cell.anchor_row, []).append(item)

    current: List[_CellItem] = []

    def flush() -> None:
        nonlocal current
        if not current:
            return
        content, context, count = _render_items(base, current, context_cells, tokenizer)
        chunks_data.append((list(current), context, content, count))
        current = []

    for row_index in sorted(rows):
        row = rows[row_index]
        if any(item.fragment is not None for item in row):
            flush()
            group: List[_CellItem] = []
            for item in row:
                if item.fragment is not None:
                    if group:
                        rendered, context, count = _render_items(base, group, context_cells, tokenizer)
                        chunks_data.append((list(group), context, rendered, count))
                        group = []
                    content = _table_payload(base, [item])
                    token_count = _content_token_count(tokenizer, content)
                    chunks_data.append(([item], [], content, token_count))
                    continue
                candidate_group = group + [item]
                try:
                    rendered, context, count = _render_items(base, candidate_group, context_cells, tokenizer)
                except EmbeddingChunkingError:
                    rendered, context, count = "", [], EMBEDDING_HARD_MAX_TOKENS + 1
                if group and count > EMBEDDING_TARGET_TOKENS:
                    rendered, context, count = _render_items(base, group, context_cells, tokenizer)
                    chunks_data.append((list(group), context, rendered, count))
                    group = [item]
                elif not group and count > EMBEDDING_TARGET_TOKENS:
                    raise EmbeddingChunkingError(
                        "CELL_FRAGMENTATION_INCOMPLETE",
                        f"cell {item.source_cell_id} still exceeds the target after fragmentation",
                    )
                else:
                    group = candidate_group
            if group:
                rendered, context, count = _render_items(base, group, context_cells, tokenizer)
                chunks_data.append((list(group), context, rendered, count))
            continue
        candidate = current + row
        try:
            rendered, context, count = _render_items(base, candidate, context_cells, tokenizer)
        except EmbeddingChunkingError:
            rendered = ""
            context = []
            count = EMBEDDING_HARD_MAX_TOKENS + 1
        if candidate and count <= EMBEDDING_TARGET_TOKENS:
            current = candidate
            continue
        flush()
        # An oversized row is split into consecutive anchor-column groups.
        group: List[_CellItem] = []
        for item in row:
            candidate_group = group + [item]
            try:
                rendered, context, count = _render_items(base, candidate_group, context_cells, tokenizer)
            except EmbeddingChunkingError:
                rendered = ""
                context = []
                count = EMBEDDING_HARD_MAX_TOKENS + 1
            if group and count > EMBEDDING_TARGET_TOKENS:
                rendered, context, count = _render_items(base, group, context_cells, tokenizer)
                chunks_data.append((list(group), context, rendered, count))
                group = [item]
            elif not group and count > EMBEDDING_TARGET_TOKENS:
                # A non-fragmented single cell should be impossible here; keep
                # this assertion explicit so no truncation can be introduced.
                raise EmbeddingChunkingError(
                    "CELL_FRAGMENTATION_INCOMPLETE",
                    f"cell {item.source_cell_id} still exceeds the target after fragmentation",
                )
            else:
                group = candidate_group
        if group:
            rendered, context, count = _render_items(base, group, context_cells, tokenizer)
            chunks_data.append((list(group), context, rendered, count))
    flush()
    if not chunks_data:
        content = _table_payload(base, [])
        chunks_data.append(([], [], content, _content_token_count(tokenizer, content)))

    chunks: List[EmbeddingChunk] = []
    chunk_count = len(chunks_data)
    for index, (primary, context, content, token_count) in enumerate(chunks_data):
        token_count = _content_token_count(tokenizer, content)
        primary_ids = [item.source_cell_id for item in primary]
        context_ids = [item.source_cell_id for item in context]
        rows_used = [item.cell.anchor_row for item in primary]
        cols_used = [item.cell.anchor_column for item in primary]
        fragment = primary[0].fragment if len(primary) == 1 else None
        chunks.append(
            EmbeddingChunk(
                schema_version=EMBEDDING_CHUNK_SCHEMA_VERSION,
                chunk_id=_chunk_id(representation.representation_id, index, primary_ids, context_ids, fragment),
                representation_id=representation.representation_id,
                source_type=EvidenceSource.TABLE,
                chunk_index=index,
                chunk_count=chunk_count,
                primary_source_cell_ids=primary_ids,
                context_source_cell_ids=context_ids,
                anchor_row_start=(min(rows_used) if rows_used else None),
                anchor_row_end=(max(rows_used) if rows_used else None),
                anchor_column_start=(min(cols_used) if cols_used else None),
                anchor_column_end=(max(cols_used) if cols_used else None),
                content=content,
                content_token_count=token_count,
                chunking_config_fingerprint=CHUNKING_CONFIG_FINGERPRINT,
                cell_fragment=fragment,
            )
        )
    return chunks


def _make_text_chunk(
    representation: RetrievalRepresentation,
    tokenizer: TokenizerLike,
) -> EmbeddingChunk:
    if representation.source_type is not EvidenceSource.TEXT:
        raise EmbeddingChunkingError("REPRESENTATION_TYPE_MISMATCH", "expected a TEXT representation")
    count = _content_token_count(tokenizer, representation.content)
    if count > EMBEDDING_TARGET_TOKENS:
        raise EmbeddingChunkingError(
            "TEXT_REPRESENTATION_OVER_TARGET",
            f"text representation {representation.representation_id} has {count} tokens",
        )
    return EmbeddingChunk(
        schema_version=EMBEDDING_CHUNK_SCHEMA_VERSION,
        chunk_id=_chunk_id(representation.representation_id, 0, [], [], None),
        representation_id=representation.representation_id,
        source_type=EvidenceSource.TEXT,
        chunk_index=0,
        chunk_count=1,
        primary_source_cell_ids=[],
        context_source_cell_ids=[],
        anchor_row_start=None,
        anchor_row_end=None,
        anchor_column_start=None,
        anchor_column_end=None,
        content=representation.content,
        content_token_count=count,
        chunking_config_fingerprint=CHUNKING_CONFIG_FINGERPRINT,
    )


def build_embedding_chunks(
    representations: Sequence[RetrievalRepresentation],
    normalized_tables: Mapping[str, NormalizedTable],
    tokenizer: TokenizerLike,
) -> List[EmbeddingChunk]:
    """Build complete, deterministic chunks in representation source order."""

    result: List[EmbeddingChunk] = []
    for representation in representations:
        if representation.source_type is EvidenceSource.TABLE:
            table = normalized_tables.get(representation.source_ref)
            if table is None:
                raise EmbeddingChunkingError(
                    "TABLE_PROVENANCE_MISSING",
                    f"no normalized table for {representation.source_ref}",
                )
            result.extend(_make_table_chunks(representation, table, tokenizer))
        else:
            result.append(_make_text_chunk(representation, tokenizer))
    return result
