"""Deterministic TASK-08B Plan-requirement evidence coverage checks."""

from __future__ import annotations

from typing import List, Set

from src.evidence.m5_schemas import (
    SchemaLinkMatchBasis,
    SchemaLinkStatus,
)
from src.supervisor.schemas import EvidenceSource, RetrievalRequirement
from src.verification.schemas import (
    VerificationCheckResult,
    VerificationFailureCategory,
    VerificationRequest,
)


_EXACT_LINK_BASES = {
    SchemaLinkMatchBasis.EXACT_METRIC_PATH,
    SchemaLinkMatchBasis.EXACT_PERIOD_PATH,
    SchemaLinkMatchBasis.EXACT_METRIC_AND_PERIOD,
}


def _declared_complete_ids(
    request: VerificationRequest,
    requirement: RetrievalRequirement,
) -> Set[str]:
    ids: Set[str] = set()
    for result in request.evidence_completeness.requirements:
        declared = result.requirement
        if declared.requirement_id != requirement.requirement_id:
            continue
        if (
            declared.source_type is not requirement.source_type
            or declared.metric != requirement.metric
            or declared.period != requirement.period
            or declared.required != requirement.required
            or not result.satisfied
        ):
            continue
        ids.update(result.evidence_ids)
    if requirement.requirement_id in request.evidence_completeness.missing_requirement_ids:
        return set()
    return ids


def _table_coverage_ids(
    request: VerificationRequest,
    requirement: RetrievalRequirement,
) -> Set[str]:
    evidence_by_id = {
        item.evidence_id: item
        for item in request.evidence_items
        if item.source_type is EvidenceSource.TABLE
    }
    ids: Set[str] = set()
    for link in request.schema_links:
        if (
            link.requirement_id != requirement.requirement_id
            or link.status is not SchemaLinkStatus.RESOLVED
            or link.match_basis not in _EXACT_LINK_BASES
            or link.metric != requirement.metric
            or link.period != requirement.period
        ):
            continue
        evidence = evidence_by_id.get(link.evidence_id)
        if evidence is None:
            continue
        if evidence.metric != requirement.metric or evidence.period != requirement.period:
            continue
        if not any(
            location.source_cell_id == link.matched_source_cell_id
            and location.report_id == evidence.report_ref
            and location.page_id == evidence.page_ref
            and location.table_id == evidence.table_ref
            for location in request.cell_locations
        ):
            continue
        ids.add(evidence.evidence_id)
    return ids


def _text_coverage_ids(
    request: VerificationRequest,
    requirement: RetrievalRequirement,
) -> Set[str]:
    return {
        item.evidence_id
        for item in request.evidence_items
        if item.source_type is EvidenceSource.TEXT
        and (requirement.metric is None or item.metric == requirement.metric)
        and (requirement.period is None or item.period == requirement.period)
        and item.table_ref is None
        and bool(item.paragraph_ref)
        and bool(item.report_ref)
        and bool(item.text_span)
        and (
            item.associated_table_ref is None
            or bool(item.provenance_link_ids)
        )
    }


def verify_evidence_support(
    request: VerificationRequest,
) -> List[VerificationCheckResult]:
    """Check every required Plan requirement without widening source types."""
    checks: List[VerificationCheckResult] = []
    for requirement in request.plan.retrieval_requirements:
        if not requirement.required:
            continue
        actual = (
            _table_coverage_ids(request, requirement)
            if requirement.source_type is EvidenceSource.TABLE
            else _text_coverage_ids(request, requirement)
        )
        declared = _declared_complete_ids(request, requirement)
        satisfied = bool(actual.intersection(declared))
        reason = (
            "REQUIRED_TABLE_EVIDENCE_MISSING"
            if requirement.source_type is EvidenceSource.TABLE
            else "REQUIRED_TEXT_EVIDENCE_MISSING"
        )
        checks.append(
            VerificationCheckResult(
                check_id=f"evidence_coverage:{requirement.requirement_id}",
                category=VerificationFailureCategory.INSUFFICIENT_EVIDENCE,
                passed=satisfied,
                reason_code=None if satisfied else reason,
                subject_ids=[requirement.requirement_id],
            )
        )
    return checks
