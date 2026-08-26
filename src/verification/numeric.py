"""Deterministic TASK-081 execution-output numeric verification."""

from __future__ import annotations

from typing import List, Optional

from src.evidence.schemas import CanonicalDecimal, Scale
from src.numeric.scale_conversion import ScaleConversionStatus, convert_canonical_scale
from src.programmer.schemas import ProgramOperation, ProgramOutputKind
from src.sandbox.schemas import ExecutionDatum
from src.verification.schemas import (
    VerificationCheckResult,
    VerificationFailureCategory,
    VerificationRequest,
)


def _check(
    check_id: str,
    passed: bool,
    reason_code: str,
    *subject_ids: Optional[str],
) -> VerificationCheckResult:
    subjects = []
    for item in subject_ids:
        if item and item not in subjects:
            subjects.append(item)
    return VerificationCheckResult(
        check_id=check_id,
        category=VerificationFailureCategory.NUMERIC,
        passed=passed,
        reason_code=None if passed else reason_code,
        subject_ids=subjects,
    )


def _output_step(request: VerificationRequest):
    return next(
        (step for step in request.program.steps if step.step_id == request.program.output_ref),
        None,
    )


def _ordered_expected_values(request: VerificationRequest):
    step = _output_step(request)
    if step is None or step.operation is not ProgramOperation.COLLECT:
        return None
    inputs = {item.input_id: item for item in request.program.inputs}
    bindings = {
        item.placeholder: item for item in request.binding_map.bindings
    }
    expected = []
    for input_ref in step.input_refs:
        program_input = inputs.get(input_ref)
        if program_input is None:
            return None
        binding = bindings.get(program_input.placeholder)
        if binding is None:
            return None
        conversion = convert_canonical_scale(
            binding.value,
            source_scale=binding.source_scale,
            source_unit=binding.source_unit,
            requested_output_scale=binding.requested_output_scale,
            requested_output_unit=binding.requested_output_unit,
        )
        if conversion.status is not ScaleConversionStatus.SUCCESS:
            return None
        expected.append(conversion.canonical_value)
    return expected


def verify_numeric(request: VerificationRequest) -> List[VerificationCheckResult]:
    """Check numeric output shape and canonical values without formula recomputation."""
    result = request.execution_result
    output = result.output
    checks = [
        _check(
            "numeric:program_id",
            result.program_id == request.program.program_id,
            "PROGRAM_ID_MISMATCH",
            result.program_id,
            request.program.program_id,
        )
    ]
    if output is None:
        checks.append(
            _check(
                "numeric:output",
                False,
                "SUCCESS_OUTPUT_MISSING",
                request.program.program_id,
            )
        )
        return checks

    kind_valid = isinstance(output.kind, ProgramOutputKind)
    checks.append(
        _check(
            "numeric:output_kind",
            kind_valid and output.kind is request.program.output_kind,
            "OUTPUT_KIND_MISMATCH",
            request.program.program_id,
        )
    )

    values = output.values if isinstance(output.values, list) else []
    if request.program.output_kind is ProgramOutputKind.SCALAR:
        expected_count = 1
        count_reason = "SCALAR_VALUE_COUNT_MISMATCH"
    else:
        step = _output_step(request)
        expected_count = len(step.input_refs) if step is not None else len(request.program.inputs)
        count_reason = "ORDERED_VALUE_COUNT_MISMATCH"
    checks.append(
        _check(
            "numeric:output_count",
            isinstance(output.values, list) and len(values) == expected_count,
            count_reason,
            request.program.output_ref,
        )
    )

    for index, datum in enumerate(values):
        value = getattr(datum, "value", None)
        scale = getattr(datum, "scale", None)
        unit = getattr(datum, "unit", None)
        subject = f"output:{index}"
        checks.extend(
            [
                _check(
                    f"numeric:datum_structure:{index}",
                    isinstance(datum, ExecutionDatum),
                    "INVALID_OUTPUT_DATUM",
                    subject,
                ),
                _check(
                    f"numeric:value:{index}",
                    isinstance(value, CanonicalDecimal),
                    "INVALID_CANONICAL_DECIMAL",
                    subject,
                ),
                _check(
                    f"numeric:scale_structure:{index}",
                    scale is None
                    or (isinstance(scale, Scale) and scale is not Scale.OTHER),
                    "INVALID_SCALE_STRUCTURE",
                    subject,
                ),
                _check(
                    f"numeric:unit_structure:{index}",
                    unit is None or (isinstance(unit, str) and bool(unit)),
                    "INVALID_UNIT_STRUCTURE",
                    subject,
                ),
            ]
        )

    if request.program.output_kind is ProgramOutputKind.ORDERED_VALUES:
        expected_values = _ordered_expected_values(request)
        if expected_values is not None and len(values) == len(expected_values):
            actual_values = [getattr(item, "value", None) for item in values]
            checks.append(
                _check(
                    "numeric:ordered_values",
                    actual_values == expected_values,
                    "ORDERED_VALUES_ORDER_MISMATCH",
                    request.program.output_ref,
                )
            )
    return checks
