"""TASK-108 shared Qwen3-8B role serving + per-model serving topology.

ADR-049 mandates one process per model with the Qwen3-8B weights *shared* across
the NLU, Supervisor, and optional Verifier roles, and a separate STRONG process
(Qwen2.5-Coder-14B) for the Programmer. This module resolves a role or a
deterministic ``ModelTier`` to its serving endpoint. It re-derives no routing:
``Plan.model_tier`` remains the single source of truth (ADR-010/030), and this
module only maps that already-decided tier to an endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.indexing.embedding_chunker import BGE_M3_MODEL_ID
from src.retrieval.reranker import BGE_RERANKER_MODEL_ID
from src.serving.schemas import ServingEndpoint, ServingError, ServingRole, ServingTask
from src.supervisor.schemas import ModelTier


# Default model identities are configurable deployment choices, never hardwired
# into business logic. Base URLs must always be supplied by deployment config.
DEFAULT_SHARED_QWEN3_MODEL_ID = "Qwen/Qwen3-8B"
DEFAULT_STRONG_LLM_MODEL_ID = "Qwen/Qwen2.5-Coder-14B-Instruct"
DEFAULT_BGE_M3_MODEL_ID = BGE_M3_MODEL_ID
DEFAULT_BGE_RERANKER_MODEL_ID = BGE_RERANKER_MODEL_ID

# The three roles that share the single Qwen3-8B process (ADR-016).
SHARED_QWEN3_ROLES = frozenset(
    {ServingRole.NLU, ServingRole.SUPERVISOR, ServingRole.VERIFIER}
)


@dataclass(frozen=True)
class ServingTopology:
    """The four ADR-049 serving processes and their role/tier mapping."""

    shared_llm: ServingEndpoint  # CHEAP tier + NLU/Supervisor/Verifier
    strong_llm: ServingEndpoint  # STRONG tier + Programmer
    embedding: ServingEndpoint  # BGE-M3 query embedding
    reranker: ServingEndpoint  # BGE-reranker-v2-m3 scoring

    def __post_init__(self) -> None:
        if self.shared_llm.task is not ServingTask.GENERATE:
            raise ServingError("INVALID_TOPOLOGY", "shared_llm must be a GENERATE endpoint")
        if self.strong_llm.task is not ServingTask.GENERATE:
            raise ServingError("INVALID_TOPOLOGY", "strong_llm must be a GENERATE endpoint")
        if self.embedding.task is not ServingTask.EMBED:
            raise ServingError("INVALID_TOPOLOGY", "embedding must be an EMBED endpoint")
        if self.reranker.task is not ServingTask.SCORE:
            raise ServingError("INVALID_TOPOLOGY", "reranker must be a SCORE endpoint")
        # Failure isolation: one process per model => distinct base URLs.
        base_urls = [
            self.shared_llm.base_url,
            self.strong_llm.base_url,
            self.embedding.base_url,
            self.reranker.base_url,
        ]
        if len(set(base_urls)) != len(base_urls):
            raise ServingError(
                "INVALID_TOPOLOGY",
                "each model must be a separate process with a distinct base_url",
            )

    def endpoint_for_role(self, role: ServingRole) -> ServingEndpoint:
        """Resolve a pipeline role to its serving endpoint.

        NLU, Supervisor, and the optional Verifier share the single Qwen3-8B
        process; the Programmer uses the separate STRONG process.
        """
        if not isinstance(role, ServingRole):
            raise ServingError("INVALID_ROLE", "role must be a ServingRole")
        if role in SHARED_QWEN3_ROLES:
            return self.shared_llm
        if role is ServingRole.PROGRAMMER:
            return self.strong_llm
        raise ServingError("INVALID_ROLE", f"no endpoint for role {role.value}")

    def endpoint_for_tier(self, tier: ModelTier) -> ServingEndpoint:
        """Map an already-routed ``Plan.model_tier`` to its LLM endpoint.

        This consumes the deterministic M4 route; it never re-classifies a plan.
        """
        if not isinstance(tier, ModelTier):
            raise ServingError("INVALID_TIER", "tier must be a ModelTier")
        if tier is ModelTier.CHEAP:
            return self.shared_llm
        if tier is ModelTier.STRONG:
            return self.strong_llm
        raise ServingError("INVALID_TIER", f"unknown model tier {tier}")

    def to_dict(self) -> dict[str, object]:
        return {
            "shared_llm": self.shared_llm.to_dict(),
            "strong_llm": self.strong_llm.to_dict(),
            "embedding": self.embedding.to_dict(),
            "reranker": self.reranker.to_dict(),
        }


def build_topology(
    *,
    shared_llm_base_url: str,
    strong_llm_base_url: str,
    embedding_base_url: str,
    reranker_base_url: str,
    shared_llm_model_id: str = DEFAULT_SHARED_QWEN3_MODEL_ID,
    strong_llm_model_id: str = DEFAULT_STRONG_LLM_MODEL_ID,
    embedding_model_id: str = DEFAULT_BGE_M3_MODEL_ID,
    reranker_model_id: str = DEFAULT_BGE_RERANKER_MODEL_ID,
    timeout_s: float = 30.0,
) -> ServingTopology:
    """Build the four-process topology from deployment-supplied base URLs."""

    return ServingTopology(
        shared_llm=ServingEndpoint(
            name="qwen3-8b",
            base_url=shared_llm_base_url,
            model_id=shared_llm_model_id,
            task=ServingTask.GENERATE,
            timeout_s=timeout_s,
        ),
        strong_llm=ServingEndpoint(
            name="qwen-coder-14b",
            base_url=strong_llm_base_url,
            model_id=strong_llm_model_id,
            task=ServingTask.GENERATE,
            timeout_s=timeout_s,
        ),
        embedding=ServingEndpoint(
            name="bge-m3",
            base_url=embedding_base_url,
            model_id=embedding_model_id,
            task=ServingTask.EMBED,
            timeout_s=timeout_s,
        ),
        reranker=ServingEndpoint(
            name="bge-reranker-v2-m3",
            base_url=reranker_base_url,
            model_id=reranker_model_id,
            task=ServingTask.SCORE,
            timeout_s=timeout_s,
        ),
    )


def topology_from_env(env: Optional[dict[str, str]] = None) -> ServingTopology:
    """Build a topology from environment variables (deployment convenience).

    Required base-URL variables:
    ``SERVING_SHARED_LLM_URL``, ``SERVING_STRONG_LLM_URL``,
    ``SERVING_EMBEDDING_URL``, ``SERVING_RERANKER_URL``.
    """

    import os

    source = os.environ if env is None else env
    required = {
        "shared_llm_base_url": "SERVING_SHARED_LLM_URL",
        "strong_llm_base_url": "SERVING_STRONG_LLM_URL",
        "embedding_base_url": "SERVING_EMBEDDING_URL",
        "reranker_base_url": "SERVING_RERANKER_URL",
    }
    kwargs: dict[str, str] = {}
    for arg, key in required.items():
        value = source.get(key)
        if not value:
            raise ServingError("MISSING_CONFIG", f"{key} is required")
        kwargs[arg] = value
    return build_topology(**kwargs)
