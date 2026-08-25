"""Trusted Decimal-only interpreter for the validated M6 Program DSL."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, DecimalException, localcontext
from enum import Enum
from time import monotonic_ns
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple, Union

from src.evidence.m5_schemas import ValueBinding
from src.evidence.schemas import CanonicalDecimal, Scale
from src.formulas.schemas import FormulaImplementation, FormulaImplementationKind
from src.numeric.scale_conversion import (
    ScaleConversionFailureCode,
    ScaleConversionStatus,
    convert_canonical_scale,
)
from src.programmer.schemas import ProgramOperation, ProgramOutputKind
from src.sandbox.decimal_policy import (
    make_execution_decimal_context,
    serialize_execution_decimal,
)
from src.sandbox.policy import ExecutionPolicyError, validate_execution_request
from src.sandbox.schemas import (
    EXECUTION_RESULT_SCHEMA_VERSION,
    ExecutionDatum,
    ExecutionFailure,
    ExecutionFailureStage,
    ExecutionOutput,
    ExecutionResult,
    SandboxExecutionRequest,
)
from src.supervisor.formula_registry import get_formula_implementation
from src.understanding.requested_scale_unit_parser import RequestedScale


class InterpreterFailureCode(str, Enum):
    INCOMPATIBLE_SCALES = "INCOMPATIBLE_SCALES"
    INVALID_FORMULA = "INVALID_FORMULA"
    DIVISION_BY_ZERO = "DIVISION_BY_ZERO"
    DECIMAL_ARITHMETIC_ERROR = "DECIMAL_ARITHMETIC_ERROR"
    OUTPUT_KIND_MISMATCH = "OUTPUT_KIND_MISMATCH"


@dataclass(frozen=True)
class _RuntimeDatum:
    decimal_value: Decimal
    canonical_value: CanonicalDecimal
    scale: Optional[Scale]
    unit: Optional[str]
    requested_scale: Optional[RequestedScale]
    requested_unit: Optional[str]


_RuntimeValue = Union[_RuntimeDatum, Tuple[_RuntimeDatum, ...]]
_MAGNITUDE_SCALES = frozenset(
    {Scale.RAW, Scale.THOUSAND, Scale.MILLION, Scale.BILLION}
)


class _InterpreterError(Exception):
    def __init__(self, failure: ExecutionFailure) -> None:
        self.failure = failure
        super().__init__(f"{failure.stage.value}:{failure.code}: {failure.message}")


def _elapsed_ms(start_ns: int) -> int:
    return max(0, (monotonic_ns() - start_ns) // 1_000_000)


def _program_id(value: Any) -> str:
    if isinstance(value, SandboxExecutionRequest):
        return value.program.program_id
    if isinstance(value, Mapping):
        program = value.get("program")
        if isinstance(program, Mapping):
            candidate = program.get("program_id")
            if isinstance(candidate, str) and candidate:
                return candidate
    return "unavailable"


def failure_result(
    program_id: str,
    failure: ExecutionFailure,
    *,
    execution_ms: int,
) -> ExecutionResult:
    return ExecutionResult(
        schema_version=EXECUTION_RESULT_SCHEMA_VERSION,
        program_id=program_id,
        success=False,
        output=None,
        failure=failure,
        execution_ms=execution_ms,
    )


def _fail(stage: ExecutionFailureStage, code: str | Enum, message: str) -> None:
    normalized_code = code.value if isinstance(code, Enum) else code
    raise _InterpreterError(
        ExecutionFailure(stage=stage, code=normalized_code, message=message)
    )


def _runtime_datum(binding: ValueBinding) -> _RuntimeDatum:
    return _RuntimeDatum(
        decimal_value=Decimal(binding.value),
        canonical_value=binding.value,
        scale=binding.source_scale,
        unit=binding.source_unit,
        requested_scale=binding.requested_output_scale,
        requested_unit=binding.requested_output_unit,
    )


def _converted(
    datum: _RuntimeDatum,
    *,
    output_scale: Optional[Scale | RequestedScale],
    output_unit: Optional[str],
) -> _RuntimeDatum:
    result = convert_canonical_scale(
        datum.canonical_value,
        source_scale=datum.scale,
        source_unit=datum.unit,
        requested_output_scale=output_scale,
        requested_output_unit=output_unit,
    )
    if result.status is ScaleConversionStatus.REJECTED:
        _fail(
            ExecutionFailureStage.CONVERSION,
            result.failure_code or ScaleConversionFailureCode.INVALID_INPUT,
            result.failure_message or "scale conversion rejected",
        )
    return _RuntimeDatum(
        decimal_value=Decimal(result.canonical_value),
        canonical_value=result.canonical_value,
        scale=result.output_scale,
        unit=result.output_unit,
        requested_scale=None,
        requested_unit=None,
    )


def _apply_requested_conversion(datum: _RuntimeDatum) -> _RuntimeDatum:
    return _converted(
        datum,
        output_scale=datum.requested_scale,
        output_unit=datum.requested_unit,
    )


def _require_scalars(values: Sequence[_RuntimeValue], operation: str) -> list[_RuntimeDatum]:
    if any(not isinstance(value, _RuntimeDatum) for value in values):
        _fail(
            ExecutionFailureStage.ARITHMETIC,
            InterpreterFailureCode.OUTPUT_KIND_MISMATCH,
            f"{operation} requires scalar inputs",
        )
    return [value for value in values if isinstance(value, _RuntimeDatum)]


def _compatible_unit(values: Sequence[_RuntimeDatum]) -> Optional[str]:
    source_units = [value.unit for value in values]
    present_source_units = {unit for unit in source_units if unit is not None}
    if (present_source_units and any(unit is None for unit in source_units)) or len(
        present_source_units
    ) > 1:
        _fail(
            ExecutionFailureStage.CONVERSION,
            ScaleConversionFailureCode.UNSUPPORTED_UNIT_CONVERSION,
            "formula inputs must use exactly compatible units",
        )
    source_unit = next(iter(present_source_units), None)

    requested_units = [value.requested_unit for value in values]
    present_requested_units = {
        unit for unit in requested_units if unit is not None
    }
    if (
        present_requested_units
        and any(unit is None for unit in requested_units)
    ) or len(present_requested_units) > 1:
        _fail(
            ExecutionFailureStage.CONVERSION,
            ScaleConversionFailureCode.UNSUPPORTED_UNIT_CONVERSION,
            "requested output units must agree exactly",
        )
    requested_unit = next(iter(present_requested_units), None)
    if requested_unit is not None and requested_unit != source_unit:
        _fail(
            ExecutionFailureStage.CONVERSION,
            ScaleConversionFailureCode.UNSUPPORTED_UNIT_CONVERSION,
            "requested output unit must equal the source unit",
        )
    return source_unit


def _scale_category(values: Sequence[_RuntimeDatum]) -> str:
    scales = [value.scale for value in values]
    if any(scale is None for scale in scales):
        _fail(
            ExecutionFailureStage.CONVERSION,
            ScaleConversionFailureCode.SOURCE_SCALE_REQUIRED,
            "formula inputs require resolved source scales",
        )
    if all(scale is Scale.PERCENT for scale in scales):
        return "PERCENT"
    if all(scale in _MAGNITUDE_SCALES for scale in scales):
        return "MAGNITUDE"
    _fail(
        ExecutionFailureStage.CONVERSION,
        InterpreterFailureCode.INCOMPATIBLE_SCALES,
        "percent and magnitude scales cannot be mixed",
    )


def _normalize(
    datum: _RuntimeDatum, target_scale: Scale, target_unit: Optional[str]
) -> _RuntimeDatum:
    return _converted(
        datum,
        output_scale=target_scale,
        output_unit=target_unit,
    )


def _growth(
    values: Sequence[_RuntimeDatum], implementation: FormulaImplementation
) -> _RuntimeDatum:
    if len(values) != 2:
        _fail(
            ExecutionFailureStage.ARITHMETIC,
            InterpreterFailureCode.INVALID_FORMULA,
            "GROWTH_RATE requires previous and current",
        )
    unit = _compatible_unit(values)
    category = _scale_category(values)
    requested_scales = [value.requested_scale for value in values]
    if any(
        scale is not None and scale is not RequestedScale.PERCENT
        for scale in requested_scales
    ):
        _fail(
            ExecutionFailureStage.CONVERSION,
            ScaleConversionFailureCode.UNSUPPORTED_SCALE_CONVERSION,
            "GROWTH_RATE output scale is PERCENT",
        )

    if category == "MAGNITUDE":
        operands = [_normalize(value, Scale.RAW, unit) for value in values]
    else:
        operands = list(values)
    previous, current = (operand.decimal_value for operand in operands)
    if previous.is_zero():
        _fail(
            ExecutionFailureStage.ARITHMETIC,
            InterpreterFailureCode.DIVISION_BY_ZERO,
            "GROWTH_RATE previous value cannot be zero",
        )
    if implementation.constants != ["100"]:
        _fail(
            ExecutionFailureStage.ARITHMETIC,
            InterpreterFailureCode.INVALID_FORMULA,
            "GROWTH_RATE implementation is unavailable",
        )
    constant = Decimal(implementation.constants[0])
    result = (current - previous) / previous * constant
    canonical = serialize_execution_decimal(result)
    return _RuntimeDatum(
        decimal_value=result,
        canonical_value=canonical,
        scale=Scale.PERCENT,
        unit=None,
        requested_scale=None,
        requested_unit=None,
    )


def _average(values: Sequence[_RuntimeDatum]) -> _RuntimeDatum:
    unit = _compatible_unit(values)
    category = _scale_category(values)
    if category == "PERCENT":
        if any(
            value.requested_scale not in (None, RequestedScale.PERCENT)
            for value in values
        ):
            _fail(
                ExecutionFailureStage.CONVERSION,
                InterpreterFailureCode.INCOMPATIBLE_SCALES,
                "percent inputs cannot request a magnitude output scale",
            )
        operands = list(values)
        target_scale = Scale.PERCENT
    else:
        if any(value.requested_scale is RequestedScale.PERCENT for value in values):
            _fail(
                ExecutionFailureStage.CONVERSION,
                InterpreterFailureCode.INCOMPATIBLE_SCALES,
                "magnitude inputs cannot request PERCENT output",
            )
        requested = [value.requested_scale for value in values]
        target_scale = Scale.RAW
        if all(scale is not None for scale in requested) and len(set(requested)) == 1:
            target_scale = Scale(requested[0].value)
        operands = [_normalize(value, target_scale, unit) for value in values]

    total = sum(
        (operand.decimal_value for operand in operands),
        start=Decimal("0"),
    )
    result = total / Decimal(len(operands))
    canonical = serialize_execution_decimal(result)
    return _RuntimeDatum(
        decimal_value=result,
        canonical_value=canonical,
        scale=target_scale,
        unit=unit,
        requested_scale=None,
        requested_unit=None,
    )


def _apply_formula(formula_id: Optional[str], values: Sequence[_RuntimeDatum]) -> _RuntimeDatum:
    if formula_id is None:
        _fail(
            ExecutionFailureStage.ARITHMETIC,
            InterpreterFailureCode.INVALID_FORMULA,
            "formula_id is required",
        )
    implementation = get_formula_implementation(formula_id)
    if implementation is None:
        _fail(
            ExecutionFailureStage.ARITHMETIC,
            InterpreterFailureCode.INVALID_FORMULA,
            f"formula is not registered: {formula_id}",
        )
    if implementation.implementation_kind is FormulaImplementationKind.PERCENT_GROWTH:
        return _growth(values, implementation)
    if implementation.implementation_kind is FormulaImplementationKind.ARITHMETIC_MEAN:
        return _average(values)
    _fail(
        ExecutionFailureStage.ARITHMETIC,
        InterpreterFailureCode.INVALID_FORMULA,
        f"formula implementation is unsupported: {formula_id}",
    )


def _output(program_kind: ProgramOutputKind, value: _RuntimeValue) -> ExecutionOutput:
    if program_kind is ProgramOutputKind.SCALAR:
        if not isinstance(value, _RuntimeDatum):
            _fail(
                ExecutionFailureStage.ARITHMETIC,
                InterpreterFailureCode.OUTPUT_KIND_MISMATCH,
                "SCALAR output resolved to ordered values",
            )
        values = [value]
    else:
        if not isinstance(value, tuple) or not value:
            _fail(
                ExecutionFailureStage.ARITHMETIC,
                InterpreterFailureCode.OUTPUT_KIND_MISMATCH,
                "ORDERED_VALUES output did not resolve to an ordered collection",
            )
        values = list(value)
    return ExecutionOutput(
        kind=program_kind,
        values=[
            ExecutionDatum(
                value=item.canonical_value,
                scale=item.scale,
                unit=item.unit,
            )
            for item in values
        ],
    )


def _interpret(request: SandboxExecutionRequest) -> ExecutionOutput:
    bindings = {
        binding.placeholder: binding for binding in request.binding_map.bindings
    }
    state: Dict[str, _RuntimeValue] = {
        item.input_id: _runtime_datum(bindings[item.placeholder])
        for item in request.program.inputs
    }

    with localcontext(make_execution_decimal_context()):
        for step in request.program.steps:
            referenced = [state[ref] for ref in step.input_refs]
            if step.operation is ProgramOperation.IDENTITY:
                scalar = _require_scalars(referenced, "IDENTITY")[0]
                state[step.step_id] = _apply_requested_conversion(scalar)
            elif step.operation is ProgramOperation.COLLECT:
                scalars = _require_scalars(referenced, "COLLECT")
                state[step.step_id] = tuple(
                    _apply_requested_conversion(item) for item in scalars
                )
            elif step.operation is ProgramOperation.APPLY_REGISTERED_FORMULA:
                scalars = _require_scalars(
                    referenced, "APPLY_REGISTERED_FORMULA"
                )
                state[step.step_id] = _apply_formula(step.formula_id, scalars)
            else:
                _fail(
                    ExecutionFailureStage.VALIDATION,
                    "FORBIDDEN_OPERATION",
                    "Program contains a non-allowlisted operation",
                )
    return _output(request.program.output_kind, state[request.program.output_ref])


def interpret_execution_request(
    value: SandboxExecutionRequest | Mapping[str, Any],
) -> ExecutionResult:
    """Revalidate and interpret one request in the current trusted worker."""
    start_ns = monotonic_ns()
    program_id = _program_id(value)
    try:
        request = validate_execution_request(value)
        program_id = request.program.program_id
        output = _interpret(request)
        return ExecutionResult(
            schema_version=EXECUTION_RESULT_SCHEMA_VERSION,
            program_id=program_id,
            success=True,
            output=output,
            failure=None,
            execution_ms=_elapsed_ms(start_ns),
        )
    except ExecutionPolicyError as error:
        return failure_result(
            program_id,
            error.failure,
            execution_ms=_elapsed_ms(start_ns),
        )
    except _InterpreterError as error:
        return failure_result(
            program_id,
            error.failure,
            execution_ms=_elapsed_ms(start_ns),
        )
    except DecimalException:
        return failure_result(
            program_id,
            ExecutionFailure(
                stage=ExecutionFailureStage.ARITHMETIC,
                code=InterpreterFailureCode.DECIMAL_ARITHMETIC_ERROR.value,
                message="Decimal arithmetic failed",
            ),
            execution_ms=_elapsed_ms(start_ns),
        )
