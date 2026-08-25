"""Batch 3 deterministic orchestration before Evidence Builder exists."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence

from src.indexing.embedding_artifact_builder import (
    EmbeddingArtifactBuilderError,
    _iter_input_records,
    _validate_input_manifest,
)
from src.indexing.embedding_schemas import EmbeddingChunk
from src.indexing.provenance_sidecar import ProvenanceSidecar, ProvenanceSidecarError
from src.indexing.schemas import ScaleHintSource, ScaleUnitHint
from src.retrieval.rrf import fuse_rrf
from src.retrieval.reranker import BGEReranker, rerank_candidates
from src.retrieval.schemas import (
    HintAssociation,
    MultiTableRetrievalResult,
    NarrativeRetrievalResult,
    RetrievedScaleUnitHint,
    RetrievalCandidate,
    RetrievalContractError,
    RetrievalQuery,
    RetrievalSubquery,
    SubqueryRetrievalResult,
    make_candidate_id,
)
from src.supervisor.schemas import EvidenceSource
from src.understanding.schemas import StatementScope


class Batch3RetrievalError(RetrievalContractError):
    """A typed Batch 3 orchestration, decomposition, or enrichment failure."""


class _BM25Backend(Protocol):
    def search(
        self, query: RetrievalQuery, *, top_k: int, eligible_source_types: Sequence[EvidenceSource]
    ) -> List[RetrievalCandidate]: ...


class _VectorBackend(Protocol):
    def search(
        self,
        query: RetrievalQuery,
        embeddings: Sequence[object],
        *,
        top_k: int,
        eligible_source_types: Sequence[EvidenceSource],
    ) -> List[RetrievalCandidate]: ...


QueryEmbedder = Callable[[RetrievalQuery], Sequence[object]]


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _deduplicate_exact(values: Iterable[str]) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _subquery_id(
    *, parent_query_id: str, metric: Optional[str], period: Optional[str], scope: StatementScope
) -> str:
    return sha256(
        _canonical_json(
            {
                "parent_query_id": parent_query_id,
                "metric": metric,
                "period": period,
                "statement_scope": scope.value,
                "source_type": EvidenceSource.TABLE.value,
            }
        )
    ).hexdigest()


def make_multi_table_subqueries(query: RetrievalQuery) -> List[RetrievalSubquery]:
    """Expand supplied metrics and periods without inferring any missing value."""

    if not isinstance(query, RetrievalQuery):
        raise TypeError("query must be a RetrievalQuery")
    if query.statement_scope is None:
        raise Batch3RetrievalError(
            "MULTI_TABLE_SCOPE_REQUIRED", "TABLE subqueries require an explicit statement scope"
        )
    from src.retrieval.query_embedding import make_retrieval_query_id

    metrics = list(query.target_metrics)
    periods = list(query.periods)
    if metrics and periods:
        pairs = [(metric, period) for metric in metrics for period in periods]
    elif metrics:
        pairs = [(metric, None) for metric in metrics]
    elif periods:
        pairs = [(None, period) for period in periods]
    else:
        raise Batch3RetrievalError(
            "MULTI_TABLE_DECOMPOSITION_EMPTY", "target_metrics or periods must be supplied"
        )
    parent_query_id = make_retrieval_query_id(query)
    result: List[RetrievalSubquery] = []
    for metric, period in pairs:
        query_texts = _deduplicate_exact(
            value
            for value in (
                query.raw_question,
                metric,
                period,
                f"{query.company.ticker} {query.company.name}",
            )
            if value is not None
        )
        result.append(
            RetrievalSubquery(
                subquery_id=_subquery_id(
                    parent_query_id=parent_query_id,
                    metric=metric,
                    period=period,
                    scope=query.statement_scope,
                ),
                parent_query_id=parent_query_id,
                metric=metric,
                period=period,
                statement_scope=query.statement_scope,
                source_type=EvidenceSource.TABLE,
                query_texts=query_texts,
                required=True,
            )
        )
    if len({subquery.subquery_id for subquery in result}) != len(result):
        raise Batch3RetrievalError(
            "MULTI_TABLE_DUPLICATE_SUBQUERY", "duplicate input dimensions are unsupported"
        )
    return result


def query_for_subquery(parent: RetrievalQuery, subquery: RetrievalSubquery) -> RetrievalQuery:
    """Create the canonical TABLE-only request used by one subquery execution."""

    if subquery.statement_scope is not parent.statement_scope:
        raise Batch3RetrievalError("SUBQUERY_PARENT_MISMATCH", "statement scope differs")
    return replace(
        parent,
        periods=[] if subquery.period is None else [subquery.period],
        target_metrics=[] if subquery.metric is None else [subquery.metric],
        evidence_sources=[EvidenceSource.TABLE],
        eligible_source_types=[EvidenceSource.TABLE],
        query_texts=list(subquery.query_texts),
    )


class CorpusChunkLookup:
    """Resolve only returned TABLE chunks from immutable M2 corpus records."""

    def __init__(self, corpus_artifact_root: str) -> None:
        try:
            self._corpus = _validate_input_manifest(corpus_artifact_root, verify_hashes=True)
        except EmbeddingArtifactBuilderError as error:
            raise Batch3RetrievalError("SCALE_HINT_CORPUS_INVALID", str(error)) from error

    def for_candidates(
        self, candidates: Sequence[RetrievalCandidate]
    ) -> Dict[str, EmbeddingChunk]:
        wanted = {
            candidate.candidate_id
            for candidate in candidates
            if candidate.source_type is EvidenceSource.TABLE
        }
        found: Dict[str, EmbeddingChunk] = {}
        if not wanted:
            return found
        try:
            for record in _iter_input_records(self._corpus):
                candidate_id = make_candidate_id(
                    record.representation.source_type,
                    record.representation.representation_id,
                    record.chunk.chunk_id,
                )
                if candidate_id in wanted:
                    found[candidate_id] = record.chunk
        except EmbeddingArtifactBuilderError as error:
            raise Batch3RetrievalError("SCALE_HINT_CORPUS_INVALID", str(error)) from error
        missing = wanted - set(found)
        if missing:
            raise Batch3RetrievalError(
                "SCALE_HINT_CHUNK_MISSING", ",".join(sorted(missing))
            )
        return found


class ScaleUnitHintRetriever:
    """Return persisted direct/link-associated hints without choosing a winner."""

    def __init__(self, sidecar: ProvenanceSidecar, chunk_lookup: CorpusChunkLookup) -> None:
        required = (
            "get_hints_by_source_ref",
            "get_hints_by_table_id",
            "get_links_by_table_id",
            "get_links_by_paragraph_id",
        )
        if any(not callable(getattr(sidecar, name, None)) for name in required):
            raise TypeError("sidecar must implement the persisted provenance lookup contract")
        self._sidecar = sidecar
        self._chunk_lookup = chunk_lookup

    @staticmethod
    def _materialize(
        candidate: RetrievalCandidate, hint: ScaleUnitHint, association: HintAssociation
    ) -> RetrievedScaleUnitHint:
        return RetrievedScaleUnitHint(
            hint_id=hint.hint_id,
            candidate_id=candidate.candidate_id,
            source_kind=hint.source_kind,
            source_ref=hint.source_ref,
            source_span=hint.source_span,
            raw_hint_text=hint.raw_hint_text,
            scale_candidate=hint.scale_candidate,
            unit_candidate=hint.unit_candidate,
            status=hint.status,
            association=association,
        )

    @staticmethod
    def _order(values: Sequence[RetrievedScaleUnitHint]) -> List[RetrievedScaleUnitHint]:
        return sorted(
            values,
            key=lambda value: (
                value.source_span.start,
                value.source_span.end,
                value.hint_id,
                value.association.value,
            ),
        )

    def retrieve(
        self, candidates: Sequence[RetrievalCandidate]
    ) -> Dict[str, List[RetrievedScaleUnitHint]]:
        if not isinstance(candidates, (list, tuple)) or not all(
            isinstance(candidate, RetrievalCandidate) for candidate in candidates
        ):
            raise Batch3RetrievalError("SCALE_HINT_INPUT_INVALID", "candidates are invalid")
        chunks = self._chunk_lookup.for_candidates(candidates)
        result: Dict[str, List[RetrievedScaleUnitHint]] = {}
        try:
            for candidate in candidates:
                values: List[RetrievedScaleUnitHint] = []
                if candidate.source_type is EvidenceSource.TABLE:
                    chunk = chunks[candidate.candidate_id]
                    source_refs = _deduplicate_exact(
                        [*chunk.primary_source_cell_ids, *chunk.context_source_cell_ids]
                    )
                    for source_ref in source_refs:
                        values.extend(
                            self._materialize(candidate, hint, HintAssociation.DIRECT)
                            for hint in self._sidecar.get_hints_by_source_ref(source_ref)
                            if hint.source_kind in {ScaleHintSource.HEADER, ScaleHintSource.CELL}
                        )
                    values.extend(
                        self._materialize(candidate, hint, HintAssociation.DIRECT)
                        for hint in self._sidecar.get_hints_by_source_ref(candidate.table_id or "")
                        if hint.source_kind is ScaleHintSource.CAPTION
                    )
                    for link in self._sidecar.get_links_by_table_id(candidate.table_id or ""):
                        values.extend(
                            self._materialize(candidate, hint, HintAssociation.LINKED)
                            for hint in self._sidecar.get_hints_by_source_ref(link.paragraph_id)
                            if hint.source_kind is ScaleHintSource.TEXT
                        )
                else:
                    values.extend(
                        self._materialize(candidate, hint, HintAssociation.DIRECT)
                        for hint in self._sidecar.get_hints_by_source_ref(candidate.paragraph_id or "")
                        if hint.source_kind is ScaleHintSource.TEXT
                    )
                    for link in self._sidecar.get_links_by_paragraph_id(candidate.paragraph_id or ""):
                        values.extend(
                            self._materialize(candidate, hint, HintAssociation.LINKED)
                            for hint in self._sidecar.get_hints_by_table_id(link.table_id)
                            if hint.source_kind is not ScaleHintSource.TEXT
                        )
                unique = {(value.hint_id, value.association.value): value for value in values}
                result[candidate.candidate_id] = self._order(list(unique.values()))
        except ProvenanceSidecarError as error:
            raise Batch3RetrievalError(error.code, str(error)) from error
        return result


class Batch3Retriever:
    """Composable Batch 3 pipeline; it returns candidates, never EvidenceItem."""

    def __init__(
        self,
        bm25: _BM25Backend,
        vector: _VectorBackend,
        query_embedder: QueryEmbedder,
        *,
        reranker: Optional[BGEReranker] = None,
        sidecar: Optional[ProvenanceSidecar] = None,
        chunk_lookup: Optional[CorpusChunkLookup] = None,
    ) -> None:
        self._bm25 = bm25
        self._vector = vector
        self._query_embedder = query_embedder
        self._reranker = reranker
        self._sidecar = sidecar
        self._chunk_lookup = chunk_lookup

    def retrieve(
        self,
        query: RetrievalQuery,
        *,
        top_k: int,
        eligible_source_types: Sequence[EvidenceSource],
    ) -> List[RetrievalCandidate]:
        """Run the exact Backend -> RRF -> reranker sequence for one request."""

        bm25_candidates = self._bm25.search(
            query, top_k=top_k, eligible_source_types=eligible_source_types
        )
        embeddings = self._query_embedder(query)
        vector_candidates = self._vector.search(
            query,
            embeddings,
            top_k=top_k,
            eligible_source_types=eligible_source_types,
        )
        fused = fuse_rrf(bm25_candidates, vector_candidates, top_k=top_k)
        return rerank_candidates(
            query, fused, reranker=self._reranker, top_k=top_k
        )

    def retrieve_multi_table(
        self, query: RetrievalQuery, *, top_k: int
    ) -> MultiTableRetrievalResult:
        """Execute every required TABLE subquery independently and expose partial state."""

        subqueries = make_multi_table_subqueries(query)
        subquery_results: List[SubqueryRetrievalResult] = []
        missing: List[str] = []
        for subquery in subqueries:
            try:
                candidates = self.retrieve(
                    query_for_subquery(query, subquery),
                    top_k=top_k,
                    eligible_source_types=[EvidenceSource.TABLE],
                )
            except RetrievalContractError as error:
                subquery_results.append(
                    SubqueryRetrievalResult(
                        subquery_id=subquery.subquery_id, candidates=[], failure_code=error.code
                    )
                )
                missing.append(subquery.subquery_id)
                continue
            subquery_results.append(
                SubqueryRetrievalResult(
                    subquery_id=subquery.subquery_id, candidates=candidates, failure_code=None
                )
            )
            if not candidates:
                missing.append(subquery.subquery_id)
        return MultiTableRetrievalResult(
            parent_query_id=subqueries[0].parent_query_id,
            subquery_results=subquery_results,
            complete=not missing,
            missing_subquery_ids=missing,
        )

    def retrieve_narrative(
        self, query: RetrievalQuery, *, top_k: int
    ) -> NarrativeRetrievalResult:
        """Run TEXT-only retrieval and expose only persisted paragraph-table associations."""

        if self._sidecar is None:
            raise Batch3RetrievalError("NARRATIVE_SIDECAR_REQUIRED", "sidecar is required")
        candidates = self.retrieve(
            replace(
                query,
                evidence_sources=[EvidenceSource.TEXT],
                eligible_source_types=[EvidenceSource.TEXT],
            ),
            top_k=top_k,
            eligible_source_types=[EvidenceSource.TEXT],
        )
        links: Dict[str, List[str]] = {}
        try:
            for candidate in candidates:
                if candidate.source_type is not EvidenceSource.TEXT:
                    raise Batch3RetrievalError(
                        "NARRATIVE_SOURCE_TYPE_INVALID", candidate.candidate_id
                    )
                links[candidate.candidate_id] = _deduplicate_exact(
                    link.table_id
                    for link in self._sidecar.get_links_by_paragraph_id(candidate.paragraph_id or "")
                )
        except ProvenanceSidecarError as error:
            raise Batch3RetrievalError(error.code, str(error)) from error
        return NarrativeRetrievalResult(
            candidates=candidates, linked_table_ids_by_candidate=links
        )

    def retrieve_scale_unit_hints(
        self, candidates: Sequence[RetrievalCandidate]
    ) -> Dict[str, List[RetrievedScaleUnitHint]]:
        """Enrich candidates from persisted sidecar hints without resolving scale/unit."""

        if self._sidecar is None or self._chunk_lookup is None:
            raise Batch3RetrievalError(
                "SCALE_HINT_SIDECAR_REQUIRED", "sidecar and immutable M2 chunk lookup are required"
            )
        return ScaleUnitHintRetriever(self._sidecar, self._chunk_lookup).retrieve(candidates)
