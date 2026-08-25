"""Read immutable M2 corpus records as provenance-preserving M3 candidates."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence

from src.indexing.embedding_artifact_builder import (
    EmbeddingArtifactBuilderError,
    _InputArtifact,
    _iter_input_records,
    _validate_input_manifest,
)
from src.indexing.embedding_schemas import EmbeddingChunk
from src.indexing.schemas import RetrievalRepresentation
from src.retrieval.schemas import RetrievalCandidate, RetrievalContractError, make_candidate_id
from src.understanding.schemas import SchemaValidationError


class CorpusCandidateError(RetrievalContractError):
    """A typed failure while loading source candidates from M2."""


@dataclass(frozen=True)
class CandidateLocation:
    """The immutable M2 source position persisted in the M2B SQLite mapping."""

    chunk_id: str
    representation_id: str
    source_type: str
    source_shard: str
    source_record_index: int
    source_order: int


def candidate_from_representation_chunk(
    representation: RetrievalRepresentation, chunk: EmbeddingChunk
) -> RetrievalCandidate:
    """Create the one chunk-level M3 candidate without changing provenance."""

    if chunk.representation_id != representation.representation_id:
        raise CorpusCandidateError(
            "CANDIDATE_PROVENANCE_MISMATCH", "chunk representation_id differs from representation"
        )
    if chunk.source_type is not representation.source_type:
        raise CorpusCandidateError(
            "CANDIDATE_PROVENANCE_MISMATCH", "chunk source_type differs from representation"
        )
    return RetrievalCandidate(
        candidate_id=make_candidate_id(
            representation.source_type, representation.representation_id, chunk.chunk_id
        ),
        representation_id=representation.representation_id,
        chunk_id=chunk.chunk_id,
        source_type=representation.source_type,
        report_id=representation.report_id,
        page_ids=list(representation.page_ids),
        table_id=representation.table_id,
        paragraph_id=representation.paragraph_id,
        ticker=representation.ticker,
        company_name=representation.company_name,
        report_year=representation.report_year,
        statement_scope=representation.statement_scope,
        period_labels=list(representation.period_labels),
        row_paths=[list(path) for path in representation.row_paths],
        column_paths=[list(path) for path in representation.column_paths],
        content=chunk.content,
        bm25_score=None,
        vector_score=None,
        rrf_score=None,
        rerank_score=None,
        rank=None,
    )


def iter_corpus_candidates(corpus_artifact_root: str | Path) -> Iterable[RetrievalCandidate]:
    """Yield every committed M2 chunk as a separate candidate in source order."""

    try:
        artifact = _validate_input_manifest(corpus_artifact_root, verify_hashes=True)
        for record in _iter_input_records(artifact):
            yield candidate_from_representation_chunk(record.representation, record.chunk)
    except CorpusCandidateError:
        raise
    except EmbeddingArtifactBuilderError as error:
        raise CorpusCandidateError("CORPUS_ARTIFACT_INVALID", str(error)) from error


class CorpusCandidateLoader:
    """Loads only selected M2 records after the full immutable manifest audit."""

    def __init__(self, corpus_artifact_root: str | Path) -> None:
        try:
            self._artifact: _InputArtifact = _validate_input_manifest(
                corpus_artifact_root, verify_hashes=True
            )
        except EmbeddingArtifactBuilderError as error:
            raise CorpusCandidateError("CORPUS_ARTIFACT_INVALID", str(error)) from error
        self._shard_entries = {
            str(entry["file"]): entry for entry in self._artifact.shard_entries
        }

    @property
    def artifact_id(self) -> str:
        return self._artifact.artifact_id

    @property
    def corpus_fingerprint(self) -> str:
        return self._artifact.corpus_fingerprint

    def load_locations(self, locations: Sequence[CandidateLocation]) -> Dict[str, RetrievalCandidate]:
        """Resolve selected locations by exact source-shard and line position."""

        grouped: Dict[str, List[CandidateLocation]] = {}
        for location in locations:
            if not isinstance(location, CandidateLocation):
                raise TypeError("locations must contain CandidateLocation values")
            grouped.setdefault(location.source_shard, []).append(location)
        candidates: Dict[str, RetrievalCandidate] = {}
        for source_shard, group in grouped.items():
            entry = self._shard_entries.get(source_shard)
            if entry is None:
                raise CorpusCandidateError("CORPUS_SOURCE_SHARD_MISSING", source_shard)
            path = self._artifact.root / source_shard
            try:
                raw = path.read_bytes()
            except OSError as error:
                raise CorpusCandidateError("CORPUS_SOURCE_SHARD_MISSING", source_shard) from error
            if sha256(raw).hexdigest() != entry["sha256"]:
                raise CorpusCandidateError("CORPUS_SOURCE_SHARD_CORRUPT", source_shard)
            lines = raw.splitlines(keepends=True)
            for location in group:
                if location.source_record_index < 0 or location.source_record_index >= len(lines):
                    raise CorpusCandidateError("CORPUS_SOURCE_LOCATION_INVALID", source_shard)
                line = lines[location.source_record_index]
                if not line.endswith(b"\n"):
                    raise CorpusCandidateError("CORPUS_SOURCE_LOCATION_INVALID", source_shard)
                try:
                    payload = json.loads(line.decode("utf-8"))
                    representation = RetrievalRepresentation.from_dict(payload["representation"])
                    chunk = EmbeddingChunk.from_dict(payload["chunk"])
                except (KeyError, UnicodeDecodeError, ValueError, SchemaValidationError) as error:
                    raise CorpusCandidateError("CORPUS_SOURCE_RECORD_INVALID", source_shard) from error
                if payload.get("source_order") != location.source_order:
                    raise CorpusCandidateError("CORPUS_SOURCE_LOCATION_INVALID", source_shard)
                candidate = candidate_from_representation_chunk(representation, chunk)
                if (
                    candidate.chunk_id != location.chunk_id
                    or candidate.representation_id != location.representation_id
                    or candidate.source_type.value != location.source_type
                ):
                    raise CorpusCandidateError(
                        "CORPUS_VECTOR_PROVENANCE_MISMATCH", candidate.candidate_id
                    )
                if candidate.candidate_id in candidates:
                    raise CorpusCandidateError("DUPLICATE_CANDIDATE_LOCATION", candidate.candidate_id)
                candidates[candidate.candidate_id] = candidate
        return candidates
