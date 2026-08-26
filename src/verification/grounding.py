"""Deterministic TASK-080 grounding checks over canonical provenance only."""

from __future__ import annotations

from typing import List, Optional, Sequence

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


def _subjects(*values: Optional[str]) -> List[str]:
    result: List[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _check(
    check_id: str,
    passed: bool,
    reason_code: str,
    *subject_ids: Optional[str],
) -> VerificationCheckResult:
    return VerificationCheckResult(
        check_id=check_id,
        category=VerificationFailureCategory.GROUNDING,
        passed=passed,
        reason_code=None if passed else reason_code,
        subject_ids=_subjects(*subject_ids),
    )


def _one(items: Sequence, *, missing: str, ambiguous: str):
    if not items:
        return None, missing
    if len(items) != 1:
        return None, ambiguous
    return items[0], None


def _year(period: Optional[str]) -> Optional[int]:
    if period is None or len(period) < 4 or not period[:4].isdigit():
        return None
    return int(period[:4])


def _table_checks(
    request: VerificationRequest,
    requirement: RetrievalRequirement,
) -> List[VerificationCheckResult]:
    prefix = f"grounding:{requirement.requirement_id}"
    links = [
        link
        for link in request.schema_links
        if link.requirement_id == requirement.requirement_id
    ]
    link, link_error = _one(
        links,
        missing="SCHEMA_LINK_MISSING",
        ambiguous="SCHEMA_LINK_AMBIGUOUS",
    )
    if link is not None and link.status is not SchemaLinkStatus.RESOLVED:
        link_error = f"SCHEMA_LINK_{link.status.value}"
    if link is not None and link.match_basis not in _EXACT_LINK_BASES:
        link_error = "SCHEMA_LINK_NOT_EXACT"
    checks = [
        _check(
            f"{prefix}:schema_binding",
            link_error is None,
            link_error or "SCHEMA_LINK_INVALID",
            requirement.requirement_id,
            None if link is None else link.evidence_id,
        )
    ]
    if link_error is not None or link is None:
        return checks

    evidence_matches = [
        item
        for item in request.evidence_items
        if item.evidence_id == link.evidence_id
    ]
    evidence, evidence_error = _one(
        evidence_matches,
        missing="EVIDENCE_ITEM_MISSING",
        ambiguous="EVIDENCE_ID_AMBIGUOUS",
    )
    checks.append(
        _check(
            f"{prefix}:evidence_binding",
            evidence_error is None
            and evidence is not None
            and evidence.source_type is EvidenceSource.TABLE,
            evidence_error or "EVIDENCE_SOURCE_TYPE_MISMATCH",
            requirement.requirement_id,
            link.evidence_id,
        )
    )
    if evidence_error is not None or evidence is None:
        return checks
    if evidence.source_type is not EvidenceSource.TABLE:
        return checks

    locations = [
        location
        for location in request.cell_locations
        if location.source_cell_id == link.matched_source_cell_id
    ]
    location, location_error = _one(
        locations,
        missing="SOURCE_CELL_MISSING",
        ambiguous="SOURCE_CELL_AMBIGUOUS",
    )
    checks.append(
        _check(
            f"{prefix}:source_cell",
            location_error is None,
            location_error or "SOURCE_CELL_MISMATCH",
            requirement.requirement_id,
            evidence.evidence_id,
            link.matched_source_cell_id,
        )
    )
    if location_error is not None or location is None:
        return checks

    common_subjects = (
        requirement.requirement_id,
        evidence.evidence_id,
        location.location_id,
    )
    checks.extend(
        [
            _check(
                f"{prefix}:ticker",
                location.ticker is not None
                and evidence.ticker is not None
                and location.ticker == evidence.ticker == request.plan.company.ticker,
                (
                    "TICKER_UNAVAILABLE"
                    if location.ticker is None or evidence.ticker is None
                    else "TICKER_MISMATCH"
                ),
                *common_subjects,
            ),
            _check(
                f"{prefix}:company",
                location.company_name is not None
                and evidence.company_name is not None
                and location.company_name
                == evidence.company_name
                == request.plan.company.name,
                (
                    "COMPANY_UNAVAILABLE"
                    if location.company_name is None or evidence.company_name is None
                    else "COMPANY_MISMATCH"
                ),
                *common_subjects,
            ),
            _check(
                f"{prefix}:report",
                bool(evidence.report_ref)
                and evidence.report_ref == location.report_id,
                "REPORT_PROVENANCE_MISMATCH",
                *common_subjects,
            ),
            _check(
                f"{prefix}:report_year",
                location.report_year is not None
                and evidence.report_year is not None
                and location.report_year == evidence.report_year == _year(requirement.period),
                (
                    "REPORT_YEAR_UNAVAILABLE"
                    if location.report_year is None or evidence.report_year is None
                    else "REPORT_YEAR_MISMATCH"
                ),
                *common_subjects,
            ),
            _check(
                f"{prefix}:statement_scope",
                location.statement_scope is not None
                and evidence.statement_scope is not None
                and location.statement_scope
                is evidence.statement_scope
                is request.plan.statement_scope,
                (
                    "STATEMENT_SCOPE_UNAVAILABLE"
                    if location.statement_scope is None
                    or evidence.statement_scope is None
                    else "STATEMENT_SCOPE_MISMATCH"
                ),
                *common_subjects,
            ),
            _check(
                f"{prefix}:period",
                requirement.period is not None
                and link.period == requirement.period
                and evidence.period == requirement.period
                and requirement.period
                in [
                    *(entry.label for entry in location.row_path),
                    *(entry.label for entry in location.column_path),
                ],
                "PERIOD_MISMATCH",
                *common_subjects,
            ),
            _check(
                f"{prefix}:metric",
                requirement.metric is not None
                and link.metric == requirement.metric
                and evidence.metric == requirement.metric,
                "METRIC_MISMATCH",
                *common_subjects,
            ),
            _check(
                f"{prefix}:table_identity",
                bool(evidence.table_ref)
                and evidence.table_ref == location.table_id,
                "TABLE_ID_MISMATCH",
                *common_subjects,
            ),
            _check(
                f"{prefix}:table_class",
                location.table_class is not None
                and location.table_class is requirement.table_class,
                (
                    "TABLE_CLASS_UNAVAILABLE"
                    if location.table_class is None
                    else "TABLE_CLASS_MISMATCH"
                ),
                *common_subjects,
            ),
            _check(
                f"{prefix}:source_cell_identity",
                link.matched_source_cell_id == location.source_cell_id,
                "SOURCE_CELL_MISMATCH",
                *common_subjects,
            ),
            _check(
                f"{prefix}:row_path",
                link.row_path == location.row_path
                and evidence.row_path
                == [entry.label for entry in location.row_path],
                "ROW_PATH_MISMATCH",
                *common_subjects,
            ),
            _check(
                f"{prefix}:column_path",
                link.column_path == location.column_path
                and evidence.column_path
                == [entry.label for entry in location.column_path],
                "COLUMN_PATH_MISMATCH",
                *common_subjects,
            ),
        ]
    )
    return checks


def _text_evidence_ids(
    request: VerificationRequest,
    requirement: RetrievalRequirement,
) -> List[str]:
    completeness_ids: List[str] = []
    for result in request.evidence_completeness.requirements:
        if result.requirement.requirement_id == requirement.requirement_id:
            completeness_ids.extend(result.evidence_ids)
    if completeness_ids:
        return list(dict.fromkeys(completeness_ids))
    return [
        item.evidence_id
        for item in request.evidence_items
        if item.source_type is EvidenceSource.TEXT
        and (requirement.metric is None or item.metric == requirement.metric)
        and (requirement.period is None or item.period == requirement.period)
    ]


def _text_checks(
    request: VerificationRequest,
    requirement: RetrievalRequirement,
) -> List[VerificationCheckResult]:
    checks: List[VerificationCheckResult] = []
    evidence_by_id = {item.evidence_id: item for item in request.evidence_items}
    for evidence_id in _text_evidence_ids(request, requirement):
        evidence = evidence_by_id.get(evidence_id)
        if evidence is None or evidence.source_type is not EvidenceSource.TEXT:
            continue
        prefix = f"grounding:{requirement.requirement_id}:{evidence.evidence_id}"
        subjects = (requirement.requirement_id, evidence.evidence_id)
        checks.extend(
            [
                _check(
                    f"{prefix}:ticker",
                    evidence.ticker is not None
                    and evidence.ticker == request.plan.company.ticker,
                    "TICKER_UNAVAILABLE" if evidence.ticker is None else "TICKER_MISMATCH",
                    *subjects,
                ),
                _check(
                    f"{prefix}:company",
                    evidence.company_name is not None
                    and evidence.company_name == request.plan.company.name,
                    "COMPANY_UNAVAILABLE"
                    if evidence.company_name is None
                    else "COMPANY_MISMATCH",
                    *subjects,
                ),
                _check(
                    f"{prefix}:report",
                    bool(evidence.report_ref),
                    "REPORT_PROVENANCE_UNAVAILABLE",
                    *subjects,
                ),
                _check(
                    f"{prefix}:report_year",
                    requirement.period is None
                    or (
                        evidence.report_year is not None
                        and evidence.report_year == _year(requirement.period)
                    ),
                    "REPORT_YEAR_UNAVAILABLE"
                    if evidence.report_year is None
                    else "REPORT_YEAR_MISMATCH",
                    *subjects,
                ),
                _check(
                    f"{prefix}:statement_scope",
                    evidence.statement_scope is not None
                    and evidence.statement_scope is request.plan.statement_scope,
                    "STATEMENT_SCOPE_UNAVAILABLE"
                    if evidence.statement_scope is None
                    else "STATEMENT_SCOPE_MISMATCH",
                    *subjects,
                ),
                _check(
                    f"{prefix}:paragraph",
                    bool(evidence.paragraph_ref)
                    and bool(evidence.page_ref)
                    and bool(evidence.text_span),
                    "PARAGRAPH_PROVENANCE_UNAVAILABLE",
                    *subjects,
                ),
                _check(
                    f"{prefix}:source_shape",
                    evidence.table_ref is None,
                    "TEXT_SOURCE_SHAPE_INVALID",
                    *subjects,
                ),
                _check(
                    f"{prefix}:metric_period",
                    (requirement.metric is None or evidence.metric == requirement.metric)
                    and (requirement.period is None or evidence.period == requirement.period),
                    "TEXT_METRIC_PERIOD_MISMATCH",
                    *subjects,
                ),
                _check(
                    f"{prefix}:link_provenance",
                    evidence.associated_table_ref is None
                    or bool(evidence.provenance_link_ids),
                    "LINK_PROVENANCE_MISSING",
                    *subjects,
                ),
            ]
        )
    return checks


def verify_grounding(request: VerificationRequest) -> List[VerificationCheckResult]:
    """Return every applicable grounding check in Plan requirement order."""
    checks: List[VerificationCheckResult] = []
    for requirement in request.plan.retrieval_requirements:
        if not requirement.required:
            continue
        if requirement.source_type is EvidenceSource.TABLE:
            checks.extend(_table_checks(request, requirement))
        else:
            checks.extend(_text_checks(request, requirement))
    return checks
