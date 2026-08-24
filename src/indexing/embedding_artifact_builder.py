"""Streaming TASK-028B/TASK-029 M2 embedding-artifact builder.

The input to this module is the committed, immutable M2 corpus artifact.  It
does not parse OCR and it never materializes the corpus chunks, vectors, or
metadata collection.  At most one embedding batch and one bounded FAISS shard
are resident in Python/FAISS memory.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

from src.indexing.corpus_artifact import (
    CORPUS_ARTIFACT_SCHEMA_VERSION,
    CORPUS_RECORD_SCHEMA_VERSION,
)
from src.indexing.embedding_chunker import (
    BGE_M3_MODEL_ID,
    BGE_M3_REVISION,
    CHUNKING_CONFIG_FINGERPRINT,
)
from src.indexing.embedding_indexer import (
    EMBEDDING_ALGORITHM_VERSION,
    embed_chunks,
    load_bge_m3_encoder,
    make_embedding_config_fingerprint,
)
from src.indexing.embedding_schemas import (
    EMBEDDING_CHUNK_SCHEMA_VERSION,
    EMBEDDING_DIMENSION,
    EMBEDDING_DTYPE,
    EMBEDDING_HARD_MAX_TOKENS,
    EMBEDDING_RECORD_SCHEMA_VERSION,
    EmbeddingChunk,
    EmbeddingRecord,
)
from src.indexing.vector_index_store import INDEX_SCHEMA_VERSION
from src.indexing.schemas import RetrievalRepresentation
from src.supervisor.schemas import EvidenceSource
from src.understanding.schemas import SchemaValidationError


APPROVED_EMBEDDING_FINGERPRINT = (
    "ebf2adc2a61d75a65db3829163a003e729095310360b9b162b570b34579585b3"
)
MAX_VECTORS_PER_SHARD = 100_000
BUILDER_SCHEMA_VERSION = "m2b-streaming-builder-v1"
CHECKPOINT_SCHEMA_VERSION = "m2b-vector-shard-checkpoint-v1"
STATE_SCHEMA_VERSION = "m2b-vector-build-state-v1"
OUTPUT_ARTIFACT_ROOT_NAME = "m2-vector-index-v1"


class EmbeddingArtifactBuilderError(SchemaValidationError):
    """A surfaced input, checkpoint, persistence, or publication failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class _InputRecord:
    source_shard: str
    source_shard_sha256: str
    source_record_index: int
    source_order: int
    report: Mapping[str, Any]
    representation: RetrievalRepresentation
    chunk: EmbeddingChunk
    source_cell_ids: List[str]


@dataclass(frozen=True)
class _InputArtifact:
    root: Path
    manifest: Mapping[str, Any]
    manifest_sha256: str
    report_by_id: Mapping[str, Mapping[str, Any]]
    shard_entries: Sequence[Mapping[str, Any]]

    @property
    def artifact_id(self) -> str:
        return str(self.manifest["artifact_id"])

    @property
    def corpus_fingerprint(self) -> str:
        return str(self.manifest["source_inventory_sha256"])

    @property
    def chunk_count(self) -> int:
        return int(self.manifest["chunk_count"])

    @property
    def corpus_id(self) -> str:
        return str(self.manifest["corpus_id"])


def _canonical_json(value: Any, *, sort_keys: bool = False) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=sort_keys,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise EmbeddingArtifactBuilderError("INPUT_SHARD_READ_FAILED", str(error)) from error
    return digest.hexdigest()


def _sha256_json(value: Any) -> str:
    return sha256(_canonical_json(value, sort_keys=True)).hexdigest()


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _atomic_write_json(path: Path, value: Any, *, sort_keys: bool = True) -> None:
    _atomic_write_bytes(path, _canonical_json(value, sort_keys=sort_keys))


