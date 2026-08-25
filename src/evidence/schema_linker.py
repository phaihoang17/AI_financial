"""Deterministic TASK-050 linking inside already-grounded M3 evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Mapping, Optional, Sequence

from src.evidence.m5_schemas import (
    SchemaLinkMatchBasis,
    SchemaLinkResult,
    SchemaLinkStatus,
)
from src.evidence.schemas import EvidenceItem
from src.retrieval.evidence import CellLocation
from src.supervisor.schemas import EvidenceSource, Plan, RetrievalRequirement
from src.understanding.metric_normalizer import (
    MetricResolutionStatus,
    initial_metric_registry,
    resolve_metric,
)
from src.understanding.schemas import SchemaValidationError


@dataclass(frozen=True)
class _AssessedLocation:
    evidence: EvidenceItem
    location: CellLocation
    metric: Optional[str]
    period: Optional[str]
    basis: SchemaLinkMatchBasis
    valid: bool


def _validate_grounded_pair(evidence: EvidenceItem, location: CellLocation) -> None:
    if evidence.source_type is not EvidenceSource.TABLE:
        raise SchemaValidationError("CellLocation can link only TABLE evidence")
    if (
        evidence.report_ref != location.report_id
        or evidence.page_ref != location.page_id
        or evidence.table_ref != location.table_id
        or evidence.row_path != [entry.label for entry in location.row_path]
        or evidence.column_path != [entry.label for entry in location.column_path]
    ):
        raise SchemaValidationError(
            "evidence and location must describe the same grounded M3 cell"
        )


def _metric_match(requirement: RetrievalRequirement, labels: Sequence[str]) -> Optional[str]:
    if requirement.metric is None:
        return None
    registry = initial_metric_registry()
    for label in labels:
        resolution = resolve_metric(label, registry)
        if (
            resolution.status is MetricResolutionStatus.RESOLVED
            and resolution.metric is not None
            and resolution.metric.canonical == requirement.metric
        ):
            return resolution.metric.canonical
    return None


def _period_match(requirement: RetrievalRequirement, labels: Sequence[str]) -> Optional[str]:
    if requirement.period is not None and requirement.period in labels:
        return requirement.period
    return None


def _assess(
    requirement: RetrievalRequirement,
    evidence: EvidenceItem,
    location: CellLocation,
) -> _AssessedLocation:
    _validate_grounded_pair(evidence, location)
    labels = [
        *(entry.label for entry in location.row_path),
        *(entry.label for entry in location.column_path),
    ]
    metric = _metric_match(requirement, labels)
    period = _period_match(requirement, labels)
    if metric is not None and period is not None:
        basis = SchemaLinkMatchBasis.EXACT_METRIC_AND_PERIOD
    elif metric is not None:
        basis = SchemaLinkMatchBasis.EXACT_METRIC_PATH
    elif period is not None:
        basis = SchemaLinkMatchBasis.EXACT_PERIOD_PATH
    elif location.row_path or location.column_path:
        basis = SchemaLinkMatchBasis.STRUCTURAL
    else:
        basis = SchemaLinkMatchBasis.NONE
    valid = (
        (requirement.metric is None or metric == requirement.metric)
        and (requirement.period is None or period == requirement.period)
    )
    return _AssessedLocation(evidence, location, metric, period, basis, valid)


def _result(
    requirement: RetrievalRequirement,
    assessed: _AssessedLocation,
    status: SchemaLinkStatus,
) -> SchemaLinkResult:
    exact = assessed.basis in {
        SchemaLinkMatchBasis.EXACT_METRIC_PATH,
        SchemaLinkMatchBasis.EXACT_PERIOD_PATH,
        SchemaLinkMatchBasis.EXACT_METRIC_AND_PERIOD,
    }
    return SchemaLinkResult(
        requirement_id=requirement.requirement_id,
        evidence_id=assessed.evidence.evidence_id,
        status=status,
        metric=assessed.metric,
        period=assessed.period,
        row_path=list(assessed.location.row_path),
        column_path=list(assessed.location.column_path),
        matched_source_cell_id=(
            assessed.location.source_cell_id if exact else None
        ),
        match_basis=assessed.basis,
    )


def link_schema(
    plan: Plan,
    evidence: Sequence[EvidenceItem],
    locations: Mapping[str, CellLocation],
) -> List[SchemaLinkResult]:
    """Link Plan TABLE requirements using only supplied evidence locations.

    Results follow Plan requirement order. Within an ambiguous or unresolved
    requirement they are ordered by evidence ID and source-cell ID.
    """
    if not isinstance(plan, Plan):
        raise SchemaValidationError("plan must be an executable Plan")
    if not isinstance(evidence, (list, tuple)) or not all(
        isinstance(item, EvidenceItem) for item in evidence
    ):
        raise SchemaValidationError("evidence must contain EvidenceItem values")
    if not isinstance(locations, Mapping):
        raise SchemaValidationError("locations must map evidence IDs to CellLocation")

    table_pairs = []
    for item in evidence:
        if item.source_type is not EvidenceSource.TABLE:
            continue
        location = locations.get(item.evidence_id)
        if location is None:
            raise SchemaValidationError(
                f"TABLE evidence {item.evidence_id} has no CellLocation"
            )
        if not isinstance(location, CellLocation):
            raise SchemaValidationError("locations must contain CellLocation values")
        _validate_grounded_pair(item, location)
        table_pairs.append((item, location))
    table_pairs.sort(key=lambda pair: (pair[0].evidence_id, pair[1].source_cell_id))

    results: List[SchemaLinkResult] = []
    for requirement in plan.retrieval_requirements:
        if requirement.source_type is not EvidenceSource.TABLE:
            continue
        if not table_pairs:
            raise SchemaValidationError(
                "TABLE schema linking requires already-grounded TABLE evidence"
            )
        assessed = [
            _assess(requirement, item, location)
            for item, location in table_pairs
        ]
        valid = [item for item in assessed if item.valid]
        if len(valid) == 1:
            results.append(
                _result(requirement, valid[0], SchemaLinkStatus.RESOLVED)
            )
        elif len(valid) > 1:
            results.extend(
                _result(requirement, item, SchemaLinkStatus.AMBIGUOUS)
                for item in valid
            )
        else:
            results.extend(
                _result(requirement, item, SchemaLinkStatus.UNRESOLVED)
                for item in assessed
            )
    return results
