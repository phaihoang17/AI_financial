"""Deterministic TASK-052 execution-only value binding."""

from __future__ import annotations

from enum import Enum
from typing import Dict, Mapping, Sequence

from src.evidence.m5_schemas import (
    BindingMap,
    MaskedEvidenceBundle,
    ScaleUnitResolution,
    ScaleUnitResolutionStatus,
    ValueBinding,
    make_value_placeholder,
)
from src.evidence.schemas import CanonicalDecimal, EvidenceItem
from src.retrieval.evidence import CellLocation
from src.understanding.schemas import SchemaValidationError


class ValueBindingFailureCode(str, Enum):
    PLACEHOLDER_MISSING = "PLACEHOLDER_MISSING"
    PLACEHOLDER_EVIDENCE_MISMATCH = "PLACEHOLDER_EVIDENCE_MISMATCH"
    DUPLICATE_PLACEHOLDER_CONFLICT = "DUPLICATE_PLACEHOLDER_CONFLICT"
    EVIDENCE_MISSING = "EVIDENCE_MISSING"
    EVIDENCE_NOT_NUMERIC = "EVIDENCE_NOT_NUMERIC"
    CELL_LOCATION_MISSING = "CELL_LOCATION_MISSING"
    CELL_LOCATION_MISMATCH = "CELL_LOCATION_MISMATCH"
    SCALE_RESOLUTION_CONFLICT = "SCALE_RESOLUTION_CONFLICT"
    SCALE_RESOLUTION_NOT_RESOLVED = "SCALE_RESOLUTION_NOT_RESOLVED"


class ValueBindingError(SchemaValidationError):
    def __init__(self, code: ValueBindingFailureCode, message: str) -> None:
        self.code = code
        super().__init__(f"{code.value}: {message}")


def _index_evidence(evidence: Sequence[EvidenceItem]) -> Dict[str, EvidenceItem]:
    if not isinstance(evidence, (list, tuple)) or not all(
        isinstance(item, EvidenceItem) for item in evidence
    ):
        raise SchemaValidationError("evidence must contain EvidenceItem values")
    result: Dict[str, EvidenceItem] = {}
    for item in evidence:
        existing = result.get(item.evidence_id)
        if existing is not None and existing.to_dict() != item.to_dict():
            raise ValueBindingError(
                ValueBindingFailureCode.DUPLICATE_PLACEHOLDER_CONFLICT,
                item.evidence_id,
            )
        result[item.evidence_id] = item
    return result


def _index_scales(
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
            raise ValueBindingError(
                ValueBindingFailureCode.SCALE_RESOLUTION_CONFLICT,
                item.evidence_id,
            )
        result[item.evidence_id] = item
    return result


def _validate_location(
    evidence: EvidenceItem,
    locations: Mapping[str, CellLocation],
) -> None:
    location = locations.get(evidence.evidence_id)
    if location is None:
        raise ValueBindingError(
            ValueBindingFailureCode.CELL_LOCATION_MISSING,
            evidence.evidence_id,
        )
    if not isinstance(location, CellLocation) or (
        evidence.report_ref != location.report_id
        or evidence.page_ref != location.page_id
        or evidence.table_ref != location.table_id
        or evidence.row_path != [entry.label for entry in location.row_path]
        or evidence.column_path != [entry.label for entry in location.column_path]
    ):
        raise ValueBindingError(
            ValueBindingFailureCode.CELL_LOCATION_MISMATCH,
            evidence.evidence_id,
        )


def build_binding_map(
    masked: MaskedEvidenceBundle,
    evidence: Sequence[EvidenceItem],
    locations: Mapping[str, CellLocation],
    scale_resolutions: Sequence[ScaleUnitResolution],
) -> BindingMap:
    """Bind masked placeholders to exact values without conversion or code input."""
    if not isinstance(masked, MaskedEvidenceBundle):
        raise SchemaValidationError("masked must be a MaskedEvidenceBundle")
    if not isinstance(locations, Mapping):
        raise SchemaValidationError("locations must map evidence IDs to CellLocation")

    placeholder_targets: Dict[str, str] = {}
    ordered_targets: Dict[str, str] = {}
    for item in masked.items:
        if not isinstance(item.placeholder, str) or not item.placeholder:
            raise ValueBindingError(
                ValueBindingFailureCode.PLACEHOLDER_MISSING,
                item.evidence_id,
            )
        existing = placeholder_targets.get(item.placeholder)
        if existing is not None and existing != item.evidence_id:
            raise ValueBindingError(
                ValueBindingFailureCode.DUPLICATE_PLACEHOLDER_CONFLICT,
                item.placeholder,
            )
        placeholder_targets[item.placeholder] = item.evidence_id
        if item.placeholder != make_value_placeholder(item.evidence_id):
            raise ValueBindingError(
                ValueBindingFailureCode.PLACEHOLDER_EVIDENCE_MISMATCH,
                item.placeholder,
            )
        ordered_targets[item.placeholder] = item.evidence_id

    evidence_by_id = _index_evidence(evidence)
    scales_by_evidence = _index_scales(scale_resolutions)
    bindings = []
    for placeholder, evidence_id in sorted(ordered_targets.items()):
        item = evidence_by_id.get(evidence_id)
        if item is None:
            raise ValueBindingError(
                ValueBindingFailureCode.EVIDENCE_MISSING,
                evidence_id,
            )
        if not isinstance(item.normalized_value, CanonicalDecimal):
            raise ValueBindingError(
                ValueBindingFailureCode.EVIDENCE_NOT_NUMERIC,
                evidence_id,
            )
        _validate_location(item, locations)

        resolution = scales_by_evidence.get(evidence_id)
        if (
            resolution is not None
            and resolution.status is not ScaleUnitResolutionStatus.RESOLVED
        ):
            raise ValueBindingError(
                ValueBindingFailureCode.SCALE_RESOLUTION_NOT_RESOLVED,
                evidence_id,
            )
        bindings.append(
            ValueBinding(
                placeholder=placeholder,
                evidence_id=evidence_id,
                value=item.normalized_value,
                source_scale=(
                    None if resolution is None else resolution.source_scale
                ),
                source_unit=(
                    None if resolution is None else resolution.source_unit
                ),
                requested_output_scale=(
                    None
                    if resolution is None
                    else resolution.requested_output_scale
                ),
                requested_output_unit=(
                    None
                    if resolution is None
                    else resolution.requested_output_unit
                ),
            )
        )
    return BindingMap(bindings=bindings)
