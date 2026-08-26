"""Deterministic M9 routing for native M7 execution failures."""

from __future__ import annotations

from typing import Mapping

from src.numeric.scale_conversion import ScaleConversionFailureCode
from src.orchestration.schemas import (
    ExecutionAction,
    ExecutionClassificationError,
    ExecutionDirective,
    next_execution_retry_state,
    terminal_retry_state,
    validate_execution_directive_transition,
)
from src.programmer.validator import ProgramValidationFailureCode
from src.sandbox.executor import ExecutionInfrastructureFailureCode
from src.sandbox.interpreter import InterpreterFailureCode
from src.sandbox.limits import ResourceLimitFailureCode
from src.sandbox.policy import ExecutionPolicyFailureCode
from src.sandbox.schemas import ExecutionFailureStage, ExecutionResult
from src.supervisor.schemas import Plan
from src.understanding.schemas import SchemaValidationError
from src.verification.schemas import RetryState


_POLICY_PROGRAMMER = frozenset(
    {
        ExecutionPolicyFailureCode.TOO_MANY_INPUTS.value,
        ExecutionPolicyFailureCode.TOO_MANY_STEPS.value,
        ExecutionPolicyFailureCode.TOO_MANY_STEP_INPUT_REFS.value,
        ExecutionPolicyFailureCode.PROGRAM_TOO_DEEP.value,
        ProgramValidationFailureCode.FORBIDDEN_OPERATION.value,
    }
)
_POLICY_PERMANENT = frozenset(
    {
        ExecutionPolicyFailureCode.INVALID_REQUEST.value,
        ExecutionPolicyFailureCode.LIMITS_PROFILE_MISMATCH.value,
    }
)
_ARITHMETIC_PROGRAMMER = frozenset(
    {
        InterpreterFailureCode.INVALID_FORMULA.value,
        InterpreterFailureCode.OUTPUT_KIND_MISMATCH.value,
    }
)
_ARITHMETIC_PERMANENT = frozenset(
    {
        InterpreterFailureCode.DIVISION_BY_ZERO.value,
        InterpreterFailureCode.DECIMAL_ARITHMETIC_ERROR.value,
    }
)
_RESOURCE_TRANSIENT = frozenset(
    {
        ResourceLimitFailureCode.WALL_CLOCK_TIMEOUT.value,
        ResourceLimitFailureCode.CPU_TIME_LIMIT.value,
        ResourceLimitFailureCode.MEMORY_LIMIT.value,
    }
)
_INFRASTRUCTURE_TRANSIENT = frozenset(
    {
        ExecutionInfrastructureFailureCode.WORKER_START_FAILED.value,
        ExecutionInfrastructureFailureCode.WORKER_CRASHED.value,
    }
)


