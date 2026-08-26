"""Deterministic M8 retry classification and bounded directive emission.

This module emits orchestration directives only.  It never invokes retrieval,
the Programmer, a model, or the sandbox.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Dict, Mapping, Tuple

from src.supervisor.schemas import ModelTier, Plan
from src.understanding.schemas import SchemaValidationError
from src.verification.schemas import (
    RetryAction,
    RetryDirective,
    RetryState,
    VerificationFailureCategory,
    VerificationReport,
)


@dataclass(frozen=True)
class FailurePolicy:
    action: RetryAction
    escalation_eligible: bool
    reason: str

    def __post_init__(self) -> None:
        if self.action not in {
            RetryAction.RETRY_RETRIEVAL,
            RetryAction.RETRY_PROGRAMMER,
            RetryAction.ABSTAIN,
        }:
            raise SchemaValidationError("failure policy action is invalid")
        if self.escalation_eligible and self.action is not RetryAction.RETRY_PROGRAMMER:
            raise SchemaValidationError(
                "only programmer-retry failures may be escalation eligible"
            )
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise SchemaValidationError("failure policy reason must be non-empty")


class RetryClassificationError(ValueError):
    """Raised when a verifier failure is absent from the explicit policy table."""


def _policies(
    category: VerificationFailureCategory,
    codes: Tuple[str, ...],
    action: RetryAction,
    *,
    escalation_eligible: bool = False,
    reason: str,
) -> Dict[Tuple[VerificationFailureCategory, str], FailurePolicy]:
    policy = FailurePolicy(action, escalation_eligible, reason)
    return {(category, code): policy for code in codes}


_GROUNDING_RETRIEVAL = (
    "SCHEMA_LINK_MISSING",
    "SCHEMA_LINK_AMBIGUOUS",
    "SCHEMA_LINK_UNRESOLVED",
    "SCHEMA_LINK_NOT_EXACT",
    "SCHEMA_LINK_INVALID",
    "EVIDENCE_ITEM_MISSING",
    "EVIDENCE_ID_AMBIGUOUS",
    "EVIDENCE_SOURCE_TYPE_MISMATCH",
    "SOURCE_CELL_MISSING",
    "SOURCE_CELL_AMBIGUOUS",
    "SOURCE_CELL_MISMATCH",
    "TICKER_MISMATCH",
    "COMPANY_MISMATCH",
    "REPORT_PROVENANCE_MISMATCH",
    "REPORT_YEAR_MISMATCH",
    "STATEMENT_SCOPE_MISMATCH",
    "PERIOD_MISMATCH",
    "METRIC_MISMATCH",
    "TABLE_ID_MISMATCH",
    "TABLE_CLASS_MISMATCH",
    "ROW_PATH_MISMATCH",
    "COLUMN_PATH_MISMATCH",
    "TEXT_METRIC_PERIOD_MISMATCH",
)

_GROUNDING_PERMANENT = (
    "TICKER_UNAVAILABLE",
    "COMPANY_UNAVAILABLE",
    "REPORT_PROVENANCE_UNAVAILABLE",
    "REPORT_YEAR_UNAVAILABLE",
    "STATEMENT_SCOPE_UNAVAILABLE",
    "TABLE_CLASS_UNAVAILABLE",
    "PARAGRAPH_PROVENANCE_UNAVAILABLE",
    "TEXT_SOURCE_SHAPE_INVALID",
    "LINK_PROVENANCE_MISSING",
)

_INSUFFICIENT_RETRIEVAL = (
    "REQUIRED_TABLE_EVIDENCE_MISSING",
    "REQUIRED_TEXT_EVIDENCE_MISSING",
)

_NUMERIC_PROGRAMMER = (
    "OUTPUT_KIND_MISMATCH",
    "SCALAR_VALUE_COUNT_MISMATCH",
    "ORDERED_VALUE_COUNT_MISMATCH",
    "ORDERED_VALUES_ORDER_MISMATCH",
)

_NUMERIC_PERMANENT = (
    "PROGRAM_ID_MISMATCH",
    "SUCCESS_OUTPUT_MISSING",
    "INVALID_OUTPUT_DATUM",
    "INVALID_CANONICAL_DECIMAL",
    "INVALID_SCALE_STRUCTURE",
    "INVALID_UNIT_STRUCTURE",
)

_SCALE_UNIT_RETRIEVAL = (
    "EVIDENCE_PROVENANCE_MISSING",
    "SCALE_UNIT_RESOLUTION_MISSING",
    "SCALE_UNIT_UNRESOLVED",
    "SCALE_UNIT_AMBIGUOUS",
    "SCALE_UNIT_HINT_PROVENANCE_BROKEN",
    "SOURCE_SCALE_REQUIRED",
)

_SCALE_UNIT_PERMANENT = (
    "BINDING_PROVENANCE_MISMATCH",
    "SOURCE_SCALE_UNIT_MISMATCH",
    "BINDING_SOURCE_SCALE_UNIT_MISMATCH",
    "REQUESTED_SCALE_UNIT_MISMATCH",
    "DEFAULT_TO_RAW_FORBIDDEN",
    "INVALID_INPUT",
    "UNSUPPORTED_SCALE_CONVERSION",
    "UNSUPPORTED_UNIT_CONVERSION",
    "UNSUPPORTED_SCALE_UNIT_CONVERSION",
    "PERCENT_MAGNITUDE_MIX",
    "UNIT_MISMATCH",
    "FORMULA_SCALE_UNIT_MISMATCH",
    "OUTPUT_SCALE_UNIT_MISMATCH",
)

_FINANCIAL_PROGRAMMER = (
    "QUESTION_TYPE_MISMATCH",
    "FORMULA_ID_MISMATCH",
    "FORMULA_REGISTRY_FINGERPRINT_MISMATCH",
    "OUTPUT_STEP_MISSING",
    "INPUT_COUNT_ORDER_MISMATCH",
    "REQUIRED_METRIC_PERIOD_MISMATCH",
    "PROGRAM_OPERATION_MISMATCH",
    "STEP_FORMULA_ID_MISMATCH",
    "PROGRAM_OUTPUT_KIND_MISMATCH",
    "FORMULA_INPUT_ORDER_MISMATCH",
    "FORMULA_INPUT_COUNT_MISMATCH",
)

_FINANCIAL_PERMANENT = (
    "VERIFY_PROFILE_MISMATCH",
    "LIGHT_QUESTION_TYPE_INVALID",
    "LIGHT_REASONING_MODE_INVALID",
    "LIGHT_FORMULA_INVALID",
    "LIGHT_REQUIRED_REQUIREMENT_COUNT_INVALID",
    "UNSUPPORTED_RATIO",
    "REASONING_MODE_MISMATCH",
    "UNSUPPORTED_FINANCIAL_LOGIC",
    "FORMULA_NOT_REGISTERED",
    "FORMULA_CONTRACT_MISMATCH",
    "FORMULA_OPERATION_MISMATCH",
    "FORMULA_METRIC_ORDER_MISMATCH",
    "FORMULA_PERIOD_ORDER_MISMATCH",
    "FORMULA_PERIOD_COUNT_MISMATCH",
)


_failure_policies: Dict[
    Tuple[VerificationFailureCategory, str], FailurePolicy
] = {}
for _entries in (
    _policies(
        VerificationFailureCategory.GROUNDING,
        _GROUNDING_RETRIEVAL,
        RetryAction.RETRY_RETRIEVAL,
        reason="another exact retrieval may repair the grounding mismatch",
    ),
    _policies(
        VerificationFailureCategory.GROUNDING,
        _GROUNDING_PERMANENT,
        RetryAction.ABSTAIN,
        reason="required canonical provenance or source schema is unavailable",
    ),
    _policies(
        VerificationFailureCategory.INSUFFICIENT_EVIDENCE,
        _INSUFFICIENT_RETRIEVAL,
        RetryAction.RETRY_RETRIEVAL,
        reason="required evidence is missing",
    ),
    _policies(
        VerificationFailureCategory.NUMERIC,
        _NUMERIC_PROGRAMMER,
        RetryAction.RETRY_PROGRAMMER,
        escalation_eligible=True,
        reason="program regeneration may repair the numeric output shape",
    ),
    _policies(
        VerificationFailureCategory.NUMERIC,
        _NUMERIC_PERMANENT,
        RetryAction.ABSTAIN,
        reason="the numeric failure is an invalid immutable execution contract",
    ),
    _policies(
        VerificationFailureCategory.SCALE_UNIT,
        _SCALE_UNIT_RETRIEVAL,
        RetryAction.RETRY_RETRIEVAL,
        reason="additional exact evidence may supply the missing scale/unit clue",
    ),
    _policies(
        VerificationFailureCategory.SCALE_UNIT,
        _SCALE_UNIT_PERMANENT,
        RetryAction.ABSTAIN,
        reason="the scale/unit policy or conversion is unsupported",
    ),
    _policies(
        VerificationFailureCategory.FINANCIAL_LOGIC,
        _FINANCIAL_PROGRAMMER,
        RetryAction.RETRY_PROGRAMMER,
        escalation_eligible=True,
        reason="program regeneration may repair the symbolic program",
    ),
    _policies(
        VerificationFailureCategory.FINANCIAL_LOGIC,
        _FINANCIAL_PERMANENT,
        RetryAction.ABSTAIN,
        reason="the Plan/profile/formula policy is unsupported or inconsistent",
    ),
):
    overlap = set(_failure_policies).intersection(_entries)
    if overlap:
        raise RuntimeError(f"duplicate failure policies: {sorted(overlap)!r}")
    _failure_policies.update(_entries)

FAILURE_POLICY_BY_REASON: Mapping[
    Tuple[VerificationFailureCategory, str], FailurePolicy
] = MappingProxyType(_failure_policies)

KNOWN_VERIFIER_REASON_CODES: Mapping[
    VerificationFailureCategory, Tuple[str, ...]
] = MappingProxyType(
    {
        category: tuple(
            code
            for mapped_category, code in FAILURE_POLICY_BY_REASON
            if mapped_category is category
        )
        for category in VerificationFailureCategory
    }
)


def classify_failure(
    category: VerificationFailureCategory,
    reason_code: str,
) -> FailurePolicy:
    """Return the exact reason-code policy; category-only fallback is forbidden."""
    if not isinstance(category, VerificationFailureCategory):
        raise RetryClassificationError(
            "category must be a VerificationFailureCategory"
        )
    if not isinstance(reason_code, str) or not reason_code:
        raise RetryClassificationError("reason_code must be non-empty")
    policy = FAILURE_POLICY_BY_REASON.get((category, reason_code))
    if policy is None:
        raise RetryClassificationError(
            f"unregistered verifier failure mapping: {category.value}/{reason_code}"
        )
    return policy


def _validate_state(plan: Plan, state: RetryState) -> None:
    if not isinstance(plan, Plan):
        raise SchemaValidationError("plan must be an executable Plan")
    if not isinstance(state, RetryState):
        raise SchemaValidationError("state must be a RetryState")
    if state.max_retries != plan.max_retries:
        raise SchemaValidationError("RetryState.max_retries must equal Plan.max_retries")
    if state.strong_escalated:
        if (
            plan.model_tier is not ModelTier.CHEAP
            or state.current_model_tier is not ModelTier.STRONG
        ):
            raise SchemaValidationError(
                "strong escalation must be the single CHEAP to STRONG transition"
            )
    elif state.current_model_tier is not plan.model_tier:
        raise SchemaValidationError(
            "current_model_tier must equal Plan.model_tier before escalation"
        )


def _terminal_state(state: RetryState) -> RetryState:
    return RetryState(
        retries_used=state.retries_used,
        max_retries=state.max_retries,
        current_model_tier=state.current_model_tier,
        strong_escalated=state.strong_escalated,
        terminal=True,
    )


def _retry_state(
    state: RetryState, *, escalate: bool = False
) -> RetryState:
    return RetryState(
        retries_used=state.retries_used + 1,
        max_retries=state.max_retries,
        current_model_tier=(
            ModelTier.STRONG if escalate else state.current_model_tier
        ),
        strong_escalated=state.strong_escalated or escalate,
        terminal=False,
    )


def build_retry_directive(
    plan: Plan,
    report: VerificationReport,
    state: RetryState,
) -> RetryDirective:
    """Emit one bounded directive without performing the directed work."""
    _validate_state(plan, state)
    if not isinstance(report, VerificationReport):
        raise TypeError(
            "report must be a VerificationReport; ExecutionResult failures bypass M8 retry classification"
        )
    if report.passed:
        return RetryDirective(
            action=RetryAction.PASS,
            failure_category=None,
            reason_code=None,
            next_state=_terminal_state(state),
            reason="verification passed",
        )

    category = report.failure_category
    reason_code = report.failure_reason
    if category is None or reason_code is None:
        raise RetryClassificationError(
            "failed VerificationReport requires a category and reason code"
        )
    policy = classify_failure(category, reason_code)

    if state.terminal:
        return RetryDirective(
            RetryAction.ABSTAIN,
            category,
            reason_code,
            _terminal_state(state),
            "retry state is terminal; another attempt is forbidden",
        )
    if policy.action is RetryAction.ABSTAIN:
        return RetryDirective(
            RetryAction.ABSTAIN,
            category,
            reason_code,
            _terminal_state(state),
            policy.reason,
        )
    if state.retries_used >= state.max_retries:
        return RetryDirective(
            RetryAction.ABSTAIN,
            category,
            reason_code,
            _terminal_state(state),
            "retry budget exhausted",
        )

    can_escalate = (
        policy.escalation_eligible
        and state.current_model_tier is ModelTier.CHEAP
        and not state.strong_escalated
    )
    if can_escalate:
        return RetryDirective(
            RetryAction.ESCALATE_STRONG,
            category,
            reason_code,
            _retry_state(state, escalate=True),
            "escalate CHEAP to STRONG for the next attempt; no model is invoked here",
        )

    if policy.action is RetryAction.RETRY_RETRIEVAL:
        reason = (
            f"{policy.reason}; keep the original Plan and exact metadata filters "
            "unchanged and invent no evidence requirement"
        )
    else:
        reason = (
            f"{policy.reason}; keep Plan and Evidence unchanged, expose no "
            "BindingMap values, and require M6 validation"
        )
    return RetryDirective(
        policy.action,
        category,
        reason_code,
        _retry_state(state),
        reason,
    )
