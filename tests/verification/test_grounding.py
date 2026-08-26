from dataclasses import replace

import pytest

from src.supervisor.schemas import EvidenceSource
from src.understanding.schemas import StatementScope
from src.verification.grounding import verify_grounding
from src.verification.schemas import VerificationRequest
from tests.verification.helpers import completeness_for, make_request


def _rebuild(request, *, evidence_items=None, cell_locations=None, schema_links=None):
    evidence = request.evidence_items if evidence_items is None else evidence_items
    return VerificationRequest(
        request.plan,
        request.program,
        evidence,
        request.cell_locations if cell_locations is None else cell_locations,
        request.schema_links if schema_links is None else schema_links,
        request.scale_unit_resolutions,
        request.binding_map,
        completeness_for(request.plan, evidence),
        request.execution_result,
        request.verify_profile,
    )


def _failed_reasons(request):
    return [check.reason_code for check in verify_grounding(request) if not check.passed]


def test_valid_table_grounding():
    assert _failed_reasons(make_request([EvidenceSource.TABLE])) == []


def test_valid_text_grounding():
    assert _failed_reasons(make_request([EvidenceSource.TEXT])) == []


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("report_ref", "wrong-report", "REPORT_PROVENANCE_MISMATCH"),
        ("statement_scope", StatementScope.RIENG, "STATEMENT_SCOPE_MISMATCH"),
        ("period", "2014", "PERIOD_MISMATCH"),
    ],
)
def test_wrong_report_scope_or_period(field, value, reason):
    request = make_request([EvidenceSource.TABLE])
    evidence = [replace(request.evidence_items[0], **{field: value})]
    assert reason in _failed_reasons(_rebuild(request, evidence_items=evidence))


def test_wrong_cell_and_path_are_typed():
    request = make_request([EvidenceSource.TABLE])
    evidence = [replace(request.evidence_items[0], row_path=["wrong-row"])]
    assert "ROW_PATH_MISMATCH" in _failed_reasons(
        _rebuild(request, evidence_items=evidence)
    )
    link = replace(request.schema_links[0], matched_source_cell_id="wrong-cell")
    assert "SOURCE_CELL_MISSING" in _failed_reasons(
        _rebuild(request, schema_links=[link])
    )


def test_broken_text_link_provenance():
    request = make_request([EvidenceSource.TABLE, EvidenceSource.TEXT])
    evidence = [
        item
        if item.source_type is EvidenceSource.TABLE
        else replace(item, provenance_link_ids=[])
        for item in request.evidence_items
    ]
    assert "LINK_PROVENANCE_MISSING" in _failed_reasons(
        _rebuild(request, evidence_items=evidence)
    )


def test_unavailable_table_class_is_typed_failure():
    request = make_request([EvidenceSource.TABLE])
    locations = [replace(request.cell_locations[0], table_class=None)]
    assert "TABLE_CLASS_UNAVAILABLE" in _failed_reasons(
        _rebuild(request, cell_locations=locations)
    )
