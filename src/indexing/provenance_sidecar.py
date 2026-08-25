"""Immutable M2A provenance sidecar for later M3 evidence retrieval.

The sidecar deliberately re-runs only deterministic M2A parsing, linking, and
hint extraction over the approved raw reports.  It never writes or regenerates
the committed M2 corpus artifact or any embedding artifact.
"""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import sqlite3
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from src.indexing.corpus_artifact import CORPUS_ARTIFACT_SCHEMA_VERSION
from src.indexing.document_parser import parse_document
from src.indexing.embedding_artifact_builder import (
    EmbeddingArtifactBuilderError,
    _validate_input_manifest,
)
from src.indexing.ids import content_sha256
from src.indexing.normalized_table_assembler import assemble_normalized_table
from src.indexing.paragraph_extractor import extract_paragraphs
from src.indexing.scale_unit_hint_extractor import extract_scale_unit_hints
from src.indexing.schemas import (
    M2A_SCHEMA_VERSION,
    NORMALIZATION_VERSION,
    REPRESENTATION_VERSION,
    NormalizedTable,
    ReportSource,
    ScaleUnitHint,
    TableParseStatus,
    TableTextLink,
)
from src.indexing.grid_builder import build_logical_table_grid
from src.indexing.table_text_linker import link_tables_to_paragraphs
from src.understanding.schemas import SchemaValidationError, StatementScope


PROVENANCE_SIDECAR_SCHEMA_VERSION = "m2-provenance-sidecar-v1"
PROVENANCE_SIDECAR_BUILD_VERSION = "m2-provenance-sidecar-build-v1"
PROVENANCE_SIDECAR_SQLITE_FILE = "provenance.sqlite"


