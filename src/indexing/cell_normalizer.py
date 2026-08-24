"""Deterministic derived-text normalization for TASK-021 source cells."""

from __future__ import annotations

from html import unescape
import unicodedata

from src.indexing.schemas import SourceCell
from src.understanding.schemas import SchemaValidationError


def _decode_html_entities_to_fixed_point(value: str) -> str:
    """Decode nested standard references so normalization remains idempotent."""

    decoded = value
    while True:
        candidate = unescape(decoded)
        if candidate == decoded:
            return decoded
        decoded = candidate


def normalize_cell_text(value: str) -> str:
    """Return canonical derived cell text without modifying the source string."""

    if not isinstance(value, str):
        raise SchemaValidationError("cell text must be a string")

    normalized = unicodedata.normalize("NFKC", value)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = _decode_html_entities_to_fixed_point(normalized)
    normalized = unicodedata.normalize("NFKC", normalized)
    return " ".join(normalized.split())


def normalize_source_cell_text(cell: SourceCell) -> str:
    """Normalize only ``extracted_text`` while retaining every source field."""

    if not isinstance(cell, SourceCell):
        raise SchemaValidationError("cell must be a SourceCell")
    return normalize_cell_text(cell.extracted_text)
