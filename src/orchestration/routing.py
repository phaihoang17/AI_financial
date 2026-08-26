"""Bounded retry execution and terminal orchestration routing."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional

from src.orchestration.answer_builder import build_pass_response
from src.orchestration.nodes import (
    OrchestrationDependencies,
    StageTransition,
    run_evidence,
    run_nlu,
    run_programmer,
    run_retrieval,
    run_sandbox,
    run_supervisor,
    run_verification,
)
from src.orchestration.execution_routing import build_execution_directive
from src.orchestration.schemas import (
    ExecutionAction,
    ExecutionClassificationError,
    FinalResponseStatus,
    M9Phase,
)
from src.orchestration.state import AttemptEntryStage, AttemptRecord, M9State
from src.orchestration.terminal import (
    build_failure_response,
    build_planning_gate_response,
)
from src.understanding.schemas import SchemaValidationError
from src.verification.retry import RetryClassificationError, build_retry_directive
from src.verification.schemas import RetryAction, RetryDirective, RetryState


@dataclass(frozen=True)
class RoutingTransition:
    state: M9State
    next_node: str


def _replace_current_attempt(state: M9State, **updates) -> M9State:
    if state.terminal or not state.attempts:
        raise SchemaValidationError("current attempt update requires active state")
    current = replace(state.attempts[-1], **updates)
    return replace(state, attempts=(*state.attempts[:-1], current))


def _terminal_failure(
    state: M9State,
    *,
    phase: M9Phase,
    code: str,
    reason: str,
    retry_state: Optional[RetryState] = None,
    status: FinalResponseStatus = FinalResponseStatus.ABSTAIN,
) -> RoutingTransition:
    response = build_failure_response(
        state,
        status=status,
        originating_phase=phase,
        reason_code=code,
        reason=reason,
        retry_state=retry_state,
    )
    return RoutingTransition(state.terminate(response), "end")


def _terminalize_stage_stop(transition: StageTransition) -> RoutingTransition:
    state = transition.state
    if transition.failure_kind == "PLANNING_GATE":
        assert state.planning_gate is not None
        return RoutingTransition(
            state.terminate(build_planning_gate_response(state, state.planning_gate)),
            "end",
        )
    if transition.failure_kind == "SUPERVISOR_RESULT":
        result = state.supervisor_result
        assert result is not None
        code = (
            "SUPERVISOR_ABSTAIN"
            if result.abstain_reason is None
            else result.abstain_reason.value
        )
        return _terminal_failure(
            state,
            phase=M9Phase.SUPERVISOR,
            code=code,
            reason=transition.failure_message or "Supervisor abstained.",
        )
    return _terminal_failure(
        state,
        phase=state.phase,
        code=transition.failure_code or "STAGE_FAILED",
        reason=transition.failure_message or "Stage failed.",
    )


def _retry_attempt(
    previous: AttemptRecord,
    *,
    entry_stage: AttemptEntryStage,
    retry_state: RetryState,
) -> AttemptRecord:
    base = {
        "attempt_index": previous.attempt_index + 1,
        "entry_stage": entry_stage,
        "model_tier": retry_state.current_model_tier,
        "retrieval_query": previous.retrieval_query,
    }
    if entry_stage in {AttemptEntryStage.PROGRAMMER, AttemptEntryStage.SANDBOX}:
        base.update(
            {
                "retrieval_candidates": previous.retrieval_candidates,
                "scale_hints_by_candidate": previous.scale_hints_by_candidate,
                "cell_locations": previous.cell_locations,
                "evidence_items": previous.evidence_items,
                "evidence_completeness": previous.evidence_completeness,
                "scale_unit_resolutions": previous.scale_unit_resolutions,
                "schema_links": previous.schema_links,
                "masked_evidence": previous.masked_evidence,
                "binding_ref": previous.binding_ref,
                "programmer_input": previous.programmer_input,
            }
        )
    if entry_stage is AttemptEntryStage.SANDBOX:
        base["programmer_result"] = previous.programmer_result
    return AttemptRecord(**base)


def _append_retry(
    state: M9State,
    *,
    entry_stage: AttemptEntryStage,
    retry_state: RetryState,
) -> RoutingTransition:
    attempt = _retry_attempt(
        state.attempts[-1], entry_stage=entry_stage, retry_state=retry_state
    )
    next_state = state.append_attempt(attempt, next_retry_state=retry_state)
    return RoutingTransition(next_state, entry_stage.value.lower())


def route_nlu(
    state: M9State, deps: OrchestrationDependencies
) -> RoutingTransition:
    transition = run_nlu(state, deps)
    if transition.stop:
        return _terminalize_stage_stop(transition)
    return RoutingTransition(transition.state, "supervisor")


def route_supervisor(
    state: M9State, deps: OrchestrationDependencies
) -> RoutingTransition:
    transition = run_supervisor(state, deps)
    if transition.stop:
        return _terminalize_stage_stop(transition)
    return RoutingTransition(transition.state, "retrieval")


def route_retrieval(
    state: M9State, deps: OrchestrationDependencies
) -> RoutingTransition:
    transition = run_retrieval(state, deps)
    if transition.stop:
        return _terminalize_stage_stop(transition)
    return RoutingTransition(transition.state, "evidence")


def route_evidence(
    state: M9State, deps: OrchestrationDependencies
) -> RoutingTransition:
    transition = run_evidence(state, deps)
    if transition.stop:
        return _terminalize_stage_stop(transition)
    return RoutingTransition(transition.state, "programmer")


def route_programmer(
    state: M9State, deps: OrchestrationDependencies
) -> RoutingTransition:
    transition = run_programmer(state, deps)
    if transition.stop:
        return _terminalize_stage_stop(transition)
    return RoutingTransition(transition.state, "sandbox")


def _execute_execution_directive(
    state: M9State, directive
) -> RoutingTransition:
    state = _replace_current_attempt(state, execution_directive=directive)
    if directive.action is ExecutionAction.ABSTAIN:
        return _terminal_failure(
            state,
            phase=M9Phase.SANDBOX,
            code=directive.execution_failure_code,
            reason=directive.reason,
            retry_state=directive.next_retry_state,
        )
    entry = {
        ExecutionAction.RETRY_RETRIEVAL: AttemptEntryStage.RETRIEVAL,
        ExecutionAction.RETRY_PROGRAMMER: AttemptEntryStage.PROGRAMMER,
        ExecutionAction.RETRY_SANDBOX: AttemptEntryStage.SANDBOX,
    }[directive.action]
    return _append_retry(
        state, entry_stage=entry, retry_state=directive.next_retry_state
    )


def route_sandbox(
    state: M9State, deps: OrchestrationDependencies
) -> RoutingTransition:
    transition = run_sandbox(state, deps)
    if not transition.stop:
        return RoutingTransition(transition.state, "verification")
    current = transition.state
    attempt = current.attempts[-1] if current.attempts else None
    if (
        transition.failure_kind != "EXECUTION_RESULT"
        or attempt is None
        or attempt.execution_result is None
    ):
        return _terminalize_stage_stop(transition)
    plan = current.supervisor_result.plan
    assert plan is not None and current.retry_state is not None
    try:
        directive = build_execution_directive(
            plan, attempt.execution_result, current.retry_state
        )
    except ExecutionClassificationError as error:
        failure = attempt.execution_result.failure
        assert failure is not None
        return _terminal_failure(
            current,
            phase=M9Phase.SANDBOX,
            code=failure.code,
            reason=str(error),
        )
    return _execute_execution_directive(current, directive)


def _execute_retry_directive(
    state: M9State, directive: RetryDirective
) -> RoutingTransition:
    state = _replace_current_attempt(state, retry_directive=directive)
    if directive.action is RetryAction.PASS:
        return RoutingTransition(replace(state, phase=M9Phase.ANSWER), "answer")
    if directive.action is RetryAction.ABSTAIN:
        return _terminal_failure(
            state,
            phase=M9Phase.VERIFICATION,
            code=directive.reason_code or "VERIFICATION_FAILED",
            reason=directive.reason,
            retry_state=directive.next_state,
        )
    entry = (
        AttemptEntryStage.RETRIEVAL
        if directive.action is RetryAction.RETRY_RETRIEVAL
        else AttemptEntryStage.PROGRAMMER
    )
    return _append_retry(
        state, entry_stage=entry, retry_state=directive.next_state
    )


def route_verification(
    state: M9State, deps: OrchestrationDependencies
) -> RoutingTransition:
    transition = run_verification(state, deps)
    attempt = transition.state.attempts[-1]
    if attempt.verification_report is None:
        return _terminalize_stage_stop(transition)
    plan = transition.state.supervisor_result.plan
    assert plan is not None and transition.state.retry_state is not None
    try:
        directive = build_retry_directive(
            plan, attempt.verification_report, transition.state.retry_state
        )
    except RetryClassificationError as error:
        failure_code = attempt.verification_report.failure_reason
        assert failure_code is not None
        return _terminal_failure(
            transition.state,
            phase=M9Phase.VERIFICATION,
            code=failure_code,
            reason=str(error),
        )
    return _execute_retry_directive(transition.state, directive)


def route_answer(
    state: M9State, deps: OrchestrationDependencies
) -> RoutingTransition:
    del deps
    attempt = state.attempts[-1]
    plan = state.supervisor_result.plan
    assert plan is not None
    assert attempt.programmer_result is not None
    assert attempt.programmer_result.program is not None
    assert attempt.execution_result is not None
    assert attempt.verification_report is not None
    assert attempt.retry_directive is not None
    try:
        response = build_pass_response(
            plan=plan,
            program=attempt.programmer_result.program,
            execution_result=attempt.execution_result,
            verification_report=attempt.verification_report,
            retry_directive=attempt.retry_directive,
            evidence_items=attempt.evidence_items,
            schema_links=attempt.schema_links,
        )
    except SchemaValidationError as error:
        return _terminal_failure(
            state,
            phase=M9Phase.VERIFICATION,
            code="ANSWER_BUILD_FAILED",
            reason=str(error),
            retry_state=attempt.retry_directive.next_state,
        )
    return RoutingTransition(state.terminate(response), "end")


# Deprecated: Batch 3 implementation-history names. Use RoutingTransition and
# the route_* functions above. Kept as identity aliases only; no duplicate
# implementation.
Batch3Transition = RoutingTransition
run_batch3_answer = route_answer
run_batch3_evidence = route_evidence
run_batch3_nlu = route_nlu
run_batch3_programmer = route_programmer
run_batch3_retrieval = route_retrieval
run_batch3_sandbox = route_sandbox
run_batch3_supervisor = route_supervisor
run_batch3_verification = route_verification
