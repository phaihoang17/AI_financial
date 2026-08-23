"""Contracts for reasoning-result evaluation without defining trace schemas."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional, Protocol

from src.understanding.schemas import (
    SchemaValidationError,
    _parse_enum,
    _require_bool,
    _require_enum,
    _require_exact_keys,
    _require_mapping,
)


class TraceComparison(str, Enum):
    EXACT = "EXACT"
    NORMALIZED_EQUIVALENT = "NORMALIZED_EQUIVALENT"
    DIFFERENT = "DIFFERENT"


class TraceEquivalenceHook(Protocol):
    def __call__(self, expected_trace: Any, actual_trace: Any) -> bool:
        """Return whether two non-identical opaque traces are equivalent."""


@dataclass
class ReasoningEvaluationResult:
    execution_correct: bool
    trace_comparison: TraceComparison
    answer_correct: bool

    def __post_init__(self) -> None:
        self.execution_correct = _require_bool(
            self.execution_correct, "execution_correct"
        )
        self.trace_comparison = _require_enum(
            self.trace_comparison, TraceComparison, "trace_comparison"
        )
        self.answer_correct = _require_bool(self.answer_correct, "answer_correct")

    @classmethod
    def from_dict(cls, value: Any) -> ReasoningEvaluationResult:
        data = _require_mapping(value, "ReasoningEvaluationResult")
        _require_exact_keys(
            data,
            {"execution_correct", "trace_comparison", "answer_correct"},
            "ReasoningEvaluationResult",
        )
        return cls(
            execution_correct=data["execution_correct"],
            trace_comparison=_parse_enum(
                data["trace_comparison"], TraceComparison, "trace_comparison"
            ),
            answer_correct=data["answer_correct"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "execution_correct": self.execution_correct,
            "trace_comparison": self.trace_comparison.value,
            "answer_correct": self.answer_correct,
        }


def compare_traces(
    expected_trace: Any,
    actual_trace: Any,
    equivalence_hook: Optional[TraceEquivalenceHook] = None,
) -> TraceComparison:
    """Compare opaque traces exactly, then through an optional equivalence hook."""
    exact_match = expected_trace == actual_trace
    if not isinstance(exact_match, bool):
        raise SchemaValidationError("exact trace comparison must return a boolean")
    if exact_match:
        return TraceComparison.EXACT

    if equivalence_hook is None:
        return TraceComparison.DIFFERENT
    if not callable(equivalence_hook):
        raise SchemaValidationError("equivalence_hook must be callable")

    equivalent = equivalence_hook(expected_trace, actual_trace)
    if not isinstance(equivalent, bool):
        raise SchemaValidationError("equivalence_hook must return a boolean")
    if equivalent:
        return TraceComparison.NORMALIZED_EQUIVALENT
    return TraceComparison.DIFFERENT
