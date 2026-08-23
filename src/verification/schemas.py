"""Validated result contracts for verification outcomes."""

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


class VerificationFailureCategory(str, Enum):
    GROUNDING = "GROUNDING"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NUMERIC = "NUMERIC"
    SCALE_UNIT = "SCALE_UNIT"
    FINANCIAL_LOGIC = "FINANCIAL_LOGIC"


@dataclass
class VerificationResult:
    passed: bool
    failure_category: Optional[VerificationFailureCategory]
    failure_reason: Optional[str]

    def __post_init__(self) -> None:
        self.passed = _require_bool(self.passed, "passed")
        if self.failure_category is not None:
            self.failure_category = _require_enum(
                self.failure_category,
                VerificationFailureCategory,
                "failure_category",
            )
        self.failure_reason = _require_optional_string(
            self.failure_reason, "failure_reason"
        )

        if self.passed:
            if self.failure_category is not None or self.failure_reason is not None:
                raise SchemaValidationError(
                    "passed verification requires null failure fields"
                )
        else:
            if self.failure_category is None:
                raise SchemaValidationError(
                    "failed verification requires a failure_category"
                )
            if self.failure_reason is None or not self.failure_reason.strip():
                raise SchemaValidationError(
                    "failed verification requires a non-empty failure_reason"
                )

    @classmethod
    def from_dict(cls, value: Any) -> VerificationResult:
        data = _require_mapping(value, "VerificationResult")
        _require_exact_keys(
            data,
            {"passed", "failure_category", "failure_reason"},
            "VerificationResult",
        )
        failure_category = data["failure_category"]
        return cls(
            passed=data["passed"],
            failure_category=(
                None
                if failure_category is None
                else _parse_enum(
                    failure_category,
                    VerificationFailureCategory,
                    "failure_category",
                )
            ),
            failure_reason=data["failure_reason"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "failure_category": (
                None
                if self.failure_category is None
                else self.failure_category.value
            ),
            "failure_reason": self.failure_reason,
        }
