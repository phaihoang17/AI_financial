"""Validated contracts for the Supervisor / Planner layer."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import math
from typing import Any, Dict, List, Mapping, Optional, Set, Type, TypeVar

from src.understanding.schemas import (
    Operation,
    PeriodKind,
    SchemaValidationError,
    StatementScope,
)
from src.understanding.planning_gate import PlanningGate


SUPERVISOR_CONTRACT_SCHEMA_VERSION = "m4-supervisor-v1"


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


class TableClass(str, Enum):
    BALANCE_SHEET = "BALANCE_SHEET"
    INCOME_STATEMENT = "INCOME_STATEMENT"
    CASH_FLOW_STATEMENT = "CASH_FLOW_STATEMENT"
    NOTES = "NOTES"


class FormulaPeriodRule(str, Enum):
    SINGLE = "SINGLE"
    EXACT_TWO = "EXACT_TWO"
    AT_LEAST_TWO = "AT_LEAST_TWO"


class SupervisorAbstainReason(str, Enum):
    PLANNING_GATE_BLOCKED = "PLANNING_GATE_BLOCKED"
    UNKNOWN_OPERATION = "UNKNOWN_OPERATION"
    UNSUPPORTED_QUESTION_TYPE = "UNSUPPORTED_QUESTION_TYPE"
    UNSUPPORTED_RATIO = "UNSUPPORTED_RATIO"
    MISSING_FORMULA = "MISSING_FORMULA"
    MISSING_TABLE_MAPPING = "MISSING_TABLE_MAPPING"
    MISSING_STATEMENT_SCOPE = "MISSING_STATEMENT_SCOPE"
    INVALID_PERIOD_REQUIREMENT = "INVALID_PERIOD_REQUIREMENT"
    MIXED_PERIOD_KIND_UNSUPPORTED = "MIXED_PERIOD_KIND_UNSUPPORTED"
    INVALID_PLAN = "INVALID_PLAN"


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


def _require_sha256(value: Any, path: str) -> str:
    value = _require_string(value, path)
    if len(value) != 64 or value != value.lower():
        raise SchemaValidationError(f"{path} must be a lowercase SHA-256")
    try:
        int(value, 16)
    except ValueError as error:
        raise SchemaValidationError(f"{path} must be a lowercase SHA-256") from error
    return value


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


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


@dataclass(frozen=True)
class MetricTableMapping:
    metric: str
    table_class: TableClass

    def __post_init__(self) -> None:
        object.__setattr__(self, "metric", _require_string(self.metric, "metric"))
        object.__setattr__(
            self,
            "table_class",
            _require_enum(self.table_class, TableClass, "table_class"),
        )

    def to_dict(self) -> Dict[str, str]:
        return {"metric": self.metric, "table_class": self.table_class.value}

    @classmethod
    def from_dict(cls, value: Any) -> "MetricTableMapping":
        data = _require_mapping(value, "MetricTableMapping")
        _require_exact_keys(data, {"metric", "table_class"}, "MetricTableMapping")
        return cls(
            metric=data["metric"],
            table_class=_parse_enum(data["table_class"], TableClass, "table_class"),
        )


@dataclass(frozen=True)
class FormulaDefinition:
    formula_id: str
    operation: Operation
    question_type: QuestionType
    derived_target: str
    required_metrics: List[str]
    period_rule: FormulaPeriodRule
    reasoning_mode: ReasoningMode
    evidence_sources: List[EvidenceSource]

    def __post_init__(self) -> None:
        object.__setattr__(self, "formula_id", _require_string(self.formula_id, "formula_id"))
        object.__setattr__(self, "operation", _require_enum(self.operation, Operation, "operation"))
        object.__setattr__(self, "question_type", _require_enum(self.question_type, QuestionType, "question_type"))
        object.__setattr__(self, "derived_target", _require_string(self.derived_target, "derived_target"))
        metrics = _require_string_list(self.required_metrics, "required_metrics")
        if len(metrics) != len(set(metrics)):
            raise SchemaValidationError("required_metrics must be deduplicated")
        object.__setattr__(self, "required_metrics", metrics)
        object.__setattr__(self, "period_rule", _require_enum(self.period_rule, FormulaPeriodRule, "period_rule"))
        if self.reasoning_mode is not ReasoningMode.PROGRAM:
            raise SchemaValidationError("M4 v1 formulas require PROGRAM reasoning")
        object.__setattr__(self, "reasoning_mode", _require_enum(self.reasoning_mode, ReasoningMode, "reasoning_mode"))
        sources = _require_enum_list(self.evidence_sources, EvidenceSource, "evidence_sources")
        if not sources or len(sources) != len(set(sources)):
            raise SchemaValidationError("evidence_sources must be non-empty and deduplicated")
        object.__setattr__(self, "evidence_sources", sources)
        expected_type = {
            Operation.RATIO: QuestionType.DERIVED_RATIO,
            Operation.GROWTH: QuestionType.MULTI_PERIOD,
            Operation.AGGREGATE: QuestionType.AGGREGATE,
        }.get(self.operation)
        if expected_type is None or self.question_type is not expected_type:
            raise SchemaValidationError("formula operation and question_type are incompatible")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "formula_id": self.formula_id,
            "operation": self.operation.value,
            "question_type": self.question_type.value,
            "derived_target": self.derived_target,
            "required_metrics": list(self.required_metrics),
            "period_rule": self.period_rule.value,
            "reasoning_mode": self.reasoning_mode.value,
            "evidence_sources": [source.value for source in self.evidence_sources],
        }

    @classmethod
    def from_dict(cls, value: Any) -> "FormulaDefinition":
        data = _require_mapping(value, "FormulaDefinition")
        _require_exact_keys(
            data,
            {"formula_id", "operation", "question_type", "derived_target", "required_metrics", "period_rule", "reasoning_mode", "evidence_sources"},
            "FormulaDefinition",
        )
        sources = data["evidence_sources"]
        if not isinstance(sources, list):
            raise SchemaValidationError("evidence_sources must be a list")
        return cls(
            formula_id=data["formula_id"],
            operation=_parse_enum(data["operation"], Operation, "operation"),
            question_type=_parse_enum(data["question_type"], QuestionType, "question_type"),
            derived_target=data["derived_target"],
            required_metrics=data["required_metrics"],
            period_rule=_parse_enum(data["period_rule"], FormulaPeriodRule, "period_rule"),
            reasoning_mode=_parse_enum(data["reasoning_mode"], ReasoningMode, "reasoning_mode"),
            evidence_sources=[
                _parse_enum(source, EvidenceSource, f"evidence_sources[{index}]")
                for index, source in enumerate(sources)
            ],
        )


def make_retrieval_requirement_id(
    source_type: EvidenceSource,
    table_class: Optional[TableClass],
    metric: Optional[str],
    period: Optional[str],
    required: bool,
) -> str:
    payload = {
        "schema_version": SUPERVISOR_CONTRACT_SCHEMA_VERSION,
        "source_type": source_type.value,
        "table_class": None if table_class is None else table_class.value,
        "metric": metric,
        "period": period,
        "required": required,
    }
    return sha256(_canonical_json(payload)).hexdigest()


@dataclass(frozen=True)
class RetrievalRequirement:
    requirement_id: str
    source_type: EvidenceSource
    table_class: Optional[TableClass]
    metric: Optional[str]
    period: Optional[str]
    required: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_type", _require_enum(self.source_type, EvidenceSource, "source_type"))
        if self.table_class is not None:
            object.__setattr__(self, "table_class", _require_enum(self.table_class, TableClass, "table_class"))
        object.__setattr__(self, "metric", _require_optional_string(self.metric, "metric"))
        object.__setattr__(self, "period", _require_optional_string(self.period, "period"))
        object.__setattr__(self, "required", _require_bool(self.required, "required"))
        if self.source_type is EvidenceSource.TABLE and (
            self.table_class is None or self.metric is None or self.period is None
        ):
            raise SchemaValidationError("TABLE requirements need table_class, metric, and period")
        expected = make_retrieval_requirement_id(
            self.source_type, self.table_class, self.metric, self.period, self.required
        )
        object.__setattr__(self, "requirement_id", _require_sha256(self.requirement_id, "requirement_id"))
        if self.requirement_id != expected:
            raise SchemaValidationError("requirement_id does not match canonical requirement payload")

    @classmethod
    def create(
        cls,
        source_type: EvidenceSource,
        table_class: Optional[TableClass],
        metric: Optional[str],
        period: Optional[str],
        required: bool = True,
    ) -> "RetrievalRequirement":
        return cls(
            make_retrieval_requirement_id(source_type, table_class, metric, period, required),
            source_type,
            table_class,
            metric,
            period,
            required,
        )

    @classmethod
    def from_dict(cls, value: Any) -> "RetrievalRequirement":
        data = _require_mapping(value, "RetrievalRequirement")
        _require_exact_keys(data, {"requirement_id", "source_type", "table_class", "metric", "period", "required"}, "RetrievalRequirement")
        table_class = data["table_class"]
        return cls(
            data["requirement_id"],
            _parse_enum(data["source_type"], EvidenceSource, "source_type"),
            None if table_class is None else _parse_enum(table_class, TableClass, "table_class"),
            data["metric"],
            data["period"],
            data["required"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "source_type": self.source_type.value,
            "table_class": None if self.table_class is None else self.table_class.value,
            "metric": self.metric,
            "period": self.period,
            "required": self.required,
        }


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
    tables_needed: List[TableClass]
    retrieval_requirements: List[RetrievalRequirement]
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
        self.tables_needed = _require_enum_list(
            self.tables_needed, TableClass, "tables_needed"
        )
        if not isinstance(self.retrieval_requirements, list) or not all(
            isinstance(requirement, RetrievalRequirement)
            for requirement in self.retrieval_requirements
        ):
            raise SchemaValidationError(
                "retrieval_requirements must contain RetrievalRequirement values"
            )
        if not self.retrieval_requirements:
            raise SchemaValidationError("retrieval_requirements must be non-empty")
        requirement_ids = [
            requirement.requirement_id for requirement in self.retrieval_requirements
        ]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise SchemaValidationError("retrieval_requirements must reject exact duplicates")
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
        if self.abstain or self.abstain_reason is not None:
            raise SchemaValidationError("Plan is executable; abstention belongs to SupervisorResult")

        requirement_tables: List[TableClass] = []
        requirement_sources = []
        requirement_metrics = []
        requirement_periods = []
        for requirement in self.retrieval_requirements:
            if requirement.source_type is EvidenceSource.TABLE:
                from src.supervisor.table_registry import table_for_metric
                if table_for_metric(requirement.metric) is not requirement.table_class:
                    raise SchemaValidationError("TABLE requirement must use MetricTableMapping")
            if requirement.table_class is not None and requirement.table_class not in requirement_tables:
                requirement_tables.append(requirement.table_class)
            if requirement.source_type not in requirement_sources:
                requirement_sources.append(requirement.source_type)
            if requirement.metric is not None and requirement.metric not in requirement_metrics:
                requirement_metrics.append(requirement.metric)
            if requirement.period is not None and requirement.period not in requirement_periods:
                requirement_periods.append(requirement.period)
        if self.tables_needed != requirement_tables:
            raise SchemaValidationError("tables_needed must be exactly derived from retrieval_requirements")
        if self.evidence_sources != requirement_sources:
            raise SchemaValidationError("evidence_sources must be exactly derived from retrieval_requirements")
        if self.target_metrics != requirement_metrics:
            raise SchemaValidationError("target_metrics must be exactly derived from retrieval_requirements")
        if self.periods != requirement_periods:
            raise SchemaValidationError("periods must be exactly derived from retrieval_requirements")
        if self.question_type is QuestionType.LOOKUP:
            if self.formula_id is not None or self.derived_target is not None:
                raise SchemaValidationError("LOOKUP requires null formula_id and derived_target")
            if self.reasoning_mode is not ReasoningMode.DIRECT:
                raise SchemaValidationError("LOOKUP requires DIRECT reasoning")
        elif self.formula_id is None:
            if self.question_type is not QuestionType.MULTI_PERIOD or self.derived_target is not None or self.reasoning_mode is not ReasoningMode.DIRECT:
                raise SchemaValidationError("only direct MULTI_PERIOD compare may omit a formula")
        else:
            from src.supervisor.formula_registry import get_formula
            formula = get_formula(self.formula_id)
            if formula is None:
                raise SchemaValidationError("formula_id must exist in FormulaRegistry")
            if self.question_type is not formula.question_type or self.derived_target != formula.derived_target or self.reasoning_mode is not formula.reasoning_mode:
                raise SchemaValidationError("Plan derived fields must match FormulaRegistry")

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
                "retrieval_requirements",
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
        retrieval_requirements = data["retrieval_requirements"]
        if not isinstance(retrieval_requirements, list):
            raise SchemaValidationError("retrieval_requirements must be a list")
        tables_needed = data["tables_needed"]
        if not isinstance(tables_needed, list):
            raise SchemaValidationError("tables_needed must be a list")

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
            tables_needed=[
                _parse_enum(table, TableClass, f"tables_needed[{index}]")
                for index, table in enumerate(tables_needed)
            ],
            retrieval_requirements=[
                RetrievalRequirement.from_dict(item)
                for item in retrieval_requirements
            ],
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
            "tables_needed": [table.value for table in self.tables_needed],
            "retrieval_requirements": [
                requirement.to_dict() for requirement in self.retrieval_requirements
            ],
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


def make_supervisor_input_fingerprint(
    understanding_payload: Mapping[str, Any], planning_gate: PlanningGate
) -> str:
    if not isinstance(understanding_payload, Mapping):
        raise SchemaValidationError("understanding_payload must be an object")
    if not isinstance(planning_gate, PlanningGate):
        raise SchemaValidationError("planning_gate must be a PlanningGate")
    return sha256(
        _canonical_json(
            {
                "schema_version": SUPERVISOR_CONTRACT_SCHEMA_VERSION,
                "query_understanding": dict(understanding_payload),
                "planning_gate": planning_gate.to_dict(),
            }
        )
    ).hexdigest()


@dataclass
class SupervisorResult:
    planning_gate: PlanningGate
    plan: Optional[Plan]
    abstain: bool
    abstain_reason: Optional[SupervisorAbstainReason]
    input_fingerprint: str

    def __post_init__(self) -> None:
        if not isinstance(self.planning_gate, PlanningGate):
            raise SchemaValidationError("planning_gate must be a PlanningGate")
        if self.plan is not None and not isinstance(self.plan, Plan):
            raise SchemaValidationError("plan must be a Plan or null")
        self.abstain = _require_bool(self.abstain, "abstain")
        if self.abstain_reason is not None:
            self.abstain_reason = _require_enum(
                self.abstain_reason, SupervisorAbstainReason, "abstain_reason"
            )
        self.input_fingerprint = _require_sha256(
            self.input_fingerprint, "input_fingerprint"
        )
        if self.abstain:
            if self.plan is not None or self.abstain_reason is None:
                raise SchemaValidationError(
                    "abstention requires null plan and a reason"
                )
        elif self.plan is None or self.abstain_reason is not None:
            raise SchemaValidationError(
                "successful supervision requires a plan and null reason"
            )
        if not self.planning_gate.allowed and self.plan is not None:
            raise SchemaValidationError("blocked PlanningGate cannot produce a Plan")

    @classmethod
    def from_dict(cls, value: Any) -> "SupervisorResult":
        data = _require_mapping(value, "SupervisorResult")
        _require_exact_keys(
            data,
            {"planning_gate", "plan", "abstain", "abstain_reason", "input_fingerprint"},
            "SupervisorResult",
        )
        reason = data["abstain_reason"]
        return cls(
            planning_gate=PlanningGate.from_dict(data["planning_gate"]),
            plan=None if data["plan"] is None else Plan.from_dict(data["plan"]),
            abstain=data["abstain"],
            abstain_reason=None if reason is None else _parse_enum(reason, SupervisorAbstainReason, "abstain_reason"),
            input_fingerprint=data["input_fingerprint"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "planning_gate": self.planning_gate.to_dict(),
            "plan": None if self.plan is None else self.plan.to_dict(),
            "abstain": self.abstain,
            "abstain_reason": None if self.abstain_reason is None else self.abstain_reason.value,
            "input_fingerprint": self.input_fingerprint,
        }
