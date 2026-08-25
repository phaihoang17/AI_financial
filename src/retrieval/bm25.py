"""TASK-031 persisted SQLite FTS5 lexical indexing and deterministic search."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
import unicodedata

from src.indexing.embedding_artifact_builder import (
    APPROVED_EMBEDDING_FINGERPRINT,
    EmbeddingArtifactBuilderError,
    _iter_input_records,
    _validate_input_manifest,
)
from src.indexing.embedding_chunker import BGE_M3_MODEL_ID, BGE_M3_REVISION, CHUNKING_CONFIG_FINGERPRINT
from src.retrieval.compatibility import (
    ArtifactCompatibilityError,
    BM25_INDEX_SCHEMA_VERSION,
    validate_bm25_artifact_compatibility,
)
from src.retrieval.corpus_candidates import candidate_from_representation_chunk
from src.retrieval.query_embedding import make_retrieval_query_id
from src.retrieval.schemas import (
    RetrievalCandidate,
    RetrievalContractError,
    RetrievalQuery,
    rank_candidates,
    resolve_eligible_source_types,
)
from src.retrieval.search_diagnostics import RetrievalSearchObservation, RetrievalSearchResult


BM25_BACKEND = "sqlite-fts5"
BM25_TOKENIZER = "unicode61 remove_diacritics 0"
_BUILD_SCHEMA_VERSION = "m3-bm25-build-v1"
_SAFE_RELATIVE_PATH = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$)).+$")


class BM25IndexError(RetrievalContractError):
    """A typed TASK-031 build, load, or search failure."""


def unicode_tokens(text: str) -> List[str]:
    """Tokenize deterministically with the same Unicode categories as FTS v1."""

    if not isinstance(text, str):
        raise TypeError("text must be a string")
    tokens: List[str] = []
    current: List[str] = []
    for character in text:
        category = unicodedata.category(character)
        if category[0] in {"L", "M", "N"} or category == "Co" or character == "_":
            current.append(character)
        elif current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    return tokens


def _sha256_file(path: Path) -> str:
    digest = sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise BM25IndexError("BM25_FILE_READ_FAILED", str(path)) from error
    return digest.hexdigest()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(_canonical_json(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as error:
        raise BM25IndexError("BM25_MANIFEST_WRITE_FAILED", str(path)) from error


def _safe_relative(value: Any, path: str) -> Path:
    if not isinstance(value, str) or not value or not _SAFE_RELATIVE_PATH.fullmatch(value):
        raise BM25IndexError("BM25_MANIFEST_INVALID", f"{path} must be a safe relative path")
    return Path(value)


def _build_logical_fingerprint(
    *, corpus_fingerprint: str, candidate_digest: str, candidate_count: int
) -> str:
    return sha256(
        _canonical_json(
            {
                "build_schema_version": _BUILD_SCHEMA_VERSION,
                "bm25_schema_version": BM25_INDEX_SCHEMA_VERSION,
                "corpus_fingerprint": corpus_fingerprint,
                "candidate_digest": candidate_digest,
                "candidate_count": candidate_count,
                "tokenizer": BM25_TOKENIZER,
            }
        )
    ).hexdigest()


def build_bm25_index(corpus_artifact_root: str | Path, output_root: str | Path) -> Dict[str, Any]:
    """Build and atomically publish a local FTS5 index over M2 embedding chunks."""

    try:
        corpus = _validate_input_manifest(corpus_artifact_root, verify_hashes=True)
    except EmbeddingArtifactBuilderError as error:
        raise BM25IndexError("CORPUS_ARTIFACT_INVALID", str(error)) from error
    root = Path(output_root)
    staging = root / ".staging" / "m3-bm25-build-v1"
    if staging.exists():
        raise BM25IndexError("BM25_STAGING_EXISTS", str(staging))
    database = staging / "bm25.sqlite"
    staging.mkdir(parents=True, exist_ok=False)
    try:
        connection = sqlite3.connect(database)
        try:
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA synchronous=FULL")
            connection.executescript(
                f"""
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE VIRTUAL TABLE candidates_fts USING fts5(
                    candidate_id UNINDEXED,
                    chunk_id UNINDEXED,
                    representation_id UNINDEXED,
                    source_type UNINDEXED,
                    content,
                    candidate_json UNINDEXED,
                    tokenize='{BM25_TOKENIZER}'
                );
                """
            )
            metadata = {
                "bm25_schema_version": BM25_INDEX_SCHEMA_VERSION,
                "backend": BM25_BACKEND,
                "tokenizer": BM25_TOKENIZER,
                "corpus_artifact_id": corpus.artifact_id,
                "corpus_fingerprint": corpus.corpus_fingerprint,
                "chunking_config_fingerprint": CHUNKING_CONFIG_FINGERPRINT,
                "embedding_fingerprint": APPROVED_EMBEDDING_FINGERPRINT,
                "model_id": BGE_M3_MODEL_ID,
                "model_revision": BGE_M3_REVISION,
            }
            connection.executemany(
                "INSERT INTO metadata(key, value) VALUES (?, ?)", sorted(metadata.items())
            )
            digest = sha256()
            count = 0
            rows: List[Tuple[str, str, str, str, str, str]] = []
            for record in _iter_input_records(corpus):
                candidate = candidate_from_representation_chunk(record.representation, record.chunk)
                if unicode_tokens(candidate.content):
                    candidate_json = candidate.to_json()
                    rows.append(
                        (
                            candidate.candidate_id,
                            candidate.chunk_id or "",
                            candidate.representation_id,
                            candidate.source_type.value,
                            candidate.content,
                            candidate_json,
                        )
                    )
                    digest.update(candidate_json.encode("utf-8"))
                    digest.update(b"\n")
                    count += 1
                if len(rows) >= 1_000:
                    connection.executemany(
                        "INSERT INTO candidates_fts VALUES (?, ?, ?, ?, ?, ?)", rows
                    )
                    rows = []
            if rows:
                connection.executemany("INSERT INTO candidates_fts VALUES (?, ?, ?, ?, ?, ?)", rows)
            if count < 1:
                raise BM25IndexError("BM25_EMPTY_INDEX", "corpus has no tokenizable chunks")
            logical_fingerprint = _build_logical_fingerprint(
                corpus_fingerprint=corpus.corpus_fingerprint,
                candidate_digest=digest.hexdigest(),
                candidate_count=count,
            )
            artifact_id = logical_fingerprint
            connection.execute(
                "INSERT INTO metadata(key, value) VALUES (?, ?)",
                ("candidate_count", str(count)),
            )
            connection.execute(
                "INSERT INTO metadata(key, value) VALUES (?, ?)",
                ("logical_index_fingerprint", logical_fingerprint),
            )
            connection.commit()
        finally:
            connection.close()
        artifact = root / "artifacts" / artifact_id
        artifact.mkdir(parents=True, exist_ok=True)
        target_database = artifact / "bm25.sqlite"
        if target_database.exists():
            if _sha256_file(target_database) != _sha256_file(database):
                raise BM25IndexError("BM25_IMMUTABLE_ARTIFACT_CONFLICT", artifact_id)
        else:
            os.replace(database, target_database)
        database_hash = _sha256_file(target_database)
        manifest: Dict[str, Any] = {
            "bm25_schema_version": BM25_INDEX_SCHEMA_VERSION,
            "artifact_status": "COMMITTED",
            "artifact_id": artifact_id,
            "artifact_directory": f"artifacts/{artifact_id}",
            "backend": BM25_BACKEND,
            "tokenizer": BM25_TOKENIZER,
            "corpus_artifact_id": corpus.artifact_id,
            "corpus_manifest_sha256": sha256((corpus.root / "manifest.json").read_bytes()).hexdigest(),
            "corpus_fingerprint": corpus.corpus_fingerprint,
            "chunking_config_fingerprint": CHUNKING_CONFIG_FINGERPRINT,
            "embedding_fingerprint": APPROVED_EMBEDDING_FINGERPRINT,
            "model_id": BGE_M3_MODEL_ID,
            "model_revision": BGE_M3_REVISION,
            "candidate_count": count,
            "logical_index_fingerprint": logical_fingerprint,
            "database": {"file": "bm25.sqlite", "sha256": database_hash},
        }
        _atomic_write_json(artifact / "manifest.json", manifest)
        _atomic_write_json(root / "manifest.json", manifest)
        return manifest
    except (sqlite3.DatabaseError, OSError) as error:
        raise BM25IndexError("BM25_BUILD_FAILED", str(error)) from error
    finally:
        if staging.exists():
            shutil.rmtree(staging)


class BM25Index:
    """Read-only SQLite FTS5 index validated before every serving instance."""

    def __init__(self, index_root: str | Path, corpus_artifact_root: str | Path) -> None:
        try:
            report = validate_bm25_artifact_compatibility(index_root, corpus_artifact_root)
        except ArtifactCompatibilityError as error:
            raise BM25IndexError(error.code, str(error)) from error
        self._root = Path(index_root)
        self._manifest = self._read_manifest()
        database = self._database_path()
        if not database.is_file() or _sha256_file(database) != self._manifest["database"]["sha256"]:
            raise BM25IndexError("BM25_INDEX_CORRUPT_OR_MISSING", str(database))
        self._database = database
        self.artifact_fingerprint = report.artifact_fingerprint
        self.embedding_fingerprint = report.model_fingerprint
        self._validate_sqlite()

    def _read_manifest(self) -> Dict[str, Any]:
        path = self._root / "manifest.json"
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as error:
            raise BM25IndexError("BM25_MANIFEST_INVALID", str(path)) from error
        if not isinstance(manifest, dict):
            raise BM25IndexError("BM25_MANIFEST_INVALID", "manifest must be an object")
        database = manifest.get("database")
        if (
            manifest.get("bm25_schema_version") != BM25_INDEX_SCHEMA_VERSION
            or manifest.get("backend") != BM25_BACKEND
            or manifest.get("tokenizer") != BM25_TOKENIZER
            or not isinstance(database, dict)
            or not isinstance(database.get("sha256"), str)
            or len(database["sha256"]) != 64
        ):
            raise BM25IndexError("BM25_MANIFEST_INVALID", "manifest configuration is incompatible")
        return manifest

    def _database_path(self) -> Path:
        artifact = _safe_relative(self._manifest.get("artifact_directory"), "artifact_directory")
        database = _safe_relative(self._manifest["database"].get("file"), "database.file")
        return self._root / artifact / database

    def _validate_sqlite(self) -> None:
        try:
            connection = sqlite3.connect(f"file:{self._database}?mode=ro", uri=True)
            try:
                if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                    raise BM25IndexError("BM25_INDEX_CORRUPT_OR_MISSING", "SQLite integrity check failed")
                metadata = dict(connection.execute("SELECT key, value FROM metadata"))
                expected = {
                    "bm25_schema_version": BM25_INDEX_SCHEMA_VERSION,
                    "backend": BM25_BACKEND,
                    "tokenizer": BM25_TOKENIZER,
                    "candidate_count": str(self._manifest["candidate_count"]),
                    "logical_index_fingerprint": self._manifest["logical_index_fingerprint"],
                }
                if any(metadata.get(key) != value for key, value in expected.items()):
                    raise BM25IndexError("BM25_INDEX_INCOMPATIBLE", "SQLite metadata differs from manifest")
                table = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='candidates_fts'"
                ).fetchone()
                if table is None:
                    raise BM25IndexError("BM25_INDEX_INCOMPATIBLE", "FTS table is missing")
            finally:
                connection.close()
        except sqlite3.DatabaseError as error:
            raise BM25IndexError("BM25_INDEX_CORRUPT_OR_MISSING", str(error)) from error

    def search_with_diagnostics(
        self,
        query: RetrievalQuery,
        *,
        top_k: int,
        eligible_source_types: Optional[Sequence[Any]] = None,
    ) -> RetrievalSearchResult:
        if not isinstance(query, RetrievalQuery):
            raise TypeError("query must be a RetrievalQuery")
        if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
            raise BM25IndexError("INVALID_TOP_K", "top_k must be a positive integer")
        try:
            eligible = resolve_eligible_source_types(query, eligible_source_types)
        except RetrievalContractError as error:
            raise BM25IndexError(error.code, str(error)) from error
        source_placeholders = ",".join("?" for _ in eligible)
        best: Dict[str, Tuple[RetrievalCandidate, float, int]] = {}
        try:
            connection = sqlite3.connect(f"file:{self._database}?mode=ro", uri=True)
            try:
                for query_text_index, query_text in enumerate(query.query_texts):
                    tokens = unicode_tokens(query_text)
                    if not tokens:
                        continue
                    expression = " AND ".join(f'"{token}"' for token in tokens)
                    rows = connection.execute(
                        """
                        SELECT candidate_json, -bm25(candidates_fts) AS public_score
                        FROM candidates_fts
                        WHERE candidates_fts MATCH ? AND source_type IN (""" + source_placeholders + """)
                        ORDER BY public_score DESC, candidate_id ASC
                        LIMIT ?
                        """,
                        (expression, *(source.value for source in eligible), top_k),
                    )
                    for candidate_json, score in rows:
                        candidate = RetrievalCandidate.from_dict(json.loads(candidate_json))
                        public_score = float(score)
                        previous = best.get(candidate.candidate_id)
                        if previous is None or public_score > previous[1]:
                            best[candidate.candidate_id] = (
                                replace(candidate, bm25_score=public_score),
                                public_score,
                                query_text_index,
                            )
            finally:
                connection.close()
        except (sqlite3.DatabaseError, ValueError, RetrievalContractError) as error:
            if isinstance(error, BM25IndexError):
                raise
            raise BM25IndexError("BM25_SEARCH_FAILED", str(error)) from error
        ranked = rank_candidates([item[0] for item in best.values()], score_field="bm25_score")[:top_k]
        query_id = make_retrieval_query_id(query)
        observations = [
            RetrievalSearchObservation(
                query_id=query_id,
                query_text_index=best[candidate.candidate_id][2],
                stage="bm25",
                candidate_id=candidate.candidate_id,
                rank=candidate.rank or 1,
                score=candidate.bm25_score or 0.0,
                artifact_fingerprint=self.artifact_fingerprint,
                embedding_fingerprint=self.embedding_fingerprint,
            )
            for candidate in ranked
        ]
        return RetrievalSearchResult(candidates=ranked, observations=observations)

    def search(
        self,
        query: RetrievalQuery,
        *,
        top_k: int,
        eligible_source_types: Optional[Sequence[Any]] = None,
    ) -> List[RetrievalCandidate]:
        return self.search_with_diagnostics(
            query, top_k=top_k, eligible_source_types=eligible_source_types
        ).candidates


def search_bm25(
    index_root: str | Path,
    corpus_artifact_root: str | Path,
    query: RetrievalQuery,
    *,
    top_k: int,
    eligible_source_types: Optional[Sequence[Any]] = None,
) -> List[RetrievalCandidate]:
    """Open a validated BM25 artifact and return only canonical candidates."""

    return BM25Index(index_root, corpus_artifact_root).search(
        query, top_k=top_k, eligible_source_types=eligible_source_types
    )