class ProvenanceSidecarError(RuntimeError):
    """A typed sidecar build, compatibility, or lookup failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise ProvenanceSidecarError("SIDECAR_FILE_READ_FAILED", str(path)) from error
    return digest.hexdigest()


def _atomic_write(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as error:
        raise ProvenanceSidecarError("SIDECAR_WRITE_FAILED", str(path)) from error


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    _atomic_write(path, _canonical_json(value))


def _safe_relative(value: Any, path: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ProvenanceSidecarError("SIDECAR_MANIFEST_INVALID", f"{path} is required")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ProvenanceSidecarError("SIDECAR_MANIFEST_INVALID", f"{path} must be relative")
    return relative


def _require_sha256(value: Any, path: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ProvenanceSidecarError("SIDECAR_MANIFEST_INVALID", f"{path} must be SHA-256")
    try:
        int(value, 16)
    except ValueError as error:
        raise ProvenanceSidecarError("SIDECAR_MANIFEST_INVALID", f"{path} must be SHA-256") from error
    if value != value.lower():
        raise ProvenanceSidecarError("SIDECAR_MANIFEST_INVALID", f"{path} must be SHA-256")
    return value


def _source_from_inventory(raw_corpus_root: Path, corpus_id: str, item: Mapping[str, Any]) -> ReportSource:
    required = {
        "report_id", "source_ref", "ticker", "company_name", "report_year", "statement_scope",
        "content_sha256",
    }
    if not required <= set(item):
        raise ProvenanceSidecarError("SIDECAR_CORPUS_INVENTORY_INVALID", "report fields are missing")
    source_ref = item["source_ref"]
    if not isinstance(source_ref, str):
        raise ProvenanceSidecarError("SIDECAR_CORPUS_INVENTORY_INVALID", "source_ref is invalid")
    path = raw_corpus_root / source_ref
    try:
        raw_text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ProvenanceSidecarError("SIDECAR_SOURCE_READ_FAILED", source_ref) from error
    if content_sha256(raw_text) != item["content_sha256"]:
        raise ProvenanceSidecarError("SIDECAR_SOURCE_CHANGED", source_ref)
    raw_scope = item["statement_scope"]
    try:
        scope = None if raw_scope is None else StatementScope(raw_scope)
    except ValueError as error:
        raise ProvenanceSidecarError("SIDECAR_CORPUS_INVENTORY_INVALID", source_ref) from error
    return ReportSource(
        schema_version=M2A_SCHEMA_VERSION,
        corpus_id=corpus_id,
        report_id=item["report_id"],
        source_ref=source_ref,
        ticker=item["ticker"],
        company_name=item["company_name"],
        report_year=item["report_year"],
        document_name=path.name,
        statement_scope=scope,
        raw_text=raw_text,
        content_sha256=item["content_sha256"],
    )


def _extract_report_provenance(source: ReportSource) -> Tuple[List[ScaleUnitHint], List[TableTextLink]]:
    document = parse_document(source)
    paragraphs = extract_paragraphs(source, document)
    tables: List[NormalizedTable] = []
    for page in document.pages:
        for table in page.tables:
            if table.parse_status is TableParseStatus.UNPARSEABLE:
                continue
            try:
                tables.append(assemble_normalized_table(table, build_logical_table_grid(table)))
            except SchemaValidationError as error:
                raise ProvenanceSidecarError(
                    "SIDECAR_TABLE_NORMALIZATION_FAILED", f"{source.source_ref}: {table.table_id}"
                ) from error
    represented_table_ids = {table.source_table_id for table in tables}
    links = [
        link for link in link_tables_to_paragraphs(source, document, paragraphs)
        if link.table_id in represented_table_ids
    ]
    hints = extract_scale_unit_hints(source, document, tables, paragraphs, links)
    return hints, links


def _create_database(path: Path, *, metadata: Mapping[str, str]) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.executescript(
            """
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE scale_unit_hints (
                hint_id TEXT PRIMARY KEY,
                report_id TEXT NOT NULL,
                page_id TEXT NOT NULL,
                table_id TEXT,
                source_kind TEXT NOT NULL,
                source_ref TEXT NOT NULL,
                source_span_start INTEGER NOT NULL,
                source_span_end INTEGER NOT NULL,
                raw_hint_text TEXT NOT NULL,
                normalized_hint_text TEXT NOT NULL,
                scale_candidate TEXT,
                unit_candidate TEXT,
                status TEXT NOT NULL,
                canonical_json TEXT NOT NULL
            );
            CREATE INDEX scale_unit_hints_source_ref_idx ON scale_unit_hints(source_ref);
            CREATE INDEX scale_unit_hints_table_id_idx ON scale_unit_hints(table_id);
            CREATE INDEX scale_unit_hints_report_page_idx ON scale_unit_hints(report_id, page_id);
            CREATE TABLE table_text_links (
                link_id TEXT PRIMARY KEY,
                table_id TEXT NOT NULL,
                paragraph_id TEXT NOT NULL,
                relation TEXT NOT NULL,
                basis TEXT NOT NULL,
                evidence_span_start INTEGER NOT NULL,
                evidence_span_end INTEGER NOT NULL,
                canonical_json TEXT NOT NULL
            );
            CREATE INDEX table_text_links_table_id_idx ON table_text_links(table_id);
            CREATE INDEX table_text_links_paragraph_id_idx ON table_text_links(paragraph_id);
            CREATE INDEX table_text_links_pair_idx ON table_text_links(table_id, paragraph_id);
            """
        )
        connection.executemany(
            "INSERT INTO metadata(key, value) VALUES (?, ?)", sorted(metadata.items())
        )
        connection.commit()
    except sqlite3.DatabaseError as error:
        connection.close()
        raise ProvenanceSidecarError("SIDECAR_SQLITE_CREATE_FAILED", str(error)) from error
    return connection


def _insert_provenance(
    connection: sqlite3.Connection,
    hints: Sequence[ScaleUnitHint],
    links: Sequence[TableTextLink],
) -> None:
    hint_rows = [
        (
            hint.hint_id, hint.report_id, hint.page_id, hint.table_id, hint.source_kind.value,
            hint.source_ref, hint.source_span.start, hint.source_span.end, hint.raw_hint_text,
            hint.normalized_hint_text, None if hint.scale_candidate is None else hint.scale_candidate.value,
            hint.unit_candidate, hint.status.value, _canonical_json(hint.to_dict()).decode("utf-8"),
        )
        for hint in hints
    ]
    link_rows = [
        (
            link.link_id, link.table_id, link.paragraph_id, link.relation.value, link.basis.value,
            link.evidence_span.start, link.evidence_span.end,
            _canonical_json(link.to_dict()).decode("utf-8"),
        )
        for link in links
    ]
    try:
        connection.execute("BEGIN")
        connection.executemany(
            """INSERT INTO scale_unit_hints VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", hint_rows
        )
        connection.executemany(
            """INSERT INTO table_text_links VALUES (?,?,?,?,?,?,?,?)""", link_rows
        )
        connection.commit()
    except sqlite3.IntegrityError as error:
        connection.rollback()
        raise ProvenanceSidecarError("SIDECAR_DUPLICATE_ID", str(error)) from error
    except sqlite3.DatabaseError as error:
        connection.rollback()
        raise ProvenanceSidecarError("SIDECAR_SQLITE_WRITE_FAILED", str(error)) from error


