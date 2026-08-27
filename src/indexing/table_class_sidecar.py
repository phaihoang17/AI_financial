"""Additive source-backed TableClass provenance sidecar.

The canonical M2 corpus stays immutable.  This module re-runs only the
deterministic M2A document parse over the approved raw reports and records, for
each parsed table, a conservative source-backed :class:`TableClass` hint derived
from the report source text that immediately precedes the table on the same
page.

Matching is deliberately conservative:

* same-page evidence only (the context window is clamped to the page and to the
  end of the previous table on that page),
* a fixed-size deterministic preceding-context window,
* Unicode NFKC normalization,
* literal, case-insensitive matching with a small bounded amount of
  non-alphanumeric formatting slack between tokens,
* no fuzzy matching, no semantic / LLM classification,
* a table whose window matches more than one distinct ``TableClass`` (or matches
  nothing) gets no hint.

Actual table provenance is never inferred from metric mappings.  ``MetricTableMapping``
is expected-table routing for the Supervisor; it is not evidence provenance and
is not consulted here.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import unicodedata
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from src.indexing.corpus_artifact import CORPUS_ARTIFACT_SCHEMA_VERSION
from src.indexing.document_parser import parse_document
from src.indexing.embedding_artifact_builder import (
    EmbeddingArtifactBuilderError,
    _validate_input_manifest,
)
from src.indexing.provenance_sidecar import (
    _atomic_write,
    _atomic_write_json,
    _canonical_json,
    _require_sha256,
    _safe_relative,
    _sha256_file,
    _source_from_inventory,
)
from src.indexing.schemas import (
    M2A_SCHEMA_VERSION,
    NORMALIZATION_VERSION,
    REPRESENTATION_VERSION,
    Page,
    SourceSpan,
    SourceTable,
    TableParseStatus,
)
from src.indexing.schemas import _require_exact_keys, _require_mapping, _require_string
from src.understanding.schemas import SchemaValidationError
from src.supervisor.schemas import TableClass


TABLE_CLASS_SIDECAR_SCHEMA_VERSION = "m2-table-class-sidecar-v1"
TABLE_CLASS_SIDECAR_BUILD_VERSION = "m2-table-class-sidecar-build-v1"
TABLE_CLASS_REGISTRY_VERSION = "m2-table-class-registry-v1"
TABLE_CLASS_SIDECAR_SQLITE_FILE = "table_class.sqlite"

# Deterministic preceding-context window, in source-text characters.
PRECEDING_CONTEXT_WINDOW_CHARS = 1200
# Bounded non-alphanumeric formatting slack allowed between literal tokens.
MAX_TOKEN_GAP_CHARS = 8

_LETTER_OR_DIGIT = "0-9A-Za-zÀ-ỹ̀-ͯ"
_GAP = r"[^%s]{0,%d}" % (_LETTER_OR_DIGIT, MAX_TOKEN_GAP_CHARS)
_TOKEN = re.compile(r"[%s]+" % _LETTER_OR_DIGIT)


class TableClassSidecarError(RuntimeError):
    """A typed sidecar build, compatibility, or lookup failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def _nfkc(value: str) -> str:
    return unicodedata.normalize("NFKC", value)


@dataclass(frozen=True)
class TableClassRegistryEntry:
    """One literal recogniser: a statement-form code or a statement title."""

    entry_id: str
    table_class: TableClass
    literal: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "entry_id", _require_string(self.entry_id, "entry_id"))
        if not isinstance(self.table_class, TableClass):
            raise SchemaValidationError("entry.table_class must be a TableClass")
        object.__setattr__(self, "literal", _require_string(self.literal, "literal"))
        tokens = [match.group(0) for match in _TOKEN.finditer(_nfkc(self.literal))]
        if not tokens:
            raise SchemaValidationError("entry.literal must contain a token")
        pattern = (
            r"(?<![%s])" % _LETTER_OR_DIGIT
            + _GAP.join(re.escape(token) for token in tokens)
            + r"(?![%s])" % _LETTER_OR_DIGIT
        )
        object.__setattr__(self, "_regex", re.compile(pattern, re.IGNORECASE))

    def search(self, text: str) -> Optional[re.Match]:
        return self._regex.search(text)


