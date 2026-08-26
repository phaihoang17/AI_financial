"""TASK-095/096 deterministic terminal response builders."""

from __future__ import annotations

from src.evaluation.harness import EvaluationFailureStage
from src.orchestration.schemas import (
    FailureAttribution,
    FinalResponse,
    FinalResponseStatus,
    M9Phase,
    terminal_retry_state,
)
from src.orchestration.state import M9State
from src.understanding.planning_gate import PlanningGate
from src.understanding.schemas import Operation, SchemaValidationError
from src.verification.schemas import RetryState


_FAILURE_STAGE_BY_PHASE = {
    M9Phase.NLU: EvaluationFailureStage.NLU,
    M9Phase.SUPERVISOR: EvaluationFailureStage.SUPERVISOR,
    M9Phase.RETRIEVAL: EvaluationFailureStage.RETRIEVAL,
    M9Phase.EVIDENCE: EvaluationFailureStage.EVIDENCE,
    M9Phase.PROGRAMMER: EvaluationFailureStage.PROGRAMMER,
    M9Phase.SANDBOX: EvaluationFailureStage.SANDBOX,
    M9Phase.VERIFICATION: EvaluationFailureStage.VERIFICATION,
}


def _relevant_identifiers(
    state: M9State,
    stage: EvaluationFailureStage,
    reason_code: str,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Project identifiers from the unrecovered terminal attempt only."""
    if not state.attempts:
        return (), (), ()
    attempt = state.attempts[-1]
    plan = None if state.supervisor_result is None else state.supervisor_result.plan
    requirement_order = (
        ()
        if plan is None
        else tuple(item.requirement_id for item in plan.retrieval_requirements)
    )
    evidence_order = tuple(item.evidence_id for item in attempt.evidence_items)
    program = (
        None
        if attempt.programmer_result is None
        else attempt.programmer_result.program
    )

    requirement_ids: set[str] = set()
    evidence_ids: set[str] = set()
    program_ids: set[str] = set()

    if stage is EvaluationFailureStage.EVIDENCE:
        completeness = attempt.evidence_completeness
        if completeness is not None:
            if reason_code == "EVIDENCE_INCOMPLETE":
                requirement_ids.update(completeness.missing_requirement_ids)
            else:
                requirement_ids.update(
                    item.requirement.requirement_id
                    for item in completeness.requirements
                    if item.evidence_ids
                )
        evidence_ids.update(evidence_order)
    elif stage is EvaluationFailureStage.PROGRAMMER:
        if attempt.programmer_input is not None:
            for item in attempt.programmer_input.masked_evidence.items:
                requirement_ids.add(item.requirement_id)
                evidence_ids.add(item.evidence_id)
    elif stage is EvaluationFailureStage.SANDBOX:
        if program is not None:
            program_ids.add(program.program_id)
            for item in program.inputs:
                requirement_ids.add(item.requirement_id)
                evidence_ids.add(item.evidence_id)
    elif stage is EvaluationFailureStage.VERIFICATION:
        if program is not None:
            program_ids.add(program.program_id)
        report = attempt.verification_report
        if report is not None:
            subject_ids = {
                subject
                for check in report.checks
                if not check.passed and check.reason_code == reason_code
                for subject in check.subject_ids
            }
            requirement_ids.update(subject_ids.intersection(requirement_order))
            evidence_ids.update(subject_ids.intersection(evidence_order))
            if program is not None and program.program_id in subject_ids:
                program_ids.add(program.program_id)

    return (
        tuple(item for item in requirement_order if item in requirement_ids),
        tuple(item for item in evidence_order if item in evidence_ids),
        (() if program is None or program.program_id not in program_ids else (program.program_id,)),
    )


def build_failure_response(
    state: M9State,
    *,
    status: FinalResponseStatus,
    originating_phase: M9Phase,
    reason_code: str,
    reason: str,
    retry_state: RetryState | None = None,
) -> FinalResponse:
    """Build a terminal clarification/abstain with typed origin attribution."""
    if status not in {FinalResponseStatus.CLARIFICATION, FinalResponseStatus.ABSTAIN}:
        raise SchemaValidationError("failure response must be CLARIFICATION or ABSTAIN")
    stage = _FAILURE_STAGE_BY_PHASE.get(originating_phase)
    if stage is None:
        raise SchemaValidationError("originating phase is not a failure stage")
    current_retry = state.retry_state if retry_state is None else retry_state
    terminal_retry = None
    if current_retry is not None:
        terminal_retry = (
            current_retry
            if current_retry.terminal
            else terminal_retry_state(current_retry)
        )
    attempt_index = state.attempts[-1].attempt_index if state.attempts else None
    requirement_ids, evidence_ids, program_ids = _relevant_identifiers(
        state, stage, reason_code
    )
    attribution = FailureAttribution(
        stage,
        reason_code,
        reason,
        attempt_index,
        requirement_ids,
        evidence_ids,
        program_ids,
    )
    return FinalResponse(
        status=status,
        answer=None,
        reason_code=reason_code,
        message=reason,
        retry_state=terminal_retry,
        failure_attribution=attribution,
    )


def build_planning_gate_response(state: M9State, gate: PlanningGate) -> FinalResponse:
    """Clarify only from the gate's existing actionable findings."""
    if gate.allowed:
        raise SchemaValidationError("allowed PlanningGate is not terminal")
    findings = [
        *(f"MISSING_{item.value}" for item in gate.missing_information),
        *(f"AMBIGUOUS_{item.value}" for item in gate.ambiguities),
    ]
    if findings:
        code = "+".join(findings)
        reason = "Clarification required for existing NLU findings: " + ", ".join(findings)
        status = FinalResponseStatus.CLARIFICATION
    else:
        understanding = state.query_understanding
        code = (
            "UNKNOWN_OPERATION"
            if understanding is not None and understanding.operation is Operation.UNKNOWN
            else "PLANNING_GATE_BLOCKED"
        )
        reason = "PlanningGate blocked the request without an actionable identity finding."
        status = FinalResponseStatus.ABSTAIN
    return build_failure_response(
        state,
        status=status,
        originating_phase=M9Phase.NLU,
        reason_code=code,
        reason=reason,
    )
