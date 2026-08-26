"""M10 Batch 4 model serving integration (TASK-108, TASK-109).

This package implements the thin OpenAI-compatible serving boundary decided in
ADR-049. It contains *no* business logic: role/tier -> endpoint resolution reuses
the deterministic ``Plan.model_tier`` route, the query encoder reuses the
existing M2B embedding contract, and the reranker reuses the pinned
``BGEReranker`` scoring/ranking contract. No model is deployed here; live-GPU
serving validation remains ``GPU_PRODUCTION_VALIDATION_PENDING``.
"""

from src.serving.schemas import (
    SERVING_SCHEMA_VERSION,
    ServingEndpoint,
    ServingError,
    ServingRole,
    ServingTask,
    Transport,
)
from src.serving.topology import (
    DEFAULT_BGE_M3_MODEL_ID,
    DEFAULT_BGE_RERANKER_MODEL_ID,
    DEFAULT_STRONG_LLM_MODEL_ID,
    DEFAULT_SHARED_QWEN3_MODEL_ID,
    SHARED_QWEN3_ROLES,
    ServingTopology,
    build_topology,
)
from src.serving.client import HttpTransport, OpenAICompatibleClient
from src.serving.retrieval_serving import (
    ServingBGEReranker,
    ServingQueryEncoder,
    serving_bge_reranker,
    serving_query_encoder,
)

__all__ = [
    "SERVING_SCHEMA_VERSION",
    "ServingEndpoint",
    "ServingError",
    "ServingRole",
    "ServingTask",
    "Transport",
    "DEFAULT_BGE_M3_MODEL_ID",
    "DEFAULT_BGE_RERANKER_MODEL_ID",
    "DEFAULT_STRONG_LLM_MODEL_ID",
    "DEFAULT_SHARED_QWEN3_MODEL_ID",
    "SHARED_QWEN3_ROLES",
    "ServingTopology",
    "build_topology",
    "HttpTransport",
    "OpenAICompatibleClient",
    "ServingBGEReranker",
    "ServingQueryEncoder",
    "serving_bge_reranker",
    "serving_query_encoder",
]
