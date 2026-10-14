"""Text-to-Pandas transport and trace contracts (ADR-069).

``PandasProgram`` is only a transport envelope around executable Pandas code:
it has no operator vocabulary and is never interpreted. Its metadata
(datasets, expected result type and unit) is checked against the deterministic
understanding and the executed result; the code itself is what runs.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.understanding.schemas import SchemaValidationError

ENVELOPE_KEYS = frozenset({
    "status", "selected_datasets", "language", "execution_engine", "code", "expected_result_type",
    "result_unit", "explanation",
})


class ProgramStatus(str, Enum):
    OK = "OK"
    UNSUPPORTED = "UNSUPPORTED"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"


class ResultType(str, Enum):
    SCALAR = "scalar"
    SERIES = "series"
    DATAFRAME = "dataframe"


RESULT_UNITS = frozenset({"VND", "VND/share", "PERCENT", "TIMES", "COUNT", "TEXT", "YEAR", "MIXED", "NONE"})


@dataclass(frozen=True)
class PandasProgram:
    status: ProgramStatus
    selected_datasets: tuple[str, ...]
    code: str
    expected_result_type: ResultType | None
    result_unit: str | None
    explanation: str
    language: str = "python"
    execution_engine: str = "pandas"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "selected_datasets": list(self.selected_datasets),
            "language": self.language,
            "execution_engine": self.execution_engine,
            "code": self.code,
            "expected_result_type": None if self.expected_result_type is None else self.expected_result_type.value,
            "result_unit": self.result_unit,
            "explanation": self.explanation,
        }

    @classmethod
    def from_dict(cls, value: Any, *, dataset_names: tuple[str, ...]) -> PandasProgram:
        if not isinstance(value, Mapping):
            raise SchemaValidationError("envelope must be a JSON object")
        unknown = set(value) - ENVELOPE_KEYS
        missing = {"status", "code"} - set(value)
        if unknown or missing:
            raise SchemaValidationError(
                f"envelope keys invalid (unknown: {sorted(unknown)}, missing: {sorted(missing)})"
            )
        try:
            status = ProgramStatus(value["status"])
        except ValueError as error:
            raise SchemaValidationError("status must be OK, UNSUPPORTED or NEEDS_CLARIFICATION") from error
        if value.get("language", "python") != "python" or value.get("execution_engine", "pandas") != "pandas":
            raise SchemaValidationError("language must be python and execution_engine pandas")
        datasets = value.get("selected_datasets") or []
        if not isinstance(datasets, list) or any(not isinstance(name, str) or name not in dataset_names for name in datasets) \
                or len(datasets) != len(set(datasets)):
            raise SchemaValidationError(f"selected_datasets must name only: {', '.join(dataset_names)}")
        code = value["code"] if isinstance(value["code"], str) else None
        if code is None:
            raise SchemaValidationError("code must be a string")
        explanation = value.get("explanation") or ""
        if not isinstance(explanation, str):
            raise SchemaValidationError("explanation must be a string")
        result_type = None
        unit = value.get("result_unit")
        if status is ProgramStatus.OK:
            if not code.strip():
                raise SchemaValidationError("status OK requires code")
            try:
                result_type = ResultType(value.get("expected_result_type"))
            except ValueError as error:
                raise SchemaValidationError("expected_result_type must be scalar, series or dataframe") from error
            if not isinstance(unit, str) or unit not in RESULT_UNITS:
                raise SchemaValidationError(f"result_unit must be one of {sorted(RESULT_UNITS)}")
        return cls(status, tuple(datasets), code, result_type, unit, explanation)


class AttemptStage(str, Enum):
    GENERATION = "GENERATION"
    VALIDATION = "VALIDATION"
    EXECUTION = "EXECUTION"
    VERIFICATION = "VERIFICATION"
    PASSED = "PASSED"
    DECLINED = "DECLINED"


@dataclass
class AttemptRecord:
    """One generate -> validate -> execute -> verify attempt, kept for the trace."""

    attempt: int
    program: dict[str, Any] | None = None
    outcome: AttemptStage = AttemptStage.GENERATION
    failure_code: str | None = None
    failure_message: str | None = None
    validation: dict[str, Any] | None = None
    execution: dict[str, Any] | None = None
    verification: dict[str, Any] | None = None
    generation_ms: int = 0
    execution_ms: int = 0
    llm_usage: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt": self.attempt,
            "program": self.program,
            "outcome": self.outcome.value,
            "failure_code": self.failure_code,
            "failure_message": self.failure_message,
            "validation": self.validation,
            "execution": self.execution,
            "verification": self.verification,
            "generation_ms": self.generation_ms,
            "execution_ms": self.execution_ms,
            "llm_usage": self.llm_usage,
        }


class ResponseStatus(str, Enum):
    ANSWERED = "ANSWERED"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    UNSUPPORTED = "UNSUPPORTED"
    NO_DATA = "NO_DATA"
    FAILED = "FAILED"
