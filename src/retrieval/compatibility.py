"""TASK-03C compatibility validation before an M3 backend is used."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, Mapping, Optional

from src.indexing.corpus_artifact import (
    CORPUS_ARTIFACT_SCHEMA_VERSION,
    CORPUS_RECORD_SCHEMA_VERSION,
)
from src.indexing.embedding_artifact_builder import (
    APPROVED_EMBEDDING_FINGERPRINT,
    BUILDER_SCHEMA_VERSION,
)
from src.indexing.embedding_chunker import (
    BGE_M3_MODEL_ID,
    BGE_M3_REVISION,
    CHUNKING_CONFIG_FINGERPRINT,
)
from src.indexing.embedding_schemas import (
    EMBEDDING_CHUNK_SCHEMA_VERSION,
    EMBEDDING_DIMENSION,
    EMBEDDING_DTYPE,
)
from src.indexing.schemas import M2A_SCHEMA_VERSION, NORMALIZATION_VERSION, REPRESENTATION_VERSION
from src.indexing.vector_index_store import INDEX_SCHEMA_VERSION, INDEX_METRIC
from src.understanding.schemas import SchemaValidationError


BM25_INDEX_SCHEMA_VERSION = "m3-bm25-index-v1"
SQLITE_METADATA_SCHEMA_VERSION = INDEX_SCHEMA_VERSION


class ArtifactCompatibilityError(SchemaValidationError):
    """A typed, hard compatibility failure that must stop retrieval."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class CompatibilityReport:
    status: str
    artifact_id: str
    artifact_fingerprint: str
    corpus_fingerprint: str
    model_fingerprint: str
    vector_count: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "artifact_id": self.artifact_id,
            "artifact_fingerprint": self.artifact_fingerprint,
            "corpus_fingerprint": self.corpus_fingerprint,
            "model_fingerprint": self.model_fingerprint,
            "vector_count": self.vector_count,
        }