# Ordered, deterministic.  Codes and titles are separate entries so that a table
# preceded by both still yields exactly one class (a "multiple match" that
# agrees), while a window mixing forms of different classes yields no hint.
TABLE_CLASS_REGISTRY: Tuple[TableClassRegistryEntry, ...] = (
    TableClassRegistryEntry("BALANCE_SHEET::B01-DN", TableClass.BALANCE_SHEET, "B01-DN"),
    TableClassRegistryEntry("BALANCE_SHEET::B01-DN/HN", TableClass.BALANCE_SHEET, "B01-DN/HN"),
    TableClassRegistryEntry(
        "BALANCE_SHEET::BANG-CAN-DOI-KE-TOAN",
        TableClass.BALANCE_SHEET,
        "BẢNG CÂN ĐỐI KẾ TOÁN",
    ),
    TableClassRegistryEntry("INCOME_STATEMENT::B02-DN", TableClass.INCOME_STATEMENT, "B02-DN"),
    TableClassRegistryEntry(
        "INCOME_STATEMENT::BAO-CAO-KQHDKD",
        TableClass.INCOME_STATEMENT,
        "BÁO CÁO KẾT QUẢ HOẠT ĐỘNG KINH DOANH",
    ),
    TableClassRegistryEntry("CASH_FLOW_STATEMENT::B03-DN", TableClass.CASH_FLOW_STATEMENT, "B03-DN"),
    TableClassRegistryEntry(
        "CASH_FLOW_STATEMENT::BAO-CAO-LCTT",
        TableClass.CASH_FLOW_STATEMENT,
        "BÁO CÁO LƯU CHUYỂN TIỀN TỆ",
    ),
    TableClassRegistryEntry("NOTES::B09-DN", TableClass.NOTES, "B09-DN"),
    TableClassRegistryEntry(
        "NOTES::THUYET-MINH-BCTC",
        TableClass.NOTES,
        "THUYẾT MINH BÁO CÁO TÀI CHÍNH",
    ),
)


def _registry_fingerprint(
    registry: Sequence[TableClassRegistryEntry] = TABLE_CLASS_REGISTRY,
) -> str:
    return sha256(
        _canonical_json(
            {
                "registry_version": TABLE_CLASS_REGISTRY_VERSION,
                "max_token_gap_chars": MAX_TOKEN_GAP_CHARS,
                "preceding_context_window_chars": PRECEDING_CONTEXT_WINDOW_CHARS,
                "entries": [
                    {
                        "entry_id": entry.entry_id,
                        "table_class": entry.table_class.value,
                        "literal": entry.literal,
                    }
                    for entry in registry
                ],
            }
        )
    ).hexdigest()


@dataclass(frozen=True)
class TableClassHint:
    """One source-backed table classification for exactly one parsed table."""

    hint_id: str
    report_id: str
    page_id: str
    table_id: str
    table_class: TableClass
    registry_entry_id: str
    matched_source_span: SourceSpan
    matched_text: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "hint_id", _require_string(self.hint_id, "hint_id"))
        object.__setattr__(self, "report_id", _require_string(self.report_id, "report_id"))
        object.__setattr__(self, "page_id", _require_string(self.page_id, "page_id"))
        object.__setattr__(self, "table_id", _require_string(self.table_id, "table_id"))
        if not isinstance(self.table_class, TableClass):
            raise SchemaValidationError("hint.table_class must be a TableClass")
        object.__setattr__(
            self, "registry_entry_id", _require_string(self.registry_entry_id, "registry_entry_id")
        )
        if not isinstance(self.matched_source_span, SourceSpan):
            raise SchemaValidationError("hint.matched_source_span must be a SourceSpan")
        object.__setattr__(self, "matched_text", _require_string(self.matched_text, "matched_text"))
        if self.matched_source_span.end - self.matched_source_span.start != len(self.matched_text):
            raise SchemaValidationError("matched_source_span width must equal matched_text length")
        expected = compute_hint_id(
            self.report_id,
            self.page_id,
            self.table_id,
            self.table_class,
            self.registry_entry_id,
            self.matched_source_span,
        )
        if self.hint_id != expected:
            raise SchemaValidationError("hint_id does not match the canonical hint payload")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hint_id": self.hint_id,
            "report_id": self.report_id,
            "page_id": self.page_id,
            "table_id": self.table_id,
            "table_class": self.table_class.value,
            "registry_entry_id": self.registry_entry_id,
            "matched_source_span": {
                "start": self.matched_source_span.start,
                "end": self.matched_source_span.end,
            },
            "matched_text": self.matched_text,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "TableClassHint":
        data = _require_mapping(value, "TableClassHint")
        _require_exact_keys(
            data,
            {
                "hint_id",
                "report_id",
                "page_id",
                "table_id",
                "table_class",
                "registry_entry_id",
                "matched_source_span",
                "matched_text",
            },
            "TableClassHint",
        )
        try:
            table_class = TableClass(data["table_class"])
        except ValueError as error:
            raise SchemaValidationError("TableClassHint.table_class is invalid") from error
        return cls(
            hint_id=data["hint_id"],
            report_id=data["report_id"],
            page_id=data["page_id"],
            table_id=data["table_id"],
            table_class=table_class,
            registry_entry_id=data["registry_entry_id"],
            matched_source_span=SourceSpan.from_dict(
                data["matched_source_span"], "TableClassHint.matched_source_span"
            ),
            matched_text=data["matched_text"],
        )


