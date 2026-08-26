from copy import deepcopy

import pytest

from src.evaluation.harness import EvaluationFailureStage
from src.evidence.schemas import CanonicalDecimal, Scale
from src.orchestration.schemas import (
    AnswerEvidenceReference,
    ExecutionAction,
    ExecutionClassificationError,
    ExecutionDirective,
    FailureAttribution,
    FinalAnswer,
    FinalResponse,
    FinalResponseStatus,
    make_plan_fingerprint,
    make_retrieval_policy_fingerprint,
    next_execution_retry_state,
    terminal_retry_state,
    validate_execution_directive_transition,
)
from src.programmer.schemas import ProgramOutputKind
from src.sandbox.schemas import (
    ExecutionDatum,
    ExecutionFailureStage,
    ExecutionOutput,
)
from src.supervisor.schemas import EvidenceSource
from src.understanding.schemas import SchemaValidationError
from src.verification.schemas import RetryState
from tests.orchestration.helpers import ARTIFACT_VERSIONS, approved_contracts


def test_fingerprints_are_deterministic_and_policy_covers_versions_and_top_k():
    _, _, _, plan, query, _, _ = approved_contracts()
    reversed_versions = dict(reversed(list(ARTIFACT_VERSIONS.items())))

    assert make_plan_fingerprint(plan) == make_plan_fingerprint(plan)
    first = make_retrieval_policy_fingerprint(
        plan, query, top_k=10, artifact_versions=ARTIFACT_VERSIONS
    )
    reordered = make_retrieval_policy_fingerprint(
        plan, query, top_k=10, artifact_versions=reversed_versions
    )
    changed_top_k = make_retrieval_policy_fingerprint(
        plan, query, top_k=11, artifact_versions=ARTIFACT_VERSIONS
    )
    changed_versions = make_retrieval_policy_fingerprint(
        plan,
        query,
        top_k=10,
        artifact_versions={**ARTIFACT_VERSIONS, "bm25": "different"},
    )

    assert first == reordered
    assert first != changed_top_k
    assert first != changed_versions


def test_retrieval_policy_rejects_hidden_filter_or_source_widening():
    _, _, _, plan, query, _, _ = approved_contracts()
    widened = deepcopy(query)
    widened.eligible_source_types.append(EvidenceSource.TEXT)

    with pytest.raises(SchemaValidationError, match="source eligibility"):
        make_retrieval_policy_fingerprint(
            plan,
            widened,
            top_k=10,
            artifact_versions=ARTIFACT_VERSIONS,
        )


def _retry_state(plan, *, retries_used=0, terminal=False):
    return RetryState(
        retries_used=retries_used,
        max_retries=plan.max_retries,
        current_model_tier=plan.model_tier,
        strong_escalated=False,
        terminal=terminal,
    )


def test_execution_directive_round_trip_preserves_failure_and_budget():
    _, _, _, plan, _, _, _ = approved_contracts()
    current = _retry_state(plan)
    directive = ExecutionDirective(
        action=ExecutionAction.RETRY_SANDBOX,
        execution_failure_stage=ExecutionFailureStage.ARITHMETIC,
        execution_failure_code="DIVISION_BY_ZERO",
        next_retry_state=next_execution_retry_state(current),
        reason="Retry the isolated sandbox only.",
    )

    restored = ExecutionDirective.from_dict(directive.to_dict())
    validated = validate_execution_directive_transition(plan, current, restored)

    assert validated == directive
    assert validated.execution_failure_stage is ExecutionFailureStage.ARITHMETIC
    assert validated.execution_failure_code == "DIVISION_BY_ZERO"
    assert validated.next_retry_state.retries_used == 1
    assert validated.next_retry_state.current_model_tier is plan.model_tier


