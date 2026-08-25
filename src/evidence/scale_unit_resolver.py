"""Deterministic TASK-054 source-scale and requested-output resolution."""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from src.evidence.m5_schemas import (
    ScaleUnitResolution,
    ScaleUnitResolutionStatus,
)
from src.evidence.schemas import EvidenceItem, Scale
from src.indexing.schemas import NumericParseResult, ScaleHintSource
from src.retrieval.evidence import CellLocation
from src.retrieval.schemas import HintAssociation, RetrievedScaleUnitHint
from src.supervisor.schemas import EvidenceSource
from src.understanding.requested_scale_unit_parser import RequestedScale
from src.understanding.schemas import QueryUnderstanding, SchemaValidationError


_DIRECT_PRECEDENCE = {
    ScaleHintSource.CELL: 0,
    ScaleHintSource.HEADER: 1,
    ScaleHintSource.CAPTION: 2,
    ScaleHintSource.TEXT: 3,
}
_LINKED_PRECEDENCE = 4


def _validate_grounded_pair(evidence: EvidenceItem, location: CellLocation) -> None:
    if not isinstance(evidence, EvidenceItem):
        raise SchemaValidationError("evidence must be an EvidenceItem")
    if not isinstance(location, CellLocation):
        raise SchemaValidationError("location must be a CellLocation")
    if evidence.source_type is not EvidenceSource.TABLE:
        raise SchemaValidationError("CellLocation can resolve only TABLE evidence")
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


def _requested_scale(understanding: QueryUnderstanding) -> Optional[RequestedScale]:
    if understanding.requested_scale is None:
        return None
    try:
        return RequestedScale(understanding.requested_scale)
    except ValueError as error:
        raise SchemaValidationError(
            "requested_scale must be an approved requested output scale"
        ) from error


def _precedence(hint: RetrievedScaleUnitHint) -> int:
    if hint.association is HintAssociation.LINKED:
        return _LINKED_PRECEDENCE
    return _DIRECT_PRECEDENCE[hint.source_kind]


def _hint_order(hint: RetrievedScaleUnitHint) -> Tuple[int, int, int, str]:
    return (
        _precedence(hint),
        hint.source_span.start,
        hint.source_span.end,
        hint.hint_id,
    )


def resolve_scale_unit(
    understanding: QueryUnderstanding,
    evidence: EvidenceItem,
    location: CellLocation,
    hints: Sequence[RetrievedScaleUnitHint],
) -> ScaleUnitResolution:
    """Resolve one grounded cell without mutating evidence or numeric values."""
    if not isinstance(understanding, QueryUnderstanding):
        raise SchemaValidationError("understanding must be a QueryUnderstanding")
    _validate_grounded_pair(evidence, location)
    if not isinstance(hints, (list, tuple)) or not all(
        isinstance(hint, RetrievedScaleUnitHint) for hint in hints
    ):
        raise SchemaValidationError(
            "hints must contain RetrievedScaleUnitHint values"
        )

    scoped_by_id = {
        hint.hint_id: hint
        for hint in hints
        if hint.candidate_id == location.candidate_id
    }
    scoped = sorted(scoped_by_id.values(), key=_hint_order)
    considered_ids = [hint.hint_id for hint in scoped]

    clues: Dict[int, List[RetrievedScaleUnitHint]] = defaultdict(list)
    for hint in scoped:
        if hint.scale_candidate is not None or hint.unit_candidate is not None:
            clues[_precedence(hint)].append(hint)

    numeric = location.numeric
    percent_literal = (
        isinstance(numeric, NumericParseResult) and numeric.percent_literal
    )
    available_levels = set(clues)
    if percent_literal:
        available_levels.add(_DIRECT_PRECEDENCE[ScaleHintSource.CELL])

    output_scale = _requested_scale(understanding)
    output_unit = understanding.requested_unit
    if not available_levels:
        return ScaleUnitResolution(
            evidence_id=evidence.evidence_id,
            status=ScaleUnitResolutionStatus.UNRESOLVED,
            source_scale=None,
            source_unit=None,
            requested_output_scale=output_scale,
            requested_output_unit=output_unit,
            winning_hint_ids=[],
            considered_hint_ids=considered_ids,
        )

    winning_level = min(available_levels)
    winning_hints = clues.get(winning_level, [])
    scales = {
        hint.scale_candidate
        for hint in winning_hints
        if hint.scale_candidate is not None
    }
    units = {
        hint.unit_candidate
        for hint in winning_hints
        if hint.unit_candidate is not None
    }
    if percent_literal and winning_level == _DIRECT_PRECEDENCE[ScaleHintSource.CELL]:
        scales.add(Scale.PERCENT)

    if len(scales) > 1 or len(units) > 1:
        return ScaleUnitResolution(
            evidence_id=evidence.evidence_id,
            status=ScaleUnitResolutionStatus.AMBIGUOUS,
            source_scale=None,
            source_unit=None,
            requested_output_scale=output_scale,
            requested_output_unit=output_unit,
            winning_hint_ids=[],
            considered_hint_ids=considered_ids,
        )

    source_scale = next(iter(scales)) if scales else None
    source_unit = next(iter(units)) if units else None
    return ScaleUnitResolution(
        evidence_id=evidence.evidence_id,
        status=ScaleUnitResolutionStatus.RESOLVED,
        source_scale=source_scale,
        source_unit=source_unit,
        requested_output_scale=output_scale,
        requested_output_unit=output_unit,
        winning_hint_ids=[hint.hint_id for hint in winning_hints],
        considered_hint_ids=considered_ids,
    )


def resolve_scale_units(
    understanding: QueryUnderstanding,
    evidence: Sequence[EvidenceItem],
    locations: Mapping[str, CellLocation],
    hints_by_candidate: Mapping[str, Sequence[RetrievedScaleUnitHint]],
) -> List[ScaleUnitResolution]:
    """Resolve supplied M3 TABLE evidence in stable evidence-id order."""
    if not isinstance(evidence, (list, tuple)):
        raise SchemaValidationError("evidence must be a list")
    results: List[ScaleUnitResolution] = []
    for item in sorted(evidence, key=lambda value: value.evidence_id):
        if not isinstance(item, EvidenceItem):
            raise SchemaValidationError("evidence must contain EvidenceItem values")
        if item.source_type is not EvidenceSource.TABLE:
            continue
        location = locations.get(item.evidence_id)
        if location is None:
            raise SchemaValidationError(
                f"TABLE evidence {item.evidence_id} has no CellLocation"
            )
        results.append(
            resolve_scale_unit(
                understanding,
                item,
                location,
                list(hints_by_candidate.get(location.candidate_id, [])),
            )
        )
    return results
