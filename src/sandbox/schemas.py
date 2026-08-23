"""Validated result contracts for sandboxed execution."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Dict, Optional, Union

from src.understanding.schemas import (
    SchemaValidationError,
    _require_bool,
    _require_exact_keys,
    _require_mapping,
    _require_optional_string,
)


ExecutionValue = Union[int, float, str]


def _require_execution_value(value: Any) -> Optional[ExecutionValue]:
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SchemaValidationError("result must be a number, string, or null")
    if not math.isfinite(float(value)):
        raise SchemaValidationError("result must be finite")
    return value


def _require_execution_ms(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SchemaValidationError("execution_ms must be an integer")
    if value < 0:
        raise SchemaValidationError("execution_ms must be non-negative")
    return value


@dataclass
class ExecutionResult:
    success: bool
    result: Optional[ExecutionValue]
    error_type: Optional[str]
    error_message: Optional[str]
    execution_ms: int

    def __post_init__(self) -> None:
        self.success = _require_bool(self.success, "success")
        self.result = _require_execution_value(self.result)
        self.error_type = _require_optional_string(self.error_type, "error_type")
        self.error_message = _require_optional_string(
            self.error_message, "error_message"
        )
        self.execution_ms = _require_execution_ms(self.execution_ms)

    @classmethod
    def from_dict(cls, value: Any) -> ExecutionResult:
        data = _require_mapping(value, "ExecutionResult")
        _require_exact_keys(
            data,
            {"success", "result", "error_type", "error_message", "execution_ms"},
            "ExecutionResult",
        )
        return cls(
            success=data["success"],
            result=data["result"],
            error_type=data["error_type"],
            error_message=data["error_message"],
            execution_ms=data["execution_ms"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "result": self.result,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "execution_ms": self.execution_ms,
        }
