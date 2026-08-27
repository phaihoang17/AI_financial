"""Live serving probes for the pinned retrieval models (TASK-109 / ADR-057).

These probes call a *real* configured serving endpoint through an
:class:`~src.serving.client.OpenAICompatibleClient` (whose production transport
is :class:`~src.serving.client.HttpTransport`) and validate the live response
against the pinned local implementation.

They are deliberately NOT the injected-transport unit tests in
``tests/serving``: those prove marshalling logic, not that a model is served.
A probe result only counts as live-serving evidence when the client was bound
to a real endpoint. The parity comparison is against caller-supplied reference
output from the pinned local implementation (``load_bge_m3_encoder`` /
``load_bge_reranker`` on CPU); this module never loads a model itself, so it
imports without torch.

Tolerance rationale: production GPU inference runs bf16/fp16 while the pinned
local reference runs fp32 on CPU, so exact float equality is not achievable.
For BGE-M3 the index uses cosine / inner product on L2-normalized vectors, so
the meaningful bar is directional agreement: cosine similarity >= 0.999 between
the normalized served vector and the normalized local vector (about a 2.5-degree
cone). For the cross-encoder the load-bearing invariant is the induced ranking
(``rerank_candidates`` only consumes score order), so order parity is the gate
and raw-score closeness is advisory only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Dict, List, Optional, Sequence

from src.indexing.embedding_schemas import EMBEDDING_DIMENSION
from src.serving.client import OpenAICompatibleClient
from src.serving.schemas import ServingError, ServingTask


LIVE_PROBE_SCHEMA_VERSION = "m10-live-serving-probe-v1"

# See module docstring for the justification of these values.
DEFAULT_EMBED_MIN_COSINE = 0.999
DEFAULT_RERANK_SCORE_ABS_TOL = 0.05


class LiveProbeError(Exception):
    """A typed live-serving probe failure (configuration or contract)."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def _finite_floats(values: Any, path: str) -> List[float]:
    if not isinstance(values, (list, tuple)) or not values:
        raise LiveProbeError("PROBE_RESPONSE_INVALID", f"{path} must be a non-empty sequence")
    out: List[float] = []
    for index, value in enumerate(values):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise LiveProbeError("PROBE_RESPONSE_INVALID", f"{path}[{index}] is not numeric")
        number = float(value)
        if not math.isfinite(number):
            raise LiveProbeError("PROBE_NON_FINITE_OUTPUT", f"{path}[{index}] is not finite")
        out.append(number)
    return out


def _l2_normalize(vector: Sequence[float], path: str) -> List[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0 or not math.isfinite(norm):
        raise LiveProbeError("PROBE_ZERO_VECTOR", f"{path} has zero or non-finite L2 norm")
    return [value / norm for value in vector]


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def _descending_order(scores: Sequence[float]) -> List[int]:
    """Deterministic score-descending order with a stable index tie-break."""
    return sorted(range(len(scores)), key=lambda index: (-scores[index], index))


@dataclass(frozen=True)
class EmbeddingProbeResult:
    status: str
    code: Optional[str]
    endpoint_name: str
    sample_size: int
    dimension: Optional[int]
    min_cosine_similarity: Optional[float]
    raw_vectors_were_unit_norm: Optional[bool]
    min_cosine_threshold: float
    detail: Dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status == "PASS"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": LIVE_PROBE_SCHEMA_VERSION,
            "probe": "BGE_M3_EMBEDDING",
            "evidence": "LIVE",
            "status": self.status,
            "code": self.code,
            "endpoint_name": self.endpoint_name,
            "sample_size": self.sample_size,
            "dimension": self.dimension,
            "min_cosine_similarity": self.min_cosine_similarity,
            "raw_vectors_were_unit_norm": self.raw_vectors_were_unit_norm,
            "min_cosine_threshold": self.min_cosine_threshold,
            "detail": dict(self.detail),
        }


@dataclass(frozen=True)
class RerankerProbeResult:
    status: str
    code: Optional[str]
    endpoint_name: str
    sample_size: int
    order_matches_local: Optional[bool]
    max_score_abs_diff: Optional[float]
    score_abs_tol: float
    detail: Dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status == "PASS"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": LIVE_PROBE_SCHEMA_VERSION,
            "probe": "BGE_RERANKER_SCORE",
            "evidence": "LIVE",
            "status": self.status,
            "code": self.code,
            "endpoint_name": self.endpoint_name,
            "sample_size": self.sample_size,
            "order_matches_local": self.order_matches_local,
            "max_score_abs_diff": self.max_score_abs_diff,
            "score_abs_tol": self.score_abs_tol,
            "detail": dict(self.detail),
        }


