from dataclasses import replace

from src.supervisor.schemas import EvidenceSource, Plan, VerifyProfile
from src.verification.verifier import verify
from tests.programmer.helpers import growth_programmer_input
from tests.verification.helpers import make_program_request, make_request


def _ids(report):
    return [check.check_id for check in report.checks]


def _as_light(request):
    payload = request.plan.to_dict()
    payload["verify_profile"] = VerifyProfile.LIGHT.value
    plan = Plan.from_dict(payload)
    return replace(request, plan=plan, verify_profile=VerifyProfile.LIGHT)


def test_valid_single_lookup_runs_light_matrix():
    report = verify(make_request([EvidenceSource.TABLE]))
    assert report is not None and report.passed
    check_ids = _ids(report)
    assert any(item.startswith("grounding:") for item in check_ids)
    assert any(item.startswith("evidence_coverage:") for item in check_ids)
    assert any(item.startswith("numeric:") for item in check_ids)
    assert any(item.startswith("scale_unit:") for item in check_ids)
    assert not any(item.startswith("financial_logic:") for item in check_ids)


def test_invalid_multi_requirement_light_is_rejected_and_not_downgraded():
    request = _as_light(
        make_request([EvidenceSource.TABLE, EvidenceSource.TEXT])
    )
    report = verify(request)
    assert report is not None and not report.passed
    assert "LIGHT_REQUIRED_REQUIREMENT_COUNT_INVALID" in {
        check.reason_code for check in report.checks if not check.passed
    }
    assert any(
        item.startswith("financial_logic:") for item in _ids(report)
    )


def test_invalid_formula_program_light_is_rejected_and_runs_strict():
    request = _as_light(make_program_request(growth_programmer_input()))
    report = verify(request)
    assert report is not None and not report.passed
    reasons = {check.reason_code for check in report.checks if not check.passed}
    assert "LIGHT_QUESTION_TYPE_INVALID" in reasons
    assert "LIGHT_REASONING_MODE_INVALID" in reasons
    assert "LIGHT_FORMULA_INVALID" in reasons
    assert any(item.startswith("financial_logic:") for item in _ids(report))


def test_light_scale_check_is_conditional():
    table_report = verify(make_request([EvidenceSource.TABLE]))
    text_report = verify(make_request([EvidenceSource.TEXT]))
    assert table_report is not None and text_report is not None
    assert any(item.startswith("scale_unit:") for item in _ids(table_report))
    assert not any(item.startswith("scale_unit:") for item in _ids(text_report))


def test_light_never_runs_financial_logic_verifier():
    report = verify(make_request([EvidenceSource.TABLE]))
    assert report is not None
    assert not any(
        item.startswith("financial_logic:") for item in _ids(report)
    )
