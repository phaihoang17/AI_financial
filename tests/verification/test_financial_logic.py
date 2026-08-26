from dataclasses import replace

from src.programmer.schemas import Program, ProgramOperation, ProgramStep
from src.supervisor.schemas import EvidenceSource, QuestionType
from src.verification.financial_logic import verify_financial_logic
from tests.programmer.helpers import (
    average_programmer_input,
    compare_programmer_input,
    growth_programmer_input,
    rebuild_program,
)
from tests.verification.helpers import make_program_request, make_request


def _failed(request):
    return [
        check.reason_code
        for check in verify_financial_logic(request)
        if not check.passed
    ]


def _replace_step(program: Program, step: ProgramStep) -> Program:
    return rebuild_program(program, steps=[step], output_ref=step.step_id)


def test_valid_lookup_uses_identity():
    assert _failed(make_request([EvidenceSource.TABLE])) == []


def test_formula_free_ordered_compare_uses_collect():
    assert _failed(make_program_request(compare_programmer_input())) == []


def test_growth_formula_and_input_order_are_verified():
    request = make_program_request(growth_programmer_input())
    assert _failed(request) == []
    step = request.program.steps[0]
    program = _replace_step(
        request.program,
        replace(step, input_refs=list(reversed(step.input_refs))),
    )
    assert "FORMULA_INPUT_ORDER_MISMATCH" in _failed(
        replace(request, program=program)
    )


def test_average_formula_and_input_order_are_verified():
    request = make_program_request(average_programmer_input())
    assert _failed(request) == []
    step = request.program.steps[0]
    program = _replace_step(
        request.program,
        replace(step, input_refs=list(reversed(step.input_refs))),
    )
    assert "FORMULA_INPUT_ORDER_MISMATCH" in _failed(
        replace(request, program=program)
    )


def test_wrong_formula_is_rejected():
    request = make_program_request(growth_programmer_input())
    step = replace(request.program.steps[0], formula_id="AVERAGE")
    program = rebuild_program(
        request.program,
        formula_id="AVERAGE",
        steps=[step],
    )
    reasons = _failed(replace(request, program=program))
    assert "FORMULA_ID_MISMATCH" in reasons
    assert "STEP_FORMULA_ID_MISMATCH" in reasons


def test_wrong_program_operation_is_rejected():
    request = make_program_request(growth_programmer_input())
    step = replace(
        request.program.steps[0],
        operation=ProgramOperation.IDENTITY,
        input_refs=[request.program.inputs[0].input_id],
        formula_id=None,
    )
    program = _replace_step(request.program, step)
    assert "PROGRAM_OPERATION_MISMATCH" in _failed(
        replace(request, program=program)
    )


def test_unsupported_ratio_never_verifies_successfully():
    request = make_request([EvidenceSource.TABLE])
    request.plan.question_type = QuestionType.DERIVED_RATIO
    reasons = _failed(request)
    assert "UNSUPPORTED_RATIO" in reasons
    assert "UNSUPPORTED_FINANCIAL_LOGIC" in reasons
