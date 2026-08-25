"""TASK-034 deterministic reciprocal-rank fusion."""

from __future__ import annotations

from dataclasses import replace
from typing import Dict, List, Sequence

from src.retrieval.schemas import RetrievalCandidate, RetrievalContractError, rank_candidates


RRF_K = 60


class RRFFusionError(RetrievalContractError):
    """A typed invalid-input or provenance error from reciprocal-rank fusion."""


def _validate_ranked_backend(
    candidates: Sequence[RetrievalCandidate], *, score_field: str, backend: str
) -> None:
    if not isinstance(candidates, (list, tuple)):
        raise RRFFusionError("RRF_INPUT_INVALID", f"{backend} candidates must be a list")
    expected_ranks = list(range(1, len(candidates) + 1))
    observed_ranks: List[int] = []
    candidate_ids = set()
    for candidate in candidates:
        if not isinstance(candidate, RetrievalCandidate):
            raise RRFFusionError("RRF_INPUT_INVALID", f"{backend} values must be RetrievalCandidate")
        if candidate.candidate_id in candidate_ids:
            raise RRFFusionError("RRF_DUPLICATE_CANDIDATE", f"{backend}: {candidate.candidate_id}")
        candidate_ids.add(candidate.candidate_id)
        if getattr(candidate, score_field) is None:
            raise RRFFusionError("RRF_STAGE_SCORE_MISSING", f"{backend}: {score_field}")
        if candidate.rank is None:
            raise RRFFusionError("RRF_RANK_INVALID", f"{backend}: rank is required")
        observed_ranks.append(candidate.rank)
    if observed_ranks != expected_ranks:
        raise RRFFusionError(
            "RRF_RANK_INVALID", f"{backend} ranks must be one-based and contiguous"
        )


def _provenance_key(candidate: RetrievalCandidate) -> dict:
    """Return every invariant value; backend scores and rank are stage outputs."""

    value = candidate.to_dict()
    for field in ("bm25_score", "vector_score", "rrf_score", "rerank_score", "rank"):
        value.pop(field)
    return value


def fuse_rrf(
    bm25_candidates: Sequence[RetrievalCandidate],
    vector_candidates: Sequence[RetrievalCandidate],
    *,
    top_k: int,
) -> List[RetrievalCandidate]:
    """Fuse two complete ranked backend lists using unweighted RRF with k=60."""

    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
        raise RRFFusionError("INVALID_TOP_K", "top_k must be a positive integer")
    _validate_ranked_backend(bm25_candidates, score_field="bm25_score", backend="bm25")
    _validate_ranked_backend(vector_candidates, score_field="vector_score", backend="vector")

    merged: Dict[str, RetrievalCandidate] = {}
    scores: Dict[str, float] = {}
    for candidates, score_field in (
        (bm25_candidates, "bm25_score"),
        (vector_candidates, "vector_score"),
    ):
        for candidate in candidates:
            existing = merged.get(candidate.candidate_id)
            if existing is None:
                merged[candidate.candidate_id] = candidate
            else:
                if _provenance_key(existing) != _provenance_key(candidate):
                    raise RRFFusionError(
                        "RRF_PROVENANCE_MISMATCH", candidate.candidate_id
                    )
                merged[candidate.candidate_id] = replace(
                    existing,
                    bm25_score=(
                        candidate.bm25_score
                        if candidate.bm25_score is not None
                        else existing.bm25_score
                    ),
                    vector_score=(
                        candidate.vector_score
                        if candidate.vector_score is not None
                        else existing.vector_score
                    ),
                )
            scores[candidate.candidate_id] = scores.get(candidate.candidate_id, 0.0) + (
                1.0 / (RRF_K + int(candidate.rank))
            )

    fused = [
        replace(candidate, rrf_score=scores[candidate_id])
        for candidate_id, candidate in merged.items()
    ]
    return rank_candidates(fused, score_field="rrf_score")[:top_k]
