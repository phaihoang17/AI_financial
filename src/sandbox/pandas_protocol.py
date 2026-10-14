"""Bounded JSON protocol between the parent and the Text-to-Pandas worker (ADR-069).

The request carries generated code plus the per-question data slice; the result
carries a JSON-safe description of ``result`` / ``evidence`` or one typed failure.
Pickle is never used; values cross the boundary as plain JSON.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from typing import Any, Dict, Mapping, Optional

from src.understanding.schemas import SchemaValidationError

T2P_REQUEST_SCHEMA_VERSION = "t2p-exec-request-v1"
T2P_RESULT_SCHEMA_VERSION = "t2p-exec-result-v1"
#: Rows serialized per result/evidence frame; longer frames are marked truncated.
MAX_RESULT_ROWS = 500
#: Characters kept from a runtime error message returned to the generator.
MAX_ERROR_MESSAGE_CHARS = 400


class PandasFailureStage(str, Enum):
    VALIDATION = "VALIDATION"
    RUNTIME = "RUNTIME"
    SECURITY = "SECURITY"
    RESOURCE = "RESOURCE"
    SERIALIZATION = "SERIALIZATION"
    INFRASTRUCTURE = "INFRASTRUCTURE"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SchemaValidationError(message)


@dataclass(frozen=True)
class PandasFailure:
    stage: PandasFailureStage
    code: str
    message: str
    line: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"stage": self.stage.value, "code": self.code, "message": self.message, "line": self.line}

    @classmethod
    def from_dict(cls, value: Any) -> "PandasFailure":
        _require(isinstance(value, Mapping) and set(value) == {"stage", "code", "message", "line"},
                 "PandasFailure fields are invalid")
        _require(isinstance(value["code"], str) and value["code"], "failure code must be a string")
        _require(isinstance(value["message"], str), "failure message must be a string")
        _require(value["line"] is None or (isinstance(value["line"], int) and not isinstance(value["line"], bool)),
                 "failure line must be an integer or null")
        try:
            stage = PandasFailureStage(value["stage"])
        except ValueError as error:
            raise SchemaValidationError("failure stage is invalid") from error
        return cls(stage, value["code"], value["message"], value["line"])


_VALUE_KINDS = {"none", "scalar", "series", "dataframe", "list", "dict"}


def _check_value(value: Any, path: str) -> None:
    _require(isinstance(value, Mapping) and value.get("kind") in _VALUE_KINDS, f"{path}.kind is invalid")


@dataclass(frozen=True)
class PandasExecutionResult:
    schema_version: str
    request_id: str
    success: bool
    result: Optional[Dict[str, Any]]
    evidence: Optional[Dict[str, Any]]
    failure: Optional[PandasFailure]
    execution_ms: int

    def __post_init__(self) -> None:
        _require(self.schema_version == T2P_RESULT_SCHEMA_VERSION, "result schema version is unsupported")
        _require(isinstance(self.request_id, str) and bool(self.request_id), "request_id is required")
        _require(isinstance(self.success, bool), "success must be a boolean")
        _require(isinstance(self.execution_ms, int) and self.execution_ms >= 0, "execution_ms is invalid")
        if self.success:
            _require(self.failure is None and self.result is not None, "success requires a result and no failure")
            _check_value(self.result, "result")
            if self.evidence is not None:
                _check_value(self.evidence, "evidence")
        else:
            _require(self.failure is not None and self.result is None and self.evidence is None,
                     "failure requires a failure and no values")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "success": self.success,
            "result": self.result,
            "evidence": self.evidence,
            "failure": None if self.failure is None else self.failure.to_dict(),
            "execution_ms": self.execution_ms,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "PandasExecutionResult":
        _require(isinstance(value, Mapping) and set(value) == {
            "schema_version", "request_id", "success", "result", "evidence", "failure", "execution_ms"
        }, "PandasExecutionResult fields are invalid")
        return cls(
            schema_version=value["schema_version"],
            request_id=value["request_id"],
            success=value["success"],
            result=value["result"],
            evidence=value["evidence"],
            failure=None if value["failure"] is None else PandasFailure.from_dict(value["failure"]),
            execution_ms=value["execution_ms"],
        )


def failure_result(request_id: str, stage: PandasFailureStage, code: str, message: str, *,
                   execution_ms: int = 0, line: Optional[int] = None) -> PandasExecutionResult:
    return PandasExecutionResult(
        T2P_RESULT_SCHEMA_VERSION, request_id or "unavailable", False, None, None,
        PandasFailure(stage, code, message[:MAX_ERROR_MESSAGE_CHARS], line), execution_ms,
    )


def encode(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
