"""TASK-105 semantic-cache experiment over the deterministic M9 fixtures.

Compares no-cache vs. query-level vs. field-level vs. adaptive caching and
monitors the correctness delta. It runs the real M9 graph to obtain the
*verified* answer for each case, then checks that any cache hit returns exactly
that verified answer — so a reported cache never bypasses verification and any
correctness regression would be caught (there is none on exact/structural keys).

Semantic (embedding-similarity) fuzzy matching and its production hit-rate /
quality measurement require the retrieval models and remain
``GPU_PRODUCTION_VALIDATION_PENDING``; this measures the CPU cache structure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from src.evaluation.e2e import RetrievedEvidenceE2ECase
from src.evaluation.e2e_fixtures import e2e_fixture_cases
from src.orchestration.schemas import FinalResponse, FinalResponseStatus, make_plan_fingerprint
from src.orchestration.semantic_cache import CacheKeyPolicy, SemanticCache
from src.understanding.schemas import QueryUnderstanding, SchemaValidationError


CACHE_EXPERIMENT_SCHEMA_VERSION = "m10-semantic-cache-experiment-v1"
MEASUREMENT_STATUS = "FIXTURE_STRUCTURE"
PRODUCTION_MEASUREMENT_BLOCKER = "GPU_PRODUCTION_VALIDATION_PENDING"
_EXPERIMENT_POLICIES = (
    CacheKeyPolicy.NONE,
    CacheKeyPolicy.QUERY,
    CacheKeyPolicy.FIELD,
    CacheKeyPolicy.ADAPTIVE,
)


@dataclass(frozen=True)
class _CaseOutcome:
    case_id: str
    understanding: QueryUnderstanding
    final_response: FinalResponse
    invalidation_fingerprint: str

    @property
    def cacheable(self) -> bool:
        return self.final_response.status is FinalResponseStatus.PASS


def _run_case(case: RetrievedEvidenceE2ECase) -> _CaseOutcome:
    state = case.graph_factory().run(
        request_id=f"cache-{case.case_id}", raw_question=case.raw_question
    ).state
    if state.query_understanding is None or state.final_response is None:
        raise SchemaValidationError("case did not produce understanding and a final response")
    plan = None if state.supervisor_result is None else state.supervisor_result.plan
    fingerprint = "NO_PLAN" if plan is None else make_plan_fingerprint(plan)
    return _CaseOutcome(case.case_id, state.query_understanding, state.final_response, fingerprint)


@dataclass(frozen=True)
class CachePolicyResult:
    policy: str
    cacheable_cases: int
    stored: int
    warm_hits: int
    correctness_regressions: int
    hit_rate: Optional[float]

    def to_dict(self) -> dict:
        return {
            "policy": self.policy,
            "cacheable_cases": self.cacheable_cases,
            "stored": self.stored,
            "warm_hits": self.warm_hits,
            "correctness_regressions": self.correctness_regressions,
            "hit_rate": self.hit_rate,
        }


def _evaluate_policy(policy: CacheKeyPolicy, outcomes: Sequence[_CaseOutcome]) -> CachePolicyResult:
    cache = SemanticCache(policy, enabled=policy is not CacheKeyPolicy.NONE)
    cacheable = [item for item in outcomes if item.cacheable]

    # Cold pass: every lookup misses; verified answers are stored.
    stored = 0
    for item in outcomes:
        cache.get(item.understanding, item.invalidation_fingerprint)
        if item.cacheable and cache.put(
            item.understanding, item.invalidation_fingerprint, item.final_response
        ):
            stored += 1

    # Warm pass: a hit must equal the freshly verified answer (no bypass).
    warm_hits = 0
    regressions = 0
    for item in cacheable:
        cached = cache.get(item.understanding, item.invalidation_fingerprint)
        if cached is None:
            continue
        warm_hits += 1
        assert item.final_response.answer is not None
        if cached.to_dict() != item.final_response.answer.to_dict():
            regressions += 1

    hit_rate = None if not cacheable else warm_hits / len(cacheable)
    return CachePolicyResult(
        policy=policy.value,
        cacheable_cases=len(cacheable),
        stored=stored,
        warm_hits=warm_hits,
        correctness_regressions=regressions,
        hit_rate=hit_rate,
    )


@dataclass(frozen=True)
class CacheExperimentReport:
    policy_results: Tuple[CachePolicyResult, ...]
    total_cases: int

    @property
    def correctness_preserved(self) -> bool:
        return all(item.correctness_regressions == 0 for item in self.policy_results)

    def to_dict(self) -> dict:
        return {
            "schema_version": CACHE_EXPERIMENT_SCHEMA_VERSION,
            "measurement_status": MEASUREMENT_STATUS,
            "production_measurement_blocker": PRODUCTION_MEASUREMENT_BLOCKER,
            "total_cases": self.total_cases,
            "correctness_preserved": self.correctness_preserved,
            "policy_results": [item.to_dict() for item in self.policy_results],
        }


def run_cache_experiment(
    cases: Optional[Sequence[RetrievedEvidenceE2ECase]] = None,
) -> CacheExperimentReport:
    selected = list(e2e_fixture_cases() if cases is None else cases)
    outcomes: List[_CaseOutcome] = [_run_case(case) for case in selected]
    results = tuple(_evaluate_policy(policy, outcomes) for policy in _EXPERIMENT_POLICIES)
    return CacheExperimentReport(policy_results=results, total_cases=len(outcomes))
