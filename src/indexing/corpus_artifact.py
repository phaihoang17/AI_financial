"""Deterministic, resumable M2 corpus-chunk artifact builder.

This module materializes the model-independent M2 output through the
EmbeddingChunk boundary.  It intentionally does not create embeddings, FAISS
indexes, or lexical indexes.
"""

from __future__ import annotations

from dataclasses import dataclass
import csv
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from src.indexing.document_parser import parse_document
from src.indexing.embedding_chunker import (
    BGE_M3_MODEL_ID,
    BGE_M3_REVISION,
    CHUNKING_CONFIG_FINGERPRINT,
    load_bge_m3_tokenizer,
    build_embedding_chunks,
)
from src.indexing.embedding_schemas import (
    EMBEDDING_CHUNK_SCHEMA_VERSION,
    EMBEDDING_TARGET_TOKENS,
    EmbeddingChunk,
)
from src.indexing.grid_builder import build_logical_table_grid
from src.indexing.ids import content_sha256, make_report_id
from src.indexing.normalized_table_assembler import assemble_normalized_table
from src.indexing.paragraph_extractor import extract_paragraphs
from src.indexing.retrieval_representation_builder import build_retrieval_representations
from src.indexing.scale_unit_hint_extractor import extract_scale_unit_hints
from src.indexing.schemas import (
    M2A_SCHEMA_VERSION,
    NORMALIZATION_VERSION,
    REPRESENTATION_VERSION,
    NormalizedTable,
    ReportSource,
    RetrievalRepresentation,
    TableParseStatus,
)
from src.indexing.table_text_linker import link_tables_to_paragraphs
from src.supervisor.schemas import EvidenceSource
from src.understanding.schemas import SchemaValidationError, StatementScope


CORPUS_ARTIFACT_SCHEMA_VERSION = "m2-corpus-artifact-v1"
CORPUS_RECORD_SCHEMA_VERSION = "m2-corpus-chunk-record-v1"
DEFAULT_MAX_RECORDS_PER_SHARD = 50_000
DEFAULT_MAX_SHARD_BYTES = 256 * 1024 * 1024
_EXTRACTED_SUFFIX = "_extracted.txt"
_CHECKPOINT_NAME = "checkpoint.json"
_MANIFEST_NAME = "manifest.json"
_STATE_VERSION = "m2-corpus-build-state-v1"
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


