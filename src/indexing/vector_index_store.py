"""Deterministic FAISS-shard and SQLite persistence for M2B embeddings."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import sqlite3
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from src.indexing.embedding_schemas import EMBEDDING_DIMENSION, EmbeddingChunk, EmbeddingRecord
from src.indexing.embedding_indexer import EMBEDDING_ALGORITHM_VERSION
from src.indexing.schemas import RetrievalRepresentation
from src.understanding.schemas import SchemaValidationError


INDEX_SCHEMA_VERSION = "m2b-sharded-index-v1"
MAX_VECTORS_PER_SHARD = 100_000
INDEX_METRIC = "INNER_PRODUCT"


class IndexStorageError(SchemaValidationError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def _canonical(value: Any) -> bytes:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _atomic_write_json(path: Path, value: Any) -> None:
    _atomic_write_bytes(path, _canonical(value))


def _load_faiss_numpy() -> Tuple[Any, Any]:
    try:
        import faiss
        import numpy as np
    except ImportError as error:  # pragma: no cover - deployment dependent
        raise IndexStorageError("FAISS_DEPENDENCY_MISSING", "faiss and numpy are required") from error
    return faiss, np


def _build_id(
    records: Sequence[EmbeddingRecord],
    model_fingerprint: str,
    chunking_config_fingerprint: str,
) -> str:
    payload = {
        "index_schema_version": INDEX_SCHEMA_VERSION,
        "model_fingerprint": model_fingerprint,
        "chunking_config_fingerprint": chunking_config_fingerprint,
        "embedding_ids": [record.embedding_id for record in sorted(records, key=lambda item: item.embedding_id)],
    }
    return _sha256_bytes(_canonical(payload))


def _validate_inputs(
    records: Sequence[EmbeddingRecord],
    chunks: Sequence[EmbeddingChunk],
    representations: Sequence[RetrievalRepresentation],
    model_fingerprint: str,
    chunking_config_fingerprint: str,
) -> Tuple[List[EmbeddingRecord], Dict[str, EmbeddingChunk], Dict[str, RetrievalRepresentation]]:
    if not records:
        raise IndexStorageError("EMPTY_INDEX", "at least one embedding record is required")
    if len({record.embedding_id for record in records}) != len(records):
        raise IndexStorageError("DUPLICATE_EMBEDDING_ID", "embedding IDs must be unique")
    if any(
        record.dimension != EMBEDDING_DIMENSION
        or record.dtype != "float32"
        or not record.normalized
        for record in records
    ):
        raise IndexStorageError("EMBEDDING_CONFIG_INVALID", "records must be normalized float32 vectors of dimension 1024")
    chunk_by_id = {chunk.chunk_id: chunk for chunk in chunks}
    if len(chunk_by_id) != len(chunks):
        raise IndexStorageError("DUPLICATE_CHUNK_ID", "chunk IDs must be unique")
    representation_by_id = {representation.representation_id: representation for representation in representations}
    if len(representation_by_id) != len(representations):
        raise IndexStorageError("DUPLICATE_REPRESENTATION_ID", "representation IDs must be unique")
    if {record.chunk_id for record in records} != set(chunk_by_id):
        raise IndexStorageError("INCOMPLETE_RECORD_SET", "every chunk must have exactly one embedding record")
    for record in records:
        chunk = chunk_by_id.get(record.chunk_id)
        if chunk is None or record.representation_id != chunk.representation_id:
            raise IndexStorageError("RECORD_PROVENANCE_MISMATCH", "record and chunk provenance differ")
        if record.model_fingerprint != model_fingerprint:
            raise IndexStorageError("MODEL_FINGERPRINT_MISMATCH", "record fingerprint differs from build fingerprint")
        if chunk.representation_id not in representation_by_id:
            raise IndexStorageError("REPRESENTATION_MISSING", "chunk representation is missing")
        if chunk.chunking_config_fingerprint != chunking_config_fingerprint:
            raise IndexStorageError("CHUNKING_FINGERPRINT_MISMATCH", "chunk fingerprint differs from build fingerprint")
    return sorted(records, key=lambda item: (item.embedding_id, item.chunk_id)), chunk_by_id, representation_by_id


def _write_shard(path: Path, records: Sequence[EmbeddingRecord]) -> None:
    faiss, np = _load_faiss_numpy()
    matrix = np.asarray([record.vector for record in records], dtype=np.float32)
    if matrix.ndim != 2 or matrix.shape[1] != records[0].dimension:
        raise IndexStorageError("VECTOR_SHAPE_INVALID", "vectors do not form a fixed-dimensional matrix")
    index = faiss.IndexIDMap2(faiss.IndexFlatIP(records[0].dimension))
    local_ids = np.arange(len(records), dtype=np.int64)
    index.add_with_ids(matrix, local_ids)
    temporary = path.with_name(f".{path.name}.tmp")
    faiss.write_index(index, str(temporary))
    with temporary.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _write_metadata(
    path: Path,
    records: Sequence[EmbeddingRecord],
    chunks: Mapping[str, EmbeddingChunk],
    representations: Mapping[str, RetrievalRepresentation],
    model_fingerprint: str,
    chunking_config_fingerprint: str,
    shard_lookup: Mapping[str, Tuple[str, int]],
) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists():
        temporary.unlink()
    connection = sqlite3.connect(temporary)
    try:
        connection.executescript(
            """
            PRAGMA journal_mode=DELETE;
            PRAGMA synchronous=FULL;
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE representations (
                representation_id TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                source_ref TEXT NOT NULL,
                report_id TEXT NOT NULL,
                provenance_json TEXT NOT NULL
            );
            CREATE TABLE chunks (
                chunk_id TEXT PRIMARY KEY,
                representation_id TEXT NOT NULL,
                source_type TEXT NOT NULL,
                chunk_json TEXT NOT NULL,
                FOREIGN KEY (representation_id) REFERENCES representations(representation_id)
            );
            CREATE TABLE embeddings (
                embedding_id TEXT PRIMARY KEY,
                chunk_id TEXT NOT NULL UNIQUE,
                representation_id TEXT NOT NULL,
                shard_id TEXT NOT NULL,
                vector_ordinal INTEGER NOT NULL,
                model_fingerprint TEXT NOT NULL,
                chunking_config_fingerprint TEXT NOT NULL,
                FOREIGN KEY (chunk_id) REFERENCES chunks(chunk_id),
                FOREIGN KEY (representation_id) REFERENCES representations(representation_id)
            );
            CREATE INDEX embeddings_representation_idx ON embeddings(representation_id);
            """
        )
        connection.executemany(
            "INSERT INTO metadata(key, value) VALUES (?, ?)",
            [
                ("index_schema_version", INDEX_SCHEMA_VERSION),
                ("model_fingerprint", model_fingerprint),
                ("chunking_config_fingerprint", chunking_config_fingerprint),
            ],
        )
        connection.executemany(
            "INSERT INTO representations VALUES (?, ?, ?, ?, ?)",
            [
                (
                    representation.representation_id,
                    representation.source_type.value,
                    representation.source_ref,
                    representation.report_id,
                    json.dumps(representation.to_dict(), ensure_ascii=False, separators=(",", ":")),
                )
                for representation in representations.values()
            ],
        )
        connection.executemany(
            "INSERT INTO chunks VALUES (?, ?, ?, ?)",
            [
                (
                    chunk.chunk_id,
                    chunk.representation_id,
                    chunk.source_type.value,
                    json.dumps(chunk.to_dict(), ensure_ascii=False, separators=(",", ":")),
                )
                for chunk in chunks.values()
            ],
        )
        connection.executemany(
            "INSERT INTO embeddings VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    record.embedding_id,
                    record.chunk_id,
                    record.representation_id,
                    shard_lookup[record.embedding_id][0],
                    shard_lookup[record.embedding_id][1],
                    record.model_fingerprint,
                    chunking_config_fingerprint,
                )
                for record in records
            ],
        )
        connection.commit()
    finally:
        connection.close()
    with temporary.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def build_sharded_index(
    records: Sequence[EmbeddingRecord],
    chunks: Sequence[EmbeddingChunk],
    representations: Sequence[RetrievalRepresentation],
    artifact_root: str | Path,
    *,
    model_fingerprint: str,
    chunking_config_fingerprint: str,
) -> Dict[str, Any]:
    """Build and atomically publish an immutable sharded artifact."""

    records, chunks_by_id, representations_by_id = _validate_inputs(
        records, chunks, representations, model_fingerprint, chunking_config_fingerprint
    )
    root = Path(artifact_root)
    root.mkdir(parents=True, exist_ok=True)
    build_id = _build_id(records, model_fingerprint, chunking_config_fingerprint)
    staging = root / ".staging" / build_id
    final = root / "artifacts" / build_id
    staging_shards = staging / "shards"
    staging_shards.mkdir(parents=True, exist_ok=True)
    shard_entries: List[Dict[str, Any]] = []
    shard_lookup: Dict[str, Tuple[str, int]] = {}
    for shard_number, start in enumerate(range(0, len(records), MAX_VECTORS_PER_SHARD)):
        shard_records = records[start:start + MAX_VECTORS_PER_SHARD]
        shard_id = f"shard-{shard_number:05d}"
        shard_path = staging_shards / f"{shard_id}.faiss"
        if not shard_path.exists():
            _write_shard(shard_path, shard_records)
        for ordinal, record in enumerate(shard_records):
            shard_lookup[record.embedding_id] = (shard_id, ordinal)
        shard_entries.append({
            "shard_id": shard_id,
            "file": f"shards/{shard_id}.faiss",
            "vector_count": len(shard_records),
            "sha256": _sha256_file(shard_path),
        })
    metadata_path = staging / "metadata.sqlite"
    _write_metadata(
        metadata_path,
        records,
        chunks_by_id,
        representations_by_id,
        model_fingerprint,
        chunking_config_fingerprint,
        shard_lookup,
    )
    sqlite_entry = {"file": "metadata.sqlite", "sha256": _sha256_file(metadata_path)}
    manifest = {
        "manifest_schema_version": INDEX_SCHEMA_VERSION,
        "artifact_id": build_id,
        "artifact_directory": f"artifacts/{build_id}",
        "embedding_algorithm_version": EMBEDDING_ALGORITHM_VERSION,
        "model_fingerprint": model_fingerprint,
        "chunking_config_fingerprint": chunking_config_fingerprint,
        "dimension": records[0].dimension,
        "dtype": records[0].dtype,
        "metric": INDEX_METRIC,
        "normalized": True,
        "max_vectors_per_shard": MAX_VECTORS_PER_SHARD,
        "vector_count": len(records),
        "chunk_count": len(chunks_by_id),
        "representation_count": len(representations_by_id),
        "shards": shard_entries,
        "metadata": sqlite_entry,
    }
    _atomic_write_json(staging / "manifest.json", manifest)
    final.parent.mkdir(parents=True, exist_ok=True)
    if not final.exists():
        os.replace(staging, final)
    else:
        # Published artifact directories are immutable; an identical build can
        # be resumed/reused without rewriting it.
        shutil.rmtree(staging)
    published_manifest = dict(manifest)
    published_manifest["artifact_directory"] = f"artifacts/{build_id}"
    _atomic_write_json(root / "manifest.json", published_manifest)
    return published_manifest


def load_sharded_manifest(
    artifact_root: str | Path,
    *,
    verify: bool = True,
    expected_model_fingerprint: str | None = None,
    expected_chunking_config_fingerprint: str | None = None,
) -> Dict[str, Any]:
    root = Path(artifact_root)
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise IndexStorageError("MANIFEST_MISSING", "top-level manifest.json is missing")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise IndexStorageError("MANIFEST_INVALID", "manifest is not valid JSON") from error
    if manifest.get("manifest_schema_version") != INDEX_SCHEMA_VERSION:
        raise IndexStorageError("INDEX_VERSION_MISMATCH", "unsupported manifest schema version")
    if (
        expected_model_fingerprint is not None
        and manifest.get("model_fingerprint") != expected_model_fingerprint
    ):
        raise IndexStorageError("MODEL_FINGERPRINT_MISMATCH", "manifest model fingerprint differs")
    if (
        expected_chunking_config_fingerprint is not None
        and manifest.get("chunking_config_fingerprint") != expected_chunking_config_fingerprint
    ):
        raise IndexStorageError("CHUNKING_FINGERPRINT_MISMATCH", "manifest chunking fingerprint differs")
    artifact_directory = manifest.get("artifact_directory")
    if not isinstance(artifact_directory, str) or Path(artifact_directory).is_absolute() or ".." in Path(artifact_directory).parts:
        raise IndexStorageError("MANIFEST_PATH_INVALID", "artifact directory must be relative")
    artifact = root / artifact_directory
    if verify:
        if not artifact.is_dir():
            raise IndexStorageError("ARTIFACT_MISSING", "published artifact directory is missing")
        for entry in manifest.get("shards", []):
            path = artifact / entry["file"]
            if not path.is_file() or _sha256_file(path) != entry["sha256"]:
                raise IndexStorageError("SHARD_CORRUPT_OR_MISSING", entry.get("shard_id", "unknown"))
        metadata = manifest.get("metadata", {})
        metadata_path = artifact / metadata.get("file", "")
        if not metadata_path.is_file() or _sha256_file(metadata_path) != metadata.get("sha256"):
            raise IndexStorageError("METADATA_CORRUPT_OR_MISSING", "metadata.sqlite")
        connection = sqlite3.connect(metadata_path)
        try:
            result = connection.execute("PRAGMA integrity_check").fetchone()
            if result != ("ok",):
                raise IndexStorageError("METADATA_CORRUPT", "SQLite integrity check failed")
        finally:
            connection.close()
    return manifest
