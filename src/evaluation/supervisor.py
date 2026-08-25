"""Exact, deterministic Supervisor evaluation contracts and metrics."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.supervisor.planner import supervise_batch1
from src.supervisor.schemas import (
    EvidenceSource,
    ModelTier,
    QuestionType,
    ReasoningMode,
    RetrievalRequirement,
    SupervisorAbstainReason,
    SupervisorResult,
    TableClass,
    VerifyProfile,
)
from src.understanding.planning_gate import PlanningGate
from src.understanding.schemas import (
    QueryUnderstanding,
    SchemaValidationError,
    _parse_enum,
    _require_bool,
    _require_enum,
    _require_exact_keys,
    _require_mapping,
    _require_optional_string,
    _require_string,
)


SUPERVISOR_EVALUATION_VERSION = 1


class SupervisorField(str, Enum):
    ABSTAIN = "abstain"
    QUESTION_TYPE = "question_type"
    REASONING_MODE = "reasoning_mode"
    FORMULA_ID = "formula_id"
    TARGET_METRICS = "target_metrics"
    PERIODS = "periods"
    TABLES_NEEDED = "tables_needed"
    EVIDENCE_SOURCES = "evidence_sources"
    RETRIEVAL_REQUIREMENTS = "retrieval_requirements"
    REQUIRES_SCALE_RESOLUTION = "requires_scale_resolution"
    MODEL_TIER = "model_tier"
    VERIFY_PROFILE = "verify_profile"


_FIELD_ORDER: Tuple[SupervisorField, ...] = tuple(SupervisorField)


def _require_non_empty_string(value: Any, path: str) -> str:
    value = _require_string(value, path)
    if not value.strip():
        raise SchemaValidationError(f"{path} must be non-empty")
    return value


def _require_version(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SchemaValidationError("version must be a positive integer")
    return value


def _require_string_list(value: Any, path: str) -> List[str]:
    if not isinstance(value, list):
        raise SchemaValidationError(f"{path} must be a list")
    result = [_require_string(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if len(result) != len(set(result)):
        raise SchemaValidationError(f"{path} must be deduplicated")
    return result


def _require_enum_list(value: Any, enum_type, path: str) -> list:
    if not isinstance(value, list):
        raise SchemaValidationError(f"{path} must be a list")
    result = [
        _require_enum(item, enum_type, f"{path}[{index}]")
        for index, item in enumerate(value)
    ]
    if len(result) != len(set(result)):
        raise SchemaValidationError(f"{path} must be deduplicated")
    return result


@dataclass(frozen=True)
class ExpectedRetrievalRequirement:
    """RetrievalRequirement expectation without its derived identity field."""

    source_type: EvidenceSource
    table_class: Optional[TableClass]
    metric: Optional[str]
    period: Optional[str]
    required: bool

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_type",
            _require_enum(self.source_type, EvidenceSource, "source_type"),
        )
        if self.table_class is not None:
            object.__setattr__(
                self,
                "table_class",
                _require_enum(self.table_class, TableClass, "table_class"),
            )
        object.__setattr__(
            self, "metric", _require_optional_string(self.metric, "metric")
        )
        object.__setattr__(
            self, "period", _require_optional_string(self.period, "period")
        )
        object.__setattr__(self, "required", _require_bool(self.required, "required"))
        if self.source_type is EvidenceSource.TABLE and (
            self.table_class is None or self.metric is None or self.period is None
        ):
            raise SchemaValidationError(
                "expected TABLE requirements need table_class, metric, and period"
            )

    @classmethod
    def from_requirement(
        cls, requirement: RetrievalRequirement
    ) -> "ExpectedRetrievalRequirement":
        if not isinstance(requirement, RetrievalRequirement):
            raise SchemaValidationError(
                "requirement must be a RetrievalRequirement"
            )
        return cls(
            source_type=requirement.source_type,
            table_class=requirement.table_class,
            metric=requirement.metric,
            period=requirement.period,
            required=requirement.required,
        )

    @classmethod
    def from_dict(cls, value: Any) -> "ExpectedRetrievalRequirement":
        data = _require_mapping(value, "ExpectedRetrievalRequirement")
        _require_exact_keys(
            data,
            {"source_type", "table_class", "metric", "period", "required"},
            "ExpectedRetrievalRequirement",
        )
        table_class = data["table_class"]
        return cls(
            source_type=_parse_enum(
                data["source_type"], EvidenceSource, "source_type"
            ),
            table_class=(
                None
                if table_class is None
                else _parse_enum(table_class, TableClass, "table_class")
            ),
            metric=data["metric"],
            period=data["period"],
            required=data["required"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_type": self.source_type.value,
            "table_class": (
                None if self.table_class is None else self.table_class.value
            ),
            "metric": self.metric,
            "period": self.period,
            "required": self.required,
        }


@dataclass
class ExpectedSupervisorResult:
    """Typed value stored under SupervisorEvaluationCase.expected."""

    abstain: bool
    abstain_reason: Optional[SupervisorAbstainReason]
    question_type: Optional[QuestionType]
    reasoning_mode: Optional[ReasoningMode]
    formula_id: Optional[str]
    target_metrics: List[str]
    periods: List[str]
    tables_needed: List[TableClass]
    evidence_sources: List[EvidenceSource]
    retrieval_requirements: List[ExpectedRetrievalRequirement]
    requires_scale_resolution: Optional[bool]
    model_tier: Optional[ModelTier]
    verify_profile: Optional[VerifyProfile]

    def __post_init__(self) -> None:
        self.abstain = _require_bool(self.abstain, "expected.abstain")
        if self.abstain_reason is not None:
            self.abstain_reason = _require_enum(
                self.abstain_reason,
                SupervisorAbstainReason,
                "expected.abstain_reason",
            )
        if self.question_type is not None:
            self.question_type = _require_enum(
                self.question_type, QuestionType, "expected.question_type"
            )
        if self.reasoning_mode is not None:
            self.reasoning_mode = _require_enum(
                self.reasoning_mode, ReasoningMode, "expected.reasoning_mode"
            )
        self.formula_id = _require_optional_string(
            self.formula_id, "expected.formula_id"
        )
        self.target_metrics = _require_string_list(
            self.target_metrics, "expected.target_metrics"
        )
        self.periods = _require_string_list(self.periods, "expected.periods")
        self.tables_needed = _require_enum_list(
            self.tables_needed, TableClass, "expected.tables_needed"
        )
        self.evidence_sources = _require_enum_list(
            self.evidence_sources, EvidenceSource, "expected.evidence_sources"
        )
        if not isinstance(self.retrieval_requirements, list) or not all(
            isinstance(item, ExpectedRetrievalRequirement)
            for item in self.retrieval_requirements
        ):
            raise SchemaValidationError(
                "expected.retrieval_requirements must contain ExpectedRetrievalRequirement values"
            )
        if self.requires_scale_resolution is not None:
            self.requires_scale_resolution = _require_bool(
                self.requires_scale_resolution,
                "expected.requires_scale_resolution",
            )
        if self.model_tier is not None:
            self.model_tier = _require_enum(
                self.model_tier, ModelTier, "expected.model_tier"
            )
        if self.verify_profile is not None:
            self.verify_profile = _require_enum(
                self.verify_profile, VerifyProfile, "expected.verify_profile"
            )

        plan_scalars = (
            self.question_type,
            self.reasoning_mode,
            self.requires_scale_resolution,
            self.model_tier,
            self.verify_profile,
        )
        plan_lists = (
            self.target_metrics,
            self.periods,
            self.tables_needed,
            self.evidence_sources,
            self.retrieval_requirements,
        )
        if self.abstain:
            if self.abstain_reason is None:
                raise SchemaValidationError(
                    "abstaining expectation requires abstain_reason"
                )
            if any(value is not None for value in plan_scalars) or self.formula_id is not None:
                raise SchemaValidationError(
                    "abstaining expectation cannot contain plan scalar fields"
                )
            if any(plan_lists):
                raise SchemaValidationError(
                    "abstaining expectation cannot contain plan list fields"
                )
        else:
            if self.abstain_reason is not None:
                raise SchemaValidationError(
                    "successful expectation requires null abstain_reason"
                )
            if any(value is None for value in plan_scalars):
                raise SchemaValidationError(
                    "successful expectation requires all non-formula plan scalar fields"
                )
            if not all(plan_lists):
                raise SchemaValidationError(
                    "successful expectation requires non-empty plan list fields"
                )

    @classmethod
    def abstention(
        cls, reason: SupervisorAbstainReason
    ) -> "ExpectedSupervisorResult":
        return cls(
            abstain=True,
            abstain_reason=reason,
            question_type=None,
            reasoning_mode=None,
            formula_id=None,
            target_metrics=[],
            periods=[],
            tables_needed=[],
            evidence_sources=[],
            retrieval_requirements=[],
            requires_scale_resolution=None,
            model_tier=None,
            verify_profile=None,
        )

    @classmethod
    def from_dict(cls, value: Any) -> "ExpectedSupervisorResult":
        data = _require_mapping(value, "expected")
        _require_exact_keys(
            data,
            {
                "abstain",
                "abstain_reason",
                "question_type",
                "reasoning_mode",
                "formula_id",
                "target_metrics",
                "periods",
                "tables_needed",
                "evidence_sources",
                "retrieval_requirements",
                "requires_scale_resolution",
                "model_tier",
                "verify_profile",
            },
            "expected",
        )
        requirements = data["retrieval_requirements"]
        if not isinstance(requirements, list):
            raise SchemaValidationError(
                "expected.retrieval_requirements must be a list"
            )
        return cls(
            abstain=data["abstain"],
            abstain_reason=(
                None
                if data["abstain_reason"] is None
                else _parse_enum(
                    data["abstain_reason"],
                    SupervisorAbstainReason,
                    "expected.abstain_reason",
                )
            ),
            question_type=(
                None
                if data["question_type"] is None
                else _parse_enum(
                    data["question_type"],
                    QuestionType,
                    "expected.question_type",
                )
            ),
            reasoning_mode=(
                None
                if data["reasoning_mode"] is None
                else _parse_enum(
                    data["reasoning_mode"],
                    ReasoningMode,
                    "expected.reasoning_mode",
                )
            ),
            formula_id=data["formula_id"],
            target_metrics=data["target_metrics"],
            periods=data["periods"],
            tables_needed=[
                _parse_enum(item, TableClass, f"expected.tables_needed[{index}]")
                for index, item in enumerate(data["tables_needed"])
            ],
            evidence_sources=[
                _parse_enum(
                    item,
                    EvidenceSource,
                    f"expected.evidence_sources[{index}]",
                )
                for index, item in enumerate(data["evidence_sources"])
            ],
            retrieval_requirements=[
                ExpectedRetrievalRequirement.from_dict(item)
                for item in requirements
            ],
            requires_scale_resolution=data["requires_scale_resolution"],
            model_tier=(
                None
                if data["model_tier"] is None
                else _parse_enum(
                    data["model_tier"], ModelTier, "expected.model_tier"
                )
            ),
            verify_profile=(
                None
                if data["verify_profile"] is None
                else _parse_enum(
                    data["verify_profile"],
                    VerifyProfile,
                    "expected.verify_profile",
                )
            ),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "abstain": self.abstain,
            "abstain_reason": (
                None if self.abstain_reason is None else self.abstain_reason.value
            ),
            "question_type": (
                None if self.question_type is None else self.question_type.value
            ),
            "reasoning_mode": (
                None if self.reasoning_mode is None else self.reasoning_mode.value
            ),
            "formula_id": self.formula_id,
            "target_metrics": list(self.target_metrics),
            "periods": list(self.periods),
            "tables_needed": [item.value for item in self.tables_needed],
            "evidence_sources": [item.value for item in self.evidence_sources],
            "retrieval_requirements": [
                item.to_dict() for item in self.retrieval_requirements
            ],
            "requires_scale_resolution": self.requires_scale_resolution,
            "model_tier": (
                None if self.model_tier is None else self.model_tier.value
            ),
            "verify_profile": (
                None if self.verify_profile is None else self.verify_profile.value
            ),
        }


@dataclass
class SupervisorEvaluationCase:
    case_id: str
    version: int
    query_understanding: QueryUnderstanding
    planning_gate: PlanningGate
    expected: ExpectedSupervisorResult

    def __post_init__(self) -> None:
        self.case_id = _require_non_empty_string(self.case_id, "case_id")
        self.version = _require_version(self.version)
        if not isinstance(self.query_understanding, QueryUnderstanding):
            raise SchemaValidationError(
                "query_understanding must be a QueryUnderstanding"
            )
        if not isinstance(self.planning_gate, PlanningGate):
            raise SchemaValidationError("planning_gate must be a PlanningGate")
        if not isinstance(self.expected, ExpectedSupervisorResult):
            raise SchemaValidationError(
                "expected must be an ExpectedSupervisorResult"
            )

    @classmethod
    def from_dict(cls, value: Any) -> "SupervisorEvaluationCase":
        data = _require_mapping(value, "SupervisorEvaluationCase")
        _require_exact_keys(
            data,
            {
                "case_id",
                "version",
                "query_understanding",
                "planning_gate",
                "expected",
            },
            "SupervisorEvaluationCase",
        )
        return cls(
            case_id=data["case_id"],
            version=data["version"],
            query_understanding=QueryUnderstanding.from_dict(
                data["query_understanding"]
            ),
            planning_gate=PlanningGate.from_dict(data["planning_gate"]),
            expected=ExpectedSupervisorResult.from_dict(data["expected"]),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "version": self.version,
            "query_understanding": self.query_understanding.to_dict(),
            "planning_gate": self.planning_gate.to_dict(),
            "expected": self.expected.to_dict(),
        }


@dataclass(frozen=True)
class SupervisorFieldComparison:
    field: SupervisorField
    matches: bool

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "field", _require_enum(self.field, SupervisorField, "field")
        )
        object.__setattr__(self, "matches", _require_bool(self.matches, "matches"))

    @classmethod
    def from_dict(cls, value: Any) -> "SupervisorFieldComparison":
        data = _require_mapping(value, "SupervisorFieldComparison")
        _require_exact_keys(
            data, {"field", "matches"}, "SupervisorFieldComparison"
        )
        return cls(
            field=_parse_enum(data["field"], SupervisorField, "field"),
            matches=data["matches"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {"field": self.field.value, "matches": self.matches}


def _actual_plan_value(actual: SupervisorResult, field: SupervisorField) -> Any:
    plan = actual.plan
    if field is SupervisorField.ABSTAIN:
        return None
    if plan is None:
        if field in {
            SupervisorField.TARGET_METRICS,
            SupervisorField.PERIODS,
            SupervisorField.TABLES_NEEDED,
            SupervisorField.EVIDENCE_SOURCES,
            SupervisorField.RETRIEVAL_REQUIREMENTS,
        }:
            return []
        return None
    if field is SupervisorField.QUESTION_TYPE:
        return plan.question_type
    if field is SupervisorField.REASONING_MODE:
        return plan.reasoning_mode
    if field is SupervisorField.FORMULA_ID:
        return plan.formula_id
    if field is SupervisorField.TARGET_METRICS:
        return list(plan.target_metrics)
    if field is SupervisorField.PERIODS:
        return list(plan.periods)
    if field is SupervisorField.TABLES_NEEDED:
        return list(plan.tables_needed)
    if field is SupervisorField.EVIDENCE_SOURCES:
        return list(plan.evidence_sources)
    if field is SupervisorField.RETRIEVAL_REQUIREMENTS:
        return [
            ExpectedRetrievalRequirement.from_requirement(item)
            for item in plan.retrieval_requirements
        ]
    if field is SupervisorField.REQUIRES_SCALE_RESOLUTION:
        return plan.requires_scale_resolution
    if field is SupervisorField.MODEL_TIER:
        return plan.model_tier
    return plan.verify_profile


def _expected_value(
    expected: ExpectedSupervisorResult, field: SupervisorField
) -> Any:
    if field is SupervisorField.QUESTION_TYPE:
        return expected.question_type
    if field is SupervisorField.REASONING_MODE:
        return expected.reasoning_mode
    if field is SupervisorField.FORMULA_ID:
        return expected.formula_id
    if field is SupervisorField.TARGET_METRICS:
        return expected.target_metrics
    if field is SupervisorField.PERIODS:
        return expected.periods
    if field is SupervisorField.TABLES_NEEDED:
        return expected.tables_needed
    if field is SupervisorField.EVIDENCE_SOURCES:
        return expected.evidence_sources
    if field is SupervisorField.RETRIEVAL_REQUIREMENTS:
        return expected.retrieval_requirements
    if field is SupervisorField.REQUIRES_SCALE_RESOLUTION:
        return expected.requires_scale_resolution
    if field is SupervisorField.MODEL_TIER:
        return expected.model_tier
    return expected.verify_profile


def _field_matches(
    case: SupervisorEvaluationCase,
    actual: SupervisorResult,
    field: SupervisorField,
) -> bool:
    expected = case.expected
    if field is SupervisorField.ABSTAIN:
        return (
            actual.abstain is expected.abstain
            and (actual.plan is None) is expected.abstain
            and actual.abstain_reason is expected.abstain_reason
        )
    return _actual_plan_value(actual, field) == _expected_value(expected, field)


def _expected_comparisons(
    case: SupervisorEvaluationCase, actual: SupervisorResult
) -> List[SupervisorFieldComparison]:
    return [
        SupervisorFieldComparison(field, _field_matches(case, actual, field))
        for field in _FIELD_ORDER
    ]


@dataclass
class SupervisorEvaluationCaseResult:
    case: SupervisorEvaluationCase
    actual: SupervisorResult
    field_comparisons: List[SupervisorFieldComparison]
    passed: bool

    def __post_init__(self) -> None:
        if not isinstance(self.case, SupervisorEvaluationCase):
            raise SchemaValidationError(
                "case must be a SupervisorEvaluationCase"
            )
        if not isinstance(self.actual, SupervisorResult):
            raise SchemaValidationError("actual must be a SupervisorResult")
        if not isinstance(self.field_comparisons, list) or not all(
            isinstance(item, SupervisorFieldComparison)
            for item in self.field_comparisons
        ):
            raise SchemaValidationError(
                "field_comparisons must contain SupervisorFieldComparison values"
            )
        fields = [item.field for item in self.field_comparisons]
        if fields != list(_FIELD_ORDER):
            raise SchemaValidationError(
                "field_comparisons must contain each field once in canonical order"
            )
        expected = _expected_comparisons(self.case, self.actual)
        if self.field_comparisons != expected:
            raise SchemaValidationError(
                "field_comparisons must equal exact structural comparisons"
            )
        self.passed = _require_bool(self.passed, "passed")
        if self.passed is not all(item.matches for item in expected):
            raise SchemaValidationError(
                "passed must equal the conjunction of field comparisons"
            )

    @classmethod
    def from_dict(cls, value: Any) -> "SupervisorEvaluationCaseResult":
        data = _require_mapping(value, "SupervisorEvaluationCaseResult")
        _require_exact_keys(
            data,
            {"case", "actual", "field_comparisons", "passed"},
            "SupervisorEvaluationCaseResult",
        )
        comparisons = data["field_comparisons"]
        if not isinstance(comparisons, list):
            raise SchemaValidationError("field_comparisons must be a list")
        return cls(
            case=SupervisorEvaluationCase.from_dict(data["case"]),
            actual=SupervisorResult.from_dict(data["actual"]),
            field_comparisons=[
                SupervisorFieldComparison.from_dict(item) for item in comparisons
            ],
            passed=data["passed"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case": self.case.to_dict(),
            "actual": self.actual.to_dict(),
            "field_comparisons": [
                item.to_dict() for item in self.field_comparisons
            ],
            "passed": self.passed,
        }


@dataclass(frozen=True)
class SupervisorEvaluationReport:
    results: Tuple[SupervisorEvaluationCaseResult, ...]

    def __post_init__(self) -> None:
        if not self.results or not all(
            isinstance(item, SupervisorEvaluationCaseResult)
            for item in self.results
        ):
            raise SchemaValidationError(
                "results must contain SupervisorEvaluationCaseResult values"
            )
        identities = [(item.case.case_id, item.case.version) for item in self.results]
        if len(identities) != len(set(identities)):
            raise SchemaValidationError("evaluation case identities must be unique")

    @property
    def case_count(self) -> int:
        return len(self.results)

    @property
    def passed_count(self) -> int:
        return sum(item.passed for item in self.results)

    @property
    def failed_count(self) -> int:
        return self.case_count - self.passed_count

    @property
    def exact_match_rate(self) -> float:
        return self.passed_count / self.case_count

    @property
    def per_field_accuracy(self) -> Dict[str, float]:
        return {
            field.value: sum(
                comparison.matches
                for result in self.results
                for comparison in result.field_comparisons
                if comparison.field is field
            )
            / self.case_count
            for field in _FIELD_ORDER
        }

    @property
    def abstain_precision(self) -> float:
        predicted = [item for item in self.results if item.actual.abstain]
        if not predicted:
            return 1.0
        true_positive = sum(item.case.expected.abstain for item in predicted)
        return true_positive / len(predicted)

    @property
    def abstain_recall(self) -> float:
        expected = [item for item in self.results if item.case.expected.abstain]
        if not expected:
            return 1.0
        true_positive = sum(item.actual.abstain for item in expected)
        return true_positive / len(expected)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_count": self.case_count,
            "passed_count": self.passed_count,
            "failed_count": self.failed_count,
            "exact_match_rate": self.exact_match_rate,
            "per_field_accuracy": self.per_field_accuracy,
            "abstain_precision": self.abstain_precision,
            "abstain_recall": self.abstain_recall,
            "results": [item.to_dict() for item in self.results],
        }


def compare_supervisor_case(
    case: SupervisorEvaluationCase, actual: SupervisorResult
) -> SupervisorEvaluationCaseResult:
    """Compare one supplied result without normalization or downstream work."""
    if not isinstance(case, SupervisorEvaluationCase):
        raise SchemaValidationError("case must be a SupervisorEvaluationCase")
    if not isinstance(actual, SupervisorResult):
        raise SchemaValidationError("actual must be a SupervisorResult")
    comparisons = _expected_comparisons(case, actual)
    return SupervisorEvaluationCaseResult(
        case=case,
        actual=actual,
        field_comparisons=comparisons,
        passed=all(item.matches for item in comparisons),
    )


def evaluate_supervisor_case(
    case: SupervisorEvaluationCase,
) -> SupervisorEvaluationCaseResult:
    """Run only the deterministic Supervisor and compare its structured result."""
    if not isinstance(case, SupervisorEvaluationCase):
        raise SchemaValidationError("case must be a SupervisorEvaluationCase")
    actual = supervise_batch1(case.query_understanding, case.planning_gate)
    return compare_supervisor_case(case, actual)


def evaluate_supervisor_cases(
    cases: Sequence[SupervisorEvaluationCase],
) -> SupervisorEvaluationReport:
    if not cases:
        raise SchemaValidationError("cases must be non-empty")
    return SupervisorEvaluationReport(
        tuple(evaluate_supervisor_case(case) for case in cases)
    )
