from dataclasses import fields, replace

import pytest

from src.supervisor.schemas import ModelTier, Plan
from src.understanding.schemas import SchemaValidationError
from src.verification.retry import (
    FAILURE_POLICY_BY_REASON,
    KNOWN_VERIFIER_REASON_CODES,
    RetryClassificationError,
    build_retry_directive,
    classify_failure,
)
from src.verification.schemas import (
    RetryAction,
    RetryDirective,
    RetryState,
    VerificationCheckResult,
    VerificationFailureCategory,
    VerificationReport,
)
from src.verification.verifier import verify
from tests.verification.helpers import failed_execution, make_request
from src.supervisor.schemas import EvidenceSource


def _report(category: VerificationFailureCategory, code: str) -> VerificationReport:
    return VerificationReport.create(
        [VerificationCheckResult("failure", category, False, code, ["subject"])]
    )


def _plan(*, max_retries: int, tier: ModelTier = ModelTier.CHEAP) -> Plan:
    request = make_request([EvidenceSource.TABLE])
    payload = request.plan.to_dict()
    payload["max_retries"] = max_retries
    payload["model_tier"] = tier.value
    return Plan.from_dict(payload)


def test_retry_contract_fields_and_round_trip():
    assert [item.name for item in fields(RetryState)] == [
        "retries_used",
        "max_retries",
        "current_model_tier",
        "strong_escalated",
        "terminal",
    ]
    assert [item.name for item in fields(RetryDirective)] == [
        "action",
        "failure_category",
        "reason_code",
        "next_state",
        "reason",
    ]
    state = RetryState(1, 2, ModelTier.STRONG, True, False)
    directive = RetryDirective(
        RetryAction.RETRY_PROGRAMMER,
        VerificationFailureCategory.NUMERIC,
        "OUTPUT_KIND_MISMATCH",
        state,
        "regenerate",
    )
    assert RetryState.from_dict(state.to_dict()) == state
    assert RetryDirective.from_dict(directive.to_dict()) == directive


@pytest.mark.parametrize(
    "state",
    [
        lambda: RetryState(-1, 1, ModelTier.CHEAP, False, False),
        lambda: RetryState(2, 1, ModelTier.CHEAP, False, False),
        lambda: RetryState(0, 1, ModelTier.CHEAP, True, False),
    ],
)
def test_retry_state_rejects_invalid_or_downgraded_shapes(state):
    with pytest.raises(SchemaValidationError):
        state()


