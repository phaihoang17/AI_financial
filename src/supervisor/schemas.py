"""Validated contracts for the Supervisor / Planner layer."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Any, Dict, List, Mapping, Optional, Set, Type, TypeVar

from src.understanding.schemas import (
    PeriodKind,
    SchemaValidationError,
    StatementScope,
)


class QuestionType(str, Enum):
    LOOKUP = "LOOKUP"
    DERIVED_RATIO = "DERIVED_RATIO"
    MULTI_PERIOD = "MULTI_PERIOD"
    AGGREGATE = "AGGREGATE"


class EvidenceSource(str, Enum):
    TABLE = "TABLE"
    TEXT = "TEXT"


class ReasoningMode(str, Enum):
    DIRECT = "DIRECT"
    PROGRAM = "PROGRAM"
    TABLE_TRANSFORM = "TABLE_TRANSFORM"


class ModelTier(str, Enum):
    CHEAP = "CHEAP"
    STRONG = "STRONG"


class VerifyProfile(str, Enum):
    LIGHT = "LIGHT"
    STRICT = "STRICT"


EnumT = TypeVar("EnumT", bound=Enum)


def _require_mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SchemaValidationError(f"{path} must be an object")
    return value


def _require_exact_keys(
    data: Mapping[str, Any], expected: Set[str], path: str
) -> None:
    actual = set(data.keys())
    missing = expected - actual
    unknown = actual - expected
    if missing:
        raise SchemaValidationError(
            f"{path} is missing fields: {', '.join(sorted(missing))}"
        )
    if unknown:
        raise SchemaValidationError(
            f"{path} has unknown fields: {', '.join(sorted(map(str, unknown)))}"
        )


def _require_string(value: Any, path: str) -> str:
    if not isinstance(value, str):
        raise SchemaValidationError(f"{path} must be a string")
    return value


def _require_optional_string(value: Any, path: str) -> Optional[str]:
    if value is not None and not isinstance(value, str):
        raise SchemaValidationError(f"{path} must be a string or null")
    return value


def _require_string_list(value: Any, path: str) -> List[str]:
    if not isinstance(value, list):
        raise SchemaValidationError(f"{path} must be a list")
    return [_require_string(item, f"{path}[{index}]") for index, item in enumerate(value)]


def _require_bool(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise SchemaValidationError(f"{path} must be a boolean")
    return value


def _require_confidence(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SchemaValidationError(f"{path} must be a number")
    normalized = float(value)
    if not math.isfinite(normalized) or not 0.0 <= normalized <= 1.0:
        raise SchemaValidationError(f"{path} must be between 0 and 1")
    return normalized


def _require_retry_budget(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SchemaValidationError("max_retries must be an integer")
    if value < 0:
        raise SchemaValidationError("max_retries must be non-negative")
    return value


def _parse_enum(value: Any, enum_type: Type[EnumT], path: str) -> EnumT:
    if not isinstance(value, str):
        raise SchemaValidationError(f"{path} must be a string enum value")
    try:
        return enum_type(value)
    except ValueError as error:
        allowed = ", ".join(member.value for member in enum_type)
        raise SchemaValidationError(f"{path} must be one of: {allowed}") from error


def _require_enum(value: Any, enum_type: Type[EnumT], path: str) -> EnumT:
    if not isinstance(value, enum_type):
        raise SchemaValidationError(f"{path} must be a {enum_type.__name__}")
    return value


def _require_enum_list(
    value: Any, enum_type: Type[EnumT], path: str
) -> List[EnumT]:
    if not isinstance(value, list):
        raise SchemaValidationError(f"{path} must be a list")
    return [
        _require_enum(item, enum_type, f"{path}[{index}]")
        for index, item in enumerate(value)
    ]


@dataclass
class PlanCompany:
    name: str
    ticker: str

    def __post_init__(self) -> None:
        self.name = _require_string(self.name, "company.name")
        self.ticker = _require_string(self.ticker, "company.ticker")

    @classmethod
    def from_dict(cls, value: Any) -> PlanCompany:
        data = _require_mapping(value, "company")
        _require_exact_keys(data, {"name", "ticker"}, "company")
        return cls(name=data["name"], ticker=data["ticker"])

    def to_dict(self) -> Dict[str, str]:
        return {"name": self.name, "ticker": self.ticker}


@dataclass
class Plan:
    question_type: QuestionType
    company: PlanCompany
    periods: List[str]
    period_kind: PeriodKind
    statement_scope: StatementScope
    target_metrics: List[str]
    derived_target: Optional[str]
    formula_id: Optional[str]
    tables_needed: List[str]
    evidence_sources: List[EvidenceSource]
    reasoning_mode: ReasoningMode
    requires_scale_resolution: bool
    model_tier: ModelTier
    verify_profile: VerifyProfile
    max_retries: int
    confidence: float
    abstain: bool
    abstain_reason: Optional[str]

    def __post_init__(self) -> None:
        self.question_type = _require_enum(
            self.question_type, QuestionType, "question_type"
        )
        if not isinstance(self.company, PlanCompany):
            raise SchemaValidationError("company must be a PlanCompany")
        self.periods = _require_string_list(self.periods, "periods")
        self.period_kind = _require_enum(
            self.period_kind, PeriodKind, "period_kind"
        )
        self.statement_scope = _require_enum(
            self.statement_scope, StatementScope, "statement_scope"
        )
        self.target_metrics = _require_string_list(
            self.target_metrics, "target_metrics"
        )
        self.derived_target = _require_optional_string(
            self.derived_target, "derived_target"
        )
        self.formula_id = _require_optional_string(self.formula_id, "formula_id")
        self.tables_needed = _require_string_list(
            self.tables_needed, "tables_needed"
        )
        self.evidence_sources = _require_enum_list(
            self.evidence_sources, EvidenceSource, "evidence_sources"
        )
        self.reasoning_mode = _require_enum(
            self.reasoning_mode, ReasoningMode, "reasoning_mode"
        )
        self.requires_scale_resolution = _require_bool(
            self.requires_scale_resolution, "requires_scale_resolution"
        )
        self.model_tier = _require_enum(self.model_tier, ModelTier, "model_tier")
        self.verify_profile = _require_enum(
            self.verify_profile, VerifyProfile, "verify_profile"
        )
        self.max_retries = _require_retry_budget(self.max_retries)
        self.confidence = _require_confidence(self.confidence, "confidence")
        self.abstain = _require_bool(self.abstain, "abstain")
        self.abstain_reason = _require_optional_string(
            self.abstain_reason, "abstain_reason"
        )

    @classmethod
    def from_dict(cls, value: Any) -> Plan:
        data = _require_mapping(value, "Plan")
        _require_exact_keys(
            data,
            {
                "question_type",
                "company",
                "periods",
                "period_kind",
                "statement_scope",
                "target_metrics",
                "derived_target",
                "formula_id",
                "tables_needed",
                "evidence_sources",
                "reasoning_mode",
                "requires_scale_resolution",
                "model_tier",
                "verify_profile",
                "max_retries",
                "confidence",
                "abstain",
                "abstain_reason",
            },
            "Plan",
        )

        evidence_sources = data["evidence_sources"]
        if not isinstance(evidence_sources, list):
            raise SchemaValidationError("evidence_sources must be a list")

        return cls(
            question_type=_parse_enum(
                data["question_type"], QuestionType, "question_type"
            ),
            company=PlanCompany.from_dict(data["company"]),
            periods=data["periods"],
            period_kind=_parse_enum(data["period_kind"], PeriodKind, "period_kind"),
            statement_scope=_parse_enum(
                data["statement_scope"], StatementScope, "statement_scope"
            ),
            target_metrics=data["target_metrics"],
            derived_target=data["derived_target"],
            formula_id=data["formula_id"],
            tables_needed=data["tables_needed"],
            evidence_sources=[
                _parse_enum(source, EvidenceSource, f"evidence_sources[{index}]")
                for index, source in enumerate(evidence_sources)
            ],
            reasoning_mode=_parse_enum(
                data["reasoning_mode"], ReasoningMode, "reasoning_mode"
            ),
            requires_scale_resolution=data["requires_scale_resolution"],
            model_tier=_parse_enum(data["model_tier"], ModelTier, "model_tier"),
            verify_profile=_parse_enum(
                data["verify_profile"], VerifyProfile, "verify_profile"
            ),
            max_retries=data["max_retries"],
            confidence=data["confidence"],
            abstain=data["abstain"],
            abstain_reason=data["abstain_reason"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question_type": self.question_type.value,
            "company": self.company.to_dict(),
            "periods": list(self.periods),
            "period_kind": self.period_kind.value,
            "statement_scope": self.statement_scope.value,
            "target_metrics": list(self.target_metrics),
            "derived_target": self.derived_target,
            "formula_id": self.formula_id,
            "tables_needed": list(self.tables_needed),
            "evidence_sources": [source.value for source in self.evidence_sources],
            "reasoning_mode": self.reasoning_mode.value,
            "requires_scale_resolution": self.requires_scale_resolution,
            "model_tier": self.model_tier.value,
            "verify_profile": self.verify_profile.value,
            "max_retries": self.max_retries,
            "confidence": self.confidence,
            "abstain": self.abstain,
            "abstain_reason": self.abstain_reason,
        }
