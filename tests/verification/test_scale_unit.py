from dataclasses import replace

from src.evidence.m5_schemas import BindingMap
from src.evidence.schemas import Scale
from src.sandbox.schemas import ExecutionDatum
from src.supervisor.schemas import EvidenceSource
from src.understanding.requested_scale_unit_parser import RequestedScale
from src.verification.scale_unit import verify_scale_unit
from tests.programmer.helpers import growth_programmer_input
from tests.verification.helpers import make_program_request, make_request


def _failed(request):
    return [
        check.reason_code for check in verify_scale_unit(request) if not check.passed
    ]


def _with_bindings(request, bindings):
    return replace(
        request,
        binding_map=BindingMap(sorted(bindings, key=lambda item: item.placeholder)),
    )


def _with_output_datum(request, datum):
    output = replace(request.execution_result.output, values=[datum])
    return replace(
        request,
        execution_result=replace(request.execution_result, output=output),
    )


def test_correct_source_and_output_scale_are_verified():
    assert _failed(make_request([EvidenceSource.TABLE])) == []


def test_incorrect_source_scale_is_typed():
    request = make_request([EvidenceSource.TABLE])
    evidence = [replace(request.evidence_items[0], scale=Scale.BILLION)]
    assert "SOURCE_SCALE_UNIT_MISMATCH" in _failed(
        replace(request, evidence_items=evidence)
    )


def test_resolution_to_binding_source_scale_mismatch_is_typed():
    request = make_request([EvidenceSource.TABLE])
    resolutions = [
        replace(request.scale_unit_resolutions[0], source_scale=Scale.BILLION)
    ]
    assert "BINDING_SOURCE_SCALE_UNIT_MISMATCH" in _failed(
        replace(request, scale_unit_resolutions=resolutions)
    )


def test_output_scale_mismatch_is_typed():
    request = make_request([EvidenceSource.TABLE])
    datum = replace(request.execution_result.output.values[0], scale=Scale.BILLION)
    assert "OUTPUT_SCALE_UNIT_MISMATCH" in _failed(
        _with_output_datum(request, datum)
    )


def test_percent_never_mixes_with_magnitude_scale():
    request = make_request([EvidenceSource.TABLE])
    resolution = replace(
        request.scale_unit_resolutions[0],
        source_scale=Scale.PERCENT,
        requested_output_scale=RequestedScale.MILLION,
    )
    binding = replace(
        request.binding_map.bindings[0],
        source_scale=Scale.PERCENT,
        requested_output_scale=RequestedScale.MILLION,
    )
    request = replace(request, scale_unit_resolutions=[resolution])
    request = _with_bindings(request, [binding])
    assert "UNSUPPORTED_SCALE_CONVERSION" in _failed(request)


def test_unit_mismatch_and_unsupported_conversion_are_typed():
    request = make_request([EvidenceSource.TABLE])
    resolution = replace(
        request.scale_unit_resolutions[0],
        source_unit="VND",
        requested_output_unit="USD",
    )
    binding = replace(
        request.binding_map.bindings[0],
        source_unit="VND",
        requested_output_unit="USD",
    )
    request = replace(request, scale_unit_resolutions=[resolution])
    request = _with_bindings(request, [binding])
    assert "UNSUPPORTED_UNIT_CONVERSION" in _failed(request)


def test_growth_output_must_be_percent():
    request = make_program_request(growth_programmer_input())
    assert _failed(request) == []
    datum = ExecutionDatum(
        request.execution_result.output.values[0].value,
        Scale.MILLION,
        None,
    )
    assert "OUTPUT_SCALE_UNIT_MISMATCH" in _failed(
        _with_output_datum(request, datum)
    )