def test_all_existing_reason_codes_have_explicit_category_specific_policies():
    expected = {
        VerificationFailureCategory.GROUNDING: {
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
            "TICKER_UNAVAILABLE",
            "TICKER_MISMATCH",
            "COMPANY_UNAVAILABLE",
            "COMPANY_MISMATCH",
            "REPORT_PROVENANCE_UNAVAILABLE",
            "REPORT_PROVENANCE_MISMATCH",
            "REPORT_YEAR_UNAVAILABLE",
            "REPORT_YEAR_MISMATCH",
            "STATEMENT_SCOPE_UNAVAILABLE",
            "STATEMENT_SCOPE_MISMATCH",
            "PERIOD_MISMATCH",
            "METRIC_MISMATCH",
            "TABLE_ID_MISMATCH",
            "TABLE_CLASS_UNAVAILABLE",
            "TABLE_CLASS_MISMATCH",
            "ROW_PATH_MISMATCH",
            "COLUMN_PATH_MISMATCH",
            "PARAGRAPH_PROVENANCE_UNAVAILABLE",
            "TEXT_SOURCE_SHAPE_INVALID",
            "TEXT_METRIC_PERIOD_MISMATCH",
            "LINK_PROVENANCE_MISSING",
        },
        VerificationFailureCategory.INSUFFICIENT_EVIDENCE: {
            "REQUIRED_TABLE_EVIDENCE_MISSING",
            "REQUIRED_TEXT_EVIDENCE_MISSING",
        },
        VerificationFailureCategory.NUMERIC: {
            "PROGRAM_ID_MISMATCH",
            "SUCCESS_OUTPUT_MISSING",
            "OUTPUT_KIND_MISMATCH",
            "SCALAR_VALUE_COUNT_MISMATCH",
            "ORDERED_VALUE_COUNT_MISMATCH",
            "INVALID_OUTPUT_DATUM",
            "INVALID_CANONICAL_DECIMAL",
            "INVALID_SCALE_STRUCTURE",
            "INVALID_UNIT_STRUCTURE",
            "ORDERED_VALUES_ORDER_MISMATCH",
        },
        VerificationFailureCategory.SCALE_UNIT: {
            "EVIDENCE_PROVENANCE_MISSING",
            "SCALE_UNIT_RESOLUTION_MISSING",
            "BINDING_PROVENANCE_MISMATCH",
            "SCALE_UNIT_UNRESOLVED",
            "SCALE_UNIT_AMBIGUOUS",
            "SCALE_UNIT_HINT_PROVENANCE_BROKEN",
            "SOURCE_SCALE_UNIT_MISMATCH",
            "BINDING_SOURCE_SCALE_UNIT_MISMATCH",
            "REQUESTED_SCALE_UNIT_MISMATCH",
            "DEFAULT_TO_RAW_FORBIDDEN",
            "INVALID_INPUT",
            "SOURCE_SCALE_REQUIRED",
            "UNSUPPORTED_SCALE_CONVERSION",
            "UNSUPPORTED_UNIT_CONVERSION",
            "UNSUPPORTED_SCALE_UNIT_CONVERSION",
            "OUTPUT_SCALE_UNIT_MISMATCH",
            "PERCENT_MAGNITUDE_MIX",
            "UNIT_MISMATCH",
            "FORMULA_SCALE_UNIT_MISMATCH",
        },
        VerificationFailureCategory.FINANCIAL_LOGIC: {
            "VERIFY_PROFILE_MISMATCH",
            "LIGHT_QUESTION_TYPE_INVALID",
            "LIGHT_REASONING_MODE_INVALID",
            "LIGHT_FORMULA_INVALID",
            "LIGHT_REQUIRED_REQUIREMENT_COUNT_INVALID",
            "QUESTION_TYPE_MISMATCH",
            "FORMULA_ID_MISMATCH",
            "FORMULA_REGISTRY_FINGERPRINT_MISMATCH",
            "OUTPUT_STEP_MISSING",
            "UNSUPPORTED_RATIO",
            "REASONING_MODE_MISMATCH",
            "INPUT_COUNT_ORDER_MISMATCH",
            "REQUIRED_METRIC_PERIOD_MISMATCH",
            "UNSUPPORTED_FINANCIAL_LOGIC",
            "PROGRAM_OPERATION_MISMATCH",
            "STEP_FORMULA_ID_MISMATCH",
            "PROGRAM_OUTPUT_KIND_MISMATCH",
            "FORMULA_INPUT_ORDER_MISMATCH",
            "FORMULA_NOT_REGISTERED",
            "FORMULA_CONTRACT_MISMATCH",
            "FORMULA_OPERATION_MISMATCH",
            "FORMULA_METRIC_ORDER_MISMATCH",
            "FORMULA_PERIOD_ORDER_MISMATCH",
            "FORMULA_PERIOD_COUNT_MISMATCH",
            "FORMULA_INPUT_COUNT_MISMATCH",
        },
    }
    assert {
        category: set(codes)
        for category, codes in KNOWN_VERIFIER_REASON_CODES.items()
    } == expected
    assert len(FAILURE_POLICY_BY_REASON) == sum(map(len, expected.values()))


@pytest.mark.parametrize(
    ("category", "code", "action"),
    [
        (VerificationFailureCategory.GROUNDING, "PERIOD_MISMATCH", RetryAction.RETRY_RETRIEVAL),
        (VerificationFailureCategory.GROUNDING, "TABLE_CLASS_UNAVAILABLE", RetryAction.ABSTAIN),
        (VerificationFailureCategory.INSUFFICIENT_EVIDENCE, "REQUIRED_TABLE_EVIDENCE_MISSING", RetryAction.RETRY_RETRIEVAL),
        (VerificationFailureCategory.NUMERIC, "OUTPUT_KIND_MISMATCH", RetryAction.RETRY_PROGRAMMER),
        (VerificationFailureCategory.NUMERIC, "INVALID_CANONICAL_DECIMAL", RetryAction.ABSTAIN),
        (VerificationFailureCategory.SCALE_UNIT, "SCALE_UNIT_AMBIGUOUS", RetryAction.RETRY_RETRIEVAL),
        (VerificationFailureCategory.SCALE_UNIT, "UNSUPPORTED_UNIT_CONVERSION", RetryAction.ABSTAIN),
        (VerificationFailureCategory.FINANCIAL_LOGIC, "PROGRAM_OPERATION_MISMATCH", RetryAction.RETRY_PROGRAMMER),
        (VerificationFailureCategory.FINANCIAL_LOGIC, "FORMULA_NOT_REGISTERED", RetryAction.ABSTAIN),
    ],
)
def test_failure_classification_depends_on_reason_code(category, code, action):
    assert classify_failure(category, code).action is action