def test_execution_directive_rejects_unknown_classification_and_free_retry():
    _, _, _, plan, _, _, _ = approved_contracts()
    current = _retry_state(plan)
    with pytest.raises(ExecutionClassificationError):
        ExecutionDirective(
            action=ExecutionAction.ABSTAIN,
            execution_failure_stage=ExecutionFailureStage.ARITHMETIC,
            execution_failure_code="UNKNOWN_CODE",
            next_retry_state=terminal_retry_state(current),
            reason="Unknown classification.",
        )
    unknown_stage = {
        "action": "ABSTAIN",
        "execution_failure_stage": "UNKNOWN_STAGE",
        "execution_failure_code": "UNKNOWN_CODE",
        "next_retry_state": terminal_retry_state(current).to_dict(),
        "reason": "Unknown classification.",
    }
    with pytest.raises(ExecutionClassificationError):
        ExecutionDirective.from_dict(unknown_stage)

    free_retry = ExecutionDirective(
        action=ExecutionAction.RETRY_PROGRAMMER,
        execution_failure_stage=ExecutionFailureStage.ARITHMETIC,
        execution_failure_code="INVALID_FORMULA",
        next_retry_state=current,
        reason="Invalid retry transition.",
    )
    with pytest.raises(SchemaValidationError, match="consume one retry"):
        validate_execution_directive_transition(plan, current, free_retry)


def test_execution_directive_rejects_terminal_and_exhausted_transitions():
    _, _, _, plan, _, _, _ = approved_contracts()
    exhausted = _retry_state(plan, retries_used=plan.max_retries)
    terminal = terminal_retry_state(exhausted)
    abstain = ExecutionDirective(
        action=ExecutionAction.ABSTAIN,
        execution_failure_stage=ExecutionFailureStage.ARITHMETIC,
        execution_failure_code="DIVISION_BY_ZERO",
        next_retry_state=terminal,
        reason="Budget exhausted.",
    )

    assert validate_execution_directive_transition(plan, exhausted, abstain)
    with pytest.raises(SchemaValidationError, match="terminal retry state"):
        validate_execution_directive_transition(plan, terminal, abstain)


def _final_answer(plan):
    output = ExecutionOutput(
        kind=ProgramOutputKind.SCALAR,
        values=[ExecutionDatum(CanonicalDecimal("12.5"), Scale.MILLION, None)],
    )
    evidence = AnswerEvidenceReference(
        evidence_id="evidence-1",
        requirement_id=plan.retrieval_requirements[0].requirement_id,
        source_type=EvidenceSource.TABLE,
        report_ref="report-1",
        page_ref="page-1",
        table_ref="table-1",
        paragraph_ref=None,
        metric="LNST",
        period="2015",
        row_path=("LNST",),
        column_path=("2015",),
    )
    return FinalAnswer(
        question_type=plan.question_type,
        target_metrics=tuple(plan.target_metrics),
        derived_target=plan.derived_target,
        formula_id=plan.formula_id,
        output=output,
        evidence=(evidence,),
    )


def test_final_response_pass_and_abstain_invariants():
    _, _, _, plan, _, _, _ = approved_contracts()
    terminal = terminal_retry_state(_retry_state(plan))
    answer = _final_answer(plan)
    passed = FinalResponse(
        status=FinalResponseStatus.PASS,
        answer=answer,
        reason_code=None,
        message="Verified answer.",
        retry_state=terminal,
        failure_attribution=None,
    )
    assert FinalResponse.from_dict(passed.to_dict()) == passed

    attribution = FailureAttribution(
        stage=EvaluationFailureStage.SANDBOX,
        code="DIVISION_BY_ZERO",
        reason="Execution failed.",
        attempt_index=0,
    )
    abstain = FinalResponse(
        status=FinalResponseStatus.ABSTAIN,
        answer=None,
        reason_code="DIVISION_BY_ZERO",
        message="No verified answer.",
        retry_state=terminal,
        failure_attribution=attribution,
    )
    assert FinalResponse.from_dict(abstain.to_dict()) == abstain

    with pytest.raises(SchemaValidationError, match="PASS requires answer"):
        FinalResponse(
            status=FinalResponseStatus.PASS,
            answer=None,
            reason_code=None,
            message="Invalid.",
            retry_state=terminal,
            failure_attribution=None,
        )
    with pytest.raises(SchemaValidationError, match="null answer"):
        FinalResponse(
            status=FinalResponseStatus.CLARIFICATION,
            answer=answer,
            reason_code="MISSING_COMPANY",
            message="Which company?",
            retry_state=None,
            failure_attribution=attribution,
        )