def _safe_relative_file(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise EmbeddingArtifactBuilderError("MANIFEST_INVALID", f"{path} must be a relative path")
    relative = Path(value)
    if ".." in relative.parts:
        raise EmbeddingArtifactBuilderError("MANIFEST_INVALID", f"{path} escapes the artifact")
    return value


def _sha256(value: Any, path: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise EmbeddingArtifactBuilderError("MANIFEST_INVALID", f"{path} must be SHA-256 hex")
    try:
        int(value, 16)
    except ValueError as error:
        raise EmbeddingArtifactBuilderError("MANIFEST_INVALID", f"{path} must be SHA-256 hex") from error
    if value.lower() != value:
        raise EmbeddingArtifactBuilderError("MANIFEST_INVALID", f"{path} must be lowercase SHA-256 hex")
    return value


def _non_negative_int(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise EmbeddingArtifactBuilderError("MANIFEST_INVALID", f"{path} must be a non-negative integer")
    return value


def _resolve_input_artifact(path_value: str | Path) -> Path:
    root = Path(path_value)
    if (root / "manifest.json").is_file():
        return root
    current = root / "CURRENT"
    if current.is_file():
        artifact_id = current.read_text(encoding="utf-8").strip()
        if artifact_id and (root / artifact_id / "manifest.json").is_file():
            return root / artifact_id
    raise EmbeddingArtifactBuilderError(
        "INPUT_MANIFEST_MISSING",
        f"{root} is not a committed M2 corpus artifact",
    )


def _validate_input_manifest(path_value: str | Path, *, verify_hashes: bool = True) -> _InputArtifact:
    root = _resolve_input_artifact(path_value)
    manifest_path = root / "manifest.json"
    try:
        raw = manifest_path.read_bytes()
        manifest = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise EmbeddingArtifactBuilderError("INPUT_MANIFEST_INVALID", str(error)) from error
    if not isinstance(manifest, dict):
        raise EmbeddingArtifactBuilderError("INPUT_MANIFEST_INVALID", "manifest must be an object")

    expected_versions = {
        "manifest_schema_version": CORPUS_ARTIFACT_SCHEMA_VERSION,
        "record_schema_version": CORPUS_RECORD_SCHEMA_VERSION,
        "m2a_schema_version": "m2a.v1",
        "normalization_version": "m2a-normalization-v1",
        "representation_version": "m2a-representation-v1",
        "chunk_schema_version": EMBEDDING_CHUNK_SCHEMA_VERSION,
    }
    for key, expected in expected_versions.items():
        if manifest.get(key) != expected:
            raise EmbeddingArtifactBuilderError(
                "INPUT_SCHEMA_MISMATCH", f"{key} is incompatible with the M2 contract"
            )
    if manifest.get("artifact_status") != "COMMITTED":
        raise EmbeddingArtifactBuilderError("INPUT_NOT_COMMITTED", "M2 corpus artifact is not committed")
    if manifest.get("format") != "jsonl-utf8-canonical":
        raise EmbeddingArtifactBuilderError("INPUT_FORMAT_MISMATCH", "M2 corpus format is incompatible")
    if manifest.get("source_order") != "relative_posix_source_ref" or manifest.get(
        "record_order"
    ) != "report_source_order_then_representation_then_chunk_index":
        raise EmbeddingArtifactBuilderError("INPUT_ORDER_MISMATCH", "M2 corpus ordering is incompatible")
    if manifest.get("chunking_config_fingerprint") != CHUNKING_CONFIG_FINGERPRINT:
        raise EmbeddingArtifactBuilderError(
            "CHUNKING_FINGERPRINT_MISMATCH", "M2 chunking fingerprint is incompatible"
        )
    tokenizer = manifest.get("tokenizer")
    if not isinstance(tokenizer, dict) or tokenizer.get("model_id") != BGE_M3_MODEL_ID:
        raise EmbeddingArtifactBuilderError("INPUT_MODEL_MISMATCH", "M2 tokenizer model is incompatible")
    if tokenizer.get("revision") != BGE_M3_REVISION or tokenizer.get("max_tokens") != 7168:
        raise EmbeddingArtifactBuilderError("INPUT_MODEL_MISMATCH", "M2 tokenizer revision/configuration is incompatible")

    corpus_fingerprint = _sha256(manifest.get("source_inventory_sha256"), "source_inventory_sha256")
    reports = manifest.get("reports")
    if not isinstance(reports, list) or not reports:
        raise EmbeddingArtifactBuilderError("INPUT_MANIFEST_INVALID", "reports must be a non-empty list")
    report_by_id: Dict[str, Mapping[str, Any]] = {}
    for index, report in enumerate(reports):
        if not isinstance(report, dict):
            raise EmbeddingArtifactBuilderError("INPUT_MANIFEST_INVALID", f"reports[{index}] is invalid")
        report_id = report.get("report_id")
        if not isinstance(report_id, str) or not report_id or report_id in report_by_id:
            raise EmbeddingArtifactBuilderError("INPUT_MANIFEST_INVALID", "report IDs must be unique")
        report_by_id[report_id] = report
    if _sha256_json(reports) != corpus_fingerprint:
        raise EmbeddingArtifactBuilderError(
            "CORPUS_FINGERPRINT_MISMATCH", "M2 report inventory does not match its corpus fingerprint"
        )

    chunk_count = _non_negative_int(manifest.get("chunk_count"), "chunk_count")
    report_count = _non_negative_int(manifest.get("report_count"), "report_count")
    if report_count != len(reports):
        raise EmbeddingArtifactBuilderError("INPUT_COUNT_MISMATCH", "manifest report_count is invalid")
    shards = manifest.get("shards")
    if not isinstance(shards, list) or not shards:
        raise EmbeddingArtifactBuilderError("INPUT_MANIFEST_INVALID", "shards must be a non-empty list")
    seen_files = set()
    seen_ids = set()
    total_records = 0
    total_table_records = 0
    total_text_records = 0
    shard_entries: List[Mapping[str, Any]] = []
    for index, entry in enumerate(shards):
        if not isinstance(entry, dict):
            raise EmbeddingArtifactBuilderError("INPUT_MANIFEST_INVALID", f"shards[{index}] is invalid")
        shard_id = entry.get("shard_id")
        if not isinstance(shard_id, str) or not shard_id or shard_id in seen_ids:
            raise EmbeddingArtifactBuilderError("INPUT_MANIFEST_INVALID", "input shard IDs must be unique")
        relative = _safe_relative_file(entry.get("file"), f"shards[{index}].file")
        if not relative.startswith("shards/"):
            raise EmbeddingArtifactBuilderError("INPUT_MANIFEST_INVALID", "input shards must be under shards/")
        if relative in seen_files:
            raise EmbeddingArtifactBuilderError("INPUT_MANIFEST_INVALID", "input shard files must be unique")
        seen_files.add(relative)
        seen_ids.add(shard_id)
        record_count = _non_negative_int(entry.get("record_count"), f"shards[{index}].record_count")
        total_records += record_count
        total_table_records += _non_negative_int(
            entry.get("table_chunk_count"), f"shards[{index}].table_chunk_count"
        )
        total_text_records += _non_negative_int(
            entry.get("text_chunk_count"), f"shards[{index}].text_chunk_count"
        )
        _sha256(entry.get("sha256"), f"shards[{index}].sha256")
        shard_entries.append(entry)
    if total_records != chunk_count:
        raise EmbeddingArtifactBuilderError("INPUT_COUNT_MISMATCH", "manifest chunk_count differs from shard counts")
    if manifest.get("shard_count") != len(shards) or manifest.get("table_chunk_count") != total_table_records or manifest.get("text_chunk_count") != total_text_records:
        raise EmbeddingArtifactBuilderError("INPUT_COUNT_MISMATCH", "manifest shard/type counts are invalid")
    if manifest.get("omitted_representation_count") != 0 or manifest.get("omitted_source_cell_count") != 0:
        raise EmbeddingArtifactBuilderError("INPUT_COVERAGE_INVALID", "M2 artifact reports omitted data")
    if chunk_count < 1:
        raise EmbeddingArtifactBuilderError("EMPTY_INPUT", "M2 corpus artifact contains no embedding chunks")

    if verify_hashes:
        for entry in shard_entries:
            shard_path = root / str(entry["file"])
            if not shard_path.is_file():
                raise EmbeddingArtifactBuilderError("INPUT_SHARD_MISSING", str(entry["file"]))
            actual_hash = _sha256_file(shard_path)
            if actual_hash != entry["sha256"]:
                raise EmbeddingArtifactBuilderError("INPUT_SHARD_HASH_MISMATCH", str(entry["file"]))
            if shard_path.stat().st_size != entry.get("byte_count"):
                raise EmbeddingArtifactBuilderError("INPUT_SHARD_SIZE_MISMATCH", str(entry["file"]))

    return _InputArtifact(
        root=root,
        manifest=manifest,
        manifest_sha256=sha256(raw).hexdigest(),
        report_by_id=report_by_id,
        shard_entries=tuple(shard_entries),
    )


def _report_matches_inventory(report: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
    keys = ("report_id", "source_ref", "content_sha256", "ticker", "company_name", "report_year", "statement_scope")
    return all(report.get(key) == expected.get(key) for key in keys)


def _iter_input_records(input_artifact: _InputArtifact) -> Iterator[_InputRecord]:
    source_order = 0
    seen_report_ids = set()
    for shard_entry in input_artifact.shard_entries:
        relative = str(shard_entry["file"])
        shard_path = input_artifact.root / relative
        record_count = int(shard_entry["record_count"])
        actual_count = 0
        actual_table_count = 0
        actual_text_count = 0
        try:
            handle = shard_path.open("rb")
        except OSError as error:
            raise EmbeddingArtifactBuilderError("INPUT_SHARD_READ_FAILED", relative) from error
        with handle:
            for source_record_index, line in enumerate(handle):
                if not line.endswith(b"\n"):
                    raise EmbeddingArtifactBuilderError("INPUT_SHARD_INVALID", f"{relative} has an unterminated line")
                try:
                    value = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, ValueError) as error:
                    raise EmbeddingArtifactBuilderError("INPUT_RECORD_INVALID", relative) from error
                if _canonical_json(value) + b"\n" != line:
                    raise EmbeddingArtifactBuilderError("INPUT_RECORD_NOT_CANONICAL", relative)
                if not isinstance(value, dict) or value.get("record_schema_version") != CORPUS_RECORD_SCHEMA_VERSION:
                    raise EmbeddingArtifactBuilderError("INPUT_RECORD_SCHEMA_MISMATCH", relative)
                if value.get("source_order") != source_order:
                    raise EmbeddingArtifactBuilderError("INPUT_SOURCE_ORDER_MISMATCH", relative)
                report = value.get("report")
                if not isinstance(report, dict) or report.get("report_id") not in input_artifact.report_by_id:
                    raise EmbeddingArtifactBuilderError("INPUT_PROVENANCE_INVALID", relative)
                if not _report_matches_inventory(report, input_artifact.report_by_id[report["report_id"]]):
                    raise EmbeddingArtifactBuilderError("INPUT_PROVENANCE_INVALID", relative)
                try:
                    representation = RetrievalRepresentation.from_dict(value["representation"])
                    chunk = EmbeddingChunk.from_dict(value["chunk"])
                except (KeyError, TypeError, SchemaValidationError) as error:
                    raise EmbeddingArtifactBuilderError("INPUT_RECORD_INVALID", f"{relative}: {error}") from error
                source_cell_ids = value.get("source_cell_ids")
                if not isinstance(source_cell_ids, list) or any(not isinstance(item, str) for item in source_cell_ids):
                    raise EmbeddingArtifactBuilderError("INPUT_PROVENANCE_INVALID", relative)
                if representation.report_id != report["report_id"]:
                    raise EmbeddingArtifactBuilderError("INPUT_PROVENANCE_INVALID", relative)
                if chunk.representation_id != representation.representation_id or chunk.source_type is not representation.source_type:
                    raise EmbeddingArtifactBuilderError("INPUT_PROVENANCE_INVALID", relative)
                if chunk.chunking_config_fingerprint != CHUNKING_CONFIG_FINGERPRINT:
                    raise EmbeddingArtifactBuilderError("CHUNKING_FINGERPRINT_MISMATCH", chunk.chunk_id)
                if representation.source_type is EvidenceSource.TABLE:
                    if representation.table_id is None or representation.paragraph_id is not None:
                        raise EmbeddingArtifactBuilderError("INPUT_PROVENANCE_INVALID", relative)
                    actual_table_count += 1
                elif representation.paragraph_id is None or representation.table_id is not None:
                    raise EmbeddingArtifactBuilderError("INPUT_PROVENANCE_INVALID", relative)
                else:
                    actual_text_count += 1
                seen_report_ids.add(str(report["report_id"]))
                yield _InputRecord(
                    source_shard=relative,
                    source_shard_sha256=str(shard_entry["sha256"]),
                    source_record_index=source_record_index,
                    source_order=source_order,
                    report=report,
                    representation=representation,
                    chunk=chunk,
                    source_cell_ids=list(source_cell_ids),
                )
                source_order += 1
                actual_count += 1
        if actual_count != record_count:
            raise EmbeddingArtifactBuilderError("INPUT_COUNT_MISMATCH", relative)
        if actual_table_count != int(shard_entry["table_chunk_count"]) or actual_text_count != int(shard_entry["text_chunk_count"]):
            raise EmbeddingArtifactBuilderError("INPUT_TYPE_COUNT_MISMATCH", relative)
    if source_order != input_artifact.chunk_count:
        raise EmbeddingArtifactBuilderError("INPUT_COUNT_MISMATCH", "input record count differs from manifest")
    if seen_report_ids != set(input_artifact.report_by_id):
        raise EmbeddingArtifactBuilderError("INPUT_REPORT_COVERAGE_FAILED", "M2 report inventory is not covered")


def _load_faiss_numpy() -> Tuple[Any, Any]:
    try:
        import faiss
        import numpy as np
    except ImportError as error:  # pragma: no cover - deployment dependent
        raise EmbeddingArtifactBuilderError("FAISS_DEPENDENCY_MISSING", "faiss and numpy are required") from error
    return faiss, np


def _validate_faiss_file(path: Path, expected_count: int) -> None:
    faiss, _ = _load_faiss_numpy()
    try:
        index = faiss.read_index(str(path))
    except Exception as error:
        raise EmbeddingArtifactBuilderError("FAISS_SHARD_INVALID", str(path)) from error
    if type(index).__name__ != "IndexIDMap2" or getattr(index, "d", None) != EMBEDDING_DIMENSION:
        raise EmbeddingArtifactBuilderError("FAISS_CONFIG_INVALID", str(path))
    if getattr(index, "ntotal", None) != expected_count:
        raise EmbeddingArtifactBuilderError("FAISS_COUNT_MISMATCH", str(path))
    base = getattr(index, "index", None)
    if base is None or type(faiss.downcast_index(base)).__name__ != "IndexFlatIP":
        raise EmbeddingArtifactBuilderError("FAISS_CONFIG_INVALID", str(path))
    if getattr(index, "metric_type", faiss.METRIC_INNER_PRODUCT) != faiss.METRIC_INNER_PRODUCT:
        raise EmbeddingArtifactBuilderError("FAISS_METRIC_INVALID", str(path))
    try:
        ids = faiss.vector_to_array(index.id_map)
    except Exception as error:
        raise EmbeddingArtifactBuilderError("FAISS_IDS_INVALID", str(path)) from error
    expected_ids = list(range(expected_count))
    if list(ids) != expected_ids:
        raise EmbeddingArtifactBuilderError("FAISS_IDS_INVALID", str(path))


class _ShardAccumulator:
    def __init__(self, shard_number: int) -> None:
        faiss, _ = _load_faiss_numpy()
        self.shard_number = shard_number
        self.shard_id = f"shard-{shard_number:05d}"
        self.index = faiss.IndexIDMap2(faiss.IndexFlatIP(EMBEDDING_DIMENSION))
        self.vector_count = 0

    def add(self, records: Sequence[EmbeddingRecord]) -> List[int]:
        _, np = _load_faiss_numpy()
        if not records:
            return []
        matrix = np.asarray([record.vector for record in records], dtype=np.float32)
        if matrix.shape != (len(records), EMBEDDING_DIMENSION):
            raise EmbeddingArtifactBuilderError("VECTOR_SHAPE_INVALID", self.shard_id)
        local_ids = list(range(self.vector_count, self.vector_count + len(records)))
        self.index.add_with_ids(matrix, np.asarray(local_ids, dtype=np.int64))
        self.vector_count += len(records)
        return local_ids

    def flush(self, shards_dir: Path) -> Tuple[Path, str]:
        path = shards_dir / f"{self.shard_id}.faiss"
        temporary = path.with_name(f".{path.name}.tmp")
        if temporary.exists():
            temporary.unlink()
        faiss, _ = _load_faiss_numpy()
        try:
            faiss.write_index(self.index, str(temporary))
            with temporary.open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except Exception as error:
            raise EmbeddingArtifactBuilderError("FAISS_SHARD_WRITE_FAILED", self.shard_id) from error
        _validate_faiss_file(path, self.vector_count)
        return path, _sha256_file(path)


def _create_metadata_database(path: Path, input_artifact: _InputArtifact, max_vectors_per_shard: int) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=DELETE")
    connection.execute("PRAGMA synchronous=FULL")
    connection.executescript(
        """
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE embeddings (
            embedding_id TEXT PRIMARY KEY,
            chunk_id TEXT NOT NULL UNIQUE,
            representation_id TEXT NOT NULL,
            source_type TEXT NOT NULL,
            report_id TEXT NOT NULL,
            page_ids_json TEXT NOT NULL,
            table_id TEXT,
            paragraph_id TEXT,
            ticker TEXT NOT NULL,
            company_name TEXT,
            report_year INTEGER NOT NULL,
            statement_scope TEXT,
            period_labels_json TEXT NOT NULL,
            faiss_shard_id TEXT NOT NULL,
            faiss_shard_number INTEGER NOT NULL,
            local_vector_id INTEGER NOT NULL,
            source_shard TEXT NOT NULL,
            source_record_index INTEGER NOT NULL,
            source_order INTEGER NOT NULL UNIQUE,
            source_cell_ids_json TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            chunk_count INTEGER NOT NULL,
            chunk_schema_version TEXT NOT NULL,
            representation_version TEXT NOT NULL,
            embedding_schema_version TEXT NOT NULL,
            corpus_fingerprint TEXT NOT NULL,
            chunking_config_fingerprint TEXT NOT NULL,
            embedding_fingerprint TEXT NOT NULL,
            UNIQUE(faiss_shard_number, local_vector_id)
        );
        CREATE INDEX embeddings_chunk_idx ON embeddings(chunk_id);
        CREATE INDEX embeddings_report_idx ON embeddings(report_id);
        CREATE INDEX embeddings_representation_idx ON embeddings(representation_id);
        """
    )
    metadata = {
        "builder_schema_version": BUILDER_SCHEMA_VERSION,
        "index_schema_version": INDEX_SCHEMA_VERSION,
        "input_artifact_id": input_artifact.artifact_id,
        "input_manifest_sha256": input_artifact.manifest_sha256,
        "corpus_id": input_artifact.corpus_id,
        "corpus_fingerprint": input_artifact.corpus_fingerprint,
        "input_chunk_count": str(input_artifact.chunk_count),
        "chunking_config_fingerprint": CHUNKING_CONFIG_FINGERPRINT,
        "embedding_fingerprint": APPROVED_EMBEDDING_FINGERPRINT,
        "embedding_algorithm_version": EMBEDDING_ALGORITHM_VERSION,
        "model_id": BGE_M3_MODEL_ID,
        "model_revision": BGE_M3_REVISION,
        "dimension": str(EMBEDDING_DIMENSION),
        "dtype": EMBEDDING_DTYPE,
        "truncation": "false",
        "max_vectors_per_shard": str(max_vectors_per_shard),
        "vector_count": "0",
    }
    connection.executemany("INSERT INTO metadata(key, value) VALUES (?, ?)", sorted(metadata.items()))
    connection.commit()
    return connection


def _open_metadata_database(path: Path, input_artifact: _InputArtifact, max_vectors_per_shard: int) -> sqlite3.Connection:
    if not path.is_file():
        raise EmbeddingArtifactBuilderError("CHECKPOINT_INVALID", "metadata.sqlite is missing")
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()
        if result != ("ok",):
            raise EmbeddingArtifactBuilderError("SQLITE_INTEGRITY_FAILED", "checkpoint database failed integrity_check")
        rows = dict(connection.execute("SELECT key, value FROM metadata"))
    except sqlite3.DatabaseError as error:
        connection.close()
        raise EmbeddingArtifactBuilderError("SQLITE_INTEGRITY_FAILED", str(error)) from error
    expected = {
        "builder_schema_version": BUILDER_SCHEMA_VERSION,
        "index_schema_version": INDEX_SCHEMA_VERSION,
        "input_artifact_id": input_artifact.artifact_id,
        "input_manifest_sha256": input_artifact.manifest_sha256,
        "corpus_fingerprint": input_artifact.corpus_fingerprint,
        "chunking_config_fingerprint": CHUNKING_CONFIG_FINGERPRINT,
        "embedding_fingerprint": APPROVED_EMBEDDING_FINGERPRINT,
        "max_vectors_per_shard": str(max_vectors_per_shard),
    }
    if any(rows.get(key) != value for key, value in expected.items()):
        connection.close()
        raise EmbeddingArtifactBuilderError("CHECKPOINT_CONFIG_MISMATCH", "SQLite provenance/configuration differs")
    return connection


def _insert_batch(
    connection: sqlite3.Connection,
    input_records: Sequence[_InputRecord],
    records: Sequence[EmbeddingRecord],
    shard_number: int,
    local_ids: Sequence[int],
    input_artifact: _InputArtifact,
) -> None:
    rows = []
    for item, record, local_id in zip(input_records, records, local_ids):
        representation = item.representation
        report = item.report
        rows.append(
            (
                record.embedding_id,
                record.chunk_id,
                record.representation_id,
                representation.source_type.value,
                representation.report_id,
                json.dumps(representation.page_ids, ensure_ascii=False, separators=(",", ":")),
                representation.table_id,
                representation.paragraph_id,
                representation.ticker,
                representation.company_name,
                representation.report_year,
                None if representation.statement_scope is None else representation.statement_scope.value,
                json.dumps(representation.period_labels, ensure_ascii=False, separators=(",", ":")),
                f"shard-{shard_number:05d}",
                shard_number,
                local_id,
                item.source_shard,
                item.source_record_index,
                item.source_order,
                json.dumps(item.source_cell_ids, ensure_ascii=False, separators=(",", ":")),
                item.chunk.chunk_index,
                item.chunk.chunk_count,
                item.chunk.schema_version,
                representation.representation_version,
                record.schema_version,
                input_artifact.corpus_fingerprint,
                item.chunk.chunking_config_fingerprint,
                record.model_fingerprint,
            )
        )
    try:
        connection.execute("BEGIN")
        connection.executemany(
            """INSERT INTO embeddings(
                embedding_id, chunk_id, representation_id, source_type, report_id,
                page_ids_json, table_id, paragraph_id, ticker, company_name,
                report_year, statement_scope, period_labels_json, faiss_shard_id,
                faiss_shard_number, local_vector_id, source_shard,
                source_record_index, source_order, source_cell_ids_json, chunk_index,
                chunk_count, chunk_schema_version, representation_version,
                embedding_schema_version, corpus_fingerprint,
                chunking_config_fingerprint, embedding_fingerprint
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        connection.commit()
    except sqlite3.IntegrityError as error:
        connection.rollback()
        raise EmbeddingArtifactBuilderError("DUPLICATE_ID", str(error)) from error
    except sqlite3.DatabaseError as error:
        connection.rollback()
        raise EmbeddingArtifactBuilderError("SQLITE_WRITE_FAILED", str(error)) from error


def _write_checkpoint(
    staging: Path,
    *,
    input_artifact: _InputArtifact,
    max_vectors_per_shard: int,
    completed_shards: Sequence[Mapping[str, Any]],
) -> None:
    if not completed_shards:
        last_record = None
        records_consumed = 0
        sqlite_committed_count = 0
    else:
        last = completed_shards[-1]
        last_record = last["last_completed_record"]
        records_consumed = int(last["input_progress"]["records_consumed"])
        sqlite_committed_count = int(last["sqlite_committed_count"])
    state = {
        "state_schema_version": STATE_SCHEMA_VERSION,
        "builder_schema_version": BUILDER_SCHEMA_VERSION,
        "input_artifact_id": input_artifact.artifact_id,
        "input_manifest_sha256": input_artifact.manifest_sha256,
        "corpus_fingerprint": input_artifact.corpus_fingerprint,
        "chunking_config_fingerprint": CHUNKING_CONFIG_FINGERPRINT,
        "embedding_fingerprint": APPROVED_EMBEDDING_FINGERPRINT,
        "max_vectors_per_shard": max_vectors_per_shard,
        "input_chunk_count": input_artifact.chunk_count,
        "records_consumed": records_consumed,
        "last_completed_record": last_record,
        "sqlite_committed_count": sqlite_committed_count,
        "completed_shards": list(completed_shards),
    }
    _atomic_write_json(staging / "checkpoint.json", state)


def _read_json(path: Path, code: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise EmbeddingArtifactBuilderError(code, str(path)) from error
    if not isinstance(value, dict):
        raise EmbeddingArtifactBuilderError(code, str(path))
    return value


def _load_resume_state(
    staging: Path,
    input_artifact: _InputArtifact,
    max_vectors_per_shard: int,
) -> Tuple[List[Mapping[str, Any]], int, sqlite3.Connection]:
    state = _read_json(staging / "checkpoint.json", "CHECKPOINT_INVALID")
    expected = {
        "state_schema_version": STATE_SCHEMA_VERSION,
        "builder_schema_version": BUILDER_SCHEMA_VERSION,
        "input_artifact_id": input_artifact.artifact_id,
        "input_manifest_sha256": input_artifact.manifest_sha256,
        "corpus_fingerprint": input_artifact.corpus_fingerprint,
        "chunking_config_fingerprint": CHUNKING_CONFIG_FINGERPRINT,
        "embedding_fingerprint": APPROVED_EMBEDDING_FINGERPRINT,
        "max_vectors_per_shard": max_vectors_per_shard,
        "input_chunk_count": input_artifact.chunk_count,
    }
    if any(state.get(key) != value for key, value in expected.items()):
        raise EmbeddingArtifactBuilderError("CHECKPOINT_CONFIG_MISMATCH", "checkpoint configuration differs")
    completed = state.get("completed_shards")
    if not isinstance(completed, list):
        raise EmbeddingArtifactBuilderError("CHECKPOINT_INVALID", "completed_shards is invalid")
    connection = _open_metadata_database(staging / "metadata.sqlite", input_artifact, max_vectors_per_shard)
    validated: List[Mapping[str, Any]] = []
    cumulative_vectors = 0
    for expected_number, entry in enumerate(completed):
        if not isinstance(entry, dict) or entry.get("shard_number") != expected_number:
            connection.close()
            raise EmbeddingArtifactBuilderError("CHECKPOINT_INVALID", "completed shard sequence is invalid")
        shard_id = f"shard-{expected_number:05d}"
        if (
            entry.get("faiss_shard_id") != shard_id
            or entry.get("checkpoint_schema_version") != CHECKPOINT_SCHEMA_VERSION
            or entry.get("builder_schema_version") != BUILDER_SCHEMA_VERSION
            or entry.get("input_artifact_id") != input_artifact.artifact_id
            or entry.get("input_manifest_sha256") != input_artifact.manifest_sha256
            or entry.get("corpus_fingerprint") != input_artifact.corpus_fingerprint
            or entry.get("chunking_config_fingerprint") != CHUNKING_CONFIG_FINGERPRINT
        ):
            connection.close()
            raise EmbeddingArtifactBuilderError("CHECKPOINT_INVALID", shard_id)
        vector_count = _non_negative_int(entry.get("vector_count"), f"{shard_id}.vector_count")
        if vector_count < 1 or vector_count > max_vectors_per_shard:
            connection.close()
            raise EmbeddingArtifactBuilderError("CHECKPOINT_INVALID", shard_id)
        if entry.get("embedding_fingerprint") != APPROVED_EMBEDDING_FINGERPRINT:
            connection.close()
            raise EmbeddingArtifactBuilderError("CHECKPOINT_CONFIG_MISMATCH", shard_id)
        checkpoint_path = staging / "checkpoints" / f"{shard_id}.json"
        checkpoint = _read_json(checkpoint_path, "CHECKPOINT_INVALID")
        if dict(checkpoint) != dict(entry):
            connection.close()
            raise EmbeddingArtifactBuilderError("CHECKPOINT_INVALID", shard_id)
        shard_path = staging / "shards" / f"{shard_id}.faiss"
        expected_hash = _sha256(entry.get("shard_sha256"), f"{shard_id}.shard_sha256")
        if not shard_path.is_file() or _sha256_file(shard_path) != expected_hash:
            connection.close()
            raise EmbeddingArtifactBuilderError("COMPLETED_FAISS_CORRUPT", shard_id)
        _validate_faiss_file(shard_path, vector_count)
        cumulative_vectors += vector_count
        if entry.get("sqlite_committed_count") != cumulative_vectors:
            connection.close()
            raise EmbeddingArtifactBuilderError("CHECKPOINT_INVALID", shard_id)
        progress = entry.get("input_progress")
        last_record = entry.get("last_completed_record")
        if not isinstance(progress, dict) or not isinstance(last_record, dict):
            connection.close()
            raise EmbeddingArtifactBuilderError("CHECKPOINT_INVALID", shard_id)
        if progress.get("records_consumed") != cumulative_vectors:
            connection.close()
            raise EmbeddingArtifactBuilderError("CHECKPOINT_INVALID", shard_id)
        if last_record.get("chunk_id") != entry.get("last_completed_chunk_id"):
            connection.close()
            raise EmbeddingArtifactBuilderError("CHECKPOINT_INVALID", shard_id)
        rows = connection.execute(
            "SELECT COUNT(*) FROM embeddings WHERE faiss_shard_number=?", (expected_number,)
        ).fetchone()[0]
        if rows != vector_count:
            connection.close()
            raise EmbeddingArtifactBuilderError("CHECKPOINT_SQLITE_COUNT_MISMATCH", shard_id)
        validated.append(entry)

    state_count = state.get("sqlite_committed_count")
    if state_count != cumulative_vectors or state.get("records_consumed") != cumulative_vectors:
        connection.close()
        raise EmbeddingArtifactBuilderError("CHECKPOINT_INVALID", "checkpoint totals are invalid")
    db_count = connection.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    if db_count < cumulative_vectors:
        connection.close()
        raise EmbeddingArtifactBuilderError("CHECKPOINT_SQLITE_COUNT_MISMATCH", "metadata rows are missing")
    if validated:
        last_record = validated[-1]["last_completed_record"]
        if state.get("last_completed_record") != last_record:
            connection.close()
            raise EmbeddingArtifactBuilderError("CHECKPOINT_INVALID", "last completed record differs")

    # Rows written after the last durable vector-shard checkpoint belong to the
    # replayed incomplete shard.  Remove them before reading the input again.
    if len(validated) == 0:
        connection.execute("BEGIN")
        connection.execute("DELETE FROM embeddings")
        connection.commit()
    else:
        connection.execute("BEGIN")
        connection.execute("DELETE FROM embeddings WHERE faiss_shard_number >= ?", (len(validated),))
        connection.commit()
    for path in (staging / "shards").glob("shard-*.faiss"):
        try:
            number = int(path.stem.split("-")[1])
        except (IndexError, ValueError) as error:
            connection.close()
            raise EmbeddingArtifactBuilderError("CHECKPOINT_INVALID", path.name) from error
        if number >= len(validated):
            path.unlink()
    return validated, cumulative_vectors, connection


def _build_id(input_artifact: _InputArtifact, max_vectors_per_shard: int) -> str:
    payload = {
        "builder_schema_version": BUILDER_SCHEMA_VERSION,
        "index_schema_version": INDEX_SCHEMA_VERSION,
        "input_artifact_id": input_artifact.artifact_id,
        "input_corpus_fingerprint": input_artifact.corpus_fingerprint,
        "input_chunking_config_fingerprint": CHUNKING_CONFIG_FINGERPRINT,
        "input_chunk_count": input_artifact.chunk_count,
        "embedding_fingerprint": APPROVED_EMBEDDING_FINGERPRINT,
        "dimension": EMBEDDING_DIMENSION,
        "dtype": EMBEDDING_DTYPE,
        "truncation": False,
        "max_vectors_per_shard": max_vectors_per_shard,
    }
    return _sha256_json(payload)


def _reject_incompatible_staging(
    root: Path,
    input_artifact: _InputArtifact,
    max_vectors_per_shard: int,
) -> None:
    staging_root = root / ".staging"
    if not staging_root.is_dir():
        return
    for candidate in staging_root.iterdir():
        checkpoint_path = candidate / "checkpoint.json"
        if not checkpoint_path.is_file():
            continue
        try:
            state = _read_json(checkpoint_path, "CHECKPOINT_INVALID")
        except EmbeddingArtifactBuilderError:
            # The matching build ID will surface the detailed error below;
            # unrelated malformed staging is not allowed to block a new build.
            continue
        same_input = (
            state.get("input_artifact_id") == input_artifact.artifact_id
            and state.get("input_manifest_sha256") == input_artifact.manifest_sha256
            and state.get("corpus_fingerprint") == input_artifact.corpus_fingerprint
            and state.get("embedding_fingerprint") == APPROVED_EMBEDDING_FINGERPRINT
        )
        if same_input and state.get("max_vectors_per_shard") != max_vectors_per_shard:
            raise EmbeddingArtifactBuilderError(
                "CHECKPOINT_CONFIG_MISMATCH",
                "a resumable build for this input uses a different shard configuration",
            )


def _manifest_for_staging(
    staging: Path,
    input_artifact: _InputArtifact,
    build_id: str,
    max_vectors_per_shard: int,
    completed_shards: Sequence[Mapping[str, Any]],
    vector_count: int,
) -> Dict[str, Any]:
    shard_entries = []
    for entry in completed_shards:
        shard_id = str(entry["faiss_shard_id"])
        shard_entries.append(
            {
                "shard_id": shard_id,
                "file": f"shards/{shard_id}.faiss",
                "vector_count": int(entry["vector_count"]),
                "sha256": str(entry["shard_sha256"]),
            }
        )
    metadata_path = staging / "metadata.sqlite"
    metadata_entry = {"file": "metadata.sqlite", "sha256": _sha256_file(metadata_path)}
    return {
        "manifest_schema_version": INDEX_SCHEMA_VERSION,
        "artifact_status": "COMMITTED",
        "artifact_id": build_id,
        "artifact_directory": f"artifacts/{build_id}",
        "builder_schema_version": BUILDER_SCHEMA_VERSION,
        "input_artifact_id": input_artifact.artifact_id,
        "input_manifest_sha256": input_artifact.manifest_sha256,
        "corpus_id": input_artifact.corpus_id,
        "corpus_fingerprint": input_artifact.corpus_fingerprint,
        "input_chunking_config_fingerprint": CHUNKING_CONFIG_FINGERPRINT,
        "embedding_algorithm_version": EMBEDDING_ALGORITHM_VERSION,
        "model_id": BGE_M3_MODEL_ID,
        "model_revision": BGE_M3_REVISION,
        "embedding_fingerprint": APPROVED_EMBEDDING_FINGERPRINT,
        "model_fingerprint": APPROVED_EMBEDDING_FINGERPRINT,
        "dimension": EMBEDDING_DIMENSION,
        "dtype": EMBEDDING_DTYPE,
        "metric": "INNER_PRODUCT",
        "normalized": True,
        "truncation": False,
        "max_vectors_per_shard": max_vectors_per_shard,
        "vector_count": vector_count,
        "chunk_count": input_artifact.chunk_count,
        "representation_count": int(input_artifact.manifest["representation_count"]),
        "shard_count": len(shard_entries),
        "shards": shard_entries,
        "metadata": metadata_entry,
        "sqlite": metadata_entry,
        "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
    }


def _validate_materialized_artifact(
    artifact: Path,
    manifest: Mapping[str, Any],
    input_artifact: Optional[_InputArtifact] = None,
) -> Dict[str, Any]:
    if manifest.get("manifest_schema_version") != INDEX_SCHEMA_VERSION or manifest.get("artifact_status") != "COMMITTED":
        raise EmbeddingArtifactBuilderError("MANIFEST_INVALID", "vector artifact manifest is not committed")
    if manifest.get("embedding_fingerprint") != APPROVED_EMBEDDING_FINGERPRINT:
        raise EmbeddingArtifactBuilderError("MODEL_FINGERPRINT_MISMATCH", "embedding fingerprint is incompatible")
    if manifest.get("model_fingerprint") != APPROVED_EMBEDDING_FINGERPRINT:
        raise EmbeddingArtifactBuilderError("MODEL_FINGERPRINT_MISMATCH", "model fingerprint is incompatible")
    if manifest.get("input_chunking_config_fingerprint") != CHUNKING_CONFIG_FINGERPRINT:
        raise EmbeddingArtifactBuilderError("CHUNKING_FINGERPRINT_MISMATCH", "chunking fingerprint is incompatible")
    if manifest.get("dimension") != EMBEDDING_DIMENSION or manifest.get("dtype") != EMBEDDING_DTYPE:
        raise EmbeddingArtifactBuilderError("EMBEDDING_CONFIG_INVALID", "dimension/dtype is incompatible")
    if manifest.get("metric") != "INNER_PRODUCT" or manifest.get("normalized") is not True or manifest.get("truncation") is not False:
        raise EmbeddingArtifactBuilderError("EMBEDDING_CONFIG_INVALID", "FAISS/embedding configuration is incompatible")
    if manifest.get("max_vectors_per_shard") not in range(1, MAX_VECTORS_PER_SHARD + 1):
        raise EmbeddingArtifactBuilderError("EMBEDDING_CONFIG_INVALID", "shard bound is incompatible")
    if input_artifact is not None:
        expected = {
            "input_artifact_id": input_artifact.artifact_id,
            "input_manifest_sha256": input_artifact.manifest_sha256,
            "corpus_fingerprint": input_artifact.corpus_fingerprint,
            "chunk_count": input_artifact.chunk_count,
        }
        if any(manifest.get(key) != value for key, value in expected.items()):
            raise EmbeddingArtifactBuilderError("CORPUS_FINGERPRINT_MISMATCH", "output does not match input M2 artifact")
    vector_count = _non_negative_int(manifest.get("vector_count"), "vector_count")
    if vector_count != manifest.get("chunk_count"):
        raise EmbeddingArtifactBuilderError("COUNT_MISMATCH", "vector_count and chunk_count differ")
    shards = manifest.get("shards")
    if not isinstance(shards, list) or any(not isinstance(entry, dict) for entry in shards):
        raise EmbeddingArtifactBuilderError("COUNT_MISMATCH", "FAISS shard counts differ from vector_count")
    shard_counts = []
    for entry in shards:
        shard_counts.append(_non_negative_int(entry.get("vector_count"), "shard.vector_count"))
    if sum(shard_counts) != vector_count:
        raise EmbeddingArtifactBuilderError("COUNT_MISMATCH", "FAISS shard counts differ from vector_count")
    if manifest.get("shard_count") != len(shards):
        raise EmbeddingArtifactBuilderError("COUNT_MISMATCH", "shard_count differs from the manifest")
    for entry in shards:
        if not isinstance(entry, dict):
            raise EmbeddingArtifactBuilderError("MANIFEST_INVALID", "shard entry is invalid")
        relative = _safe_relative_file(entry.get("file"), "shard.file")
        path = artifact / relative
        expected_hash = _sha256(entry.get("sha256"), "shard.sha256")
        if not path.is_file() or _sha256_file(path) != expected_hash:
            raise EmbeddingArtifactBuilderError("FAISS_SHARD_CORRUPT", str(relative))
        _validate_faiss_file(path, int(entry["vector_count"]))
    metadata = manifest.get("metadata")
    if not isinstance(metadata, dict):
        raise EmbeddingArtifactBuilderError("MANIFEST_INVALID", "metadata entry is missing")
    metadata_path = artifact / _safe_relative_file(metadata.get("file"), "metadata.file")
    expected_metadata_hash = _sha256(metadata.get("sha256"), "metadata.sha256")
    if not metadata_path.is_file() or _sha256_file(metadata_path) != expected_metadata_hash:
        raise EmbeddingArtifactBuilderError("SQLITE_CORRUPT", "metadata.sqlite hash mismatch")
    try:
        connection = sqlite3.connect(metadata_path)
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity != ("ok",):
            raise EmbeddingArtifactBuilderError("SQLITE_INTEGRITY_FAILED", "SQLite integrity_check failed")
        metadata_rows = dict(connection.execute("SELECT key, value FROM metadata"))
        rows = connection.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        unique_ids = connection.execute(
            "SELECT COUNT(*), COUNT(DISTINCT embedding_id), COUNT(DISTINCT chunk_id) FROM embeddings"
        ).fetchone()
        duplicate_local = connection.execute(
            "SELECT 1 FROM embeddings GROUP BY faiss_shard_number, local_vector_id HAVING COUNT(*) > 1 LIMIT 1"
        ).fetchone()
        if rows != vector_count or unique_ids[0] != unique_ids[1] or unique_ids[0] != unique_ids[2] or duplicate_local is not None:
            raise EmbeddingArtifactBuilderError("SQLITE_MAPPING_INVALID", "SQLite mapping count/IDs are invalid")
        if metadata_rows.get("corpus_fingerprint") != manifest.get("corpus_fingerprint") or metadata_rows.get("embedding_fingerprint") != APPROVED_EMBEDDING_FINGERPRINT:
            raise EmbeddingArtifactBuilderError("SQLITE_MAPPING_INVALID", "SQLite provenance differs from manifest")
        if metadata_rows.get("vector_count") != str(vector_count):
            raise EmbeddingArtifactBuilderError("SQLITE_MAPPING_INVALID", "SQLite vector count differs from manifest")
    except sqlite3.DatabaseError as error:
        raise EmbeddingArtifactBuilderError("SQLITE_INTEGRITY_FAILED", str(error)) from error
    finally:
        try:
            connection.close()
        except UnboundLocalError:
            pass
    return {
        "integrity": "PASS",
        "vector_count": vector_count,
        "chunk_count": int(manifest["chunk_count"]),
        "shard_count": len(shards),
        "sqlite_row_count": rows,
        "artifact_id": manifest.get("artifact_id"),
    }


def validate_published_artifact(output_root: str | Path, *, input_artifact: str | Path | None = None) -> Dict[str, Any]:
    """Validate the current pointer, manifest, FAISS shards, and SQLite mapping."""

    root = Path(output_root)
    manifest_path = root / "manifest.json"
    current_path = root / "CURRENT"
    if not manifest_path.is_file() or not current_path.is_file():
        raise EmbeddingArtifactBuilderError("CURRENT_MISSING", "published manifest/CURRENT pointer is missing")
    manifest = _read_json(manifest_path, "MANIFEST_INVALID")
    current = current_path.read_text(encoding="utf-8").strip()
    if current != manifest.get("artifact_id"):
        raise EmbeddingArtifactBuilderError("CURRENT_MISMATCH", "CURRENT does not match the top-level manifest")
    artifact_directory = _safe_relative_file(manifest.get("artifact_directory"), "artifact_directory")
    artifact = root / artifact_directory
    if not artifact.is_dir():
        raise EmbeddingArtifactBuilderError("ARTIFACT_MISSING", artifact_directory)
    loaded_input = _validate_input_manifest(input_artifact) if input_artifact is not None else None
    return _validate_materialized_artifact(artifact, manifest, loaded_input)


def _make_checkpoint(
    input_artifact: _InputArtifact,
    input_records: Sequence[_InputRecord],
    shard: _ShardAccumulator,
    shard_hash: str,
    sqlite_committed_count: int,
) -> Dict[str, Any]:
    last = input_records[-1]
    return {
        "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
        "builder_schema_version": BUILDER_SCHEMA_VERSION,
        "input_artifact_id": input_artifact.artifact_id,
        "input_manifest_sha256": input_artifact.manifest_sha256,
        "corpus_fingerprint": input_artifact.corpus_fingerprint,
        "chunking_config_fingerprint": CHUNKING_CONFIG_FINGERPRINT,
        "faiss_shard_id": shard.shard_id,
        "shard_number": shard.shard_number,
        "vector_count": shard.vector_count,
        "sqlite_committed_count": sqlite_committed_count,
        "embedding_fingerprint": APPROVED_EMBEDDING_FINGERPRINT,
        "shard_sha256": shard_hash,
        "last_completed_chunk_id": last.chunk.chunk_id,
        "last_completed_record": {
            "source_shard": last.source_shard,
            "source_shard_sha256": last.source_shard_sha256,
            "source_record_index": last.source_record_index,
            "source_order": last.source_order,
            "chunk_id": last.chunk.chunk_id,
        },
        "input_progress": {
            "records_consumed": last.source_order + 1,
            "source_shard": last.source_shard,
            "source_shard_sha256": last.source_shard_sha256,
            "source_record_index": last.source_record_index,
            "source_order": last.source_order,
            "last_chunk_id": last.chunk.chunk_id,
            "input_shard_sha256": last.source_shard_sha256,
        },
    }


def build_streaming_embedding_artifact(
    input_artifact: str | Path,
    output_root: str | Path,
    *,
    device: str = "cpu",
    batch_size: int = 8,
    max_vectors_per_shard: int = MAX_VECTORS_PER_SHARD,
    resume: bool = False,
    encoder: Any = None,
) -> Dict[str, Any]:
    """Build and atomically publish the streaming M2 vector artifact.

    ``encoder`` is intentionally injectable for deterministic tests.  The CLI
    always uses the pinned BGE-M3 encoder.
    """

    if device not in {"cpu", "cuda", "mps"}:
        raise EmbeddingArtifactBuilderError("INVALID_DEVICE", "device must be cpu, cuda, or mps")
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
        raise EmbeddingArtifactBuilderError("INVALID_BATCH_SIZE", "batch_size must be positive")
    if isinstance(max_vectors_per_shard, bool) or not isinstance(max_vectors_per_shard, int) or not 1 <= max_vectors_per_shard <= MAX_VECTORS_PER_SHARD:
        raise EmbeddingArtifactBuilderError("INVALID_SHARD_CONFIG", "max_vectors_per_shard must be between 1 and 100000")
    if make_embedding_config_fingerprint() != APPROVED_EMBEDDING_FINGERPRINT:
        raise EmbeddingArtifactBuilderError("MODEL_FINGERPRINT_MISMATCH", "approved BGE-M3 fingerprint is not reproducible")

    input_data = _validate_input_manifest(input_artifact, verify_hashes=True)
    build_id = _build_id(input_data, max_vectors_per_shard)
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    final = root / "artifacts" / build_id
    if final.exists():
        try:
            manifest = _read_json(final / "manifest.json", "MANIFEST_INVALID")
            _validate_materialized_artifact(final, manifest, input_data)
        except EmbeddingArtifactBuilderError as error:
            raise EmbeddingArtifactBuilderError("IMMUTABLE_ARTIFACT_CONFLICT", str(error)) from error
        _publish_pointer(root, manifest)
        return dict(manifest)

    if resume:
        _reject_incompatible_staging(root, input_data, max_vectors_per_shard)
    staging = root / ".staging" / build_id
    if staging.exists() and not resume:
        raise EmbeddingArtifactBuilderError("STAGING_EXISTS", f"resumable staging exists at {staging}")
    shards_dir = staging / "shards"
    checkpoints_dir = staging / "checkpoints"
    shards_dir.mkdir(parents=True, exist_ok=True)
    checkpoints_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = staging / "metadata.sqlite"
    completed_shards: List[Mapping[str, Any]]
    committed_vectors: int
    connection: sqlite3.Connection
    if staging.exists() and resume and (staging / "checkpoint.json").exists():
        completed_shards, committed_vectors, connection = _load_resume_state(
            staging, input_data, max_vectors_per_shard
        )
    elif staging.exists() and resume:
        raise EmbeddingArtifactBuilderError("CHECKPOINT_INVALID", "staging has no checkpoint")
    else:
        connection = _create_metadata_database(metadata_path, input_data, max_vectors_per_shard)
        completed_shards = []
        committed_vectors = 0
        _write_checkpoint(
            staging,
            input_artifact=input_data,
            max_vectors_per_shard=max_vectors_per_shard,
            completed_shards=completed_shards,
        )

    if encoder is None:
        encoder = load_bge_m3_encoder(device=device)
    stream = _iter_input_records(input_data)
    last_input: Optional[_InputRecord] = None
    accumulator: Optional[_ShardAccumulator] = None
    try:
        # Validate and skip only records belonging to fully checkpointed shards.
        for _ in range(committed_vectors):
            last_input = next(stream)
        next_shard_number = len(completed_shards)
        accumulator = _ShardAccumulator(next_shard_number)
        while True:
            remaining = max_vectors_per_shard - accumulator.vector_count
            batch_inputs: List[_InputRecord] = []
            for _ in range(min(batch_size, remaining)):
                try:
                    item = next(stream)
                except StopIteration:
                    break
                batch_inputs.append(item)
            if not batch_inputs:
                break
            batch_chunks = [item.chunk for item in batch_inputs]
            try:
                records = embed_chunks(
                    batch_chunks,
                    encoder=encoder,
                    model_fingerprint=APPROVED_EMBEDDING_FINGERPRINT,
                    batch_size=batch_size,
                    device=device,
                )
            except Exception as error:
                if isinstance(error, EmbeddingArtifactBuilderError):
                    raise
                raise EmbeddingArtifactBuilderError("EMBEDDING_BATCH_FAILED", str(error)) from error
            if len(records) != len(batch_inputs):
                raise EmbeddingArtifactBuilderError("EMBEDDING_OUTPUT_INVALID", "encoder returned the wrong number of records")
            if any(record.model_fingerprint != APPROVED_EMBEDDING_FINGERPRINT for record in records):
                raise EmbeddingArtifactBuilderError("MODEL_FINGERPRINT_MISMATCH", "embedding record fingerprint differs")
            local_ids = accumulator.add(records)
            _insert_batch(connection, batch_inputs, records, accumulator.shard_number, local_ids, input_data)
            last_input = batch_inputs[-1]
            if accumulator.vector_count == max_vectors_per_shard:
                shard_path, shard_hash = accumulator.flush(shards_dir)
                checkpoint = _make_checkpoint(
                    input_data,
                    batch_inputs,
                    accumulator,
                    shard_hash,
                    committed_vectors + accumulator.vector_count,
                )
                _atomic_write_json(checkpoints_dir / f"{accumulator.shard_id}.json", checkpoint)
                completed_shards.append(checkpoint)
                committed_vectors += accumulator.vector_count
                _write_checkpoint(
                    staging,
                    input_artifact=input_data,
                    max_vectors_per_shard=max_vectors_per_shard,
                    completed_shards=completed_shards,
                )
                accumulator = _ShardAccumulator(accumulator.shard_number + 1)

        if accumulator is not None and accumulator.vector_count:
            if last_input is None:
                raise EmbeddingArtifactBuilderError("CHECKPOINT_INVALID", "vector shard has no input progress")
            shard_path, shard_hash = accumulator.flush(shards_dir)
            checkpoint = _make_checkpoint(
                input_data,
                [last_input],
                accumulator,
                shard_hash,
                committed_vectors + accumulator.vector_count,
            )
            _atomic_write_json(checkpoints_dir / f"{accumulator.shard_id}.json", checkpoint)
            completed_shards.append(checkpoint)
            committed_vectors += accumulator.vector_count
            _write_checkpoint(
                staging,
                input_artifact=input_data,
                max_vectors_per_shard=max_vectors_per_shard,
                completed_shards=completed_shards,
            )

        try:
            next(stream)
        except StopIteration:
            pass
        else:
            raise EmbeddingArtifactBuilderError("INPUT_COUNT_MISMATCH", "input contains more records than its manifest")
        if committed_vectors != input_data.chunk_count:
            raise EmbeddingArtifactBuilderError("COUNT_MISMATCH", "not every input chunk received an embedding")

        connection.execute("BEGIN")
        connection.execute(
            "UPDATE metadata SET value=? WHERE key='vector_count'", (str(committed_vectors),)
        )
        connection.commit()
        connection.close()
        connection = _open_metadata_database(metadata_path, input_data, max_vectors_per_shard)
        connection.close()
        manifest = _manifest_for_staging(
            staging,
            input_data,
            build_id,
            max_vectors_per_shard,
            completed_shards,
            committed_vectors,
        )
        _atomic_write_json(staging / "manifest.json", manifest)
        _validate_materialized_artifact(staging, manifest, input_data)
        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, final)
        _publish_pointer(root, manifest)
        return manifest
    finally:
        try:
            connection.close()
        except Exception:
            pass
        # A partially accumulated in-memory FAISS shard is deliberately not
        # checkpointed.  Resume removes its SQLite rows and rebuilds it.
        del accumulator


def _publish_pointer(root: Path, manifest: Mapping[str, Any]) -> None:
    """Publish only pointers to an already validated immutable directory."""

    _atomic_write_json(root / "manifest.json", dict(manifest))
    _atomic_write_bytes(root / "CURRENT", (str(manifest["artifact_id"]) + "\n").encode("utf-8"))


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Build the streaming M2 BGE-M3 FAISS/SQLite artifact")
    parser.add_argument("--input-artifact", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--device", choices=("cpu", "cuda", "mps"), required=True)
    parser.add_argument("--batch-size", type=int, required=True)
    parser.add_argument("--max-vectors-per-shard", type=int, default=MAX_VECTORS_PER_SHARD)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    manifest = build_streaming_embedding_artifact(
        args.input_artifact,
        args.output_root,
        device=args.device,
        batch_size=args.batch_size,
        max_vectors_per_shard=args.max_vectors_per_shard,
        resume=args.resume,
    )
    result = validate_published_artifact(args.output_root, input_artifact=args.input_artifact)
    result["artifact_id"] = manifest["artifact_id"]
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