def _cross_check_corpus_references(corpus: Any, connection: sqlite3.Connection) -> Dict[str, int]:
    hint_ids = {row[0] for row in connection.execute("SELECT hint_id FROM scale_unit_hints")}
    link_pairs = {
        (row[0], row[1]) for row in connection.execute("SELECT table_id, paragraph_id FROM table_text_links")
    }
    referenced_hints = set()
    referenced_pairs = set()
    for entry in corpus.shard_entries:
        relative = str(entry["file"])
        try:
            handle = (corpus.root / relative).open("rb")
        except OSError as error:
            raise ProvenanceSidecarError("SIDECAR_CORPUS_READ_FAILED", relative) from error
        with handle:
            for line in handle:
                try:
                    value = json.loads(line.decode("utf-8"))
                    representation = value["representation"]
                    source_type = representation["source_type"]
                    source_id = (
                        representation["table_id"] if source_type == "TABLE"
                        else representation["paragraph_id"]
                    )
                    hint_refs = representation["scale_unit_hint_ids"]
                    linked_ids = representation["linked_source_ids"]
                except (KeyError, TypeError, UnicodeDecodeError, ValueError) as error:
                    raise ProvenanceSidecarError("SIDECAR_CORPUS_RECORD_INVALID", relative) from error
                for hint_id in hint_refs:
                    if hint_id not in hint_ids:
                        raise ProvenanceSidecarError("SIDECAR_REFERENCED_HINT_MISSING", hint_id)
                    referenced_hints.add(hint_id)
                for linked_id in linked_ids:
                    pair = (source_id, linked_id) if source_type == "TABLE" else (linked_id, source_id)
                    if pair not in link_pairs:
                        raise ProvenanceSidecarError("SIDECAR_REFERENCED_LINK_MISSING", repr(pair))
                    referenced_pairs.add(pair)
    return {
        "referenced_hint_count": len(referenced_hints),
        "referenced_link_count": len(referenced_pairs),
    }


def _build_id(corpus_fingerprint: str, content_fingerprint: str) -> str:
    return sha256(
        _canonical_json(
            {
                "sidecar_schema_version": PROVENANCE_SIDECAR_SCHEMA_VERSION,
                "build_version": PROVENANCE_SIDECAR_BUILD_VERSION,
                "corpus_fingerprint": corpus_fingerprint,
                "content_fingerprint": content_fingerprint,
            }
        )
    ).hexdigest()


