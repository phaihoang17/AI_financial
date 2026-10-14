"""Read immutable M2 corpus records as provenance-preserving M3 candidates."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

from src.indexing.embedding_artifact_builder import (
    EmbeddingArtifactBuilderError,
    _InputArtifact,
    _InputRecord,
    _iter_input_records,
    _validate_input_manifest,
    parse_input_record,
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


class CorpusRecordLocator:
    """Read selected M2 records by their persisted position instead of a corpus pass.

    The committed M2B vector metadata maps every chunk to its corpus shard and
    line. Each record is re-validated exactly as a full pass validates it
    (canonical JSON, schema, provenance, source order), and its chunk and
    representation IDs must equal the requested ones.
    """

    def __init__(self, corpus: _InputArtifact, vector_metadata_path: str | Path) -> None:
        self._corpus = corpus
        self._metadata_path = Path(vector_metadata_path)
        self._shards = {str(entry["file"]): entry for entry in corpus.shard_entries}
        self._line_offsets: Dict[str, List[int]] = {}

    def _offsets(self, relative: str) -> List[int]:
        if relative not in self._line_offsets:
            data = (self._corpus.root / relative).read_bytes()
            offsets, position = [0], data.find(b"\n")
            while position != -1:
                offsets.append(position + 1)
                position = data.find(b"\n", position + 1)
            self._line_offsets[relative] = offsets
        return self._line_offsets[relative]

    def records(self, keys: Iterable[Tuple[str, str]]) -> Dict[Tuple[str, str], _InputRecord]:
        """Return the records for (chunk_id, representation_id) keys; unknown keys are absent."""
        wanted = {chunk_id: representation_id for chunk_id, representation_id in keys}
        if not wanted:
            return {}
        connection = sqlite3.connect(f"file:{self._metadata_path}?mode=ro", uri=True)
        try:
            rows = []
            chunk_ids = list(wanted)
            for start in range(0, len(chunk_ids), 500):
                batch = chunk_ids[start:start + 500]
                rows += connection.execute(
                    "SELECT chunk_id, representation_id, source_shard, source_record_index, source_order"
                    f" FROM embeddings WHERE chunk_id IN ({','.join('?' * len(batch))})", batch,
                ).fetchall()
        finally:
            connection.close()
        found: Dict[Tuple[str, str], _InputRecord] = {}
        for chunk_id, representation_id, relative, index, order in rows:
            if wanted.get(chunk_id) != representation_id:
                continue
            entry = self._shards.get(relative)
            if entry is None:
                raise CorpusCandidateError("CORPUS_SOURCE_SHARD_MISSING", relative)
            offsets = self._offsets(relative)
            if not 0 <= index < len(offsets) - 1:
                raise CorpusCandidateError("CORPUS_SOURCE_LOCATION_INVALID", relative)
            with (self._corpus.root / relative).open("rb") as handle:
                handle.seek(offsets[index])
                line = handle.read(offsets[index + 1] - offsets[index])
            try:
                record = parse_input_record(self._corpus, relative, str(entry["sha256"]), index, line, order)
            except EmbeddingArtifactBuilderError as error:
                raise CorpusCandidateError("CORPUS_SOURCE_RECORD_INVALID", str(error)) from error
            if (record.chunk.chunk_id, record.representation.representation_id) != (chunk_id, representation_id):
                raise CorpusCandidateError("CORPUS_VECTOR_PROVENANCE_MISMATCH", chunk_id)
            found[(chunk_id, representation_id)] = record
        return found