def compute_hint_id(
    report_id: str,
    page_id: str,
    table_id: str,
    table_class: TableClass,
    registry_entry_id: str,
    matched_source_span: SourceSpan,
) -> str:
    return sha256(
        _canonical_json(
            {
                "sidecar_schema_version": TABLE_CLASS_SIDECAR_SCHEMA_VERSION,
                "registry_version": TABLE_CLASS_REGISTRY_VERSION,
                "report_id": report_id,
                "page_id": page_id,
                "table_id": table_id,
                "table_class": table_class.value,
                "registry_entry_id": registry_entry_id,
                "matched_source_span": {
                    "start": matched_source_span.start,
                    "end": matched_source_span.end,
                },
            }
        )
    ).hexdigest()


@dataclass(frozen=True)
class _EntryMatch:
    entry: TableClassRegistryEntry
    span: SourceSpan
    text: str


def _preceding_window_bounds(page: Page, table: SourceTable) -> Tuple[int, int]:
    """Same-page preceding window, clamped past the previous table on the page."""

    window_end = table.source_span.start
    lower_bound = page.content_span.start
    for other in page.tables:
        if other.source_span.end <= table.source_span.start:
            lower_bound = max(lower_bound, other.source_span.end)
    window_start = max(lower_bound, window_end - PRECEDING_CONTEXT_WINDOW_CHARS)
    return window_start, window_end


def _match_entries(
    raw_text: str, window_start: int, window_end: int
) -> List[_EntryMatch]:
    raw_window = raw_text[window_start:window_end]
    norm_window = _nfkc(raw_window)
    # Only search the normalised window when normalisation preserved offsets, so
    # every recorded span still indexes the raw source text exactly.
    haystack = norm_window if len(norm_window) == len(raw_window) else raw_window
    matches: List[_EntryMatch] = []
    for entry in TABLE_CLASS_REGISTRY:
        found = entry.search(haystack)
        if found is None:
            continue
        abs_start = window_start + found.start()
        abs_end = window_start + found.end()
        raw_slice = raw_text[abs_start:abs_end]
        # Fail closed: the recorded span must round-trip and must still match the
        # literal after NFKC + case folding.
        if entry.search(_nfkc(raw_slice)) is None:
            continue
        matches.append(_EntryMatch(entry, SourceSpan(abs_start, abs_end), raw_slice))
    return matches


def classify_table(
    report_id: str, raw_text: str, page: Page, table: SourceTable
) -> Optional[TableClassHint]:
    """Return a conservative source-backed hint, or ``None`` when ambiguous."""

    if table.parse_status is TableParseStatus.UNPARSEABLE:
        return None
    window_start, window_end = _preceding_window_bounds(page, table)
    if window_end <= window_start:
        return None
    matches = _match_entries(raw_text, window_start, window_end)
    if not matches:
        return None
    distinct = {match.entry.table_class for match in matches}
    if len(distinct) != 1:
        # Conflicting / ambiguous evidence -> no hint.
        return None
    table_class = next(iter(distinct))
    chosen = min(
        matches,
        key=lambda match: (
            match.span.start,
            match.span.end,
            match.entry.entry_id,
        ),
    )
    return TableClassHint(
        hint_id=compute_hint_id(
            report_id,
            page.page_id,
            table.table_id,
            table_class,
            chosen.entry.entry_id,
            chosen.span,
        ),
        report_id=report_id,
        page_id=page.page_id,
        table_id=table.table_id,
        table_class=table_class,
        registry_entry_id=chosen.entry.entry_id,
        matched_source_span=chosen.span,
        matched_text=chosen.text,
    )


def classify_report(source: Any) -> List[TableClassHint]:
    """Deterministically classify every parseable table in one report."""

    document = parse_document(source)
    hints: List[TableClassHint] = []
    for page in document.pages:
        for table in page.tables:
            hint = classify_table(source.report_id, source.raw_text, page, table)
            if hint is not None:
                hints.append(hint)
    return hints


