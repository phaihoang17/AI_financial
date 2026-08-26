"""TASK-102 retry-rate / escalation quality-gain measurement.

The retry and CHEAP->STRONG escalation *routing* is owned entirely by the
deterministic M8 engine ``src.verification.retry.build_retry_directive`` and
executed by M9. This module adds **measurement only** — it never decides a
retry, never escalates, and creates no second routing source of truth. It reads
the terminal :class:`RetrievedEvidenceE2ECaseResult` values the M9 evaluator
already produces and reports:

- retry rate and escalation rate,
- quality recovered by retry/escalation (PASS rate among cases that retried),
- the retries-used distribution.

"Confidence-gated" here means the outcomes may be *bucketed* by ``Plan.confidence``
so a future gate can be calibrated from real data. Confidence never gates a
directive: per ADR-030 routing is deterministic and confidence-free. Production
retry-rate / quality-gain measurement over real models and traffic is
``GPU_PRODUCTION_VALIDATION_PENDING``; this measures the CPU fixture structure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping, Optional, Sequence

from src.evaluation.e2e import RetrievedEvidenceE2ECaseResult
from src.orchestration.schemas import FinalResponseStatus
from src.understanding.schemas import SchemaValidationError


RETRY_METRICS_SCHEMA_VERSION = "m10-retry-escalation-metrics-v1"
MEASUREMENT_STATUS = "FIXTURE_STRUCTURE"
PRODUCTION_MEASUREMENT_BLOCKER = "GPU_PRODUCTION_VALIDATION_PENDING"

# Default observational confidence bands (calibration only; never routes).
DEFAULT_CONFIDENCE_BANDS = ((0.0, 0.5), (0.5, 0.9), (0.9, 1.0))


def _rate(numerator: int, denominator: int) -> Optional[float]:
    """Return a rate, or ``None`` when undefined (no eligible cases)."""
    if denominator <= 0:
        return None
    return numerator / denominator


@dataclass(frozen=True)
class RetryEscalationMetrics:
    total_cases: int
    executed_cases: int  # cases that reached the retry-bearing pipeline
    retried_cases: int
    escalated_cases: int
    passed_cases: int
    retry_rate: Optional[float]  # retried / executed
    escalation_rate: Optional[float]  # escalated / executed
    retry_recovery_rate: Optional[float]  # PASS / retried
    escalation_recovery_rate: Optional[float]  # PASS / escalated
    retries_used_distribution: Mapping[str, int]
    confidence_bands: Mapping[str, Dict[str, object]]

    def to_dict(self) -> dict:
        return {
            "schema_version": RETRY_METRICS_SCHEMA_VERSION,
            "measurement_status": MEASUREMENT_STATUS,
            "production_measurement_blocker": PRODUCTION_MEASUREMENT_BLOCKER,
            "routing_source_of_truth": "src.verification.retry.build_retry_directive",
            "total_cases": self.total_cases,
            "executed_cases": self.executed_cases,
            "retried_cases": self.retried_cases,
            "escalated_cases": self.escalated_cases,
            "passed_cases": self.passed_cases,
            "retry_rate": self.retry_rate,
            "escalation_rate": self.escalation_rate,
            "retry_recovery_rate": self.retry_recovery_rate,
            "escalation_recovery_rate": self.escalation_recovery_rate,
            "retries_used_distribution": dict(sorted(self.retries_used_distribution.items())),
            "confidence_bands": {
                key: dict(value) for key, value in self.confidence_bands.items()
            },
        }


def _band_label(low: float, high: float) -> str:
    return f"[{low:.2f},{high:.2f}{']' if high >= 1.0 else ')'}"


def _in_band(confidence: float, low: float, high: float) -> bool:
    return low <= confidence < high or (high >= 1.0 and confidence == 1.0)


def retry_escalation_metrics(
    case_results: Sequence[RetrievedEvidenceE2ECaseResult],
    *,
    confidence_by_case: Optional[Mapping[str, float]] = None,
    confidence_bands: Sequence[tuple] = DEFAULT_CONFIDENCE_BANDS,
) -> RetryEscalationMetrics:
    """Measure retry/escalation behavior from terminal M9 case results."""

    if not isinstance(case_results, (list, tuple)) or not all(
        isinstance(item, RetrievedEvidenceE2ECaseResult) for item in case_results
    ):
        raise SchemaValidationError("case_results must be RetrievedEvidenceE2ECaseResult values")

    executed = [item for item in case_results if item.retries_used is not None]
    retried = [item for item in executed if (item.retries_used or 0) > 0]
    escalated = [item for item in executed if item.strong_escalated]
    passed = [item for item in case_results if item.final_status is FinalResponseStatus.PASS]

    distribution: Dict[str, int] = {}
    for item in case_results:
        key = "NONE" if item.retries_used is None else str(item.retries_used)
        distribution[key] = distribution.get(key, 0) + 1

    bands: Dict[str, Dict[str, object]] = {}
    if confidence_by_case is not None:
        for low, high in confidence_bands:
            selected = [
                item
                for item in executed
                if item.case_id in confidence_by_case
                and _in_band(confidence_by_case[item.case_id], low, high)
            ]
            band_retried = [i for i in selected if (i.retries_used or 0) > 0]
            band_pass = [i for i in selected if i.final_status is FinalResponseStatus.PASS]
            bands[_band_label(low, high)] = {
                "cases": len(selected),
                "retried": len(band_retried),
                "escalated": sum(i.strong_escalated for i in selected),
                "retry_rate": _rate(len(band_retried), len(selected)),
                "recovery_rate": _rate(len(band_pass), len(band_retried)),
            }

    return RetryEscalationMetrics(
        total_cases=len(case_results),
        executed_cases=len(executed),
        retried_cases=len(retried),
        escalated_cases=len(escalated),
        passed_cases=len(passed),
        retry_rate=_rate(len(retried), len(executed)),
        escalation_rate=_rate(len(escalated), len(executed)),
        retry_recovery_rate=_rate(
            sum(i.final_status is FinalResponseStatus.PASS for i in retried), len(retried)
        ),
        escalation_recovery_rate=_rate(
            sum(i.final_status is FinalResponseStatus.PASS for i in escalated), len(escalated)
        ),
        retries_used_distribution=distribution,
        confidence_bands=bands,
    )
