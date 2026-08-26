"""Deterministic TASK-08A scale/unit provenance verification."""

from __future__ import annotations

from typing import List, Optional

from src.evidence.m5_schemas import ScaleUnitResolutionStatus
from src.evidence.schemas import Scale
from src.indexing.schemas import NumericParseResult
from src.numeric.scale_conversion import ScaleConversionStatus, convert_canonical_scale
from src.programmer.schemas import ProgramOperation
from src.understanding.requested_scale_unit_parser import RequestedScale
from src.verification.schemas import (
    VerificationCheckResult,
    VerificationFailureCategory,
    VerificationRequest,
)


_MAGNITUDE_SCALES = {Scale.RAW, Scale.THOUSAND, Scale.MILLION, Scale.BILLION}


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
        category=VerificationFailureCategory.SCALE_UNIT,
        passed=passed,
        reason_code=None if passed else reason_code,
        subject_ids=subjects,
    )


def _requested_scale(value: Optional[RequestedScale]) -> Optional[Scale]:
    return None if value is None else Scale(value.value)


def _output_step(request: VerificationRequest):
    return next(
        (step for step in request.program.steps if step.step_id == request.program.output_ref),
        None,
    )


def _percent_literal_provenance(request: VerificationRequest, evidence_item) -> bool:
    for location in request.cell_locations:
        if (
            evidence_item.report_ref == location.report_id
            and evidence_item.page_ref == location.page_id
            and evidence_item.table_ref == location.table_id
            and evidence_item.row_path
            == [entry.label for entry in location.row_path]
            and evidence_item.column_path
            == [entry.label for entry in location.column_path]
            and isinstance(location.numeric, NumericParseResult)
            and location.numeric.percent_literal
        ):
            return True
    return False


def _formula_output_metadata(request: VerificationRequest):
    step = _output_step(request)
    if step is None or step.operation is not ProgramOperation.APPLY_REGISTERED_FORMULA:
        return None
    inputs = {item.input_id: item for item in request.program.inputs}
    bindings = {item.placeholder: item for item in request.binding_map.bindings}
    ordered = []
    for ref in step.input_refs:
        program_input = inputs.get(ref)
        if program_input is None or program_input.placeholder not in bindings:
            return None
        ordered.append(bindings[program_input.placeholder])

    scales = [item.source_scale for item in ordered]
    if any(scale is None for scale in scales):
        return (None, None, "SOURCE_SCALE_REQUIRED")
    percent_flags = [scale is Scale.PERCENT for scale in scales]
    magnitude_flags = [scale in _MAGNITUDE_SCALES for scale in scales]
    if not all(percent_flags) and not all(magnitude_flags):
        return (None, None, "PERCENT_MAGNITUDE_MIX")
    source_unit_values = [item.source_unit for item in ordered]
    present_source_units = {
        unit for unit in source_unit_values if unit is not None
    }
    if (
        present_source_units
        and any(unit is None for unit in source_unit_values)
    ) or len(present_source_units) > 1:
        return (None, None, "UNIT_MISMATCH")
    source_unit = next(iter(present_source_units), None)
    requested_unit_values = [item.requested_output_unit for item in ordered]
    present_requested_units = {
        unit for unit in requested_unit_values if unit is not None
    }
    if (
        present_requested_units
        and any(unit is None for unit in requested_unit_values)
    ) or len(present_requested_units) > 1:
        return (None, None, "UNIT_MISMATCH")
    for binding in ordered:
        if binding.requested_output_unit is not None and (
            binding.requested_output_unit != binding.source_unit
        ):
            return (None, None, "UNSUPPORTED_UNIT_CONVERSION")

    if step.formula_id == "GROWTH_RATE":
        if any(
            _requested_scale(item.requested_output_scale)
            not in {None, Scale.PERCENT}
            for item in ordered
        ):
            return (None, None, "UNSUPPORTED_SCALE_CONVERSION")
        return (Scale.PERCENT, None, None)
    if step.formula_id == "AVERAGE":
        requested_scales = [
            _requested_scale(item.requested_output_scale) for item in ordered
        ]
        if all(percent_flags):
            if any(scale not in {None, Scale.PERCENT} for scale in requested_scales):
                return (None, None, "PERCENT_MAGNITUDE_MIX")
            return (Scale.PERCENT, source_unit, None)
        if any(scale is Scale.PERCENT for scale in requested_scales):
            return (None, None, "PERCENT_MAGNITUDE_MIX")
        output_scale = (
            requested_scales[0]
            if requested_scales
            and all(
                item is not None and item is requested_scales[0]
                for item in requested_scales
            )
            else Scale.RAW
        )
        return (output_scale, source_unit, None)
    return None


