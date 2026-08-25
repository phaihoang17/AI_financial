"""Canonical M5 scale-resolution and schema-linking contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import re
from typing import Any, Dict, List, Mapping, Optional, Set

from src.evidence.schemas import CanonicalDecimal, Scale
from src.indexing.schemas import HeaderPathEntry
from src.understanding.requested_scale_unit_parser import RequestedScale
from src.understanding.schemas import SchemaValidationError


M5_MASKING_CONTRACT_VERSION = "m5-numeric-masking-v1"
_PLACEHOLDER_PATTERN = re.compile(r"val_[0-9a-f]{64}\Z")


class ScaleUnitResolutionStatus(str, Enum):
    RESOLVED = "RESOLVED"
    UNRESOLVED = "UNRESOLVED"
    AMBIGUOUS = "AMBIGUOUS"


class SchemaLinkStatus(str, Enum):
    RESOLVED = "RESOLVED"
    UNRESOLVED = "UNRESOLVED"
    AMBIGUOUS = "AMBIGUOUS"


class SchemaLinkMatchBasis(str, Enum):
    EXACT_METRIC_PATH = "EXACT_METRIC_PATH"
    EXACT_PERIOD_PATH = "EXACT_PERIOD_PATH"
    EXACT_METRIC_AND_PERIOD = "EXACT_METRIC_AND_PERIOD"
    STRUCTURAL = "STRUCTURAL"
    NONE = "NONE"


def make_value_placeholder(evidence_id: str) -> str:
    """Return the canonical full-SHA placeholder for one evidence identity."""
    evidence_id = _string(evidence_id, "evidence_id", non_empty=True)
    payload = json.dumps(
        {
            "contract_version": M5_MASKING_CONTRACT_VERSION,
            "evidence_id": evidence_id,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"val_{sha256(payload).hexdigest()}"


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SchemaValidationError(f"{path} must be an object")
    return value


def _exact_keys(data: Mapping[str, Any], expected: Set[str], path: str) -> None:
    missing = expected - set(data)
    unknown = set(data) - expected
    if missing:
        raise SchemaValidationError(
            f"{path} is missing fields: {', '.join(sorted(missing))}"
        )
    if unknown:
        raise SchemaValidationError(
            f"{path} has unknown fields: {', '.join(sorted(map(str, unknown)))}"
        )


def _string(value: Any, path: str, *, non_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise SchemaValidationError(f"{path} must be a string")
    if non_empty and not value:
        raise SchemaValidationError(f"{path} must be non-empty")
    return value


def _optional_string(value: Any, path: str) -> Optional[str]:
    if value is not None and not isinstance(value, str):
        raise SchemaValidationError(f"{path} must be a string or null")
    return value


def _enum(value: Any, enum_type: type[Enum], path: str) -> Enum:
    if not isinstance(value, enum_type):
        raise SchemaValidationError(f"{path} must be a {enum_type.__name__}")
    return value


def _parse_enum(value: Any, enum_type: type[Enum], path: str) -> Enum:
    if not isinstance(value, str):
        raise SchemaValidationError(f"{path} must be a string enum value")
    try:
        return enum_type(value)
    except ValueError as error:
        allowed = ", ".join(member.value for member in enum_type)
        raise SchemaValidationError(f"{path} must be one of: {allowed}") from error


def _string_ids(value: Any, path: str) -> List[str]:
    if not isinstance(value, list):
        raise SchemaValidationError(f"{path} must be a list")
    result = [_string(item, f"{path}[{index}]", non_empty=True) for index, item in enumerate(value)]
    if len(result) != len(set(result)):
        raise SchemaValidationError(f"{path} must not contain duplicates")
    return result


def _header_path(value: Any, path: str) -> List[HeaderPathEntry]:
    if not isinstance(value, list):
        raise SchemaValidationError(f"{path} must be a list")
    return [
        item
        if isinstance(item, HeaderPathEntry)
        else HeaderPathEntry.from_dict(item, f"{path}[{index}]")
        for index, item in enumerate(value)
    ]


@dataclass
class ScaleUnitResolution:
    """A provenance-preserving source-scale decision for one evidence item."""

    evidence_id: str
    status: ScaleUnitResolutionStatus
    source_scale: Optional[Scale]
    source_unit: Optional[str]
    requested_output_scale: Optional[RequestedScale]
    requested_output_unit: Optional[str]
    winning_hint_ids: List[str]
    considered_hint_ids: List[str]

    def __post_init__(self) -> None:
        self.evidence_id = _string(self.evidence_id, "evidence_id", non_empty=True)
        self.status = _enum(
            self.status, ScaleUnitResolutionStatus, "status"
        )  # type: ignore[assignment]
        if self.source_scale is not None:
            self.source_scale = _enum(
                self.source_scale, Scale, "source_scale"
            )  # type: ignore[assignment]
            if self.source_scale is Scale.OTHER:
                raise SchemaValidationError("source_scale cannot be OTHER")
        self.source_unit = _optional_string(self.source_unit, "source_unit")
        if self.requested_output_scale is not None:
            self.requested_output_scale = _enum(
                self.requested_output_scale,
                RequestedScale,
                "requested_output_scale",
            )  # type: ignore[assignment]
        self.requested_output_unit = _optional_string(
            self.requested_output_unit, "requested_output_unit"
        )
        self.winning_hint_ids = _string_ids(
            self.winning_hint_ids, "winning_hint_ids"
        )
        self.considered_hint_ids = _string_ids(
            self.considered_hint_ids, "considered_hint_ids"
        )
        if not set(self.winning_hint_ids).issubset(self.considered_hint_ids):
            raise SchemaValidationError(
                "winning_hint_ids must be a subset of considered_hint_ids"
            )
        if self.status is ScaleUnitResolutionStatus.RESOLVED:
            if self.source_scale is None and self.source_unit is None:
                raise SchemaValidationError(
                    "RESOLVED requires source_scale or source_unit"
                )
        else:
            if self.source_scale is not None or self.source_unit is not None:
                raise SchemaValidationError(
                    "UNRESOLVED and AMBIGUOUS require null source values"
                )
            if self.winning_hint_ids:
                raise SchemaValidationError(
                    "UNRESOLVED and AMBIGUOUS require no winning hints"
                )

    @classmethod
    def from_dict(cls, value: Any) -> "ScaleUnitResolution":
        data = _mapping(value, "ScaleUnitResolution")
        _exact_keys(
            data,
            {
                "evidence_id",
                "status",
                "source_scale",
                "source_unit",
                "requested_output_scale",
                "requested_output_unit",
                "winning_hint_ids",
                "considered_hint_ids",
            },
            "ScaleUnitResolution",
        )
        source_scale = data["source_scale"]
        output_scale = data["requested_output_scale"]
        return cls(
            evidence_id=data["evidence_id"],
            status=_parse_enum(
                data["status"], ScaleUnitResolutionStatus, "status"
            ),
            source_scale=(
                None
                if source_scale is None
                else _parse_enum(source_scale, Scale, "source_scale")
            ),
            source_unit=data["source_unit"],
            requested_output_scale=(
                None
                if output_scale is None
                else _parse_enum(
                    output_scale, RequestedScale, "requested_output_scale"
                )
            ),
            requested_output_unit=data["requested_output_unit"],
            winning_hint_ids=data["winning_hint_ids"],
            considered_hint_ids=data["considered_hint_ids"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "status": self.status.value,
            "source_scale": None if self.source_scale is None else self.source_scale.value,
            "source_unit": self.source_unit,
            "requested_output_scale": (
                None
                if self.requested_output_scale is None
                else self.requested_output_scale.value
            ),
            "requested_output_unit": self.requested_output_unit,
            "winning_hint_ids": list(self.winning_hint_ids),
            "considered_hint_ids": list(self.considered_hint_ids),
        }


@dataclass
class SchemaLinkResult:
    """An exact link from one Plan requirement to grounded M3 evidence."""

    requirement_id: str
    evidence_id: str
    status: SchemaLinkStatus
    metric: Optional[str]
    period: Optional[str]
    row_path: List[HeaderPathEntry]
    column_path: List[HeaderPathEntry]
    matched_source_cell_id: Optional[str]
    match_basis: SchemaLinkMatchBasis

    def __post_init__(self) -> None:
        self.requirement_id = _string(
            self.requirement_id, "requirement_id", non_empty=True
        )
        self.evidence_id = _string(self.evidence_id, "evidence_id", non_empty=True)
        self.status = _enum(
            self.status, SchemaLinkStatus, "status"
        )  # type: ignore[assignment]
        self.metric = _optional_string(self.metric, "metric")
        self.period = _optional_string(self.period, "period")
        self.row_path = _header_path(self.row_path, "row_path")
        self.column_path = _header_path(self.column_path, "column_path")
        self.matched_source_cell_id = _optional_string(
            self.matched_source_cell_id, "matched_source_cell_id"
        )
        self.match_basis = _enum(
            self.match_basis, SchemaLinkMatchBasis, "match_basis"
        )  # type: ignore[assignment]
        if self.status is not SchemaLinkStatus.UNRESOLVED:
            if self.matched_source_cell_id is None:
                raise SchemaValidationError(
                    "RESOLVED and AMBIGUOUS require matched_source_cell_id"
                )
            if self.match_basis in {
                SchemaLinkMatchBasis.NONE,
                SchemaLinkMatchBasis.STRUCTURAL,
            }:
                raise SchemaValidationError(
                    "RESOLVED and AMBIGUOUS require an exact match basis"
                )

    @classmethod
    def from_dict(cls, value: Any) -> "SchemaLinkResult":
        data = _mapping(value, "SchemaLinkResult")
        _exact_keys(
            data,
            {
                "requirement_id",
                "evidence_id",
                "status",
                "metric",
                "period",
                "row_path",
                "column_path",
                "matched_source_cell_id",
                "match_basis",
            },
            "SchemaLinkResult",
        )
        return cls(
            requirement_id=data["requirement_id"],
            evidence_id=data["evidence_id"],
            status=_parse_enum(data["status"], SchemaLinkStatus, "status"),
            metric=data["metric"],
            period=data["period"],
            row_path=_header_path(data["row_path"], "row_path"),
            column_path=_header_path(data["column_path"], "column_path"),
            matched_source_cell_id=data["matched_source_cell_id"],
            match_basis=_parse_enum(
                data["match_basis"], SchemaLinkMatchBasis, "match_basis"
            ),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "evidence_id": self.evidence_id,
            "status": self.status.value,
            "metric": self.metric,
            "period": self.period,
            "row_path": [entry.to_dict() for entry in self.row_path],
            "column_path": [entry.to_dict() for entry in self.column_path],
            "matched_source_cell_id": self.matched_source_cell_id,
            "match_basis": self.match_basis.value,
        }


@dataclass
class MaskedEvidence:
    """Programmer-facing evidence with no raw or normalized numeric value."""

    placeholder: str
    evidence_id: str
    requirement_id: str
    metric: Optional[str]
    period: Optional[str]
    row_path: List[HeaderPathEntry]
    column_path: List[HeaderPathEntry]

    def __post_init__(self) -> None:
        self.placeholder = _string(
            self.placeholder, "placeholder", non_empty=True
        )
        self.evidence_id = _string(self.evidence_id, "evidence_id", non_empty=True)
        self.requirement_id = _string(
            self.requirement_id, "requirement_id", non_empty=True
        )
        if not _PLACEHOLDER_PATTERN.fullmatch(self.placeholder):
            raise SchemaValidationError(
                "placeholder must use val_<full_sha256> format"
            )
        if self.placeholder != make_value_placeholder(self.evidence_id):
            raise SchemaValidationError(
                "placeholder must match evidence_id and masking contract version"
            )
        self.metric = _optional_string(self.metric, "metric")
        self.period = _optional_string(self.period, "period")
        self.row_path = _header_path(self.row_path, "row_path")
        self.column_path = _header_path(self.column_path, "column_path")

    @classmethod
    def from_dict(cls, value: Any) -> "MaskedEvidence":
        data = _mapping(value, "MaskedEvidence")
        _exact_keys(
            data,
            {
                "placeholder",
                "evidence_id",
                "requirement_id",
                "metric",
                "period",
                "row_path",
                "column_path",
            },
            "MaskedEvidence",
        )
        return cls(
            placeholder=data["placeholder"],
            evidence_id=data["evidence_id"],
            requirement_id=data["requirement_id"],
            metric=data["metric"],
            period=data["period"],
            row_path=_header_path(data["row_path"], "row_path"),
            column_path=_header_path(data["column_path"], "column_path"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "placeholder": self.placeholder,
            "evidence_id": self.evidence_id,
            "requirement_id": self.requirement_id,
            "metric": self.metric,
            "period": self.period,
            "row_path": [entry.to_dict() for entry in self.row_path],
            "column_path": [entry.to_dict() for entry in self.column_path],
        }


@dataclass
class MaskedEvidenceBundle:
    """The complete programmer-facing numeric evidence boundary."""

    items: List[MaskedEvidence]

    def __post_init__(self) -> None:
        if not isinstance(self.items, list) or not all(
            isinstance(item, MaskedEvidence) for item in self.items
        ):
            raise SchemaValidationError(
                "items must contain MaskedEvidence values"
            )
        placeholder_evidence: Dict[str, str] = {}
        seen_pairs = set()
        for item in self.items:
            existing = placeholder_evidence.get(item.placeholder)
            if existing is not None and existing != item.evidence_id:
                raise SchemaValidationError(
                    "one placeholder cannot map to multiple evidence IDs"
                )
            placeholder_evidence[item.placeholder] = item.evidence_id
            pair = (item.requirement_id, item.evidence_id)
            if pair in seen_pairs:
                raise SchemaValidationError(
                    "items must not duplicate a requirement/evidence pair"
                )
            seen_pairs.add(pair)

    @classmethod
    def from_dict(cls, value: Any) -> "MaskedEvidenceBundle":
        data = _mapping(value, "MaskedEvidenceBundle")
        _exact_keys(data, {"items"}, "MaskedEvidenceBundle")
        items = data["items"]
        if not isinstance(items, list):
            raise SchemaValidationError("items must be a list")
        return cls(
            items=[MaskedEvidence.from_dict(item) for item in items]
        )

    def to_dict(self) -> Dict[str, Any]:
        return {"items": [item.to_dict() for item in self.items]}


@dataclass
class ValueBinding:
    """Execution-only value and scale metadata for one placeholder."""

    placeholder: str
    evidence_id: str
    value: CanonicalDecimal
    source_scale: Optional[Scale]
    source_unit: Optional[str]
    requested_output_scale: Optional[RequestedScale]
    requested_output_unit: Optional[str]

    def __post_init__(self) -> None:
        self.placeholder = _string(
            self.placeholder, "placeholder", non_empty=True
        )
        self.evidence_id = _string(self.evidence_id, "evidence_id", non_empty=True)
        if not _PLACEHOLDER_PATTERN.fullmatch(self.placeholder):
            raise SchemaValidationError(
                "placeholder must use val_<full_sha256> format"
            )
        if self.placeholder != make_value_placeholder(self.evidence_id):
            raise SchemaValidationError(
                "placeholder must match evidence_id and masking contract version"
            )
        if not isinstance(self.value, CanonicalDecimal):
            raise SchemaValidationError("value must be a CanonicalDecimal")
        if self.source_scale is not None:
            self.source_scale = _enum(
                self.source_scale, Scale, "source_scale"
            )  # type: ignore[assignment]
            if self.source_scale is Scale.OTHER:
                raise SchemaValidationError("source_scale cannot be OTHER")
        self.source_unit = _optional_string(self.source_unit, "source_unit")
        if self.requested_output_scale is not None:
            self.requested_output_scale = _enum(
                self.requested_output_scale,
                RequestedScale,
                "requested_output_scale",
            )  # type: ignore[assignment]
        self.requested_output_unit = _optional_string(
            self.requested_output_unit, "requested_output_unit"
        )

    @classmethod
    def from_dict(cls, value: Any) -> "ValueBinding":
        data = _mapping(value, "ValueBinding")
        _exact_keys(
            data,
            {
                "placeholder",
                "evidence_id",
                "value",
                "source_scale",
                "source_unit",
                "requested_output_scale",
                "requested_output_unit",
            },
            "ValueBinding",
        )
        source_scale = data["source_scale"]
        requested_scale = data["requested_output_scale"]
        value_string = _string(data["value"], "value")
        return cls(
            placeholder=data["placeholder"],
            evidence_id=data["evidence_id"],
            value=CanonicalDecimal(value_string),
            source_scale=(
                None
                if source_scale is None
                else _parse_enum(source_scale, Scale, "source_scale")
            ),
            source_unit=data["source_unit"],
            requested_output_scale=(
                None
                if requested_scale is None
                else _parse_enum(
                    requested_scale, RequestedScale, "requested_output_scale"
                )
            ),
            requested_output_unit=data["requested_output_unit"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "placeholder": self.placeholder,
            "evidence_id": self.evidence_id,
            "value": self.value,
            "source_scale": None if self.source_scale is None else self.source_scale.value,
            "source_unit": self.source_unit,
            "requested_output_scale": (
                None
                if self.requested_output_scale is None
                else self.requested_output_scale.value
            ),
            "requested_output_unit": self.requested_output_unit,
        }


@dataclass
class BindingMap:
    """Execution-only bindings; this contract must not enter Programmer input."""

    bindings: List[ValueBinding]

    def __post_init__(self) -> None:
        if not isinstance(self.bindings, list) or not all(
            isinstance(binding, ValueBinding) for binding in self.bindings
        ):
            raise SchemaValidationError(
                "bindings must contain ValueBinding values"
            )
        placeholders = [binding.placeholder for binding in self.bindings]
        if len(placeholders) != len(set(placeholders)):
            raise SchemaValidationError("bindings must use unique placeholders")
        if placeholders != sorted(placeholders):
            raise SchemaValidationError(
                "bindings must be ordered by placeholder"
            )

    @classmethod
    def from_dict(cls, value: Any) -> "BindingMap":
        data = _mapping(value, "BindingMap")
        _exact_keys(data, {"bindings"}, "BindingMap")
        bindings = data["bindings"]
        if not isinstance(bindings, list):
            raise SchemaValidationError("bindings must be a list")
        return cls(bindings=[ValueBinding.from_dict(item) for item in bindings])

    def to_dict(self) -> Dict[str, Any]:
        return {"bindings": [binding.to_dict() for binding in self.bindings]}
