from dataclasses import replace

from src.evidence.schemas import Scale
from src.programmer.schemas import ProgramOperation
from src.sandbox.schemas import ExecutionDatum, ExecutionOutput
from src.supervisor.schemas import EvidenceSource, Plan, VerifyProfile
from src.verification.schemas import (
    VerificationCheckResult,
    VerificationFailureCategory,
    VerificationReport,
)
from src.verification.verifier import verify
from tests.programmer.helpers import rebuild_program
from tests.verification.helpers import (
    completeness_for,
    failed_execution,
    make_request,
)


def test_verifier_integration_passes_hybrid_request():
    report = verify(make_request([EvidenceSource.TABLE, EvidenceSource.TEXT]))
    assert report is not None
    assert report.passed
    assert report.failure_category is None


def test_deterministic_failure_precedence_preserves_every_failure():
    categories = [
        VerificationFailureCategory.FINANCIAL_LOGIC,
        VerificationFailureCategory.SCALE_UNIT,
        VerificationFailureCategory.NUMERIC,
        VerificationFailureCategory.INSUFFICIENT_EVIDENCE,
        VerificationFailureCategory.GROUNDING,
    ]
    checks = [
        VerificationCheckResult(
            f"check:{category.value}",
            category,
            False,
            f"{category.value}_FAILURE",
            [category.value],
        )
        for category in categories
    ]
    report = VerificationReport.create(checks)
    assert len([check for check in report.checks if not check.passed]) == 5
    assert report.failure_category is VerificationFailureCategory.GROUNDING
    assert report.failure_reason == "GROUNDING_FAILURE"


def test_failed_execution_bypasses_verification():
    request = make_request(
        [EvidenceSource.TABLE], execution_result=failed_execution()
    )
    mismatched = replace(request, verify_profile=VerifyProfile.STRICT)
    assert verify(mismatched) is None


def test_multiple_batch2_failures_preserve_all_checks_and_precedence():
    request = make_request([EvidenceSource.TABLE])
    plan_payload = request.plan.to_dict()
    plan_payload["verify_profile"] = VerifyProfile.STRICT.value
    plan = Plan.from_dict(plan_payload)
    request = replace(
        request,
        plan=plan,
        verify_profile=VerifyProfile.STRICT,
    )
    evidence = [replace(request.evidence_items[0], report_ref="wrong-report")]
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
        operation=ProgramOperation.COLLECT,
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
    failed_categories = {check.category for check in report.checks if not check.passed}
    assert failed_categories == set(VerificationFailureCategory)
    assert report.failure_category is VerificationFailureCategory.GROUNDING


def test_repeated_verification_is_deterministic_and_does_not_mutate_inputs():
    request = make_request([EvidenceSource.TABLE, EvidenceSource.TEXT])
    before = {
        "plan": request.plan.to_dict(),
        "program": request.program.to_dict(),
        "evidence": [item.to_dict() for item in request.evidence_items],
        "execution": request.execution_result.to_dict(),
    }
    first = verify(request)
    second = verify(request)
    assert first == second
    assert before == {
        "plan": request.plan.to_dict(),
        "program": request.program.to_dict(),
        "evidence": [item.to_dict() for item in request.evidence_items],
        "execution": request.execution_result.to_dict(),
    }
