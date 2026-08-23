"""Validated contracts for the NLU / Understanding layer."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Any, Dict, List, Mapping, Optional, Set, Type, TypeVar


class SchemaValidationError(ValueError):
    """Raised when data does not satisfy the QueryUnderstanding contract."""


class PeriodKind(str, Enum):
    NAM = "NAM"
    QUY = "QUY"
    LUY_KE = "LUY_KE"


class StatementScope(str, Enum):
    HOP_NHAT = "HOP_NHAT"
    RIENG = "RIENG"


class Operation(str, Enum):
    NONE = "none"
    RATIO = "ratio"
    GROWTH = "growth"
    AGGREGATE = "aggregate"
    COMPARE = "compare"
    UNKNOWN = "unknown"


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


def _require_string_list(value: Any, path: str) -> List[str]:
    if not isinstance(value, list):
        raise SchemaValidationError(f"{path} must be a list")
    return [_require_string(item, f"{path}[{index}]") for index, item in enumerate(value)]


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


@dataclass
class CompanyUnderstanding:
    raw: Optional[str]
    name: Optional[str]
    ticker: Optional[str]
    confidence: float

    def __post_init__(self) -> None:
        self.raw = _require_optional_string(self.raw, "company.raw")
        self.name = _require_optional_string(self.name, "company.name")
        self.ticker = _require_optional_string(self.ticker, "company.ticker")
        self.confidence = _require_confidence(self.confidence, "company.confidence")

    @classmethod
    def from_dict(cls, value: Any) -> CompanyUnderstanding:
        data = _require_mapping(value, "company")
        _require_exact_keys(data, {"raw", "name", "ticker", "confidence"}, "company")
        return cls(
            raw=data["raw"],
            name=data["name"],
            ticker=data["ticker"],
            confidence=data["confidence"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "raw": self.raw,
            "name": self.name,
            "ticker": self.ticker,
            "confidence": self.confidence,
        }


@dataclass
class PeriodUnderstanding:
    value: str
    kind: PeriodKind
    raw: Optional[str]

    def __post_init__(self) -> None:
        self.value = _require_string(self.value, "period.value")
        self.kind = _require_enum(self.kind, PeriodKind, "period.kind")
        self.raw = _require_optional_string(self.raw, "period.raw")

    @classmethod
    def from_dict(cls, value: Any, path: str = "period") -> PeriodUnderstanding:
        data = _require_mapping(value, path)
        _require_exact_keys(data, {"value", "kind", "raw"}, path)
        return cls(
            value=data["value"],
            kind=_parse_enum(data["kind"], PeriodKind, f"{path}.kind"),
            raw=data["raw"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {"value": self.value, "kind": self.kind.value, "raw": self.raw}


@dataclass
class StatementScopeUnderstanding:
    value: Optional[StatementScope]
    inferred: bool
    confidence: float

    def __post_init__(self) -> None:
        if self.value is not None:
            self.value = _require_enum(
                self.value, StatementScope, "statement_scope.value"
            )
        self.inferred = _require_bool(self.inferred, "statement_scope.inferred")
        self.confidence = _require_confidence(
            self.confidence, "statement_scope.confidence"
        )

    @classmethod
    def from_dict(cls, value: Any) -> StatementScopeUnderstanding:
        data = _require_mapping(value, "statement_scope")
        _require_exact_keys(
            data, {"value", "inferred", "confidence"}, "statement_scope"
        )
        scope = data["value"]
        return cls(
            value=(
                None
                if scope is None
                else _parse_enum(scope, StatementScope, "statement_scope.value")
            ),
            inferred=data["inferred"],
            confidence=data["confidence"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "value": None if self.value is None else self.value.value,
            "inferred": self.inferred,
            "confidence": self.confidence,
        }


@dataclass
class MetricUnderstanding:
    raw: str
    canonical: str
    confidence: float

    def __post_init__(self) -> None:
        self.raw = _require_string(self.raw, "metric.raw")
        self.canonical = _require_string(self.canonical, "metric.canonical")
        self.confidence = _require_confidence(self.confidence, "metric.confidence")

    @classmethod
    def from_dict(cls, value: Any, path: str = "metric") -> MetricUnderstanding:
        data = _require_mapping(value, path)
        _require_exact_keys(data, {"raw", "canonical", "confidence"}, path)
        return cls(
            raw=data["raw"],
            canonical=data["canonical"],
            confidence=data["confidence"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "raw": self.raw,
            "canonical": self.canonical,
            "confidence": self.confidence,
        }


@dataclass
class QueryUnderstanding:
    raw_question: str
    company: CompanyUnderstanding
    periods: List[PeriodUnderstanding]
    statement_scope: StatementScopeUnderstanding
    metrics: List[MetricUnderstanding]
    operation: Operation
    requested_scale: Optional[str]
    requested_unit: Optional[str]
    missing_information: List[str]
    ambiguities: List[str]
    confidence: float

    def __post_init__(self) -> None:
        self.raw_question = _require_string(self.raw_question, "raw_question")
        if not isinstance(self.company, CompanyUnderstanding):
            raise SchemaValidationError("company must be a CompanyUnderstanding")
        if not isinstance(self.periods, list):
            raise SchemaValidationError("periods must be a list")
        if not all(isinstance(period, PeriodUnderstanding) for period in self.periods):
            raise SchemaValidationError("periods must contain PeriodUnderstanding values")
        if not isinstance(self.statement_scope, StatementScopeUnderstanding):
            raise SchemaValidationError(
                "statement_scope must be a StatementScopeUnderstanding"
            )
        if not isinstance(self.metrics, list):
            raise SchemaValidationError("metrics must be a list")
        if not all(isinstance(metric, MetricUnderstanding) for metric in self.metrics):
            raise SchemaValidationError("metrics must contain MetricUnderstanding values")
        self.operation = _require_enum(self.operation, Operation, "operation")
        self.requested_scale = _require_optional_string(
            self.requested_scale, "requested_scale"
        )
        self.requested_unit = _require_optional_string(
            self.requested_unit, "requested_unit"
        )
        self.missing_information = _require_string_list(
            self.missing_information, "missing_information"
        )
        self.ambiguities = _require_string_list(self.ambiguities, "ambiguities")
        self.confidence = _require_confidence(self.confidence, "confidence")

    @classmethod
    def from_dict(cls, value: Any) -> QueryUnderstanding:
        data = _require_mapping(value, "QueryUnderstanding")
        _require_exact_keys(
            data,
            {
                "raw_question",
                "company",
                "periods",
                "statement_scope",
                "metrics",
                "operation",
                "requested_scale",
                "requested_unit",
                "missing_information",
                "ambiguities",
                "confidence",
            },
            "QueryUnderstanding",
        )

        periods = data["periods"]
        if not isinstance(periods, list):
            raise SchemaValidationError("periods must be a list")
        metrics = data["metrics"]
        if not isinstance(metrics, list):
            raise SchemaValidationError("metrics must be a list")

        return cls(
            raw_question=data["raw_question"],
            company=CompanyUnderstanding.from_dict(data["company"]),
            periods=[
                PeriodUnderstanding.from_dict(period, f"periods[{index}]")
                for index, period in enumerate(periods)
            ],
            statement_scope=StatementScopeUnderstanding.from_dict(
                data["statement_scope"]
            ),
            metrics=[
                MetricUnderstanding.from_dict(metric, f"metrics[{index}]")
                for index, metric in enumerate(metrics)
            ],
            operation=_parse_enum(data["operation"], Operation, "operation"),
            requested_scale=data["requested_scale"],
            requested_unit=data["requested_unit"],
            missing_information=data["missing_information"],
            ambiguities=data["ambiguities"],
            confidence=data["confidence"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "raw_question": self.raw_question,
            "company": self.company.to_dict(),
            "periods": [period.to_dict() for period in self.periods],
            "statement_scope": self.statement_scope.to_dict(),
            "metrics": [metric.to_dict() for metric in self.metrics],
            "operation": self.operation.value,
            "requested_scale": self.requested_scale,
            "requested_unit": self.requested_unit,
            "missing_information": list(self.missing_information),
            "ambiguities": list(self.ambiguities),
            "confidence": self.confidence,
        }