def test_classifier_has_no_category_or_unknown_reason_fallback():
    with pytest.raises(RetryClassificationError):
        classify_failure(VerificationFailureCategory.GROUNDING, "NEW_CODE")
    with pytest.raises(RetryClassificationError):
        classify_failure(
            VerificationFailureCategory.NUMERIC,
            "REQUIRED_TABLE_EVIDENCE_MISSING",
        )


def test_zero_retry_budget_abstains_without_consuming_retry():
    plan = _plan(max_retries=0)
    directive = build_retry_directive(
        plan,
        _report(VerificationFailureCategory.GROUNDING, "PERIOD_MISMATCH"),
        RetryState.initial(plan),
    )
    assert directive.action is RetryAction.ABSTAIN
    assert directive.next_state.retries_used == 0
    assert directive.next_state.terminal
    assert directive.reason == "retry budget exhausted"


def test_one_retry_is_consumed_then_repeated_retrieval_failure_terminates():
    plan = _plan(max_retries=1)
    report = _report(
        VerificationFailureCategory.INSUFFICIENT_EVIDENCE,
        "REQUIRED_TABLE_EVIDENCE_MISSING",
    )
    first = build_retry_directive(plan, report, RetryState.initial(plan))
    assert first.action is RetryAction.RETRY_RETRIEVAL
    assert first.next_state.retries_used == 1
    assert not first.next_state.terminal
    assert "original Plan" in first.reason
    assert "metadata filters" in first.reason
    second = build_retry_directive(plan, report, first.next_state)
    assert second.action is RetryAction.ABSTAIN
    assert second.next_state.retries_used == 1
    assert second.next_state.terminal


def test_multiple_retrieval_retries_terminate_at_total_plan_budget():
    plan = _plan(max_retries=2)
    report = _report(VerificationFailureCategory.GROUNDING, "ROW_PATH_MISMATCH")
    state = RetryState.initial(plan)
    actions = []
    for _ in range(3):
        directive = build_retry_directive(plan, report, state)
        actions.append(directive.action)
        state = directive.next_state
    assert actions == [
        RetryAction.RETRY_RETRIEVAL,
        RetryAction.RETRY_RETRIEVAL,
        RetryAction.ABSTAIN,
    ]
    assert state.retries_used == 2
    assert state.terminal


def test_repeated_programmer_failure_at_strong_tier_is_bounded():
    plan = _plan(max_retries=2, tier=ModelTier.STRONG)
    report = _report(
        VerificationFailureCategory.FINANCIAL_LOGIC,
        "PROGRAM_OPERATION_MISMATCH",
    )
    state = RetryState.initial(plan)
    actions = []
    for _ in range(3):
        directive = build_retry_directive(plan, report, state)
        actions.append(directive.action)
        state = directive.next_state
    assert actions == [
        RetryAction.RETRY_PROGRAMMER,
        RetryAction.RETRY_PROGRAMMER,
        RetryAction.ABSTAIN,
    ]
    assert "BindingMap values" in build_retry_directive(
        plan, report, RetryState.initial(plan)
    ).reason
    assert "M6 validation" in build_retry_directive(
        plan, report, RetryState.initial(plan)
    ).reason