def _expected_metadata(corpus: Any) -> Dict[str, str]:
    return {
        "sidecar_schema_version": TABLE_CLASS_SIDECAR_SCHEMA_VERSION,
        "build_version": TABLE_CLASS_SIDECAR_BUILD_VERSION,
        "registry_version": TABLE_CLASS_REGISTRY_VERSION,
        "registry_fingerprint": _registry_fingerprint(),
        "corpus_artifact_schema_version": CORPUS_ARTIFACT_SCHEMA_VERSION,
        "corpus_artifact_id": corpus.artifact_id,
        "corpus_manifest_sha256": corpus.manifest_sha256,
        "corpus_fingerprint": corpus.corpus_fingerprint,
        "m2a_schema_version": M2A_SCHEMA_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "representation_version": REPRESENTATION_VERSION,
    }


def _create_database(path: Path, *, metadata: Mapping[str, str]) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.executescript(
            """
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE table_class_hints (
                hint_id TEXT PRIMARY KEY,
                report_id TEXT NOT NULL,
                page_id TEXT NOT NULL,
                table_id TEXT NOT NULL,
                table_class TEXT NOT NULL,
                registry_entry_id TEXT NOT NULL,
                matched_span_start INTEGER NOT NULL,
                matched_span_end INTEGER NOT NULL,
                matched_text TEXT NOT NULL,
                canonical_json TEXT NOT NULL,
                UNIQUE (report_id, page_id, table_id)
            );
            CREATE INDEX table_class_hints_lookup_idx
                ON table_class_hints(report_id, page_id, table_id);
            """
        )
        connection.executemany(
            "INSERT INTO metadata(key, value) VALUES (?, ?)", sorted(metadata.items())
        )
        connection.commit()
    except sqlite3.DatabaseError as error:
        connection.close()
        raise TableClassSidecarError("SIDECAR_SQLITE_CREATE_FAILED", str(error)) from error
    return connection


def _insert_hints(connection: sqlite3.Connection, hints: Sequence[TableClassHint]) -> None:
    rows = [
        (
            hint.hint_id,
            hint.report_id,
            hint.page_id,
            hint.table_id,
            hint.table_class.value,
            hint.registry_entry_id,
            hint.matched_source_span.start,
            hint.matched_source_span.end,
            hint.matched_text,
            _canonical_json(hint.to_dict()).decode("utf-8"),
        )
        for hint in hints
    ]
    try:
        connection.execute("BEGIN")
        connection.executemany(
            "INSERT INTO table_class_hints VALUES (?,?,?,?,?,?,?,?,?,?)", rows
        )
        connection.commit()
    except sqlite3.IntegrityError as error:
        connection.rollback()
        raise TableClassSidecarError("SIDECAR_DUPLICATE_ID", str(error)) from error
    except sqlite3.DatabaseError as error:
        connection.rollback()
        raise TableClassSidecarError("SIDECAR_SQLITE_WRITE_FAILED", str(error)) from error


def _build_id(corpus_fingerprint: str, content_fingerprint: str) -> str:
    return sha256(
        _canonical_json(
            {
                "sidecar_schema_version": TABLE_CLASS_SIDECAR_SCHEMA_VERSION,
                "build_version": TABLE_CLASS_SIDECAR_BUILD_VERSION,
                "registry_fingerprint": _registry_fingerprint(),
                "corpus_fingerprint": corpus_fingerprint,
                "content_fingerprint": content_fingerprint,
            }
        )
    ).hexdigest()


