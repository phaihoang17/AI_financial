from dataclasses import fields, replace

from src.supervisor.schemas import EvidenceSource, VerifyProfile
from src.verification.schemas import (
    FAILURE_PRECEDENCE,
    VerificationCheckResult,
    VerificationFailureCategory,
    VerificationReport,
    VerificationRequest,
)
from tests.verification.helpers import make_request
from src.verification.verifier import verify


def test_contract_field_names():
    assert [field.name for field in fields(VerificationRequest)] == [
        "plan",
        "program",
        "evidence_items",
        "cell_locations",
        "schema_links",
        "scale_unit_resolutions",
        "binding_map",
        "evidence_completeness",
        "execution_result",
        "verify_profile",
    ]
    assert [field.name for field in fields(VerificationCheckResult)] == [
        "check_id",
        "category",
        "passed",
        "reason_code",
        "subject_ids",
    ]
    assert [field.name for field in fields(VerificationReport)] == [
        "passed",
        "checks",
        "failure_category",
        "failure_reason",
    ]


def test_check_and_report_round_trip():
    check = VerificationCheckResult(
        "grounding:one",
        VerificationFailureCategory.GROUNDING,
        False,
        "REPORT_PROVENANCE_MISMATCH",
        ["one"],
    )
    report = VerificationReport.create([check])
    assert VerificationReport.from_dict(report.to_dict()) == report


def test_failure_precedence_is_canonical():
    assert [item.value for item in FAILURE_PRECEDENCE] == [
        "GROUNDING",
        "INSUFFICIENT_EVIDENCE",
        "NUMERIC",
        "SCALE_UNIT",
        "FINANCIAL_LOGIC",
    ]


def test_request_profile_mismatch_is_a_typed_verification_failure():
    request = make_request([EvidenceSource.TABLE])
    report = verify(replace(request, verify_profile=VerifyProfile.STRICT))
    assert report is not None and not report.passed
    failed = [check for check in report.checks if not check.passed]
    assert failed[0].check_id == "profile:request_plan_match"
    assert failed[0].reason_code == "VERIFY_PROFILE_MISMATCH"
