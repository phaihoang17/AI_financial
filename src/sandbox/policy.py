"""TASK-070 deterministic pre-dispatch policy for validated M6 Programs."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Mapping

from src.evidence.m5_schemas import BindingMap
from src.programmer.schemas import Program, ProgrammerInput, ProgramOperation
from src.programmer.validator import ProgramValidationError, validate_program
from src.sandbox.schemas import (
    EXECUTION_LIMITS_PROFILE_ID,
    EXECUTION_REQUEST_SCHEMA_VERSION,
    ExecutionFailure,
    ExecutionFailureStage,
    SandboxExecutionRequest,
)
from src.understanding.schemas import SchemaValidationError


MAX_PROGRAM_INPUTS = 256
MAX_PROGRAM_STEPS = 256
MAX_STEP_INPUT_REFS = 256
MAX_PROGRAM_DEPTH = 64
ALLOWED_OPERATIONS = frozenset(ProgramOperation)


class ExecutionPolicyFailureCode(str, Enum):
    INVALID_REQUEST = "INVALID_REQUEST"
    LIMITS_PROFILE_MISMATCH = "LIMITS_PROFILE_MISMATCH"
    TOO_MANY_INPUTS = "TOO_MANY_INPUTS"
    TOO_MANY_STEPS = "TOO_MANY_STEPS"
    TOO_MANY_STEP_INPUT_REFS = "TOO_MANY_STEP_INPUT_REFS"
    PROGRAM_TOO_DEEP = "PROGRAM_TOO_DEEP"


class ExecutionBindingFailureCode(str, Enum):
    INVALID_BINDING_MAP = "INVALID_BINDING_MAP"
    MISSING_BINDING = "MISSING_BINDING"
    EXTRA_BINDING = "EXTRA_BINDING"
    EXTRA_MASKED_EVIDENCE = "EXTRA_MASKED_EVIDENCE"
    DUPLICATE_PLACEHOLDER = "DUPLICATE_PLACEHOLDER"
    EVIDENCE_ID_MISMATCH = "EVIDENCE_ID_MISMATCH"
    UNKNOWN_PLACEHOLDER = "UNKNOWN_PLACEHOLDER"


class ExecutionPolicyError(SchemaValidationError):
    def __init__(self, failure: ExecutionFailure) -> None:
        self.failure = failure
        super().__init__(
            f"{failure.stage.value}:{failure.code}: {failure.message}"
        )


def _fail(stage: ExecutionFailureStage, code: str | Enum, message: str) -> None:
    normalized_code = code.value if isinstance(code, Enum) else code
    raise ExecutionPolicyError(
        ExecutionFailure(stage=stage, code=normalized_code, message=message)
    )


def _raw_structural_limits(program: Any) -> None:
    data: Any = program.to_dict() if isinstance(program, Program) else program
    if not isinstance(data, Mapping):
        return
    inputs = data.get("inputs")
    steps = data.get("steps")
    if isinstance(inputs, list) and len(inputs) > MAX_PROGRAM_INPUTS:
        _fail(
            ExecutionFailureStage.POLICY,
            ExecutionPolicyFailureCode.TOO_MANY_INPUTS,
            f"Program exceeds {MAX_PROGRAM_INPUTS} inputs",
        )
    if isinstance(steps, list) and len(steps) > MAX_PROGRAM_STEPS:
        _fail(
            ExecutionFailureStage.POLICY,
            ExecutionPolicyFailureCode.TOO_MANY_STEPS,
            f"Program exceeds {MAX_PROGRAM_STEPS} steps",
        )
    if isinstance(steps, list):
        for index, step in enumerate(steps):
            if not isinstance(step, Mapping):
                continue
            refs = step.get("input_refs")
            if isinstance(refs, list) and len(refs) > MAX_STEP_INPUT_REFS:
                _fail(
                    ExecutionFailureStage.POLICY,
                    ExecutionPolicyFailureCode.TOO_MANY_STEP_INPUT_REFS,
                    f"steps[{index}] exceeds {MAX_STEP_INPUT_REFS} input refs",
                )


def _validate_depth(program: Program) -> None:
    depths: Dict[str, int] = {item.input_id: 0 for item in program.inputs}
    for step in program.steps:
        depth = 1 + max(depths[ref] for ref in step.input_refs)
        if depth > MAX_PROGRAM_DEPTH:
            _fail(
                ExecutionFailureStage.POLICY,
                ExecutionPolicyFailureCode.PROGRAM_TOO_DEEP,
                f"Program exceeds depth {MAX_PROGRAM_DEPTH}",
            )
        depths[step.step_id] = depth


def _unique_placeholder_map(values: list[Any], *, source: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for item in values:
        existing = result.get(item.placeholder)
        if existing is not None:
            _fail(
                ExecutionFailureStage.BINDING,
                ExecutionBindingFailureCode.DUPLICATE_PLACEHOLDER,
                f"{source} duplicates placeholder {item.placeholder}",
            )
        result[item.placeholder] = item.evidence_id
    return result


def _validate_binding_bijection(request: SandboxExecutionRequest) -> None:
    program = _unique_placeholder_map(request.program.inputs, source="Program")
    masked = _unique_placeholder_map(
        request.programmer_input.masked_evidence.items,
        source="MaskedEvidenceBundle",
    )
    bindings = _unique_placeholder_map(
        request.binding_map.bindings,
        source="BindingMap",
    )

    for placeholder in program:
        if placeholder not in bindings:
            _fail(
                ExecutionFailureStage.BINDING,
                ExecutionBindingFailureCode.MISSING_BINDING,
                placeholder,
            )
    for placeholder in bindings:
        if placeholder not in program:
            _fail(
                ExecutionFailureStage.BINDING,
                ExecutionBindingFailureCode.EXTRA_BINDING,
                placeholder,
            )
    for placeholder in masked:
        if placeholder not in program:
            _fail(
                ExecutionFailureStage.BINDING,
                ExecutionBindingFailureCode.EXTRA_MASKED_EVIDENCE,
                placeholder,
            )
    for placeholder, evidence_id in program.items():
        if (
            masked.get(placeholder) != evidence_id
            or bindings.get(placeholder) != evidence_id
        ):
            _fail(
                ExecutionFailureStage.BINDING,
                ExecutionBindingFailureCode.EVIDENCE_ID_MISMATCH,
                placeholder,
            )


def _parse_request(value: Any) -> tuple[Any, ProgrammerInput, BindingMap, str, str]:
    if isinstance(value, SandboxExecutionRequest):
        return (
            value.program,
            value.programmer_input,
            value.binding_map,
            value.schema_version,
            value.limits_profile_id,
        )
    if not isinstance(value, Mapping):
        _fail(
            ExecutionFailureStage.POLICY,
            ExecutionPolicyFailureCode.INVALID_REQUEST,
            "SandboxExecutionRequest must be an object",
        )
    expected = {
        "schema_version",
        "program",
        "programmer_input",
        "binding_map",
        "limits_profile_id",
    }
    if set(value) != expected:
        _fail(
            ExecutionFailureStage.POLICY,
            ExecutionPolicyFailureCode.INVALID_REQUEST,
            "SandboxExecutionRequest must contain its exact schema",
        )
    try:
        programmer_input = ProgrammerInput.from_dict(value["programmer_input"])
    except SchemaValidationError as error:
        _fail(
            ExecutionFailureStage.VALIDATION,
            "INVALID_SCHEMA",
            str(error),
        )
    try:
        binding_map = BindingMap.from_dict(value["binding_map"])
    except SchemaValidationError as error:
        _fail(
            ExecutionFailureStage.BINDING,
            ExecutionBindingFailureCode.INVALID_BINDING_MAP,
            str(error),
        )
    return (
        value["program"],
        programmer_input,
        binding_map,
        value["schema_version"],
        value["limits_profile_id"],
    )


def validate_execution_request(
    value: SandboxExecutionRequest | Mapping[str, Any],
) -> SandboxExecutionRequest:
    """Return one policy-approved request without executing its Program."""
    program_value, programmer_input, binding_map, schema_version, limits_profile = (
        _parse_request(value)
    )
    if schema_version != EXECUTION_REQUEST_SCHEMA_VERSION:
        _fail(
            ExecutionFailureStage.POLICY,
            ExecutionPolicyFailureCode.INVALID_REQUEST,
            "unsupported execution request schema version",
        )
    if limits_profile != EXECUTION_LIMITS_PROFILE_ID:
        _fail(
            ExecutionFailureStage.POLICY,
            ExecutionPolicyFailureCode.LIMITS_PROFILE_MISMATCH,
            "unsupported limits profile",
        )

    _raw_structural_limits(program_value)
    try:
        program = validate_program(program_value, programmer_input)
    except ProgramValidationError as error:
        _fail(ExecutionFailureStage.VALIDATION, error.code, str(error))

    if any(step.operation not in ALLOWED_OPERATIONS for step in program.steps):
        _fail(
            ExecutionFailureStage.POLICY,
            "FORBIDDEN_OPERATION",
            "Program contains a non-allowlisted operation",
        )
    _validate_depth(program)
    request = SandboxExecutionRequest(
        schema_version=schema_version,
        program=program,
        programmer_input=programmer_input,
        binding_map=binding_map,
        limits_profile_id=limits_profile,
    )
    _validate_binding_bijection(request)
    return request