def build_table_class_sidecar(
    corpus_artifact_root: str | Path,
    raw_corpus_root: str | Path,
    output_root: str | Path,
) -> Dict[str, Any]:
    """Materialize an immutable additive TableClass sidecar for one M2 corpus."""

    try:
        corpus = _validate_input_manifest(corpus_artifact_root, verify_hashes=True)
    except EmbeddingArtifactBuilderError as error:
        raise TableClassSidecarError("SIDECAR_CORPUS_INVALID", str(error)) from error
    root = Path(output_root)
    raw_root = Path(raw_corpus_root)
    staging = root / ".staging" / TABLE_CLASS_SIDECAR_BUILD_VERSION
    if staging.exists():
        raise TableClassSidecarError("SIDECAR_STAGING_EXISTS", str(staging))
    staging.mkdir(parents=True, exist_ok=False)
    database = staging / TABLE_CLASS_SIDECAR_SQLITE_FILE
    metadata = _expected_metadata(corpus)
    connection = _create_database(database, metadata=metadata)
    digest = sha256()
    hint_count = 0
    try:
        for item in corpus.manifest["reports"]:
            source = _source_from_inventory(raw_root, corpus.corpus_id, item)
            try:
                hints = classify_report(source)
            except SchemaValidationError as error:
                raise TableClassSidecarError(
                    "SIDECAR_CLASSIFICATION_FAILED", f"{source.source_ref}: {error}"
                ) from error
            _insert_hints(connection, hints)
            for hint in hints:
                digest.update(b"T\0")
                digest.update(_canonical_json(hint.to_dict()))
                digest.update(b"\n")
            hint_count += len(hints)
        connection.execute(
            "INSERT INTO metadata(key, value) VALUES (?, ?)", ("hint_count", str(hint_count))
        )
        connection.execute(
            "INSERT INTO metadata(key, value) VALUES (?, ?)",
            ("content_fingerprint", digest.hexdigest()),
        )
        connection.commit()
    finally:
        connection.close()
    artifact_id = _build_id(corpus.corpus_fingerprint, digest.hexdigest())
    artifact = root / "artifacts" / artifact_id
    database_hash = _sha256_file(database)
    manifest: Dict[str, Any] = {
        "sidecar_schema_version": TABLE_CLASS_SIDECAR_SCHEMA_VERSION,
        "artifact_status": "COMMITTED",
        "artifact_id": artifact_id,
        "artifact_directory": f"artifacts/{artifact_id}",
        "build_version": TABLE_CLASS_SIDECAR_BUILD_VERSION,
        "registry_version": TABLE_CLASS_REGISTRY_VERSION,
        "registry_fingerprint": _registry_fingerprint(),
        "build_config_fingerprint": sha256(_canonical_json(metadata)).hexdigest(),
        "corpus_artifact_schema_version": CORPUS_ARTIFACT_SCHEMA_VERSION,
        "corpus_artifact_id": corpus.artifact_id,
        "corpus_manifest_sha256": corpus.manifest_sha256,
        "corpus_fingerprint": corpus.corpus_fingerprint,
        "m2a_schema_version": M2A_SCHEMA_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "representation_version": REPRESENTATION_VERSION,
        "report_count": len(corpus.manifest["reports"]),
        "table_class_hint_count": hint_count,
        "content_fingerprint": digest.hexdigest(),
        "database": {"file": TABLE_CLASS_SIDECAR_SQLITE_FILE, "sha256": database_hash},
    }
    try:
        _atomic_write_json(staging / "manifest.json", manifest)
        artifact.parent.mkdir(parents=True, exist_ok=True)
        if artifact.exists():
            existing = artifact / TABLE_CLASS_SIDECAR_SQLITE_FILE
            if not existing.is_file() or _sha256_file(existing) != database_hash:
                raise TableClassSidecarError("SIDECAR_IMMUTABLE_ARTIFACT_CONFLICT", artifact_id)
            shutil.rmtree(staging)
        else:
            os.replace(staging, artifact)
        _atomic_write_json(root / "manifest.json", manifest)
        _atomic_write(root / "CURRENT", f"{artifact_id}\n".encode("utf-8"))
        return manifest
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def validate_table_class_sidecar(
    sidecar_root: str | Path, corpus_artifact_root: str | Path
) -> Dict[str, Any]:
    """Validate immutable sidecar bytes and exact M2 corpus + registry compatibility."""

    try:
        corpus = _validate_input_manifest(corpus_artifact_root, verify_hashes=True)
    except EmbeddingArtifactBuilderError as error:
        raise TableClassSidecarError("SIDECAR_CORPUS_INVALID", str(error)) from error
    root = Path(sidecar_root)
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise TableClassSidecarError("SIDECAR_MANIFEST_INVALID", str(root)) from error
    if not isinstance(manifest, dict):
        raise TableClassSidecarError("SIDECAR_MANIFEST_INVALID", "manifest must be an object")
    expected = {"artifact_status": "COMMITTED", **_expected_metadata(corpus)}
    for key, value in expected.items():
        if manifest.get(key) != value:
            code = (
                "SIDECAR_REGISTRY_MISMATCH"
                if key in {"registry_version", "registry_fingerprint", "build_version"}
                else "SIDECAR_CORPUS_MISMATCH"
            )
            raise TableClassSidecarError(code, f"manifest.{key} is incompatible")
    artifact = root / _safe_relative(manifest.get("artifact_directory"), "artifact_directory")
    database_entry = manifest.get("database")
    if not isinstance(database_entry, Mapping):
        raise TableClassSidecarError("SIDECAR_MANIFEST_INVALID", "database is missing")
    database = artifact / _safe_relative(database_entry.get("file"), "database.file")
    if not database.is_file() or _sha256_file(database) != _require_sha256(
        database_entry.get("sha256"), "database.sha256"
    ):
        raise TableClassSidecarError("SIDECAR_DATABASE_CORRUPT", str(database))
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        try:
            if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise TableClassSidecarError(
                    "SIDECAR_DATABASE_CORRUPT", "SQLite integrity check failed"
                )
            metadata = dict(connection.execute("SELECT key, value FROM metadata"))
            for key, value in _expected_metadata(corpus).items():
                if metadata.get(key) != value:
                    raise TableClassSidecarError("SIDECAR_DATABASE_INCOMPATIBLE", key)
            hint_count = connection.execute(
                "SELECT COUNT(*) FROM table_class_hints"
            ).fetchone()[0]
        finally:
            connection.close()
    except sqlite3.DatabaseError as error:
        raise TableClassSidecarError("SIDECAR_DATABASE_CORRUPT", str(database)) from error
    if hint_count != manifest.get("table_class_hint_count"):
        raise TableClassSidecarError(
            "SIDECAR_DATABASE_INCOMPATIBLE", "hint count differs from manifest"
        )
    if manifest.get("report_count") != len(corpus.manifest["reports"]):
        raise TableClassSidecarError(
            "SIDECAR_DATABASE_INCOMPATIBLE", "report count differs from corpus"
        )
    return {
        "integrity": "PASS",
        "artifact_id": manifest["artifact_id"],
        "table_class_hint_count": hint_count,
    }