def verify_scale_unit(request: VerificationRequest) -> List[VerificationCheckResult]:
    """Verify the approved scale/unit chain without resolving provenance again."""
    checks: List[VerificationCheckResult] = []
    evidence = {item.evidence_id: item for item in request.evidence_items}
    resolutions = {
        item.evidence_id: item for item in request.scale_unit_resolutions
    }
    bindings = {item.placeholder: item for item in request.binding_map.bindings}
    inputs = {item.input_id: item for item in request.program.inputs}

    for program_input in request.program.inputs:
        prefix = f"scale_unit:{program_input.input_id}"
        item = evidence.get(program_input.evidence_id)
        resolution = resolutions.get(program_input.evidence_id)
        binding = bindings.get(program_input.placeholder)
        subjects = (program_input.requirement_id, program_input.evidence_id)
        checks.extend(
            [
                _check(
                    f"{prefix}:evidence",
                    item is not None,
                    "EVIDENCE_PROVENANCE_MISSING",
                    *subjects,
                ),
                _check(
                    f"{prefix}:resolution",
                    resolution is not None,
                    "SCALE_UNIT_RESOLUTION_MISSING",
                    *subjects,
                ),
                _check(
                    f"{prefix}:binding",
                    binding is not None
                    and binding.evidence_id == program_input.evidence_id,
                    "BINDING_PROVENANCE_MISMATCH",
                    *subjects,
                ),
            ]
        )
        if item is None or resolution is None or binding is None:
            continue

        resolved = resolution.status is ScaleUnitResolutionStatus.RESOLVED
        checks.append(
            _check(
                f"{prefix}:approved_resolution",
                resolved,
                f"SCALE_UNIT_{resolution.status.value}",
                *subjects,
            )
        )
        if not resolved:
            continue

        traceable = (
            all(isinstance(hint, str) and bool(hint) for hint in resolution.considered_hint_ids)
            and all(hint in resolution.considered_hint_ids for hint in resolution.winning_hint_ids)
            and (
                bool(resolution.winning_hint_ids)
                or (
                    resolution.source_scale is Scale.PERCENT
                    and _percent_literal_provenance(request, item)
                )
            )
        )
        checks.append(
            _check(
                f"{prefix}:hint_trace",
                traceable,
                "SCALE_UNIT_HINT_PROVENANCE_BROKEN",
                *subjects,
            )
        )
        source_matches_evidence = (
            (item.scale is None or item.scale is resolution.source_scale)
            and (item.unit is None or item.unit == resolution.source_unit)
        )
        checks.append(
            _check(
                f"{prefix}:source_provenance",
                source_matches_evidence,
                "SOURCE_SCALE_UNIT_MISMATCH",
                *subjects,
            )
        )
        checks.extend(
            [
                _check(
                    f"{prefix}:binding_source",
                    binding.source_scale is resolution.source_scale
                    and binding.source_unit == resolution.source_unit,
                    "BINDING_SOURCE_SCALE_UNIT_MISMATCH",
                    *subjects,
                ),
                _check(
                    f"{prefix}:binding_request",
                    binding.requested_output_scale
                    is resolution.requested_output_scale
                    and binding.requested_output_unit
                    == resolution.requested_output_unit,
                    "REQUESTED_SCALE_UNIT_MISMATCH",
                    *subjects,
                ),
                _check(
                    f"{prefix}:no_default_raw",
                    not (
                        resolution.source_scale is None
                        and binding.source_scale is Scale.RAW
                    ),
                    "DEFAULT_TO_RAW_FORBIDDEN",
                    *subjects,
                ),
            ]
        )

    output = request.execution_result.output
    step = _output_step(request)
    if output is None or step is None:
        return checks

    if step.operation in {ProgramOperation.IDENTITY, ProgramOperation.COLLECT}:
        for index, ref in enumerate(step.input_refs):
            program_input = inputs.get(ref)
            binding = None if program_input is None else bindings.get(program_input.placeholder)
            if binding is None or index >= len(output.values):
                continue
            conversion = convert_canonical_scale(
                binding.value,
                source_scale=binding.source_scale,
                source_unit=binding.source_unit,
                requested_output_scale=binding.requested_output_scale,
                requested_output_unit=binding.requested_output_unit,
            )
            supported = conversion.status is ScaleConversionStatus.SUCCESS
            checks.append(
                _check(
                    f"scale_unit:output:{index}:conversion",
                    supported,
                    (
                        "UNSUPPORTED_SCALE_UNIT_CONVERSION"
                        if supported
                        else conversion.failure_code.value
                    ),
                    program_input.evidence_id,
                )
            )
            if supported:
                datum = output.values[index]
                checks.append(
                    _check(
                        f"scale_unit:output:{index}:metadata",
                        datum.scale is conversion.output_scale
                        and datum.unit == conversion.output_unit,
                        "OUTPUT_SCALE_UNIT_MISMATCH",
                        program_input.evidence_id,
                    )
                )
    elif step.operation is ProgramOperation.APPLY_REGISTERED_FORMULA:
        expected = _formula_output_metadata(request)
        if expected is not None:
            scale, unit, error = expected
            checks.append(
                _check(
                    "scale_unit:formula_inputs",
                    error is None,
                    error or "FORMULA_SCALE_UNIT_MISMATCH",
                    request.program.output_ref,
                )
            )
            if error is None and len(output.values) == 1:
                datum = output.values[0]
                checks.append(
                    _check(
                        "scale_unit:formula_output",
                        datum.scale is scale and datum.unit == unit,
                        "OUTPUT_SCALE_UNIT_MISMATCH",
                        request.program.output_ref,
                    )
                )
    return checks