def _read_json(path: Path, code: str) -> Dict[str, Any]:
    if not path.is_file():
        raise ArtifactCompatibilityError(code, f"missing {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise ArtifactCompatibilityError(code, f"invalid JSON at {path}") from error
    if not isinstance(value, dict):
        raise ArtifactCompatibilityError(code, f"{path} must contain an object")
    return value


def _require_equal(data: Mapping[str, Any], key: str, expected: Any, code: str) -> None:
    if key not in data:
        raise ArtifactCompatibilityError("COMPATIBILITY_METADATA_MISSING", key)
    if data[key] != expected:
        raise ArtifactCompatibilityError(code, f"{key} is incompatible")


def _require_sha256(value: Any, path: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ArtifactCompatibilityError("COMPATIBILITY_METADATA_INVALID", f"{path} must be SHA-256")
    try:
        int(value, 16)
    except ValueError as error:
        raise ArtifactCompatibilityError("COMPATIBILITY_METADATA_INVALID", f"{path} must be SHA-256") from error
    if value != value.lower():
        raise ArtifactCompatibilityError("COMPATIBILITY_METADATA_INVALID", f"{path} must be SHA-256")
    return value


def _safe_relative_file(value: Any, path: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ArtifactCompatibilityError("COMPATIBILITY_METADATA_MISSING", path)
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ArtifactCompatibilityError("COMPATIBILITY_PATH_INVALID", path)
    return relative


def _require_non_negative_int(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ArtifactCompatibilityError("COMPATIBILITY_METADATA_INVALID", f"{path} must be a non-negative integer")
    return value


def _validate_corpus_manifest(corpus_root: Path) -> tuple[Dict[str, Any], str]:
    manifest_path = corpus_root / "manifest.json"
    manifest = _read_json(manifest_path, "M2_CORPUS_MANIFEST_MISSING")
    expected = {
        "manifest_schema_version": CORPUS_ARTIFACT_SCHEMA_VERSION,
        "record_schema_version": CORPUS_RECORD_SCHEMA_VERSION,
        "m2a_schema_version": M2A_SCHEMA_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "representation_version": REPRESENTATION_VERSION,
        "chunk_schema_version": EMBEDDING_CHUNK_SCHEMA_VERSION,
        "chunking_config_fingerprint": CHUNKING_CONFIG_FINGERPRINT,
    }
    for key, value in expected.items():
        _require_equal(manifest, key, value, "M2_CORPUS_SCHEMA_MISMATCH")
    _require_equal(manifest, "artifact_status", "COMMITTED", "M2_CORPUS_NOT_COMMITTED")
    tokenizer = manifest.get("tokenizer")
    if not isinstance(tokenizer, Mapping):
        raise ArtifactCompatibilityError("COMPATIBILITY_METADATA_MISSING", "tokenizer")
    _require_equal(tokenizer, "model_id", BGE_M3_MODEL_ID, "MODEL_ID_MISMATCH")
    _require_equal(tokenizer, "revision", BGE_M3_REVISION, "MODEL_REVISION_MISMATCH")
    manifest_bytes = manifest_path.read_bytes()
    return manifest, sha256(manifest_bytes).hexdigest()


def _read_sqlite_metadata(metadata_path: Path) -> Dict[str, str]:
    if not metadata_path.is_file():
        raise ArtifactCompatibilityError("SQLITE_METADATA_MISSING", str(metadata_path))
    try:
        connection = sqlite3.connect(f"file:{metadata_path}?mode=ro", uri=True)
        try:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='metadata'"
            ).fetchone()
            if table is None:
                raise ArtifactCompatibilityError("SQLITE_SCHEMA_MISMATCH", "metadata table is missing")
            return dict(connection.execute("SELECT key, value FROM metadata"))
        finally:
            connection.close()
    except sqlite3.DatabaseError as error:
        raise ArtifactCompatibilityError("SQLITE_SCHEMA_MISMATCH", str(error)) from error


def _validate_bm25_metadata(metadata: Optional[Mapping[str, Any]]) -> None:
    """Reserve the explicit BM25 version check without implementing BM25 itself."""

    if metadata is None:
        return
    if not isinstance(metadata, Mapping):
        raise ArtifactCompatibilityError("BM25_METADATA_INVALID", "BM25 metadata must be an object")
    _require_equal(metadata, "bm25_schema_version", BM25_INDEX_SCHEMA_VERSION, "BM25_VERSION_MISMATCH")


def validate_bm25_artifact_compatibility(
    bm25_artifact_root: str | Path, corpus_artifact_root: str | Path
) -> CompatibilityReport:
    """Validate the committed TASK-031 manifest against its exact M2 corpus."""

    corpus_root = Path(corpus_artifact_root)
    corpus_manifest, corpus_manifest_sha256 = _validate_corpus_manifest(corpus_root)
    bm25_root = Path(bm25_artifact_root)
    manifest_path = bm25_root / "manifest.json"
    manifest = _read_json(manifest_path, "BM25_MANIFEST_MISSING")
    expected = {
        "bm25_schema_version": BM25_INDEX_SCHEMA_VERSION,
        "artifact_status": "COMMITTED",
        "corpus_artifact_id": corpus_manifest.get("artifact_id"),
        "corpus_manifest_sha256": corpus_manifest_sha256,
        "corpus_fingerprint": corpus_manifest.get("source_inventory_sha256"),
        "embedding_fingerprint": APPROVED_EMBEDDING_FINGERPRINT,
        "model_id": BGE_M3_MODEL_ID,
        "model_revision": BGE_M3_REVISION,
        "chunking_config_fingerprint": CHUNKING_CONFIG_FINGERPRINT,
    }
    for key, value in expected.items():
        _require_equal(manifest, key, value, "BM25_ARTIFACT_MISMATCH")
    artifact_id = manifest.get("artifact_id")
    if not isinstance(artifact_id, str) or not artifact_id:
        raise ArtifactCompatibilityError("COMPATIBILITY_METADATA_MISSING", "artifact_id")
    _require_sha256(manifest.get("corpus_fingerprint"), "corpus_fingerprint")
    _require_sha256(manifest.get("embedding_fingerprint"), "embedding_fingerprint")
    _require_non_negative_int(manifest.get("candidate_count"), "candidate_count")
    return CompatibilityReport(
        status="PASS",
        artifact_id=artifact_id,
        artifact_fingerprint=sha256(manifest_path.read_bytes()).hexdigest(),
        corpus_fingerprint=str(manifest["corpus_fingerprint"]),
        model_fingerprint=str(manifest["embedding_fingerprint"]),
        vector_count=int(manifest["candidate_count"]),
    )


def validate_retrieval_artifact_compatibility(
    vector_artifact_root: str | Path,
    corpus_artifact_root: str | Path,
    *,
    bm25_metadata: Optional[Mapping[str, Any]] = None,
) -> CompatibilityReport:
    """Validate M2/M2B metadata exactly before a retrieval backend is used.

    A compatible fixture or smoke vector artifact is valid input.  This gate
    never requires a production-scale artifact, rebuilds nothing, and makes no
    best-effort substitutions.
    """

    corpus_root = Path(corpus_artifact_root)
    corpus_manifest, corpus_manifest_sha256 = _validate_corpus_manifest(corpus_root)
    vector_root = Path(vector_artifact_root)
    vector_manifest_path = vector_root / "manifest.json"
    vector_manifest = _read_json(vector_manifest_path, "VECTOR_MANIFEST_MISSING")

    expected_vector = {
        "manifest_schema_version": INDEX_SCHEMA_VERSION,
        "artifact_status": "COMMITTED",
        "builder_schema_version": BUILDER_SCHEMA_VERSION,
        "input_artifact_id": corpus_manifest.get("artifact_id"),
        "input_manifest_sha256": corpus_manifest_sha256,
        "corpus_fingerprint": corpus_manifest.get("source_inventory_sha256"),
        "input_chunking_config_fingerprint": CHUNKING_CONFIG_FINGERPRINT,
        "model_id": BGE_M3_MODEL_ID,
        "model_revision": BGE_M3_REVISION,
        "embedding_fingerprint": APPROVED_EMBEDDING_FINGERPRINT,
        "model_fingerprint": APPROVED_EMBEDDING_FINGERPRINT,
        "dimension": EMBEDDING_DIMENSION,
        "dtype": EMBEDDING_DTYPE,
        "metric": INDEX_METRIC,
        "normalized": True,
        "truncation": False,
    }
    codes = {
        "manifest_schema_version": "FAISS_INDEX_VERSION_MISMATCH",
        "model_id": "MODEL_ID_MISMATCH",
        "model_revision": "MODEL_REVISION_MISMATCH",
        "embedding_fingerprint": "EMBEDDING_FINGERPRINT_MISMATCH",
        "model_fingerprint": "EMBEDDING_FINGERPRINT_MISMATCH",
        "dimension": "VECTOR_DIMENSION_MISMATCH",
        "dtype": "VECTOR_DTYPE_MISMATCH",
        "normalized": "VECTOR_NORMALIZATION_MISMATCH",
        "metric": "FAISS_METRIC_MISMATCH",
    }
    for key, value in expected_vector.items():
        _require_equal(vector_manifest, key, value, codes.get(key, "VECTOR_ARTIFACT_MISMATCH"))
    vector_count = _require_non_negative_int(vector_manifest.get("vector_count"), "vector_count")
    if vector_count != _require_non_negative_int(vector_manifest.get("chunk_count"), "chunk_count"):
        raise ArtifactCompatibilityError("VECTOR_COUNT_MISMATCH", "vector_count must equal chunk_count")
    artifact_id = vector_manifest.get("artifact_id")
    if not isinstance(artifact_id, str) or not artifact_id:
        raise ArtifactCompatibilityError("COMPATIBILITY_METADATA_MISSING", "artifact_id")
    _require_sha256(vector_manifest.get("embedding_fingerprint"), "embedding_fingerprint")
    _require_sha256(vector_manifest.get("corpus_fingerprint"), "corpus_fingerprint")

    artifact_directory = _safe_relative_file(vector_manifest.get("artifact_directory"), "artifact_directory")
    metadata_entry = vector_manifest.get("metadata")
    if not isinstance(metadata_entry, Mapping):
        raise ArtifactCompatibilityError("COMPATIBILITY_METADATA_MISSING", "metadata")
    metadata_file = _safe_relative_file(metadata_entry.get("file"), "metadata.file")
    metadata = _read_sqlite_metadata(vector_root / artifact_directory / metadata_file)
    expected_sqlite = {
        "index_schema_version": SQLITE_METADATA_SCHEMA_VERSION,
        "input_artifact_id": corpus_manifest.get("artifact_id"),
        "input_manifest_sha256": corpus_manifest_sha256,
        "corpus_fingerprint": corpus_manifest.get("source_inventory_sha256"),
        "chunking_config_fingerprint": CHUNKING_CONFIG_FINGERPRINT,
        "embedding_fingerprint": APPROVED_EMBEDDING_FINGERPRINT,
        "model_id": BGE_M3_MODEL_ID,
        "model_revision": BGE_M3_REVISION,
        "dimension": str(EMBEDDING_DIMENSION),
        "dtype": EMBEDDING_DTYPE,
        "truncation": "false",
    }
    for key, value in expected_sqlite.items():
        _require_equal(metadata, key, value, "SQLITE_METADATA_VERSION_MISMATCH")
    _validate_bm25_metadata(bm25_metadata)

    return CompatibilityReport(
        status="PASS",
        artifact_id=artifact_id,
        artifact_fingerprint=sha256(vector_manifest_path.read_bytes()).hexdigest(),
        corpus_fingerprint=str(vector_manifest["corpus_fingerprint"]),
        model_fingerprint=str(vector_manifest["model_fingerprint"]),
        vector_count=vector_count,
    )
