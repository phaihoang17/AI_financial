from dataclasses import replace

from src.evidence.m5_schemas import SchemaLinkMatchBasis, SchemaLinkStatus
from src.supervisor.schemas import EvidenceSource
from src.verification.evidence_support import verify_evidence_support
from src.verification.schemas import VerificationRequest
from tests.verification.helpers import completeness_for, make_request


def _rebuild(request, evidence, locations, links, *, missing=()):
    return VerificationRequest(
        request.plan,
        request.program,
        evidence,
        locations,
        links,
        request.scale_unit_resolutions,
        request.binding_map,
        completeness_for(request.plan, evidence, missing_ids=missing),
        request.execution_result,
        request.verify_profile,
    )


def _outcome(request):
    checks = verify_evidence_support(request)
    return {check.subject_ids[0]: check.passed for check in checks}


def test_table_only_completeness():
    request = make_request([EvidenceSource.TABLE])
    assert all(_outcome(request).values())


def test_text_only_completeness():
    request = make_request([EvidenceSource.TEXT])
    assert all(_outcome(request).values())


def test_hybrid_completeness():
    request = make_request([EvidenceSource.TABLE, EvidenceSource.TEXT])
    assert all(_outcome(request).values())


def test_missing_table_preserves_requirement_id():
    request = make_request([EvidenceSource.TABLE, EvidenceSource.TEXT])
    table_requirement = request.plan.retrieval_requirements[0]
    evidence = [item for item in request.evidence_items if item.source_type is EvidenceSource.TEXT]
    rebuilt = _rebuild(
        request,
        evidence,
        [],
        [],
        missing=[table_requirement.requirement_id],
    )
    failed = [check for check in verify_evidence_support(rebuilt) if not check.passed]
    assert failed[0].reason_code == "REQUIRED_TABLE_EVIDENCE_MISSING"
    assert failed[0].subject_ids == [table_requirement.requirement_id]


def test_missing_text_preserves_requirement_id():
    request = make_request([EvidenceSource.TABLE, EvidenceSource.TEXT])
    text_requirement = request.plan.retrieval_requirements[1]
    evidence = [item for item in request.evidence_items if item.source_type is EvidenceSource.TABLE]
    rebuilt = _rebuild(
        request,
        evidence,
        request.cell_locations,
        request.schema_links,
        missing=[text_requirement.requirement_id],
    )
    failed = [check for check in verify_evidence_support(rebuilt) if not check.passed]
    assert failed[0].reason_code == "REQUIRED_TEXT_EVIDENCE_MISSING"
    assert failed[0].subject_ids == [text_requirement.requirement_id]


def test_wrong_metric_or_period_does_not_satisfy():
    request = make_request([EvidenceSource.TABLE])
    evidence = [replace(request.evidence_items[0], metric="WRONG", period="2014")]
    rebuilt = _rebuild(
        request,
        evidence,
        request.cell_locations,
        request.schema_links,
    )
    assert not next(iter(_outcome(rebuilt).values()))


def test_structural_link_does_not_satisfy_exact_requirement():
    request = make_request([EvidenceSource.TABLE])
    link = replace(
        request.schema_links[0],
        status=SchemaLinkStatus.UNRESOLVED,
        matched_source_cell_id=None,
        match_basis=SchemaLinkMatchBasis.STRUCTURAL,
    )
    rebuilt = _rebuild(
        request,
        request.evidence_items,
        request.cell_locations,
        [link],
    )
    assert not next(iter(_outcome(rebuilt).values()))


def test_linked_text_cannot_satisfy_table_requirement():
    request = make_request([EvidenceSource.TABLE])
    text = make_request([EvidenceSource.TEXT]).evidence_items[0]
    rebuilt = _rebuild(
        request,
        [replace(text, associated_table_ref="table", provenance_link_ids=["link"])],
        [],
        [],
    )
    assert not next(iter(_outcome(rebuilt).values()))
