"""TASK-103 parallel independent multi-table / multi-source retrieval.

Independent TABLE subqueries (and the independent TABLE vs. TEXT source runs) may
execute concurrently, but the result must be *byte-identical* to the existing
sequential path. This module therefore reuses the exact Batch 3 decomposition,
per-subquery request construction, backend -> RRF -> reranker sequence, and
partial-state merge from :mod:`src.retrieval.batch3`. It only changes *when* the
independent units run, never *what* they compute or *how* they are ordered:
results are always reassembled in the deterministic subquery order, so ranking
semantics, filters, provenance, and merge behavior are preserved.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, Tuple

from src.retrieval.batch3 import (
    Batch3Retriever,
    make_multi_table_subqueries,
    query_for_subquery,
)
from src.retrieval.schemas import (
    MultiTableRetrievalResult,
    NarrativeRetrievalResult,
    RetrievalCandidate,
    RetrievalContractError,
    RetrievalQuery,
    SubqueryRetrievalResult,
)
from src.supervisor.schemas import EvidenceSource


def _resolve_workers(requested: Optional[int], count: int) -> int:
    if count <= 0:
        return 1
    if requested is None:
        return count
    if isinstance(requested, bool) or not isinstance(requested, int) or requested < 1:
        raise ValueError("max_workers must be a positive integer or None")
    return min(requested, count)


def retrieve_multi_table_parallel(
    retriever: Batch3Retriever,
    query: RetrievalQuery,
    *,
    top_k: int,
    max_workers: Optional[int] = None,
) -> MultiTableRetrievalResult:
    """Concurrent equivalent of ``Batch3Retriever.retrieve_multi_table``.

    Produces the same ``MultiTableRetrievalResult`` as the sequential method for
    any deterministic backend: subqueries are decomposed identically, executed
    concurrently, and merged strictly in subquery order.
    """

    subqueries = make_multi_table_subqueries(query)

    def _run(subquery) -> Tuple[Optional[List[RetrievalCandidate]], Optional[str]]:
        try:
            candidates = retriever.retrieve(
                query_for_subquery(query, subquery),
                top_k=top_k,
                eligible_source_types=[EvidenceSource.TABLE],
            )
            return candidates, None
        except RetrievalContractError as error:
            return None, error.code

    workers = _resolve_workers(max_workers, len(subqueries))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        # ThreadPoolExecutor.map preserves input order regardless of completion order.
        outputs = list(pool.map(_run, subqueries))

    subquery_results: List[SubqueryRetrievalResult] = []
    missing: List[str] = []
    for subquery, (candidates, failure_code) in zip(subqueries, outputs):
        if failure_code is not None:
            subquery_results.append(
                SubqueryRetrievalResult(
                    subquery_id=subquery.subquery_id, candidates=[], failure_code=failure_code
                )
            )
            missing.append(subquery.subquery_id)
            continue
        subquery_results.append(
            SubqueryRetrievalResult(
                subquery_id=subquery.subquery_id, candidates=candidates or [], failure_code=None
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


def retrieve_sources_parallel(
    retriever: Batch3Retriever,
    query: RetrievalQuery,
    *,
    top_k: int,
    parallel_subqueries: bool = True,
    max_workers: Optional[int] = None,
) -> Tuple[MultiTableRetrievalResult, NarrativeRetrievalResult]:
    """Run the independent TABLE and TEXT source retrievals concurrently.

    Each source uses the existing deterministic method unchanged, so the pair is
    identical to running them sequentially. When ``parallel_subqueries`` is set,
    the TABLE side additionally uses :func:`retrieve_multi_table_parallel`.
    """

    def _table() -> MultiTableRetrievalResult:
        if parallel_subqueries:
            return retrieve_multi_table_parallel(
                retriever, query, top_k=top_k, max_workers=max_workers
            )
        return retriever.retrieve_multi_table(query, top_k=top_k)

    def _text() -> NarrativeRetrievalResult:
        return retriever.retrieve_narrative(query, top_k=top_k)

    with ThreadPoolExecutor(max_workers=2) as pool:
        table_future = pool.submit(_table)
        text_future = pool.submit(_text)
        return table_future.result(), text_future.result()