def test_cheap_to_strong_escalates_once_and_never_downgrades():
    plan = _plan(max_retries=2, tier=ModelTier.CHEAP)
    report = _report(VerificationFailureCategory.NUMERIC, "OUTPUT_KIND_MISMATCH")
    first = build_retry_directive(plan, report, RetryState.initial(plan))
    assert first.action is RetryAction.ESCALATE_STRONG
    assert first.next_state == RetryState(1, 2, ModelTier.STRONG, True, False)
    second = build_retry_directive(plan, report, first.next_state)
    assert second.action is RetryAction.RETRY_PROGRAMMER
    assert second.next_state.current_model_tier is ModelTier.STRONG
    assert second.next_state.strong_escalated
    third = build_retry_directive(plan, report, second.next_state)
    assert third.action is RetryAction.ABSTAIN
    assert third.next_state.current_model_tier is ModelTier.STRONG


def test_retrieval_failure_is_not_escalation_eligible():
    plan = _plan(max_retries=1, tier=ModelTier.CHEAP)
    directive = build_retry_directive(
        plan,
        _report(VerificationFailureCategory.GROUNDING, "METRIC_MISMATCH"),
        RetryState.initial(plan),
    )
    assert directive.action is RetryAction.RETRY_RETRIEVAL
    assert directive.next_state.current_model_tier is ModelTier.CHEAP
    assert not directive.next_state.strong_escalated


def test_permanent_failure_abstains_even_when_budget_remains():
    plan = _plan(max_retries=2)
    directive = build_retry_directive(
        plan,
        _report(
            VerificationFailureCategory.FINANCIAL_LOGIC,
            "FORMULA_NOT_REGISTERED",
        ),
        RetryState.initial(plan),
    )
    assert directive.action is RetryAction.ABSTAIN
    assert directive.next_state.retries_used == 0
    assert directive.next_state.terminal


def test_terminal_state_cannot_emit_another_retry():
    plan = _plan(max_retries=2)
    state = RetryState(0, 2, ModelTier.CHEAP, False, True)
    directive = build_retry_directive(
        plan,
        _report(VerificationFailureCategory.GROUNDING, "PERIOD_MISMATCH"),
        state,
    )
    assert directive.action is RetryAction.ABSTAIN
    assert directive.next_state == state


def test_pass_is_terminal_and_consumes_no_retry():
    plan = _plan(max_retries=2)
    state = RetryState(1, 2, ModelTier.CHEAP, False, False)
    directive = build_retry_directive(plan, VerificationReport.create([]), state)
    assert directive.action is RetryAction.PASS
    assert directive.failure_category is None
    assert directive.reason_code is None
    assert directive.next_state.retries_used == 1
    assert directive.next_state.terminal


def test_plan_state_budget_and_tier_must_stay_consistent():
    plan = _plan(max_retries=2)
    report = _report(VerificationFailureCategory.GROUNDING, "PERIOD_MISMATCH")
    with pytest.raises(SchemaValidationError):
        build_retry_directive(
            plan, report, RetryState(0, 1, ModelTier.CHEAP, False, False)
        )
    with pytest.raises(SchemaValidationError):
        build_retry_directive(
            plan, report, RetryState(0, 2, ModelTier.STRONG, False, False)
        )


def test_m8_verification_and_directive_emission_never_mutate_inputs():
    request = make_request([EvidenceSource.TABLE])
    before = {
        "plan": request.plan.to_dict(),
        "evidence": [item.to_dict() for item in request.evidence_items],
        "program": request.program.to_dict(),
        "execution": request.execution_result.to_dict(),
    }
    report = verify(request)
    assert report is not None and report.passed
    first = build_retry_directive(
        request.plan, report, RetryState.initial(request.plan)
    )
    second = build_retry_directive(
        request.plan, report, RetryState.initial(request.plan)
    )
    assert first == second
    assert before == {
        "plan": request.plan.to_dict(),
        "evidence": [item.to_dict() for item in request.evidence_items],
        "program": request.program.to_dict(),
        "execution": request.execution_result.to_dict(),
    }


def test_execution_failure_remains_typed_and_outside_retry_classifier():
    request = make_request(
        [EvidenceSource.TABLE], execution_result=failed_execution()
    )
    before = request.execution_result.to_dict()
    assert verify(request) is None
    with pytest.raises(TypeError, match="ExecutionResult failures bypass"):
        build_retry_directive(
            request.plan,
            None,  # type: ignore[arg-type]
            RetryState.initial(request.plan),
        )
    assert request.execution_result.to_dict() == before