class CorpusArtifactError(RuntimeError):
    """A deterministic build or verification failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class CorpusReport:
    path: Path
    source_ref: str
    ticker: str
    company_name: str
    report_year: int
    statement_scope: Optional[StatementScope]
    report_id: str
    content_sha256: str
    byte_size: int

    def inventory_dict(self) -> Dict[str, Any]:
        return {
            "report_id": self.report_id,
            "source_ref": self.source_ref,
            "ticker": self.ticker,
            "company_name": self.company_name,
            "report_year": self.report_year,
            "statement_scope": None if self.statement_scope is None else self.statement_scope.value,
            "content_sha256": self.content_sha256,
            "byte_size": self.byte_size,
        }


@dataclass
class _Stats:
    report_count: int = 0
    representation_count: int = 0
    table_representation_count: int = 0
    text_representation_count: int = 0
    chunk_count: int = 0
    table_chunk_count: int = 0
    text_chunk_count: int = 0
    source_cell_count: int = 0
    emitted_source_cell_count: int = 0

    def to_dict(self) -> Dict[str, int]:
        return {
            "report_count": self.report_count,
            "representation_count": self.representation_count,
            "table_representation_count": self.table_representation_count,
            "text_representation_count": self.text_representation_count,
            "chunk_count": self.chunk_count,
            "table_chunk_count": self.table_chunk_count,
            "text_chunk_count": self.text_chunk_count,
            "source_cell_count": self.source_cell_count,
            "emitted_source_cell_count": self.emitted_source_cell_count,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "_Stats":
        fields = cls().__dict__.keys()
        if set(value) != set(fields):
            raise CorpusArtifactError("CHECKPOINT_INVALID", "checkpoint statistics have the wrong shape")
        parsed: Dict[str, int] = {}
        for field in fields:
            raw = value[field]
            if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
                raise CorpusArtifactError("CHECKPOINT_INVALID", f"checkpoint statistic {field} is invalid")
            parsed[field] = raw
        return cls(**parsed)


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise CorpusArtifactError("SERIALIZATION_ERROR", "value is not canonical JSON") from error


def _canonical_sorted_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise CorpusArtifactError("SERIALIZATION_ERROR", "value is not canonical JSON") from error


def _sha256_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("wb") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _atomic_write_json(path: Path, value: Any, *, sorted_keys: bool = False) -> None:
    payload = _canonical_sorted_bytes(value) if sorted_keys else _canonical_bytes(value)
    _atomic_write_bytes(path, payload)


def _statement_scope(directory_name: str) -> Optional[StatementScope]:
    if directory_name.endswith("_consolidated"):
        return StatementScope.HOP_NHAT
    if directory_name.endswith("_separate"):
        return StatementScope.RIENG
    if directory_name.endswith("_aggregated"):
        return None
    # Unlabeled report directories are intentionally represented as unknown;
    # scope is never inferred from OCR text or from a filename fragment.
    return None


def _load_company_names(path: Path) -> Dict[str, str]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = csv.DictReader(handle)
            if rows.fieldnames is None or "Mã CK" not in rows.fieldnames or "Tên công ty" not in rows.fieldnames:
                raise CorpusArtifactError("COMPANY_METADATA_INVALID", "code_stock.csv has unexpected headers")
            result = {}
            for row in rows:
                ticker = row.get("Mã CK", "")
                name = row.get("Tên công ty", "")
                if not ticker or not name or ticker in result:
                    raise CorpusArtifactError("COMPANY_METADATA_INVALID", "company metadata is incomplete or duplicated")
                result[ticker] = name
            return result
    except CorpusArtifactError:
        raise
    except OSError as error:
        raise CorpusArtifactError("COMPANY_METADATA_READ_FAILED", str(error)) from error


def _report_from_path(
    path: Path,
    corpus_root: Path,
    corpus_id: str,
    company_names: Mapping[str, str],
) -> CorpusReport:
    try:
        relative = path.relative_to(corpus_root)
    except ValueError as error:
        raise CorpusArtifactError("REPORT_PATH_INVALID", str(path)) from error
    parts = relative.parts
    if len(parts) != 4 or not path.name.endswith(_EXTRACTED_SUFFIX):
        raise CorpusArtifactError("REPORT_PATH_INVALID", f"unsupported report path: {relative.as_posix()}")
    ticker, year_text, directory_name, _ = parts
    if not ticker or not ticker.isupper() or not year_text.isdigit() or len(year_text) != 4:
        raise CorpusArtifactError("REPORT_METADATA_INVALID", f"cannot read ticker/year from {relative.as_posix()}")
    if ticker not in company_names:
        raise CorpusArtifactError("REPORT_METADATA_INVALID", f"ticker {ticker} is not in code_stock.csv")
    report_year = int(year_text)
    raw_bytes = path.read_bytes()
    try:
        raw_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise CorpusArtifactError("REPORT_UTF8_INVALID", str(path)) from error
    source_ref = relative.as_posix()
    return CorpusReport(
        path=path,
        source_ref=source_ref,
        ticker=ticker,
        company_name=company_names[ticker],
        report_year=report_year,
        statement_scope=_statement_scope(directory_name),
        report_id=make_report_id(corpus_id, source_ref),
        content_sha256=content_sha256(raw_text),
        byte_size=len(raw_bytes),
    )


def enumerate_reports(
    corpus_root: str | Path,
    *,
    corpus_id: str = "ViFinQA",
    company_metadata: str | Path | None = None,
) -> List[CorpusReport]:
    """Return the complete source inventory in deterministic POSIX order."""

    root = Path(corpus_root).resolve()
    metadata = Path(company_metadata) if company_metadata is not None else root.parent / "code_stock.csv"
    company_names = _load_company_names(metadata)
    paths = sorted(root.glob("*/*/*/*_extracted.txt"), key=lambda item: item.relative_to(root).as_posix())
    reports = [_report_from_path(path, root, corpus_id, company_names) for path in paths]
    if not reports:
        raise CorpusArtifactError("EMPTY_CORPUS", f"no extracted reports found below {root}")
    report_ids = [report.report_id for report in reports]
    if len(report_ids) != len(set(report_ids)):
        raise CorpusArtifactError("DUPLICATE_REPORT_ID", "source inventory contains duplicate report IDs")
    return reports


def _inventory_hash(reports: Sequence[CorpusReport]) -> str:
    return _sha256_bytes(_canonical_sorted_bytes([report.inventory_dict() for report in reports]))


def _artifact_id(
    reports: Sequence[CorpusReport],
    *,
    corpus_id: str,
    max_records_per_shard: int,
    max_shard_bytes: int,
) -> str:
    payload = {
        "artifact_schema_version": CORPUS_ARTIFACT_SCHEMA_VERSION,
        "record_schema_version": CORPUS_RECORD_SCHEMA_VERSION,
        "corpus_id": corpus_id,
        "source_inventory_sha256": _inventory_hash(reports),
        "m2a_schema_version": M2A_SCHEMA_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "representation_version": REPRESENTATION_VERSION,
        "chunk_schema_version": EMBEDDING_CHUNK_SCHEMA_VERSION,
        "chunking_config_fingerprint": CHUNKING_CONFIG_FINGERPRINT,
        "tokenizer_model_id": BGE_M3_MODEL_ID,
        "tokenizer_revision": BGE_M3_REVISION,
        "max_records_per_shard": max_records_per_shard,
        "max_shard_bytes": max_shard_bytes,
    }
    return _sha256_bytes(_canonical_sorted_bytes(payload))


def _make_source(report: CorpusReport, corpus_id: str) -> ReportSource:
    raw_text = report.path.read_text(encoding="utf-8")
    if content_sha256(raw_text) != report.content_sha256:
        raise CorpusArtifactError("SOURCE_CHANGED", report.source_ref)
    return ReportSource(
        schema_version=M2A_SCHEMA_VERSION,
        corpus_id=corpus_id,
        report_id=report.report_id,
        source_ref=report.source_ref,
        ticker=report.ticker,
        company_name=report.company_name,
        report_year=report.report_year,
        document_name=report.path.name,
        statement_scope=report.statement_scope,
        raw_text=raw_text,
        content_sha256=report.content_sha256,
    )


def _report_pipeline(
    report: CorpusReport,
    *,
    corpus_id: str,
    tokenizer: Any,
) -> Tuple[ReportSource, List[RetrievalRepresentation], List[EmbeddingChunk], int]:
    source = _make_source(report, corpus_id)
    document = parse_document(source)
    paragraphs = extract_paragraphs(source, document)
    normalized_tables: List[NormalizedTable] = []
    for page in document.pages:
        for table in page.tables:
            if table.parse_status is TableParseStatus.UNPARSEABLE:
                continue
            try:
                grid = build_logical_table_grid(table)
                normalized_tables.append(assemble_normalized_table(table, grid))
            except SchemaValidationError as error:
                raise CorpusArtifactError(
                    "TABLE_NORMALIZATION_FAILED",
                    f"{report.source_ref}: {table.table_id}: {error}",
                ) from error
    represented_table_ids = {table.source_table_id for table in normalized_tables}
    links = [
        link
        for link in link_tables_to_paragraphs(source, document, paragraphs)
        if link.table_id in represented_table_ids
    ]
    hints = extract_scale_unit_hints(
        source,
        document,
        normalized_tables,
        paragraphs,
        links,
    )
    representations = build_retrieval_representations(
        source,
        normalized_tables,
        paragraphs,
        links,
        hints,
    )
    normalized_by_id = {table.normalized_table_id: table for table in normalized_tables}
    chunks = build_embedding_chunks(representations, normalized_by_id, tokenizer)

    expected_cells = {
        cell.source_cell_id
        for table in normalized_tables
        for cell in table.cells
    }
    emitted_cells = {
        source_cell_id
        for chunk in chunks
        if chunk.source_type is EvidenceSource.TABLE
        for source_cell_id in chunk.primary_source_cell_ids
    }
    if expected_cells != emitted_cells:
        missing = sorted(expected_cells - emitted_cells)
        extra = sorted(emitted_cells - expected_cells)
        raise CorpusArtifactError(
            "SOURCE_CELL_COVERAGE_FAILED",
            f"{report.source_ref}: missing={missing[:3]} extra={extra[:3]}",
        )
    representation_ids = {representation.representation_id for representation in representations}
    chunk_representation_ids = {chunk.representation_id for chunk in chunks}
    if representation_ids != chunk_representation_ids:
        raise CorpusArtifactError(
            "REPRESENTATION_COVERAGE_FAILED",
            f"{report.source_ref}: missing={sorted(representation_ids - chunk_representation_ids)[:3]}",
        )
    if any(chunk.content_token_count > EMBEDDING_TARGET_TOKENS for chunk in chunks):
        raise CorpusArtifactError("CHUNK_OVER_BUDGET", report.source_ref)
    return source, representations, chunks, len(expected_cells)


def _record(
    source: ReportSource,
    representation: RetrievalRepresentation,
    chunk: EmbeddingChunk,
    source_cell_ids: Sequence[str],
    source_order: int,
) -> Dict[str, Any]:
    return {
        "record_schema_version": CORPUS_RECORD_SCHEMA_VERSION,
        "source_order": source_order,
        "report": {
            "report_id": source.report_id,
            "source_ref": source.source_ref,
            "content_sha256": source.content_sha256,
            "ticker": source.ticker,
            "company_name": source.company_name,
            "report_year": source.report_year,
            "statement_scope": None if source.statement_scope is None else source.statement_scope.value,
        },
        "representation": representation.to_dict(),
        "source_cell_ids": list(source_cell_ids),
        "chunk": chunk.to_dict(),
    }


class _ShardWriter:
    def __init__(
        self,
        directory: Path,
        *,
        max_records: int,
        max_bytes: int,
        shard_id: int = 0,
        record_count: int = 0,
        byte_count: int = 0,
    ) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.max_records = max_records
        self.max_bytes = max_bytes
        self.shard_id = shard_id
        self.record_count = record_count
        self.byte_count = byte_count
        self._handle: Optional[Any] = None
        if self.record_count:
            path = self.path
            if not path.exists():
                raise CorpusArtifactError("CHECKPOINT_INVALID", f"missing current shard: {path.name}")
            actual_size = path.stat().st_size
            if actual_size < self.byte_count:
                raise CorpusArtifactError("CHECKPOINT_INVALID", f"current shard is shorter than checkpoint: {path.name}")
            with path.open("r+b") as handle:
                handle.truncate(self.byte_count)
            self._handle = path.open("ab")
        elif self.path.exists():
            self.path.unlink()

    @property
    def path(self) -> Path:
        return self.directory / f"shard-{self.shard_id:06d}.jsonl"

    def _open(self) -> Any:
        if self._handle is None:
            self._handle = self.path.open("ab")
        return self._handle

    def _rotate(self) -> None:
        if self._handle is not None:
            self._handle.flush()
            os.fsync(self._handle.fileno())
            self._handle.close()
            self._handle = None
        self.shard_id += 1
        self.record_count = 0
        self.byte_count = 0

    def write(self, payload: Mapping[str, Any]) -> None:
        line = _canonical_bytes(payload) + b"\n"
        if len(line) > self.max_bytes:
            raise CorpusArtifactError("RECORD_EXCEEDS_SHARD_LIMIT", f"record is {len(line)} bytes")
        if self.record_count and (
            self.record_count >= self.max_records
            or self.byte_count + len(line) > self.max_bytes
        ):
            self._rotate()
        handle = self._open()
        handle.write(line)
        self.record_count += 1
        self.byte_count += len(line)

    def checkpoint(self) -> None:
        if self._handle is not None:
            self._handle.flush()
            os.fsync(self._handle.fileno())

    def close(self) -> None:
        self.checkpoint()
        if self._handle is not None:
            self._handle.close()
            self._handle = None


def _checkpoint_payload(
    *,
    artifact_id: str,
    source_inventory_sha256: str,
    next_report_index: int,
    next_source_order: int,
    writer: _ShardWriter,
    stats: _Stats,
) -> Dict[str, Any]:
    return {
        "state_version": _STATE_VERSION,
        "artifact_id": artifact_id,
        "source_inventory_sha256": source_inventory_sha256,
        "next_report_index": next_report_index,
        "next_source_order": next_source_order,
        "current_shard_id": writer.shard_id,
        "current_shard_record_count": writer.record_count,
        "current_shard_byte_count": writer.byte_count,
        "stats": stats.to_dict(),
    }


def _load_checkpoint(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise CorpusArtifactError("CHECKPOINT_INVALID", str(error)) from error
    if not isinstance(value, dict):
        raise CorpusArtifactError("CHECKPOINT_INVALID", "checkpoint must be an object")
    return value


def _recover_staging(staging: Path, artifact_id: str, inventory_hash: str) -> Tuple[Mapping[str, Any], _Stats, _ShardWriter]:
    checkpoint_path = staging / _CHECKPOINT_NAME
    state = _load_checkpoint(checkpoint_path)
    if state.get("state_version") != _STATE_VERSION or state.get("artifact_id") != artifact_id:
        raise CorpusArtifactError("CHECKPOINT_INVALID", "checkpoint version or artifact ID differs")
    if state.get("source_inventory_sha256") != inventory_hash:
        raise CorpusArtifactError("CHECKPOINT_INVALID", "checkpoint source inventory differs")
    try:
        next_report_index = int(state["next_report_index"])
        next_source_order = int(state["next_source_order"])
        shard_id = int(state["current_shard_id"])
        shard_count = int(state["current_shard_record_count"])
        shard_bytes = int(state["current_shard_byte_count"])
    except (KeyError, TypeError, ValueError) as error:
        raise CorpusArtifactError("CHECKPOINT_INVALID", "checkpoint cursor is invalid") from error
    if min(next_report_index, next_source_order, shard_id, shard_count, shard_bytes) < 0:
        raise CorpusArtifactError("CHECKPOINT_INVALID", "checkpoint cursor is negative")
    shards_dir = staging / "shards"
    for path in shards_dir.glob("shard-*.jsonl"):
        match = re.fullmatch(r"shard-(\d{6})\.jsonl", path.name)
        if match is None:
            raise CorpusArtifactError("CHECKPOINT_INVALID", f"unexpected staging file: {path.name}")
        if int(match.group(1)) > shard_id:
            path.unlink()
    stats_value = state.get("stats")
    if not isinstance(stats_value, dict):
        raise CorpusArtifactError("CHECKPOINT_INVALID", "checkpoint statistics are missing")
    stats = _Stats.from_dict(stats_value)
    writer = _ShardWriter(
        shards_dir,
        max_records=int(state["max_records_per_shard"]),
        max_bytes=int(state["max_shard_bytes"]),
        shard_id=shard_id,
        record_count=shard_count,
        byte_count=shard_bytes,
    )
    return state, stats, writer


def _manifest_base(
    reports: Sequence[CorpusReport],
    *,
    artifact_id: str,
    corpus_id: str,
    max_records_per_shard: int,
    max_shard_bytes: int,
) -> Dict[str, Any]:
    return {
        "manifest_schema_version": CORPUS_ARTIFACT_SCHEMA_VERSION,
        "record_schema_version": CORPUS_RECORD_SCHEMA_VERSION,
        "artifact_id": artifact_id,
        "artifact_status": "COMMITTED",
        "corpus_id": corpus_id,
        "source_inventory_sha256": _inventory_hash(reports),
        "reports": [report.inventory_dict() for report in reports],
        "source_order": "relative_posix_source_ref",
        "record_order": "report_source_order_then_representation_then_chunk_index",
        "m2a_schema_version": M2A_SCHEMA_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "representation_version": REPRESENTATION_VERSION,
        "chunk_schema_version": EMBEDDING_CHUNK_SCHEMA_VERSION,
        "chunking_config_fingerprint": CHUNKING_CONFIG_FINGERPRINT,
        "tokenizer": {
            "model_id": BGE_M3_MODEL_ID,
            "revision": BGE_M3_REVISION,
            "max_tokens": EMBEDDING_TARGET_TOKENS,
        },
        "format": "jsonl-utf8-canonical",
        "max_records_per_shard": max_records_per_shard,
        "max_shard_bytes": max_shard_bytes,
    }


def _shard_manifest(path: Path) -> Dict[str, Any]:
    records = 0
    table_chunks = 0
    text_chunks = 0
    with path.open("rb") as handle:
        for line in handle:
            if not line.endswith(b"\n"):
                raise CorpusArtifactError("SHARD_INVALID", f"shard line is not newline terminated: {path.name}")
            records += 1
            try:
                value = json.loads(line)
                chunk = value["chunk"]
                source_type = chunk["source_type"]
            except (ValueError, KeyError, TypeError) as error:
                raise CorpusArtifactError("SHARD_INVALID", f"invalid record in {path.name}") from error
            if source_type == EvidenceSource.TABLE.value:
                table_chunks += 1
            elif source_type == EvidenceSource.TEXT.value:
                text_chunks += 1
            else:
                raise CorpusArtifactError("SHARD_INVALID", f"unknown source type in {path.name}")
    return {
        "shard_id": path.stem,
        "file": f"shards/{path.name}",
        "record_count": records,
        "table_chunk_count": table_chunks,
        "text_chunk_count": text_chunks,
        "byte_count": path.stat().st_size,
        "sha256": _sha256_file(path),
    }


def _write_manifest(staging: Path, manifest: Mapping[str, Any]) -> None:
    _atomic_write_json(staging / _MANIFEST_NAME, manifest)


def verify_corpus_artifact(artifact: str | Path) -> Dict[str, Any]:
    """Verify all physical records, hashes, provenance, counts, and coverage."""

    root = Path(artifact)
    manifest_path = root / _MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise CorpusArtifactError("MANIFEST_INVALID", str(error)) from error
    if not isinstance(manifest, dict) or manifest.get("manifest_schema_version") != CORPUS_ARTIFACT_SCHEMA_VERSION:
        raise CorpusArtifactError("MANIFEST_INVALID", "unsupported corpus artifact manifest")
    if manifest.get("artifact_status") != "COMMITTED":
        raise CorpusArtifactError("MANIFEST_INVALID", "artifact is not committed")
    reports = manifest.get("reports")
    shards = manifest.get("shards")
    if not isinstance(reports, list) or not isinstance(shards, list):
        raise CorpusArtifactError("MANIFEST_INVALID", "reports or shards are missing")
    expected_report_ids = {item.get("report_id") for item in reports if isinstance(item, dict)}
    if len(expected_report_ids) != len(reports) or None in expected_report_ids:
        raise CorpusArtifactError("MANIFEST_INVALID", "report inventory is invalid")
    seen_report_ids: Set[str] = set()
    seen_chunk_ids: Set[str] = set()
    expected_cells_by_rep: Dict[str, Set[str]] = {}
    emitted_cells_by_rep: Dict[str, Set[str]] = {}
    seen_rep_ids: Set[str] = set()
    source_order = 0
    table_chunks = 0
    text_chunks = 0
    record_count = 0
    physical_shards: List[Dict[str, Any]] = []
    for shard_entry in shards:
        if not isinstance(shard_entry, dict):
            raise CorpusArtifactError("MANIFEST_INVALID", "shard entry is not an object")
        relative = shard_entry.get("file")
        if not isinstance(relative, str) or not relative.startswith("shards/"):
            raise CorpusArtifactError("MANIFEST_INVALID", "shard path is invalid")
        path = root / relative
        if not path.is_file():
            raise CorpusArtifactError("SHARD_MISSING", relative)
        actual_hash = _sha256_file(path)
        if actual_hash != shard_entry.get("sha256"):
            raise CorpusArtifactError("SHARD_HASH_MISMATCH", relative)
        shard_records = 0
        shard_tables = 0
        shard_text = 0
        with path.open("rb") as handle:
            for line in handle:
                try:
                    value = json.loads(line)
                    if _canonical_bytes(value) + b"\n" != line:
                        raise CorpusArtifactError("RECORD_NOT_CANONICAL", relative)
                    if value.get("record_schema_version") != CORPUS_RECORD_SCHEMA_VERSION:
                        raise CorpusArtifactError("RECORD_SCHEMA_MISMATCH", relative)
                    if value.get("source_order") != source_order:
                        raise CorpusArtifactError("SOURCE_ORDER_MISMATCH", relative)
                    report_meta = value["report"]
                    representation = RetrievalRepresentation.from_dict(value["representation"])
                    chunk = EmbeddingChunk.from_dict(value["chunk"])
                    source_cell_ids = value["source_cell_ids"]
                except CorpusArtifactError:
                    raise
                except (ValueError, KeyError, TypeError, SchemaValidationError) as error:
                    raise CorpusArtifactError("RECORD_INVALID", f"{relative}: {error}") from error
                if not isinstance(report_meta, dict) or report_meta.get("report_id") != representation.report_id:
                    raise CorpusArtifactError("PROVENANCE_INVALID", f"{relative}: report/representation mismatch")
                if representation.to_dict() != value["representation"] or chunk.to_dict() != value["chunk"]:
                    raise CorpusArtifactError("ROUND_TRIP_MISMATCH", relative)
                if chunk.chunk_id in seen_chunk_ids:
                    raise CorpusArtifactError("DUPLICATE_CHUNK_ID", chunk.chunk_id)
                seen_chunk_ids.add(chunk.chunk_id)
                if chunk.representation_id != representation.representation_id or chunk.source_type is not representation.source_type:
                    raise CorpusArtifactError("PROVENANCE_INVALID", f"{relative}: chunk/representation mismatch")
                if not isinstance(source_cell_ids, list) or any(not isinstance(item, str) for item in source_cell_ids):
                    raise CorpusArtifactError("PROVENANCE_INVALID", f"{relative}: source_cell_ids invalid")
                if chunk.source_type is EvidenceSource.TABLE:
                    table_chunks += 1
                    shard_tables += 1
                    if representation.table_id is None or representation.paragraph_id is not None:
                        raise CorpusArtifactError("PROVENANCE_INVALID", f"{relative}: table identity invalid")
                    expected_cells_by_rep.setdefault(representation.representation_id, set(source_cell_ids))
                    if expected_cells_by_rep[representation.representation_id] != set(source_cell_ids):
                        raise CorpusArtifactError("PROVENANCE_INVALID", f"{relative}: source cell set changed")
                    emitted_cells_by_rep.setdefault(representation.representation_id, set()).update(chunk.primary_source_cell_ids)
                else:
                    text_chunks += 1
                    shard_text += 1
                    if representation.paragraph_id is None or representation.table_id is not None:
                        raise CorpusArtifactError("PROVENANCE_INVALID", f"{relative}: text identity invalid")
                seen_rep_ids.add(representation.representation_id)
                seen_report_ids.add(representation.report_id)
                source_order += 1
                shard_records += 1
        physical = _shard_manifest(path)
        if physical["record_count"] != shard_entry.get("record_count"):
            raise CorpusArtifactError("SHARD_COUNT_MISMATCH", relative)
        if physical["byte_count"] != shard_entry.get("byte_count"):
            raise CorpusArtifactError("SHARD_SIZE_MISMATCH", relative)
        if physical["table_chunk_count"] != shard_entry.get("table_chunk_count") or physical["text_chunk_count"] != shard_entry.get("text_chunk_count"):
            raise CorpusArtifactError("SHARD_TYPE_COUNT_MISMATCH", relative)
        physical_shards.append(physical)
    for representation_id, expected in expected_cells_by_rep.items():
        if emitted_cells_by_rep.get(representation_id, set()) != expected:
            raise CorpusArtifactError("SOURCE_CELL_COVERAGE_FAILED", representation_id)
    if seen_report_ids != expected_report_ids:
        raise CorpusArtifactError("REPORT_COVERAGE_FAILED", f"missing={sorted(expected_report_ids - seen_report_ids)[:3]}")
    expected = {
        "report_count": len(expected_report_ids),
        "representation_count": len(seen_rep_ids),
        "chunk_count": record_count if False else source_order,
        "table_chunk_count": table_chunks,
        "text_chunk_count": text_chunks,
        "shard_count": len(physical_shards),
    }
    if manifest.get("report_count") != expected["report_count"]:
        raise CorpusArtifactError("MANIFEST_COUNT_MISMATCH", "report_count")
    for name in ("representation_count", "chunk_count", "table_chunk_count", "text_chunk_count", "shard_count"):
        if manifest.get(name) != expected[name]:
            raise CorpusArtifactError("MANIFEST_COUNT_MISMATCH", name)
    if manifest.get("omitted_representation_count") != 0 or manifest.get("omitted_source_cell_count") != 0:
        raise CorpusArtifactError("MANIFEST_COVERAGE_MISMATCH", "omitted counts must be zero")
    if manifest.get("source_cell_count") != sum(len(value) for value in expected_cells_by_rep.values()):
        raise CorpusArtifactError("MANIFEST_COUNT_MISMATCH", "source_cell_count")
    if manifest.get("emitted_source_cell_count") != sum(len(value) for value in emitted_cells_by_rep.values()):
        raise CorpusArtifactError("MANIFEST_COUNT_MISMATCH", "emitted_source_cell_count")
    return {
        "artifact": str(root),
        "report_count": len(expected_report_ids),
        "representation_count": len(seen_rep_ids),
        "chunk_count": source_order,
        "table_chunk_count": table_chunks,
        "text_chunk_count": text_chunks,
        "source_cell_count": sum(len(value) for value in expected_cells_by_rep.values()),
        "emitted_source_cell_count": sum(len(value) for value in emitted_cells_by_rep.values()),
        "shard_count": len(physical_shards),
        "integrity": "PASS",
    }


def build_corpus_artifact(
    corpus_root: str | Path,
    artifact_root: str | Path,
    *,
    corpus_id: str = "ViFinQA",
    company_metadata: str | Path | None = None,
    max_records_per_shard: int = DEFAULT_MAX_RECORDS_PER_SHARD,
    max_shard_bytes: int = DEFAULT_MAX_SHARD_BYTES,
    cache_dir: str | Path | None = None,
    resume: bool = True,
) -> Path:
    """Build and atomically publish the complete M2 chunk artifact."""

    if max_records_per_shard < 1 or max_shard_bytes < 1:
        raise CorpusArtifactError("INVALID_SHARD_CONFIG", "shard bounds must be positive")
    reports = enumerate_reports(corpus_root, corpus_id=corpus_id, company_metadata=company_metadata)
    inventory_hash = _inventory_hash(reports)
    artifact_id = _artifact_id(
        reports,
        corpus_id=corpus_id,
        max_records_per_shard=max_records_per_shard,
        max_shard_bytes=max_shard_bytes,
    )
    root = Path(artifact_root)
    final = root / artifact_id
    if final.exists():
        try:
            verify_corpus_artifact(final)
        except CorpusArtifactError as error:
            raise CorpusArtifactError("IMMUTABLE_ARTIFACT_CONFLICT", f"{final}: {error}") from error
        return final
    staging = root / ".staging" / artifact_id
    staging.mkdir(parents=True, exist_ok=True)
    shards_dir = staging / "shards"
    checkpoint_path = staging / _CHECKPOINT_NAME
    if checkpoint_path.exists() and resume:
        state, stats, writer = _recover_staging(staging, artifact_id, inventory_hash)
        if int(state.get("max_records_per_shard", max_records_per_shard)) != max_records_per_shard or int(state.get("max_shard_bytes", max_shard_bytes)) != max_shard_bytes:
            raise CorpusArtifactError("CHECKPOINT_INVALID", "shard configuration differs")
        next_report_index = int(state["next_report_index"])
        next_source_order = int(state["next_source_order"])
    elif checkpoint_path.exists() and not resume:
        raise CorpusArtifactError("STAGING_EXISTS", f"resumable staging exists at {staging}")
    else:
        stats = _Stats()
        next_report_index = 0
        next_source_order = 0
        writer = _ShardWriter(
            shards_dir,
            max_records=max_records_per_shard,
            max_bytes=max_shard_bytes,
        )
        _atomic_write_json(
            checkpoint_path,
            {
                "state_version": _STATE_VERSION,
                "artifact_id": artifact_id,
                "source_inventory_sha256": inventory_hash,
                "next_report_index": 0,
                "next_source_order": 0,
                "current_shard_id": 0,
                "current_shard_record_count": 0,
                "current_shard_byte_count": 0,
                "max_records_per_shard": max_records_per_shard,
                "max_shard_bytes": max_shard_bytes,
                "stats": stats.to_dict(),
            },
        )
    if not checkpoint_path.exists():
        raise CorpusArtifactError("CHECKPOINT_INVALID", "checkpoint was not initialized")
    # The checkpoint loader intentionally requires these configuration fields;
    # old/incomplete staging directories are not silently reused.
    current_state = _load_checkpoint(checkpoint_path)
    if current_state.get("max_records_per_shard") != max_records_per_shard or current_state.get("max_shard_bytes") != max_shard_bytes:
        raise CorpusArtifactError("CHECKPOINT_INVALID", "shard configuration is absent or differs")
    tokenizer = None
    try:
        tokenizer = load_bge_m3_tokenizer(cache_dir=cache_dir)
        for report_index in range(next_report_index, len(reports)):
            report = reports[report_index]
            source, representations, chunks, source_cell_count = _report_pipeline(
                report,
                corpus_id=corpus_id,
                tokenizer=tokenizer,
            )
            normalized_source_cells: Dict[str, List[str]] = {}
            for representation in representations:
                if representation.source_type is EvidenceSource.TABLE:
                    # All chunks for this representation carry the same source
                    # cell list; derive it from the primary IDs plus any
                    # lossless fragment/context metadata in the chunk stream.
                    normalized_source_cells.setdefault(representation.representation_id, [])
            representation_by_id = {representation.representation_id: representation for representation in representations}
            source_cell_ids_by_rep: Dict[str, List[str]] = {}
            for chunk in chunks:
                if chunk.source_type is EvidenceSource.TABLE:
                    source_cell_ids_by_rep.setdefault(chunk.representation_id, [])
                    source_cell_ids_by_rep[chunk.representation_id].extend(chunk.primary_source_cell_ids)
            # The report pipeline has already checked coverage globally. Use
            # the first-seen source-cell order from representation content
            # chunks, deduplicated deterministically.
            for representation in representations:
                if representation.source_type is not EvidenceSource.TABLE:
                    continue
                seen: Set[str] = set()
                ordered: List[str] = []
                for source_cell_id in source_cell_ids_by_rep.get(representation.representation_id, []):
                    if source_cell_id not in seen:
                        seen.add(source_cell_id)
                        ordered.append(source_cell_id)
                source_cell_ids_by_rep[representation.representation_id] = ordered
            for chunk in chunks:
                representation = representation_by_id[chunk.representation_id]
                cell_ids = source_cell_ids_by_rep.get(representation.representation_id, []) if chunk.source_type is EvidenceSource.TABLE else []
                payload = _record(source, representation, chunk, cell_ids, next_source_order)
                writer.write(payload)
                next_source_order += 1
            writer.checkpoint()
            stats.report_count += 1
            stats.representation_count += len(representations)
            stats.table_representation_count += sum(
                representation.source_type is EvidenceSource.TABLE for representation in representations
            )
            stats.text_representation_count += sum(
                representation.source_type is EvidenceSource.TEXT for representation in representations
            )
            stats.chunk_count += len(chunks)
            stats.table_chunk_count += sum(chunk.source_type is EvidenceSource.TABLE for chunk in chunks)
            stats.text_chunk_count += sum(chunk.source_type is EvidenceSource.TEXT for chunk in chunks)
            stats.source_cell_count += source_cell_count
            stats.emitted_source_cell_count += source_cell_count
            next_report_index = report_index + 1
            _atomic_write_json(
                checkpoint_path,
                _checkpoint_payload(
                    artifact_id=artifact_id,
                    source_inventory_sha256=inventory_hash,
                    next_report_index=next_report_index,
                    next_source_order=next_source_order,
                    writer=writer,
                    stats=stats,
                ) | {
                    "max_records_per_shard": max_records_per_shard,
                    "max_shard_bytes": max_shard_bytes,
                },
            )
    finally:
        if tokenizer is not None:
            del tokenizer
        writer.close()

    if next_report_index != len(reports):
        raise CorpusArtifactError("BUILD_INCOMPLETE", "not all reports were processed")
    manifest = _manifest_base(
        reports,
        artifact_id=artifact_id,
        corpus_id=corpus_id,
        max_records_per_shard=max_records_per_shard,
        max_shard_bytes=max_shard_bytes,
    )
    shard_paths = sorted(shards_dir.glob("shard-*.jsonl"))
    shard_entries = [_shard_manifest(path) for path in shard_paths]
    manifest.update(stats.to_dict())
    manifest["shard_count"] = len(shard_entries)
    manifest["shards"] = shard_entries
    manifest["omitted_representation_count"] = 0
    manifest["omitted_source_cell_count"] = 0
    _write_manifest(staging, manifest)
    verify_corpus_artifact(staging)
    final.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staging, final)
    # Published artifacts are immutable. The parent remains writable so a
    # separate artifact build can be staged without mutating this one.
    for path in [final / _MANIFEST_NAME, *sorted((final / "shards").glob("*.jsonl"))]:
        path.chmod(0o444)
    try:
        (final / _CHECKPOINT_NAME).unlink()
    except FileNotFoundError:
        pass
    return final


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Build the deterministic M2 corpus chunk artifact")
    parser.add_argument("--corpus-root", default="data/ViFinQA/financial_statements")
    parser.add_argument("--artifact-root", default="artifacts/m2-corpus-v1")
    parser.add_argument("--company-metadata", default="data/ViFinQA/code_stock.csv")
    parser.add_argument("--max-records-per-shard", type=int, default=DEFAULT_MAX_RECORDS_PER_SHARD)
    parser.add_argument("--max-shard-bytes", type=int, default=DEFAULT_MAX_SHARD_BYTES)
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()
    started = time.monotonic()
    path = build_corpus_artifact(
        args.corpus_root,
        args.artifact_root,
        company_metadata=args.company_metadata,
        max_records_per_shard=args.max_records_per_shard,
        max_shard_bytes=args.max_shard_bytes,
        cache_dir=args.cache_dir,
        resume=not args.no_resume,
    )
    result = verify_corpus_artifact(path)
    result["build_seconds"] = round(time.monotonic() - started, 3)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