class TableClassSidecar:
    """Read-only exact source-backed TableClass provenance lookup.

    Construction validates the sidecar against the exact M2 corpus identity and
    the pinned registry; a corrupt or incompatible sidecar raises here and the
    caller must fail closed rather than fall back to a heuristic.
    """

    def __init__(self, sidecar_root: str | Path, corpus_artifact_root: str | Path) -> None:
        self.integrity = validate_table_class_sidecar(sidecar_root, corpus_artifact_root)
        root = Path(sidecar_root)
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        self._database = root / manifest["artifact_directory"] / manifest["database"]["file"]

    def _rows(self, sql: str, parameters: Sequence[Any]) -> List[Tuple[Any, ...]]:
        try:
            connection = sqlite3.connect(f"file:{self._database}?mode=ro", uri=True)
            try:
                return connection.execute(sql, parameters).fetchall()
            finally:
                connection.close()
        except sqlite3.DatabaseError as error:
            raise TableClassSidecarError(
                "SIDECAR_DATABASE_CORRUPT", str(self._database)
            ) from error

    def get_hint(
        self, report_id: str, page_id: str, table_id: str
    ) -> Optional[TableClassHint]:
        rows = self._rows(
            """SELECT canonical_json FROM table_class_hints
               WHERE report_id=? AND page_id=? AND table_id=?""",
            (report_id, page_id, table_id),
        )
        if not rows:
            return None
        return TableClassHint.from_dict(json.loads(rows[0][0]))

    def table_class_for(
        self, report_id: str, page_id: str, table_id: str
    ) -> Optional[TableClass]:
        """Exact (report, page, table) lookup; a missing hint returns ``None``."""

        rows = self._rows(
            """SELECT table_class FROM table_class_hints
               WHERE report_id=? AND page_id=? AND table_id=?""",
            (report_id, page_id, table_id),
        )
        if not rows:
            return None
        try:
            return TableClass(rows[0][0])
        except ValueError as error:
            raise TableClassSidecarError(
                "SIDECAR_DATABASE_INCOMPATIBLE", f"invalid table_class {rows[0][0]!r}"
            ) from error


def main() -> int:  # pragma: no cover - CLI entry point
    import argparse

    parser = argparse.ArgumentParser(
        description="Build the immutable additive M2 TableClass provenance sidecar"
    )
    parser.add_argument("--corpus-artifact", required=True)
    parser.add_argument("--raw-corpus-root", default="data/ViFinQA/financial_statements")
    parser.add_argument("--output-root", default="artifacts/m2-table-class-v1")
    arguments = parser.parse_args()
    manifest = build_table_class_sidecar(
        arguments.corpus_artifact, arguments.raw_corpus_root, arguments.output_root
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
