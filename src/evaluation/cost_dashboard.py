"""TASK-110 cost / token dashboard.

Aggregates per-model token usage and cost, escalation cost, and cache hit rate.
Token/cost telemetry can only be real when a live model actually ran: the CPU
fixtures invoke no LLM, so with no recorded invocations the dashboard reports
token/cost as ``LIVE_PENDING`` rather than inventing numbers. Prices are always
caller-supplied (a deployment/provider choice, ADR-049); no provider price is
hardwired. The aggregation math is unit-tested with explicitly-synthetic
invocations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping, Optional, Sequence

from src.serving.schemas import ServingRole
from src.understanding.schemas import SchemaValidationError


COST_DASHBOARD_SCHEMA_VERSION = "m10-cost-token-dashboard-v1"
TOKEN_COST_LIVE_BLOCKER = "GPU_PRODUCTION_VALIDATION_PENDING"


def _non_negative_int(value, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SchemaValidationError(f"{path} must be a non-negative integer")
    return value


@dataclass(frozen=True)
class ModelInvocation:
    """One measured (or test-synthetic) model call's token usage."""

    model_id: str
    role: ServingRole
    prompt_tokens: int
    completion_tokens: int
    requests: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.model_id, str) or not self.model_id:
            raise SchemaValidationError("model_id must be a non-empty string")
        if not isinstance(self.role, ServingRole):
            raise SchemaValidationError("role must be a ServingRole")
        _non_negative_int(self.prompt_tokens, "prompt_tokens")
        _non_negative_int(self.completion_tokens, "completion_tokens")
        if isinstance(self.requests, bool) or not isinstance(self.requests, int) or self.requests < 1:
            raise SchemaValidationError("requests must be a positive integer")

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class TokenPrice:
    """Per-1K-token price for one model (caller/deployment supplied)."""

    prompt_per_1k: float
    completion_per_1k: float

    def __post_init__(self) -> None:
        for value, path in ((self.prompt_per_1k, "prompt_per_1k"), (self.completion_per_1k, "completion_per_1k")):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise SchemaValidationError(f"{path} must be a non-negative number")

    def cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        return (
            prompt_tokens / 1000.0 * self.prompt_per_1k
            + completion_tokens / 1000.0 * self.completion_per_1k
        )


@dataclass(frozen=True)
class CostTokenDashboard:
    queries: int
    invocation_count: int
    total_prompt_tokens: int
    total_completion_tokens: int
    per_model: Mapping[str, Dict[str, object]]
    tokens_per_query: Optional[float]
    cost_per_query: Optional[float]
    escalation_requests: int
    escalation_cost: Optional[float]
    cache_hit_rate: Optional[float]
    token_measurement_status: str

    def to_dict(self) -> dict:
        return {
            "schema_version": COST_DASHBOARD_SCHEMA_VERSION,
            "token_measurement_status": self.token_measurement_status,
            "token_cost_live_blocker": TOKEN_COST_LIVE_BLOCKER,
            "queries": self.queries,
            "invocation_count": self.invocation_count,
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_tokens": self.total_prompt_tokens + self.total_completion_tokens,
            "tokens_per_query": self.tokens_per_query,
            "cost_per_query": self.cost_per_query,
            "escalation_requests": self.escalation_requests,
            "escalation_cost": self.escalation_cost,
            "cache_hit_rate": self.cache_hit_rate,
            "per_model": {model: dict(fields) for model, fields in self.per_model.items()},
        }


def _priced_cost(
    invocations: Sequence["ModelInvocation"], pricing: Mapping[str, "TokenPrice"]
) -> Optional[float]:
    """Sum priced cost, or None if any invoked model is unpriced."""
    if not invocations:
        return None
    total = 0.0
    for inv in invocations:
        price = pricing.get(inv.model_id)
        if price is None:
            return None
        total += price.cost(inv.prompt_tokens, inv.completion_tokens)
    return total


def build_cost_dashboard(
    invocations: Sequence[ModelInvocation],
    *,
    queries: int,
    pricing: Optional[Mapping[str, TokenPrice]] = None,
    escalation_requests: int = 0,
    escalation_invocations: Optional[Sequence[ModelInvocation]] = None,
    cache_hit_rate: Optional[float] = None,
) -> CostTokenDashboard:
    """Aggregate token/cost telemetry; pending when nothing live was measured."""
    if isinstance(queries, bool) or not isinstance(queries, int) or queries < 1:
        raise SchemaValidationError("queries must be a positive integer")
    _non_negative_int(escalation_requests, "escalation_requests")
    if not isinstance(invocations, (list, tuple)) or not all(
        isinstance(item, ModelInvocation) for item in invocations
    ):
        raise SchemaValidationError("invocations must be ModelInvocation values")
    escalation_invocations = list(escalation_invocations or [])
    if not all(isinstance(item, ModelInvocation) for item in escalation_invocations):
        raise SchemaValidationError("escalation_invocations must be ModelInvocation values")
    pricing = dict(pricing or {})

    per_model: Dict[str, Dict[str, object]] = {}
    total_prompt = 0
    total_completion = 0
    total_cost = 0.0
    priced_all = bool(invocations)
    for inv in invocations:
        total_prompt += inv.prompt_tokens
        total_completion += inv.completion_tokens
        bucket = per_model.setdefault(
            inv.model_id,
            {"prompt_tokens": 0, "completion_tokens": 0, "requests": 0, "cost": None},
        )
        bucket["prompt_tokens"] += inv.prompt_tokens
        bucket["completion_tokens"] += inv.completion_tokens
        bucket["requests"] += inv.requests
        price = pricing.get(inv.model_id)
        if price is None:
            priced_all = False
        else:
            inv_cost = price.cost(inv.prompt_tokens, inv.completion_tokens)
            total_cost += inv_cost
            bucket["cost"] = (bucket["cost"] or 0.0) + inv_cost

    if not invocations:
        status = "LIVE_PENDING"
        tokens_per_query = None
        cost_per_query = None
    else:
        status = "LIVE"
        tokens_per_query = (total_prompt + total_completion) / queries
        cost_per_query = (total_cost / queries) if priced_all else None

    # Escalation cost is the real priced cost of the supplied escalation calls,
    # never a heuristic derived from a request count.
    escalation_cost = _priced_cost(escalation_invocations, pricing)

    return CostTokenDashboard(
        queries=queries,
        invocation_count=len(invocations),
        total_prompt_tokens=total_prompt,
        total_completion_tokens=total_completion,
        per_model=per_model,
        tokens_per_query=tokens_per_query,
        cost_per_query=cost_per_query,
        escalation_requests=escalation_requests,
        escalation_cost=escalation_cost,
        cache_hit_rate=cache_hit_rate,
        token_measurement_status=status,
    )