def _classification_table() -> Mapping[tuple[ExecutionFailureStage, str], ExecutionAction]:
    table: dict[tuple[ExecutionFailureStage, str], ExecutionAction] = {}

    def register(stage, codes, action):
        for code in codes:
            key = (stage, code)
            if key in table:
                raise RuntimeError(f"duplicate execution routing policy: {key}")
            table[key] = action

    register(ExecutionFailureStage.POLICY, _POLICY_PROGRAMMER, ExecutionAction.RETRY_PROGRAMMER)
    register(ExecutionFailureStage.POLICY, _POLICY_PERMANENT, ExecutionAction.ABSTAIN)
    register(
        ExecutionFailureStage.VALIDATION,
        (item.value for item in ProgramValidationFailureCode),
        ExecutionAction.RETRY_PROGRAMMER,
    )
    from src.sandbox.policy import ExecutionBindingFailureCode

    register(
        ExecutionFailureStage.BINDING,
        (item.value for item in ExecutionBindingFailureCode),
        ExecutionAction.ABSTAIN,
    )
    register(
        ExecutionFailureStage.CONVERSION,
        (ScaleConversionFailureCode.SOURCE_SCALE_REQUIRED.value,),
        ExecutionAction.RETRY_RETRIEVAL,
    )
    register(
        ExecutionFailureStage.CONVERSION,
        (
            item.value
            for item in ScaleConversionFailureCode
            if item is not ScaleConversionFailureCode.SOURCE_SCALE_REQUIRED
        ),
        ExecutionAction.ABSTAIN,
    )
    register(
        ExecutionFailureStage.CONVERSION,
        (InterpreterFailureCode.INCOMPATIBLE_SCALES.value,),
        ExecutionAction.ABSTAIN,
    )
    register(ExecutionFailureStage.ARITHMETIC, _ARITHMETIC_PROGRAMMER, ExecutionAction.RETRY_PROGRAMMER)
    register(ExecutionFailureStage.ARITHMETIC, _ARITHMETIC_PERMANENT, ExecutionAction.ABSTAIN)
    register(ExecutionFailureStage.RESOURCE, _RESOURCE_TRANSIENT, ExecutionAction.RETRY_SANDBOX)
    register(
        ExecutionFailureStage.RESOURCE,
        (
            item.value
            for item in ResourceLimitFailureCode
            if item.value not in _RESOURCE_TRANSIENT
        ),
        ExecutionAction.ABSTAIN,
    )
    from src.sandbox.security import SecurityFailureCode

    register(
        ExecutionFailureStage.SECURITY,
        (item.value for item in SecurityFailureCode),
        ExecutionAction.ABSTAIN,
    )
    register(
        ExecutionFailureStage.INFRASTRUCTURE,
        _INFRASTRUCTURE_TRANSIENT,
        ExecutionAction.RETRY_SANDBOX,
    )
    register(
        ExecutionFailureStage.INFRASTRUCTURE,
        (
            item.value
            for item in ExecutionInfrastructureFailureCode
            if item.value not in _INFRASTRUCTURE_TRANSIENT
        ),
        ExecutionAction.ABSTAIN,
    )
    return table


EXECUTION_ACTION_BY_FAILURE = _classification_table()


def classify_execution_failure(
    stage: ExecutionFailureStage, code: str
) -> ExecutionAction:
    """Classify one exact native stage/code pair with no fallback."""
    if not isinstance(stage, ExecutionFailureStage):
        raise ExecutionClassificationError("unregistered execution failure stage")
    if not isinstance(code, str) or not code:
        raise ExecutionClassificationError("execution failure code must be non-empty")
    action = EXECUTION_ACTION_BY_FAILURE.get((stage, code))
    if action is None:
        raise ExecutionClassificationError(
            f"unregistered execution failure: {stage.value}/{code}"
        )
    return action


def build_execution_directive(
    plan: Plan,
    result: ExecutionResult,
    retry_state: RetryState,
) -> ExecutionDirective:
    """Build one bounded execution directive without rerunning a stage."""
    if not isinstance(plan, Plan):
        raise SchemaValidationError("plan must be an executable Plan")
    if not isinstance(result, ExecutionResult) or result.success or result.failure is None:
        raise SchemaValidationError("execution directive requires a failed ExecutionResult")
    if not isinstance(retry_state, RetryState):
        raise SchemaValidationError("retry_state must be a RetryState")
    if retry_state.terminal:
        raise SchemaValidationError("terminal retry state cannot transition")
    if retry_state.max_retries != plan.max_retries:
        raise SchemaValidationError("RetryState.max_retries must equal Plan.max_retries")

    failure = result.failure
    action = classify_execution_failure(failure.stage, failure.code)
    if action is not ExecutionAction.ABSTAIN and retry_state.retries_used >= retry_state.max_retries:
        action = ExecutionAction.ABSTAIN
        reason = "retry budget exhausted"
    elif action is ExecutionAction.ABSTAIN:
        reason = "execution failure is permanent under the explicit M9 policy"
    else:
        reason = f"execute {action.value} for {failure.stage.value}/{failure.code}"
    next_state = (
        terminal_retry_state(retry_state)
        if action is ExecutionAction.ABSTAIN
        else next_execution_retry_state(retry_state)
    )
    directive = ExecutionDirective(
        action=action,
        execution_failure_stage=failure.stage,
        execution_failure_code=failure.code,
        next_retry_state=next_state,
        reason=reason,
    )
    return validate_execution_directive_transition(plan, retry_state, directive)