def build_provenance_sidecar(
    corpus_artifact_root: str | Path,
    raw_corpus_root: str | Path,
    output_root: str | Path,
) -> Dict[str, Any]:
    """Materialize immutable M2A links and hints without touching M2 corpus bytes."""

    try:
        corpus = _validate_input_manifest(corpus_artifact_root, verify_hashes=True)
    except EmbeddingArtifactBuilderError as error:
        raise ProvenanceSidecarError("SIDECAR_CORPUS_INVALID", str(error)) from error
    root = Path(output_root)
    raw_root = Path(raw_corpus_root)
    staging = root / ".staging" / PROVENANCE_SIDECAR_BUILD_VERSION
    if staging.exists():
        raise ProvenanceSidecarError("SIDECAR_STAGING_EXISTS", str(staging))
    staging.mkdir(parents=True, exist_ok=False)
    database = staging / PROVENANCE_SIDECAR_SQLITE_FILE
    metadata = {
        "sidecar_schema_version": PROVENANCE_SIDECAR_SCHEMA_VERSION,
        "build_version": PROVENANCE_SIDECAR_BUILD_VERSION,
        "corpus_artifact_schema_version": CORPUS_ARTIFACT_SCHEMA_VERSION,
        "corpus_artifact_id": corpus.artifact_id,
        "corpus_manifest_sha256": corpus.manifest_sha256,
        "corpus_fingerprint": corpus.corpus_fingerprint,
        "m2a_schema_version": M2A_SCHEMA_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "representation_version": REPRESENTATION_VERSION,
    }
    connection = _create_database(database, metadata=metadata)
    digest = sha256()
    hint_count = 0
    link_count = 0
    try:
        for item in corpus.manifest["reports"]:
            source = _source_from_inventory(raw_root, corpus.corpus_id, item)
            hints, links = _extract_report_provenance(source)
            _insert_provenance(connection, hints, links)
            for hint in hints:
                digest.update(b"H\0")
                digest.update(_canonical_json(hint.to_dict()))
                digest.update(b"\n")
            for link in links:
                digest.update(b"L\0")
                digest.update(_canonical_json(link.to_dict()))
                digest.update(b"\n")
            hint_count += len(hints)
            link_count += len(links)
        references = _cross_check_corpus_references(corpus, connection)
        connection.execute("INSERT INTO metadata(key, value) VALUES (?, ?)", ("hint_count", str(hint_count)))
        connection.execute("INSERT INTO metadata(key, value) VALUES (?, ?)", ("link_count", str(link_count)))
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
        "sidecar_schema_version": PROVENANCE_SIDECAR_SCHEMA_VERSION,
        "artifact_status": "COMMITTED",
        "artifact_id": artifact_id,
        "artifact_directory": f"artifacts/{artifact_id}",
        "build_version": PROVENANCE_SIDECAR_BUILD_VERSION,
        "build_config_fingerprint": sha256(_canonical_json(metadata)).hexdigest(),
        "corpus_artifact_schema_version": CORPUS_ARTIFACT_SCHEMA_VERSION,
        "corpus_artifact_id": corpus.artifact_id,
        "corpus_manifest_sha256": corpus.manifest_sha256,
        "corpus_fingerprint": corpus.corpus_fingerprint,
        "m2a_schema_version": M2A_SCHEMA_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "representation_version": REPRESENTATION_VERSION,
        "report_count": len(corpus.manifest["reports"]),
        "scale_unit_hint_count": hint_count,
        "table_text_link_count": link_count,
        "referenced_hint_count": references["referenced_hint_count"],
        "referenced_link_count": references["referenced_link_count"],
        "content_fingerprint": digest.hexdigest(),
        "database": {"file": PROVENANCE_SIDECAR_SQLITE_FILE, "sha256": database_hash},
    }
    try:
        _atomic_write_json(staging / "manifest.json", manifest)
        artifact.parent.mkdir(parents=True, exist_ok=True)
        if artifact.exists():
            existing = artifact / PROVENANCE_SIDECAR_SQLITE_FILE
            if not existing.is_file() or _sha256_file(existing) != database_hash:
                raise ProvenanceSidecarError("SIDECAR_IMMUTABLE_ARTIFACT_CONFLICT", artifact_id)
            shutil.rmtree(staging)
        else:
            os.replace(staging, artifact)
        _atomic_write_json(root / "manifest.json", manifest)
        _atomic_write(root / "CURRENT", f"{artifact_id}\n".encode("utf-8"))
        return manifest
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def validate_provenance_sidecar(
    sidecar_root: str | Path, corpus_artifact_root: str | Path
) -> Dict[str, Any]:
    """Validate immutable sidecar bytes and exact M2 corpus compatibility."""

    try:
        corpus = _validate_input_manifest(corpus_artifact_root, verify_hashes=True)
    except EmbeddingArtifactBuilderError as error:
        raise ProvenanceSidecarError("SIDECAR_CORPUS_INVALID", str(error)) from error
    root = Path(sidecar_root)
    try:
        manifest_path = root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise ProvenanceSidecarError("SIDECAR_MANIFEST_INVALID", str(root)) from error
    if not isinstance(manifest, dict):
        raise ProvenanceSidecarError("SIDECAR_MANIFEST_INVALID", "manifest must be an object")
    expected = {
        "sidecar_schema_version": PROVENANCE_SIDECAR_SCHEMA_VERSION,
        "artifact_status": "COMMITTED",
        "corpus_artifact_schema_version": CORPUS_ARTIFACT_SCHEMA_VERSION,
        "corpus_artifact_id": corpus.artifact_id,
        "corpus_manifest_sha256": corpus.manifest_sha256,
        "corpus_fingerprint": corpus.corpus_fingerprint,
        "m2a_schema_version": M2A_SCHEMA_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "representation_version": REPRESENTATION_VERSION,
    }
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise ProvenanceSidecarError("SIDECAR_CORPUS_MISMATCH", "manifest is incompatible")
    artifact = root / _safe_relative(manifest.get("artifact_directory"), "artifact_directory")
    database_entry = manifest.get("database")
    if not isinstance(database_entry, Mapping):
        raise ProvenanceSidecarError("SIDECAR_MANIFEST_INVALID", "database is missing")
    database = artifact / _safe_relative(database_entry.get("file"), "database.file")
    if not database.is_file() or _sha256_file(database) != _require_sha256(database_entry.get("sha256"), "database.sha256"):
        raise ProvenanceSidecarError("SIDECAR_DATABASE_CORRUPT", str(database))
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        try:
            if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise ProvenanceSidecarError("SIDECAR_DATABASE_CORRUPT", "SQLite integrity check failed")
            metadata = dict(connection.execute("SELECT key, value FROM metadata"))
            for key, value in expected.items():
                if key == "artifact_status":
                    continue
                if metadata.get(key) != value:
                    raise ProvenanceSidecarError("SIDECAR_DATABASE_INCOMPATIBLE", key)
            hint_count = connection.execute("SELECT COUNT(*) FROM scale_unit_hints").fetchone()[0]
            link_count = connection.execute("SELECT COUNT(*) FROM table_text_links").fetchone()[0]
        finally:
            connection.close()
    except sqlite3.DatabaseError as error:
        raise ProvenanceSidecarError("SIDECAR_DATABASE_CORRUPT", str(database)) from error
    if hint_count != manifest.get("scale_unit_hint_count") or link_count != manifest.get("table_text_link_count"):
        raise ProvenanceSidecarError("SIDECAR_DATABASE_INCOMPATIBLE", "counts differ from manifest")
    if manifest.get("report_count") != len(corpus.manifest["reports"]):
        raise ProvenanceSidecarError("SIDECAR_DATABASE_INCOMPATIBLE", "report count differs from corpus")
    return {
        "integrity": "PASS",
        "artifact_id": manifest["artifact_id"],
        "scale_unit_hint_count": hint_count,
        "table_text_link_count": link_count,
    }


