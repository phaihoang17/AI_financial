"""Stable, namespaced identifiers for canonical M2A artifacts."""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Iterable

from src.indexing.schemas import SourceSpan
from src.understanding.schemas import SchemaValidationError


ID_CONTRACT_VERSION = "m2a-id-v1"


def _require_part(value: Any, name: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise SchemaValidationError(f"{name} must be a string or integer")
    normalized = str(value)
    if not normalized:
        raise SchemaValidationError(f"{name} must be non-empty")
    return normalized


def _stable_id(prefix: str, parts: Iterable[Any]) -> str:
    digest = sha256()
    for raw_part in (ID_CONTRACT_VERSION, prefix, *parts):
        part = _require_part(raw_part, "id part").encode("utf-8")
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return f"{prefix}_{digest.hexdigest()}"


def content_sha256(raw_text: str) -> str:
    if not isinstance(raw_text, str):
        raise SchemaValidationError("raw_text must be a string")
    return sha256(raw_text.encode("utf-8")).hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise SchemaValidationError("value is not canonical-JSON serializable") from error
    return serialized.encode("utf-8")


def _span_parts(span: SourceSpan) -> tuple[int, int]:
    if not isinstance(span, SourceSpan):
        raise SchemaValidationError("span must be a SourceSpan")
    return span.start, span.end


def make_report_id(corpus_id: str, source_ref: str) -> str:
    return _stable_id("report", (corpus_id, source_ref))


def make_page_id(
    report_id: str, source_content_sha256: str, page_index: int, source_span: SourceSpan
) -> str:
    return _stable_id(
        "page", (report_id, source_content_sha256, page_index, *_span_parts(source_span))
    )


def make_table_id(page_id: str, table_index: int, source_span: SourceSpan) -> str:
    return _stable_id("table", (page_id, table_index, *_span_parts(source_span)))


def make_cell_id(
    table_id: str,
    source_row_index: int,
    source_cell_index: int,
    source_span: SourceSpan,
) -> str:
    return _stable_id(
        "cell",
        (
            table_id,
            source_row_index,
            source_cell_index,
            *_span_parts(source_span),
        ),
    )


def make_normalized_table_id(source_table_id: str, normalization_version: str) -> str:
    return _stable_id("ntable", (source_table_id, normalization_version))


def make_normalized_cell_id(source_cell_id: str, normalization_version: str) -> str:
    return _stable_id("ncell", (source_cell_id, normalization_version))


def make_header_id(
    normalized_table_id: str, axis: str, source_cell_ids: Iterable[str]
) -> str:
    ordered_ids = tuple(source_cell_ids)
    if not ordered_ids:
        raise SchemaValidationError("source_cell_ids must be non-empty")
    return _stable_id("header", (normalized_table_id, axis, *ordered_ids))


def make_paragraph_id(
    page_id: str, paragraph_index: int, source_span: SourceSpan
) -> str:
    return _stable_id(
        "paragraph", (page_id, paragraph_index, *_span_parts(source_span))
    )


def make_table_text_link_id(
    table_id: str, paragraph_id: str, relation: str, basis: str
) -> str:
    return _stable_id("link", (table_id, paragraph_id, relation, basis))


def make_scale_unit_hint_id(
    source_kind: str,
    source_ref: str,
    source_span: SourceSpan,
    normalized_hint_text: str,
) -> str:
    return _stable_id(
        "hint",
        (
            source_kind,
            source_ref,
            *_span_parts(source_span),
            normalized_hint_text,
        ),
    )


def make_representation_id(
    source_type: str, source_ref: str, representation_version: str
) -> str:
    return _stable_id("representation", (source_type, source_ref, representation_version))
