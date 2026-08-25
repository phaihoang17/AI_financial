from typing import Optional, Sequence

from src.evidence.m5_schemas import BindingMap, ValueBinding
from src.evidence.schemas import CanonicalDecimal, Scale
from src.programmer.generator import generate_program
from src.programmer.schemas import Program, ProgrammerInput, ProgrammerStatus
from src.sandbox.schemas import (
    EXECUTION_LIMITS_PROFILE_ID,
    EXECUTION_REQUEST_SCHEMA_VERSION,
    SandboxExecutionRequest,
)
from src.understanding.requested_scale_unit_parser import RequestedScale


def execution_request_for(
    programmer_input: ProgrammerInput,
    values: Sequence[str],
    scales: Sequence[Optional[Scale]],
    *,
    units: Optional[Sequence[Optional[str]]] = None,
    requested_scales: Optional[Sequence[Optional[RequestedScale]]] = None,
    requested_units: Optional[Sequence[Optional[str]]] = None,
    program: Optional[Program] = None,
) -> SandboxExecutionRequest:
    generated = generate_program(programmer_input)
    if generated.status is not ProgrammerStatus.GENERATED or generated.program is None:
        raise AssertionError("test ProgrammerInput did not generate a Program")
    current_program = generated.program if program is None else program
    count = len(current_program.inputs)
    if len(values) != count or len(scales) != count:
        raise AssertionError("test values/scales must match Program inputs")
    units = [None] * count if units is None else list(units)
    requested_scales = (
        [None] * count if requested_scales is None else list(requested_scales)
    )
    requested_units = (
        [None] * count if requested_units is None else list(requested_units)
    )
    if not all(
        len(items) == count
        for items in (units, requested_scales, requested_units)
    ):
        raise AssertionError("test metadata must match Program inputs")

    bindings = [
        ValueBinding(
            placeholder=item.placeholder,
            evidence_id=item.evidence_id,
            value=CanonicalDecimal(values[index]),
            source_scale=scales[index],
            source_unit=units[index],
            requested_output_scale=requested_scales[index],
            requested_output_unit=requested_units[index],
        )
        for index, item in enumerate(current_program.inputs)
    ]
    return SandboxExecutionRequest(
        schema_version=EXECUTION_REQUEST_SCHEMA_VERSION,
        program=current_program,
        programmer_input=programmer_input,
        binding_map=BindingMap(
            bindings=sorted(bindings, key=lambda item: item.placeholder)
        ),
        limits_profile_id=EXECUTION_LIMITS_PROFILE_ID,
    )