def probe_embedding_endpoint(
    client: OpenAICompatibleClient,
    *,
    sample_texts: Sequence[str],
    local_vectors: Sequence[Sequence[float]],
    min_cosine: float = DEFAULT_EMBED_MIN_COSINE,
) -> EmbeddingProbeResult:
    """Call a live BGE-M3 EMBED endpoint and check dim/finiteness/parity.

    ``local_vectors`` are the pinned local reference embeddings for the same
    ``sample_texts`` (from ``load_bge_m3_encoder`` on CPU), in the same order.
    """
    if not isinstance(client, OpenAICompatibleClient):
        raise LiveProbeError("PROBE_CONFIG_INVALID", "client must be an OpenAICompatibleClient")
    if client.endpoint.task is not ServingTask.EMBED:
        raise LiveProbeError("PROBE_CONFIG_INVALID", "client endpoint must serve EMBED")
    texts = list(sample_texts)
    refs = [list(vector) for vector in local_vectors]
    if not texts or len(texts) != len(refs):
        raise LiveProbeError("PROBE_CONFIG_INVALID", "sample_texts and local_vectors must align and be non-empty")
    if isinstance(min_cosine, bool) or not isinstance(min_cosine, (int, float)) or not (0.0 < min_cosine <= 1.0):
        raise LiveProbeError("PROBE_CONFIG_INVALID", "min_cosine must be in (0, 1]")
    name = client.endpoint.name

    try:
        served = client.embed(texts)
    except ServingError as error:
        return EmbeddingProbeResult(
            "FAIL", "PROBE_TRANSPORT_FAILED", name, len(texts), None, None, None, float(min_cosine),
            {"serving_error": error.code},
        )

    if len(served) != len(texts):
        return EmbeddingProbeResult(
            "FAIL", "PROBE_COUNT_MISMATCH", name, len(texts), None, None, None, float(min_cosine),
            {"served_count": len(served)},
        )

    raw_unit_norm = True
    min_cos: Optional[float] = None
    for index, (served_vector, reference) in enumerate(zip(served, refs)):
        if len(reference) != EMBEDDING_DIMENSION:
            raise LiveProbeError("PROBE_CONFIG_INVALID", f"local_vectors[{index}] is not {EMBEDDING_DIMENSION}-d")
        try:
            numbers = _finite_floats(served_vector, f"embedding[{index}]")
        except LiveProbeError as error:
            return EmbeddingProbeResult(
                "FAIL", error.code, name, len(texts), None, None, None, float(min_cosine),
                {"message": str(error)},
            )
        if len(numbers) != EMBEDDING_DIMENSION:
            return EmbeddingProbeResult(
                "FAIL", "PROBE_DIMENSION_MISMATCH", name, len(texts), len(numbers), None, None,
                float(min_cosine), {"expected_dimension": EMBEDDING_DIMENSION, "got": len(numbers)},
            )
        raw_norm = math.sqrt(sum(value * value for value in numbers))
        if not math.isclose(raw_norm, 1.0, rel_tol=1e-3, abs_tol=1e-3):
            raw_unit_norm = False
        try:
            cosine = _cosine(
                _l2_normalize(numbers, f"embedding[{index}]"),
                _l2_normalize(reference, f"local[{index}]"),
            )
        except LiveProbeError as error:
            return EmbeddingProbeResult(
                "FAIL", error.code, name, len(texts), EMBEDDING_DIMENSION, None, raw_unit_norm,
                float(min_cosine), {"message": str(error)},
            )
        min_cos = cosine if min_cos is None else min(min_cos, cosine)

    if min_cos is None or min_cos < float(min_cosine):
        return EmbeddingProbeResult(
            "FAIL", "PROBE_PARITY_BELOW_TOLERANCE", name, len(texts), EMBEDDING_DIMENSION, min_cos,
            raw_unit_norm, float(min_cosine), {"reason": "served vectors diverge from pinned local encoder"},
        )
    return EmbeddingProbeResult(
        "PASS", None, name, len(texts), EMBEDDING_DIMENSION, min_cos, raw_unit_norm, float(min_cosine), {},
    )


def probe_reranker_endpoint(
    client: OpenAICompatibleClient,
    *,
    sample_query: str,
    sample_documents: Sequence[str],
    local_scores: Sequence[float],
    score_abs_tol: float = DEFAULT_RERANK_SCORE_ABS_TOL,
) -> RerankerProbeResult:
    """Call a live BGE-reranker SCORE endpoint and check shape/order parity.

    ``local_scores`` are the pinned local reference scores (from
    ``load_bge_reranker`` on CPU) for the same query/documents, in order. Order
    parity is the gate; ``score_abs_tol`` on raw scores is advisory only.
    """
    if not isinstance(client, OpenAICompatibleClient):
        raise LiveProbeError("PROBE_CONFIG_INVALID", "client must be an OpenAICompatibleClient")
    if client.endpoint.task is not ServingTask.SCORE:
        raise LiveProbeError("PROBE_CONFIG_INVALID", "client endpoint must serve SCORE")
    docs = list(sample_documents)
    refs = _finite_floats(local_scores, "local_scores")
    if not isinstance(sample_query, str) or not sample_query:
        raise LiveProbeError("PROBE_CONFIG_INVALID", "sample_query must be a non-empty string")
    if len(docs) < 2 or len(docs) != len(refs):
        raise LiveProbeError("PROBE_CONFIG_INVALID", "need >=2 documents aligned with local_scores")
    name = client.endpoint.name

    try:
        served = client.score(sample_query, docs)
    except ServingError as error:
        return RerankerProbeResult(
            "FAIL", "PROBE_TRANSPORT_FAILED", name, len(docs), None, None, float(score_abs_tol),
            {"serving_error": error.code},
        )

    if len(served) != len(docs):
        return RerankerProbeResult(
            "FAIL", "PROBE_COUNT_MISMATCH", name, len(docs), None, None, float(score_abs_tol),
            {"served_count": len(served)},
        )
    try:
        served_scores = _finite_floats(served, "served_scores")
    except LiveProbeError as error:
        return RerankerProbeResult(
            "FAIL", error.code, name, len(docs), None, None, float(score_abs_tol),
            {"message": str(error)},
        )
    served_order = _descending_order(served_scores)
    local_order = _descending_order(refs)
    order_matches = served_order == local_order
    max_diff = max(abs(a - b) for a, b in zip(served_scores, refs))

    if not order_matches:
        return RerankerProbeResult(
            "FAIL", "PROBE_ORDER_MISMATCH", name, len(docs), False, max_diff, float(score_abs_tol),
            {"served_order": served_order, "local_order": local_order},
        )
    return RerankerProbeResult(
        "PASS", None, name, len(docs), True, max_diff, float(score_abs_tol),
        {"score_within_advisory_tol": bool(max_diff <= float(score_abs_tol))},
    )
