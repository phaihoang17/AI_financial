"""Deterministic TASK-051 masking for grounded numeric evidence."""

from __future__ import annotations

from enum import Enum
from typing import Dict, List, Mapping, Sequence

from src.evidence.m5_schemas import (
    MaskedEvidence,
    MaskedEvidenceBundle,
    ScaleUnitResolution,
    ScaleUnitResolutionStatus,
    SchemaLinkResult,
    SchemaLinkStatus,
    make_value_placeholder,
)
from src.evidence.schemas import CanonicalDecimal, EvidenceItem
from src.retrieval.evidence import CellLocation
from src.supervisor.schemas import Plan
from src.understanding.schemas import SchemaValidationError


class NumericMaskingFailureCode(str, Enum):
    UNKNOWN_REQUIREMENT = "UNKNOWN_REQUIREMENT"
    SCHEMA_LINK_MISSING = "SCHEMA_LINK_MISSING"
    SCHEMA_LINK_UNRESOLVED = "SCHEMA_LINK_UNRESOLVED"
    SCHEMA_LINK_AMBIGUOUS = "SCHEMA_LINK_AMBIGUOUS"
    SCHEMA_LINK_CONFLICT = "SCHEMA_LINK_CONFLICT"
    EVIDENCE_MISSING = "EVIDENCE_MISSING"
    EVIDENCE_CONFLICT = "EVIDENCE_CONFLICT"
    EVIDENCE_NOT_NUMERIC = "EVIDENCE_NOT_NUMERIC"
    CELL_LOCATION_MISSING = "CELL_LOCATION_MISSING"
    CELL_LOCATION_MISMATCH = "CELL_LOCATION_MISMATCH"
    SCALE_RESOLUTION_MISSING = "SCALE_RESOLUTION_MISSING"
    SCALE_RESOLUTION_NOT_RESOLVED = "SCALE_RESOLUTION_NOT_RESOLVED"
    SCALE_RESOLUTION_CONFLICT = "SCALE_RESOLUTION_CONFLICT"


class NumericMaskingError(SchemaValidationError):
    def __init__(self, code: NumericMaskingFailureCode, message: str) -> None:
        self.code = code
        super().__init__(f"{code.value}: {message}")


def _evidence_index(evidence: Sequence[EvidenceItem]) -> Dict[str, EvidenceItem]:
    if not isinstance(evidence, (list, tuple)) or not all(
        isinstance(item, EvidenceItem) for item in evidence
    ):
        raise SchemaValidationError("evidence must contain EvidenceItem values")
    result: Dict[str, EvidenceItem] = {}
    for item in evidence:
        existing = result.get(item.evidence_id)
        if existing is not None and existing.to_dict() != item.to_dict():
            raise NumericMaskingError(
                NumericMaskingFailureCode.EVIDENCE_CONFLICT,
                item.evidence_id,
            )
        result[item.evidence_id] = item
    return result


def _scale_index(
    resolutions: Sequence[ScaleUnitResolution],
) -> Dict[str, ScaleUnitResolution]:
    if not isinstance(resolutions, (list, tuple)) or not all(
        isinstance(item, ScaleUnitResolution) for item in resolutions
    ):
        raise SchemaValidationError(
            "scale_resolutions must contain ScaleUnitResolution values"
        )
    result: Dict[str, ScaleUnitResolution] = {}
    for item in resolutions:
        existing = result.get(item.evidence_id)
        if existing is not None and existing.to_dict() != item.to_dict():
            raise NumericMaskingError(
                NumericMaskingFailureCode.SCALE_RESOLUTION_CONFLICT,
                item.evidence_id,
            )
        result[item.evidence_id] = item
    return result


def _validate_chain(
    evidence: EvidenceItem,
    link: SchemaLinkResult,
    locations: Mapping[str, CellLocation],
) -> None:
    location = locations.get(evidence.evidence_id)
    if location is None:
        raise NumericMaskingError(
            NumericMaskingFailureCode.CELL_LOCATION_MISSING,
            evidence.evidence_id,
        )
    if not isinstance(location, CellLocation) or (
        evidence.report_ref != location.report_id
        or evidence.page_ref != location.page_id
        or evidence.table_ref != location.table_id
        or evidence.row_path != [entry.label for entry in location.row_path]
        or evidence.column_path != [entry.label for entry in location.column_path]
        or link.matched_source_cell_id != location.source_cell_id
        or link.row_path != location.row_path
        or link.column_path != location.column_path
    ):
        raise NumericMaskingError(
            NumericMaskingFailureCode.CELL_LOCATION_MISMATCH,
            evidence.evidence_id,
        )


