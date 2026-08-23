"""Exact field-level evaluation contracts for QueryUnderstanding v1."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Tuple

from src.understanding.schemas import (
    QueryUnderstanding,
    SchemaValidationError,
    _parse_enum,
    _require_bool,
    _require_enum,
    _require_exact_keys,
    _require_mapping,
    _require_string,
)


class NLUEvaluationField(str, Enum):
    COMPANY = "COMPANY"
    PERIODS = "PERIODS"
    STATEMENT_SCOPE = "STATEMENT_SCOPE"
    METRICS = "METRICS"
    OPERATION = "OPERATION"
    MISSING_INFORMATION = "MISSING_INFORMATION"
    AMBIGUITIES = "AMBIGUITIES"


_FIELD_ORDER: Tuple[NLUEvaluationField, ...] = tuple(NLUEvaluationField)


@dataclass
class NLUFieldComparison:
    field: NLUEvaluationField
    matches: bool

    def __post_init__(self) -> None:
        self.field = _require_enum(self.field, NLUEvaluationField, "field")
        self.matches = _require_bool(self.matches, "matches")

    @classmethod
    def from_dict(cls, value: Any, path: str = "NLUFieldComparison") -> NLUFieldComparison:
        data = _require_mapping(value, path)
        _require_exact_keys(data, {"field", "matches"}, path)
        return cls(
            field=_parse_enum(data["field"], NLUEvaluationField, f"{path}.field"),
            matches=data["matches"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {"field": self.field.value, "matches": self.matches}


@dataclass
class NLUEvaluationCase:
    case_id: str
    question: str
    expected: QueryUnderstanding

    def __post_init__(self) -> None:
        self.case_id = _require_string(self.case_id, "case_id")
        if not self.case_id.strip():
            raise SchemaValidationError("case_id must be non-empty")
        self.question = _require_string(self.question, "question")
        if not isinstance(self.expected, QueryUnderstanding):
            raise SchemaValidationError(
                "expected must be a QueryUnderstanding"
            )
        if self.question != self.expected.raw_question:
            raise SchemaValidationError(
                "question must equal expected.raw_question"
            )

    @classmethod
    def from_dict(cls, value: Any) -> NLUEvaluationCase:
        data = _require_mapping(value, "NLUEvaluationCase")
        _require_exact_keys(
            data, {"case_id", "question", "expected"}, "NLUEvaluationCase"
        )
        return cls(
            case_id=data["case_id"],
            question=data["question"],
            expected=QueryUnderstanding.from_dict(data["expected"]),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "question": self.question,
            "expected": self.expected.to_dict(),
        }


def _exact_field_matches(
    field: NLUEvaluationField,
    expected: QueryUnderstanding,
    actual: QueryUnderstanding,
) -> bool:
    if field is NLUEvaluationField.COMPANY:
        return expected.company.to_dict() == actual.company.to_dict()
    if field is NLUEvaluationField.PERIODS:
        return [item.to_dict() for item in expected.periods] == [
            item.to_dict() for item in actual.periods
        ]
    if field is NLUEvaluationField.STATEMENT_SCOPE:
        return (
            expected.statement_scope.to_dict()
            == actual.statement_scope.to_dict()
        )
    if field is NLUEvaluationField.METRICS:
        return [item.to_dict() for item in expected.metrics] == [
            item.to_dict() for item in actual.metrics
        ]
    if field is NLUEvaluationField.OPERATION:
        return expected.operation is actual.operation
    if field is NLUEvaluationField.MISSING_INFORMATION:
        return expected.missing_information == actual.missing_information
    return expected.ambiguities == actual.ambiguities


def _expected_comparisons(
    expected: QueryUnderstanding, actual: QueryUnderstanding
) -> List[NLUFieldComparison]:
    return [
        NLUFieldComparison(
            field=field,
            matches=_exact_field_matches(field, expected, actual),
        )
        for field in _FIELD_ORDER
    ]


@dataclass
class NLUEvaluationResult:
    case: NLUEvaluationCase
    actual: QueryUnderstanding
    field_comparisons: List[NLUFieldComparison]

    def __post_init__(self) -> None:
        if not isinstance(self.case, NLUEvaluationCase):
            raise SchemaValidationError("case must be an NLUEvaluationCase")
        if not isinstance(self.actual, QueryUnderstanding):
            raise SchemaValidationError("actual must be a QueryUnderstanding")
        if not isinstance(self.field_comparisons, list) or not all(
            isinstance(comparison, NLUFieldComparison)
            for comparison in self.field_comparisons
        ):
            raise SchemaValidationError(
                "field_comparisons must be a list of NLUFieldComparison values"
            )

        fields = [comparison.field for comparison in self.field_comparisons]
        if fields != list(_FIELD_ORDER):
            raise SchemaValidationError(
                "field_comparisons must contain each approved field once in canonical order"
            )
        expected = _expected_comparisons(self.case.expected, self.actual)
        if self.field_comparisons != expected:
            raise SchemaValidationError(
                "field_comparisons must equal exact structural comparisons"
            )

    @classmethod
    def from_dict(cls, value: Any) -> NLUEvaluationResult:
        data = _require_mapping(value, "NLUEvaluationResult")
        _require_exact_keys(
            data,
            {"case", "actual", "field_comparisons"},
            "NLUEvaluationResult",
        )
        comparisons = data["field_comparisons"]
        if not isinstance(comparisons, list):
            raise SchemaValidationError("field_comparisons must be a list")
        return cls(
            case=NLUEvaluationCase.from_dict(data["case"]),
            actual=QueryUnderstanding.from_dict(data["actual"]),
            field_comparisons=[
                NLUFieldComparison.from_dict(
                    comparison, f"field_comparisons[{index}]"
                )
                for index, comparison in enumerate(comparisons)
            ],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case": self.case.to_dict(),
            "actual": self.actual.to_dict(),
            "field_comparisons": [
                comparison.to_dict() for comparison in self.field_comparisons
            ],
        }


def evaluate_nlu_case(
    case: NLUEvaluationCase, actual: QueryUnderstanding
) -> NLUEvaluationResult:
    """Compare only the approved fields, without evaluator-side normalization."""
    if not isinstance(case, NLUEvaluationCase):
        raise SchemaValidationError("case must be an NLUEvaluationCase")
    if not isinstance(actual, QueryUnderstanding):
        raise SchemaValidationError("actual must be a QueryUnderstanding")
    return NLUEvaluationResult(
        case=case,
        actual=actual,
        field_comparisons=_expected_comparisons(case.expected, actual),
    )
