from dataclasses import replace

import pytest

from src.evidence.schemas import Scale
from src.programmer.schemas import ProgramOperation
from src.sandbox.schemas import ExecutionDatum, ExecutionOutput
from src.supervisor.schemas import EvidenceSource
from src.verification.schemas import VerificationFailureCategory
from src.verification.verifier import verify
from tests.programmer.helpers import (
    average_programmer_input,
    compare_programmer_input,
    growth_programmer_input,
    rebuild_program,
)
from tests.verification.helpers import (
    completeness_for,
    make_program_request,
    make_request,
)


def _ids(report):
    return [check.check_id for check in report.checks]


@pytest.mark.parametrize(
    "programmer_input",
    [
        growth_programmer_input,
        average_programmer_input,
        compare_programmer_input,
    ],
)
def test_strict_growth_average_and_compare(programmer_input):
    report = verify(make_program_request(programmer_input()))
    assert report is not None and report.passed
    assert any(item.startswith("financial_logic:") for item in _ids(report))


def test_strict_multiple_requirements_runs_all_applicable_checks():
    report = verify(
        make_request([EvidenceSource.TABLE, EvidenceSource.TEXT])
    )
    assert report is not None and report.passed
    check_ids = _ids(report)
    for prefix in (
        "grounding:",
        "evidence_coverage:",
        "numeric:",
        "scale_unit:",
        "financial_logic:",
    ):
        assert any(item.startswith(prefix) for item in check_ids)


def test_strict_preserves_multiple_failures_and_precedence():
    request = make_program_request(growth_programmer_input())
    evidence = [replace(request.evidence_items[0], report_ref="wrong-report")]
    evidence.extend(request.evidence_items[1:])
    completeness = completeness_for(
        request.plan,
        evidence,
        missing_ids=[request.plan.retrieval_requirements[0].requirement_id],
    )
    datum = object.__new__(ExecutionDatum)
    object.__setattr__(datum, "value", 12.5)
    object.__setattr__(datum, "scale", Scale.BILLION)
    object.__setattr__(datum, "unit", None)
    output = object.__new__(ExecutionOutput)
    object.__setattr__(output, "kind", request.program.output_kind)
    object.__setattr__(output, "values", [datum])
    step = replace(
        request.program.steps[0],
        operation=ProgramOperation.APPLY_REGISTERED_FORMULA,
        formula_id="AVERAGE",
    )
    program = rebuild_program(request.program, steps=[step])
    execution = replace(
        request.execution_result,
        program_id=program.program_id,
        output=output,
    )
    report = verify(
        replace(
            request,
            program=program,
            evidence_items=evidence,
            evidence_completeness=completeness,
            execution_result=execution,
        )
    )
    assert report is not None
    failed = [check for check in report.checks if not check.passed]
    categories = {check.category for check in failed}
    assert VerificationFailureCategory.GROUNDING in categories
    assert VerificationFailureCategory.INSUFFICIENT_EVIDENCE in categories
    assert VerificationFailureCategory.NUMERIC in categories
    assert VerificationFailureCategory.SCALE_UNIT in categories
    assert VerificationFailureCategory.FINANCIAL_LOGIC in categories
    assert len(failed) == len(categories) == 5
    assert report.failure_category is VerificationFailureCategory.GROUNDING
