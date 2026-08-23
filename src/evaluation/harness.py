"""Validated contracts and path boundaries for the evaluation harness."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

from src.understanding.schemas import (
    SchemaValidationError,
    _parse_enum,
    _require_bool,
    _require_enum,
    _require_exact_keys,
    _require_mapping,
    _require_optional_string,
)


class EvaluationMode(str, Enum):
    NLU = "NLU"
    SUPERVISOR = "SUPERVISOR"
    RETRIEVAL = "RETRIEVAL"
    ORACLE_EVIDENCE = "ORACLE_EVIDENCE"
    RETRIEVED_EVIDENCE_E2E = "RETRIEVED_EVIDENCE_E2E"


class EvaluationFailureStage(str, Enum):
    NLU = "NLU"
    SUPERVISOR = "SUPERVISOR"
    RETRIEVAL = "RETRIEVAL"
    EVIDENCE = "EVIDENCE"
    PROGRAMMER = "PROGRAMMER"
    SANDBOX = "SANDBOX"
    VERIFICATION = "VERIFICATION"


@dataclass
class EvaluationResult:
    mode: EvaluationMode
    passed: bool
    failure_stage: Optional[EvaluationFailureStage]
    failure_reason: Optional[str]

    def __post_init__(self) -> None:
        self.mode = _require_enum(self.mode, EvaluationMode, "mode")
        self.passed = _require_bool(self.passed, "passed")
        if self.failure_stage is not None:
            self.failure_stage = _require_enum(
                self.failure_stage,
                EvaluationFailureStage,
                "failure_stage",
            )
        self.failure_reason = _require_optional_string(
            self.failure_reason, "failure_reason"
        )

        if self.passed:
            if self.failure_stage is not None or self.failure_reason is not None:
                raise SchemaValidationError(
                    "passed evaluation requires null failure fields"
                )
        else:
            if self.failure_stage is None:
                raise SchemaValidationError(
                    "failed evaluation requires a failure_stage"
                )
            if self.failure_reason is None or not self.failure_reason.strip():
                raise SchemaValidationError(
                    "failed evaluation requires a non-empty failure_reason"
                )

    @classmethod
    def from_dict(cls, value: Any) -> EvaluationResult:
        data = _require_mapping(value, "EvaluationResult")
        _require_exact_keys(
            data,
            {"mode", "passed", "failure_stage", "failure_reason"},
            "EvaluationResult",
        )
        failure_stage = data["failure_stage"]
        return cls(
            mode=_parse_enum(data["mode"], EvaluationMode, "mode"),
            passed=data["passed"],
            failure_stage=(
                None
                if failure_stage is None
                else _parse_enum(
                    failure_stage,
                    EvaluationFailureStage,
                    "failure_stage",
                )
            ),
            failure_reason=data["failure_reason"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode.value,
            "passed": self.passed,
            "failure_stage": (
                None if self.failure_stage is None else self.failure_stage.value
            ),
            "failure_reason": self.failure_reason,
        }


_STAGE_LEVEL_MODES = frozenset(
    {
        EvaluationMode.NLU,
        EvaluationMode.SUPERVISOR,
        EvaluationMode.RETRIEVAL,
    }
)


def stage_level_path(result: EvaluationResult) -> EvaluationResult:
    """Accept a result only through a documented stage-level mode."""
    if not isinstance(result, EvaluationResult):
        raise SchemaValidationError("result must be an EvaluationResult")
    if result.mode not in _STAGE_LEVEL_MODES:
        raise SchemaValidationError("mode is not a stage-level evaluation mode")
    return result


def oracle_evidence_path(result: EvaluationResult) -> EvaluationResult:
    """Accept only the oracle-evidence end-to-end path."""
    if not isinstance(result, EvaluationResult):
        raise SchemaValidationError("result must be an EvaluationResult")
    if result.mode is not EvaluationMode.ORACLE_EVIDENCE:
        raise SchemaValidationError("mode is not ORACLE_EVIDENCE")
    return result


def retrieved_evidence_e2e_path(result: EvaluationResult) -> EvaluationResult:
    """Accept only the retrieved-evidence end-to-end path."""
    if not isinstance(result, EvaluationResult):
        raise SchemaValidationError("result must be an EvaluationResult")
    if result.mode is not EvaluationMode.RETRIEVED_EVIDENCE_E2E:
        raise SchemaValidationError("mode is not RETRIEVED_EVIDENCE_E2E")
    return result