class ProvenanceSidecar:
    """Read-only exact M2A provenance lookup repository."""

    def __init__(self, sidecar_root: str | Path, corpus_artifact_root: str | Path) -> None:
        self.integrity = validate_provenance_sidecar(sidecar_root, corpus_artifact_root)
        root = Path(sidecar_root)
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        self._database = root / manifest["artifact_directory"] / manifest["database"]["file"]

    def _rows(self, sql: str, parameters: Sequence[Any]) -> Iterable[Tuple[str]]:
        try:
            connection = sqlite3.connect(f"file:{self._database}?mode=ro", uri=True)
            try:
                return connection.execute(sql, parameters).fetchall()
            finally:
                connection.close()
        except sqlite3.DatabaseError as error:
            raise ProvenanceSidecarError("SIDECAR_DATABASE_CORRUPT", str(self._database)) from error

    def get_hint(self, hint_id: str) -> Optional[ScaleUnitHint]:
        rows = self._rows("SELECT canonical_json FROM scale_unit_hints WHERE hint_id=?", (hint_id,))
        if not rows:
            return None
        return ScaleUnitHint.from_dict(json.loads(rows[0][0]))

    def get_hints_by_source_ref(self, source_ref: str) -> List[ScaleUnitHint]:
        rows = self._rows(
            """SELECT canonical_json FROM scale_unit_hints WHERE source_ref=?
               ORDER BY source_span_start, source_span_end, hint_id""",
            (source_ref,),
        )
        return [ScaleUnitHint.from_dict(json.loads(row[0])) for row in rows]

    def get_hints_by_table_id(self, table_id: str) -> List[ScaleUnitHint]:
        """Return every persisted cell/header/caption hint associated with one table."""

        rows = self._rows(
            """SELECT canonical_json FROM scale_unit_hints WHERE table_id=?
               ORDER BY source_span_start, source_span_end, hint_id""",
            (table_id,),
        )
        return [ScaleUnitHint.from_dict(json.loads(row[0])) for row in rows]

    def get_links_by_table_id(self, table_id: str) -> List[TableTextLink]:
        rows = self._rows(
            """SELECT canonical_json FROM table_text_links WHERE table_id=?
               ORDER BY evidence_span_start, evidence_span_end, link_id""",
            (table_id,),
        )
        return [TableTextLink.from_dict(json.loads(row[0])) for row in rows]

    def get_links_by_paragraph_id(self, paragraph_id: str) -> List[TableTextLink]:
        rows = self._rows(
            """SELECT canonical_json FROM table_text_links WHERE paragraph_id=?
               ORDER BY evidence_span_start, evidence_span_end, link_id""",
            (paragraph_id,),
        )
        return [TableTextLink.from_dict(json.loads(row[0])) for row in rows]


def main() -> int:
    """Build the sidecar without invoking any embedding model."""

    import argparse

    parser = argparse.ArgumentParser(description="Build the immutable M2 provenance sidecar")
    parser.add_argument("--corpus-artifact", required=True)
    parser.add_argument("--raw-corpus-root", default="data/ViFinQA/financial_statements")
    parser.add_argument("--output-root", default="artifacts/m2-provenance-v1")
    arguments = parser.parse_args()
    manifest = build_provenance_sidecar(
        arguments.corpus_artifact, arguments.raw_corpus_root, arguments.output_root
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
