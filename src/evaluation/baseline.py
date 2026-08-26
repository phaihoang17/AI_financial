"""TASK-100 strong-tier / strict-verification correctness baseline.

M10 optimization tasks (CHEAP routing, caching, parallelization, escalation)
must be measured against a fixed correctness reference. Per ADR-024 and the M10
header in ``docs/TASKS.md``, that reference is established *before* any
optimization is activated.

This module does not introduce a new evaluation mechanism. It isolates the
already-deterministic M9 retrieved-evidence fixture cases whose ``Plan`` routes
to ``ModelTier.STRONG`` with ``VerifyProfile.STRICT`` and records their
correctness through the existing :func:`evaluate_e2e_cases` evaluator. No new
metric is defined and no acceptance threshold is invented; numeric thresholds
remain ``TBD`` exactly as ``docs/TASKS.md`` requires. The production TABLE and
GPU validation blockers are carried through unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from src.evaluation.e2e import (
    PRODUCTION_TABLE_E2E_BLOCKER,
    PRODUCTION_TABLE_E2E_STATUS,
    RetrievedEvidenceE2ECase,
    RetrievedEvidenceE2EReport,
    evaluate_e2e_cases,
)
from src.evaluation.e2e_fixtures import e2e_fixture_cases
from src.supervisor.schemas import ModelTier, VerifyProfile
from src.understanding.schemas import SchemaValidationError


BASELINE_SCHEMA_VERSION = "m10-strong-strict-correctness-baseline-v1"
BASELINE_MODEL_TIER = ModelTier.STRONG
BASELINE_VERIFY_PROFILE = VerifyProfile.STRICT

# Numeric acceptance thresholds are not specified by the source materials and
# must not be invented (see docs/TASKS.md). The baseline records achieved
# correctness only; a gate is added later when a threshold is defined.
BASELINE_ACCEPTANCE_THRESHOLDS = None


def strong_strict_baseline_cases(
    cases: Sequence[RetrievedEvidenceE2ECase] | None = None,
) -> tuple[RetrievedEvidenceE2ECase, ...]:
    """Select the strong-tier / strict-verification subset of E2E cases.

    A case qualifies only when it carries a concrete ``Plan`` routed to
    ``ModelTier.STRONG`` and ``VerifyProfile.STRICT``. Clarification/abstain
    cases without a plan carry no tier or profile and are excluded.
    """
    source = e2e_fixture_cases() if cases is None else cases
    selected = tuple(
        case
        for case in source
        if case.expected_plan is not None
        and case.expected_plan.model_tier is BASELINE_MODEL_TIER
        and case.expected_plan.verify_profile is BASELINE_VERIFY_PROFILE
    )
    if not selected:
        raise SchemaValidationError(
            "strong-tier/strict-verification baseline requires at least one case"
        )
    return selected


@dataclass(frozen=True)
class StrongStrictBaselineReport:
    """Recorded correctness reference for the STRONG/STRICT configuration."""

    e2e_report: RetrievedEvidenceE2EReport

    @property
    def total_cases(self) -> int:
        return len(self.e2e_report.case_results)

    @property
    def failed_cases(self) -> int:
        return sum(not item.passed for item in self.e2e_report.case_results)

    @property
    def established(self) -> bool:
        """The baseline is measurable only when every selected case passes."""
        return self.failed_cases == 0

    def to_dict(self) -> dict:
        return {
            "schema_version": BASELINE_SCHEMA_VERSION,
            "baseline_config": {
                "model_tier": BASELINE_MODEL_TIER.value,
                "verify_profile": BASELINE_VERIFY_PROFILE.value,
            },
            "acceptance_thresholds": BASELINE_ACCEPTANCE_THRESHOLDS,
            "established": self.established,
            "production_table_e2e_status": PRODUCTION_TABLE_E2E_STATUS,
            "production_table_e2e_blocker": PRODUCTION_TABLE_E2E_BLOCKER,
            "e2e_report": self.e2e_report.to_dict(),
        }


def evaluate_strong_strict_baseline(
    cases: Sequence[RetrievedEvidenceE2ECase] | None = None,
) -> StrongStrictBaselineReport:
    """Run the existing E2E evaluator over the STRONG/STRICT baseline subset."""
    return StrongStrictBaselineReport(
        evaluate_e2e_cases(strong_strict_baseline_cases(cases))
    )
