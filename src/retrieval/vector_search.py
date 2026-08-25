"""TASK-033 exact metadata-filtered search over immutable M2B FAISS shards."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from src.indexing.embedding_artifact_builder import (
    APPROVED_EMBEDDING_FINGERPRINT,
    EmbeddingArtifactBuilderError,
    validate_published_artifact,
)
from src.indexing.embedding_schemas import EMBEDDING_DIMENSION
from src.retrieval.compatibility import (
    ArtifactCompatibilityError,
    CompatibilityReport,
    validate_retrieval_artifact_compatibility,
)
from src.retrieval.corpus_candidates import CandidateLocation, CorpusCandidateError, CorpusCandidateLoader
from src.retrieval.metadata_filter import report_metadata_sql_constraints
from src.retrieval.query_embedding import QueryEmbedding, make_retrieval_query_id
from src.retrieval.schemas import (
    RetrievalCandidate,
    RetrievalContractError,
    RetrievalQuery,
    make_candidate_id,
    rank_candidates,
    resolve_eligible_source_types,
)
from src.retrieval.search_diagnostics import RetrievalSearchObservation, RetrievalSearchResult
from src.supervisor.schemas import EvidenceSource


class VectorSearchError(RetrievalContractError):
    """A typed TASK-033 artifact, query, or exact-search failure."""


@dataclass(frozen=True)
class _VectorLocation:
    candidate_id: str
    source: CandidateLocation
    faiss_shard_id: str
    faiss_shard_number: int
    local_vector_id: int


def _load_faiss_numpy() -> Tuple[Any, Any]:
    try:
        import faiss
        import numpy as np
    except ImportError as error:  # pragma: no cover - deployment dependent
        raise VectorSearchError("FAISS_DEPENDENCY_MISSING", "faiss and numpy are required") from error
    return faiss, np


def _safe_relative(value: Any, path: str) -> Path:
    if not isinstance(value, str) or not value:
        raise VectorSearchError("VECTOR_MANIFEST_INVALID", f"{path} must be a relative path")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise VectorSearchError("VECTOR_MANIFEST_INVALID", f"{path} must be a relative path")
    return relative


class VectorSearcher:
    """A fully validated, exact FlatIP search service for one immutable artifact."""

    def __init__(self, vector_artifact_root: str | Path, corpus_artifact_root: str | Path) -> None:
        try:
            self._compatibility: CompatibilityReport = validate_retrieval_artifact_compatibility(
                vector_artifact_root, corpus_artifact_root
            )
            # TASK-03C compares M2/M2B contracts; this additional published-artifact
            # audit rejects every missing/corrupt shard before this service can run.
            validate_published_artifact(vector_artifact_root, input_artifact=corpus_artifact_root)
        except ArtifactCompatibilityError as error:
            raise VectorSearchError(error.code, str(error)) from error
        except EmbeddingArtifactBuilderError as error:
            raise VectorSearchError("VECTOR_ARTIFACT_INVALID", str(error)) from error
        self._vector_root = Path(vector_artifact_root)
        self._manifest = self._load_manifest()
        self._artifact = self._vector_root / _safe_relative(
            self._manifest.get("artifact_directory"), "artifact_directory"
        )
        metadata = self._manifest.get("metadata")
        if not isinstance(metadata, Mapping):
            raise VectorSearchError("VECTOR_MANIFEST_INVALID", "metadata is missing")
        self._metadata_path = self._artifact / _safe_relative(metadata.get("file"), "metadata.file")
        if not self._metadata_path.is_file():
            raise VectorSearchError("VECTOR_METADATA_MISSING", str(self._metadata_path))
        try:
            self._loader = CorpusCandidateLoader(corpus_artifact_root)
        except CorpusCandidateError as error:
            raise VectorSearchError(error.code, str(error)) from error
        self._shard_paths = self._load_shard_paths()

    @property
    def artifact_fingerprint(self) -> str:
        return self._compatibility.artifact_fingerprint

    @property
    def embedding_fingerprint(self) -> str:
        return self._compatibility.model_fingerprint

    def _load_manifest(self) -> Dict[str, Any]:
        try:
            manifest = json.loads((self._vector_root / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as error:
            raise VectorSearchError("VECTOR_MANIFEST_INVALID", "unable to read vector manifest") from error
        if not isinstance(manifest, dict):
            raise VectorSearchError("VECTOR_MANIFEST_INVALID", "manifest must be an object")
        return manifest

    def _load_shard_paths(self) -> Dict[str, Path]:
        entries = self._manifest.get("shards")
        if not isinstance(entries, list) or not entries:
            raise VectorSearchError("VECTOR_MANIFEST_INVALID", "shards are missing")
        paths: Dict[str, Path] = {}
        for expected_number, entry in enumerate(entries):
            if not isinstance(entry, Mapping):
                raise VectorSearchError("VECTOR_MANIFEST_INVALID", "shard entry is invalid")
            shard_id = entry.get("shard_id")
            if not isinstance(shard_id, str) or shard_id != f"shard-{expected_number:05d}":
                raise VectorSearchError("VECTOR_MANIFEST_INVALID", "shard IDs are not deterministic")
            if shard_id in paths:
                raise VectorSearchError("VECTOR_MANIFEST_INVALID", "duplicate shard ID")
            path = self._artifact / _safe_relative(entry.get("file"), "shard.file")
            if not path.is_file():
                raise VectorSearchError("FAISS_SHARD_CORRUPT_OR_MISSING", shard_id)
            paths[shard_id] = path
        return paths

    def _validate_embeddings(
        self, query: RetrievalQuery, embeddings: Sequence[QueryEmbedding]
    ) -> List[QueryEmbedding]:
        if not isinstance(query, RetrievalQuery):
            raise TypeError("query must be a RetrievalQuery")
        values = list(embeddings)
        if len(values) != len(query.query_texts):
            raise VectorSearchError(
                "QUERY_EMBEDDING_COUNT_MISMATCH", "every retrieval query text requires one embedding"
            )
        query_id = make_retrieval_query_id(query)
        for index, embedding in enumerate(values):
            if not isinstance(embedding, QueryEmbedding):
                raise TypeError("embeddings must contain QueryEmbedding values")
            if embedding.query_id != query_id:
                raise VectorSearchError("QUERY_ID_MISMATCH", "query embedding belongs to another query")
            if embedding.query_text_index != index or embedding.query_text != query.query_texts[index]:
                raise VectorSearchError(
                    "QUERY_EMBEDDING_ORDER_MISMATCH", "query embedding source order differs from RetrievalQuery"
                )
            if embedding.embedding_fingerprint != self.embedding_fingerprint:
                raise VectorSearchError(
                    "EMBEDDING_FINGERPRINT_MISMATCH", "query and document embeddings are incompatible"
                )
        return values

    def _eligible_locations(
        self, query: RetrievalQuery, eligible_source_types: Sequence[EvidenceSource]
    ) -> Dict[str, List[_VectorLocation]]:
        where, parameters = report_metadata_sql_constraints(query, table_alias="e")
        source_placeholders = ",".join("?" for _ in eligible_source_types)
        sql = f"""
            SELECT chunk_id, representation_id, source_type, source_shard,
                   source_record_index, source_order, faiss_shard_id,
                   faiss_shard_number, local_vector_id
            FROM embeddings AS e
            WHERE {where} AND e.source_type IN ({source_placeholders})
            ORDER BY faiss_shard_number ASC, local_vector_id ASC
        """
        try:
            connection = sqlite3.connect(f"file:{self._metadata_path}?mode=ro", uri=True)
            try:
                rows = connection.execute(
                    sql, [*parameters, *(source.value for source in eligible_source_types)]
                ).fetchall()
            finally:
                connection.close()
        except sqlite3.DatabaseError as error:
            raise VectorSearchError("VECTOR_METADATA_INVALID", str(error)) from error
        grouped: Dict[str, List[_VectorLocation]] = {}
        seen_ids = set()
        for row in rows:
            (
                chunk_id,
                representation_id,
                source_type,
                source_shard,
                source_record_index,
                source_order,
                faiss_shard_id,
                faiss_shard_number,
                local_vector_id,
            ) = row
            try:
                candidate_id = make_candidate_id(
                    EvidenceSource(source_type), representation_id, chunk_id
                )
            except (TypeError, ValueError, RetrievalContractError) as error:
                raise VectorSearchError("VECTOR_METADATA_PROVENANCE_INVALID", str(error)) from error
            if candidate_id in seen_ids:
                raise VectorSearchError("VECTOR_METADATA_PROVENANCE_INVALID", "duplicate candidate ID")
            if faiss_shard_id not in self._shard_paths or not isinstance(local_vector_id, int):
                raise VectorSearchError("VECTOR_METADATA_PROVENANCE_INVALID", "invalid FAISS location")
            seen_ids.add(candidate_id)
            location = _VectorLocation(
                candidate_id=candidate_id,
                source=CandidateLocation(
                    chunk_id=chunk_id,
                    representation_id=representation_id,
                    source_type=source_type,
                    source_shard=source_shard,
                    source_record_index=source_record_index,
                    source_order=source_order,
                ),
                faiss_shard_id=faiss_shard_id,
                faiss_shard_number=faiss_shard_number,
                local_vector_id=local_vector_id,
            )
            grouped.setdefault(faiss_shard_id, []).append(location)
        return grouped

    @staticmethod
    def _read_validated_shard(path: Path, shard_id: str) -> Any:
        faiss, _ = _load_faiss_numpy()
        try:
            index = faiss.read_index(str(path))
        except Exception as error:
            raise VectorSearchError("FAISS_SHARD_CORRUPT_OR_MISSING", shard_id) from error
        base = getattr(index, "index", None)
        if (
            type(index).__name__ != "IndexIDMap2"
            or getattr(index, "d", None) != EMBEDDING_DIMENSION
            or base is None
            or type(faiss.downcast_index(base)).__name__ != "IndexFlatIP"
            or getattr(index, "metric_type", None) != faiss.METRIC_INNER_PRODUCT
        ):
            raise VectorSearchError("FAISS_SHARD_INCOMPATIBLE", shard_id)
        return index

    @staticmethod
    def _search_eligible_in_shard(
        index: Any, local_vector_ids: Sequence[int], query_vector: Sequence[float], top_k: int
    ) -> Iterable[Tuple[float, int]]:
        faiss, np = _load_faiss_numpy()
        identifiers = np.asarray(list(local_vector_ids), dtype=np.int64)
        if identifiers.size == 0:
            return []
        try:
            vectors = index.reconstruct_batch(identifiers)
            temporary = faiss.IndexIDMap2(faiss.IndexFlatIP(EMBEDDING_DIMENSION))
            temporary.add_with_ids(np.asarray(vectors, dtype=np.float32), identifiers)
            distances, found_ids = temporary.search(
                np.asarray([query_vector], dtype=np.float32), min(top_k, len(identifiers))
            )
        except Exception as error:
            raise VectorSearchError("FAISS_FILTERED_SEARCH_FAILED", str(error)) from error
        return [
            (float(score), int(vector_id))
            for score, vector_id in zip(distances[0], found_ids[0])
            if int(vector_id) >= 0
        ]

    def search_with_diagnostics(
        self,
        query: RetrievalQuery,
        embeddings: Sequence[QueryEmbedding],
        *,
        top_k: int,
        eligible_source_types: Optional[Sequence[Any]] = None,
    ) -> RetrievalSearchResult:
        if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
            raise VectorSearchError("INVALID_TOP_K", "top_k must be a positive integer")
        try:
            eligible = resolve_eligible_source_types(query, eligible_source_types)
        except RetrievalContractError as error:
            raise VectorSearchError(error.code, str(error)) from error
        values = self._validate_embeddings(query, embeddings)
        locations_by_shard = self._eligible_locations(query, eligible)
        best: Dict[str, Tuple[_VectorLocation, float, int]] = {}
        for query_embedding in values:
            for shard_id in sorted(self._shard_paths):
                locations = locations_by_shard.get(shard_id, [])
                if not locations:
                    continue
                by_local_id = {location.local_vector_id: location for location in locations}
                if len(by_local_id) != len(locations):
                    raise VectorSearchError("VECTOR_METADATA_PROVENANCE_INVALID", "duplicate local vector ID")
                index = self._read_validated_shard(self._shard_paths[shard_id], shard_id)
                for score, local_vector_id in self._search_eligible_in_shard(
                    index, sorted(by_local_id), query_embedding.vector, top_k
                ):
                    location = by_local_id.get(local_vector_id)
                    if location is None:
                        raise VectorSearchError("FAISS_FILTERED_SEARCH_FAILED", "FAISS returned ineligible ID")
                    previous = best.get(location.candidate_id)
                    if previous is None or score > previous[1]:
                        best[location.candidate_id] = (
                            location,
                            score,
                            query_embedding.query_text_index,
                        )
        if not best:
            return RetrievalSearchResult(candidates=[], observations=[])
        try:
            source_candidates = self._loader.load_locations([item[0].source for item in best.values()])
        except CorpusCandidateError as error:
            raise VectorSearchError(error.code, str(error)) from error
        candidates: List[RetrievalCandidate] = []
        for candidate_id, (location, score, _) in best.items():
            source = source_candidates.get(candidate_id)
            if source is None:
                raise VectorSearchError("CORPUS_VECTOR_PROVENANCE_MISMATCH", candidate_id)
            candidates.append(replace(source, vector_score=score))
        ranked = rank_candidates(candidates, score_field="vector_score")[:top_k]
        query_id = make_retrieval_query_id(query)
        observations = [
            RetrievalSearchObservation(
                query_id=query_id,
                query_text_index=best[candidate.candidate_id][2],
                stage="vector",
                candidate_id=candidate.candidate_id,
                rank=candidate.rank or 1,
                score=candidate.vector_score or 0.0,
                artifact_fingerprint=self.artifact_fingerprint,
                embedding_fingerprint=self.embedding_fingerprint,
            )
            for candidate in ranked
        ]
        return RetrievalSearchResult(candidates=ranked, observations=observations)

    def search(
        self,
        query: RetrievalQuery,
        embeddings: Sequence[QueryEmbedding],
        *,
        top_k: int,
        eligible_source_types: Optional[Sequence[Any]] = None,
    ) -> List[RetrievalCandidate]:
        return self.search_with_diagnostics(
            query,
            embeddings,
            top_k=top_k,
            eligible_source_types=eligible_source_types,
        ).candidates


def search_vectors(
    vector_artifact_root: str | Path,
    corpus_artifact_root: str | Path,
    query: RetrievalQuery,
    embeddings: Sequence[QueryEmbedding],
    *,
    top_k: int,
    eligible_source_types: Optional[Sequence[Any]] = None,
) -> List[RetrievalCandidate]:
    """Open a fully validated vector artifact and execute exact filtered search."""

    return VectorSearcher(vector_artifact_root, corpus_artifact_root).search(
        query, embeddings, top_k=top_k, eligible_source_types=eligible_source_types
    )