def mask_numeric_evidence(
    plan: Plan,
    evidence: Sequence[EvidenceItem],
    locations: Mapping[str, CellLocation],
    schema_links: Sequence[SchemaLinkResult],
    scale_resolutions: Sequence[ScaleUnitResolution],
) -> MaskedEvidenceBundle:
    """Build value-free Programmer evidence in Plan requirement order."""
    if not isinstance(plan, Plan):
        raise SchemaValidationError("plan must be an executable Plan")
    if not isinstance(locations, Mapping):
        raise SchemaValidationError("locations must map evidence IDs to CellLocation")
    if not isinstance(schema_links, (list, tuple)) or not all(
        isinstance(item, SchemaLinkResult) for item in schema_links
    ):
        raise SchemaValidationError(
            "schema_links must contain SchemaLinkResult values"
        )

    evidence_by_id = _evidence_index(evidence)
    scales_by_evidence = _scale_index(scale_resolutions)
    requirements_by_id = {
        requirement.requirement_id: requirement
        for requirement in plan.retrieval_requirements
    }
    links_by_requirement: Dict[str, List[SchemaLinkResult]] = {}
    for link in schema_links:
        if link.requirement_id not in requirements_by_id:
            raise NumericMaskingError(
                NumericMaskingFailureCode.UNKNOWN_REQUIREMENT,
                link.requirement_id,
            )
        links_by_requirement.setdefault(link.requirement_id, []).append(link)

    masked: List[MaskedEvidence] = []
    for requirement in plan.retrieval_requirements:
        links = links_by_requirement.get(requirement.requirement_id, [])
        ambiguous = [
            link for link in links if link.status is SchemaLinkStatus.AMBIGUOUS
        ]
        resolved = [
            link for link in links if link.status is SchemaLinkStatus.RESOLVED
        ]
        unresolved = [
            link for link in links if link.status is SchemaLinkStatus.UNRESOLVED
        ]

        if ambiguous:
            if requirement.required:
                raise NumericMaskingError(
                    NumericMaskingFailureCode.SCHEMA_LINK_AMBIGUOUS,
                    requirement.requirement_id,
                )
            continue
        if len(resolved) > 1 or (resolved and unresolved):
            raise NumericMaskingError(
                NumericMaskingFailureCode.SCHEMA_LINK_CONFLICT,
                requirement.requirement_id,
            )
        if not resolved:
            if not requirement.required:
                continue
            code = (
                NumericMaskingFailureCode.SCHEMA_LINK_UNRESOLVED
                if unresolved
                else NumericMaskingFailureCode.SCHEMA_LINK_MISSING
            )
            raise NumericMaskingError(code, requirement.requirement_id)

        link = resolved[0]
        item = evidence_by_id.get(link.evidence_id)
        if item is None:
            raise NumericMaskingError(
                NumericMaskingFailureCode.EVIDENCE_MISSING,
                link.evidence_id,
            )
        if not isinstance(item.normalized_value, CanonicalDecimal):
            raise NumericMaskingError(
                NumericMaskingFailureCode.EVIDENCE_NOT_NUMERIC,
                item.evidence_id,
            )
        _validate_chain(item, link, locations)

        if plan.requires_scale_resolution:
            resolution = scales_by_evidence.get(item.evidence_id)
            if resolution is None:
                raise NumericMaskingError(
                    NumericMaskingFailureCode.SCALE_RESOLUTION_MISSING,
                    item.evidence_id,
                )
            if resolution.status is not ScaleUnitResolutionStatus.RESOLVED:
                raise NumericMaskingError(
                    NumericMaskingFailureCode.SCALE_RESOLUTION_NOT_RESOLVED,
                    item.evidence_id,
                )

        masked.append(
            MaskedEvidence(
                placeholder=make_value_placeholder(item.evidence_id),
                evidence_id=item.evidence_id,
                requirement_id=requirement.requirement_id,
                metric=link.metric,
                period=link.period,
                row_path=list(link.row_path),
                column_path=list(link.column_path),
            )
        )
    return MaskedEvidenceBundle(items=masked)
