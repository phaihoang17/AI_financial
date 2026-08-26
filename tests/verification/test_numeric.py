from dataclasses import replace

from src.programmer.schemas import ProgramOutputKind
from src.sandbox.schemas import ExecutionDatum, ExecutionOutput
from src.supervisor.schemas import EvidenceSource
from src.verification.numeric import verify_numeric
from tests.programmer.helpers import compare_programmer_input
from tests.verification.helpers import make_program_request, make_request


def _failed(request):
    return [check.reason_code for check in verify_numeric(request) if not check.passed]


def _forged_output(kind, values):
    output = object.__new__(ExecutionOutput)
    object.__setattr__(output, "kind", kind)
    object.__setattr__(output, "values", values)
    return output


def _forged_datum(value, scale=None, unit=None):
    datum = object.__new__(ExecutionDatum)
    object.__setattr__(datum, "value", value)
    object.__setattr__(datum, "scale", scale)
    object.__setattr__(datum, "unit", unit)
    return datum


def test_valid_lookup_numeric_output():
    assert _failed(make_request([EvidenceSource.TABLE])) == []


def test_ordered_compare_preserves_count_and_order():
    request = make_program_request(compare_programmer_input())
    assert _failed(request) == []


def test_numeric_shape_mismatch_is_typed():
    request = make_request([EvidenceSource.TABLE])
    output = _forged_output(ProgramOutputKind.SCALAR, [])
    execution = replace(request.execution_result, output=output)
    assert "SCALAR_VALUE_COUNT_MISMATCH" in _failed(
        replace(request, execution_result=execution)
    )


def test_invalid_canonical_decimal_and_float_are_rejected():
    request = make_request([EvidenceSource.TABLE])
    output = _forged_output(
        ProgramOutputKind.SCALAR,
        [_forged_datum(12.5, request.execution_result.output.values[0].scale)],
    )
    execution = replace(request.execution_result, output=output)
    assert "INVALID_CANONICAL_DECIMAL" in _failed(
        replace(request, execution_result=execution)
    )


def test_ordered_compare_rejects_swapped_values():
    request = make_program_request(compare_programmer_input())
    output = replace(
        request.execution_result.output,
        values=list(reversed(request.execution_result.output.values)),
    )
    execution = replace(request.execution_result, output=output)
    assert "ORDERED_VALUES_ORDER_MISMATCH" in _failed(
        replace(request, execution_result=execution)
    )
