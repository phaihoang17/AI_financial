"""Canonical M7 Batch 1 request, output, and failure contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional

from src.evidence.m5_schemas import BindingMap
from src.evidence.schemas import CanonicalDecimal, Scale
from src.programmer.schemas import Program, ProgrammerInput, ProgramOutputKind
from src.understanding.schemas import SchemaValidationError


EXECUTION_REQUEST_SCHEMA_VERSION = "m7-execution-request-v1"
EXECUTION_RESULT_SCHEMA_VERSION = "m7-execution-result-v1"
EXECUTION_LIMITS_PROFILE_ID = "m7-limits-v1"


class ExecutionFailureStage(str, Enum):
    POLICY = "POLICY"
    VALIDATION = "VALIDATION"
    BINDING = "BINDING"
    CONVERSION = "CONVERSION"
    ARITHMETIC = "ARITHMETIC"
    RESOURCE = "RESOURCE"
    SECURITY = "SECURITY"
    INFRASTRUCTURE = "INFRASTRUCTURE"


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SchemaValidationError(f"{path} must be an object")
    return value


def _exact_keys(data: Mapping[str, Any], expected: set[str], path: str) -> None:
    missing = expected - set(data)
    unknown = set(data) - expected
    if missing:
        raise SchemaValidationError(
            f"{path} is missing fields: {', '.join(sorted(missing))}"
        )
    if unknown:
        raise SchemaValidationError(
            f"{path} has unknown fields: {', '.join(sorted(map(str, unknown)))}"
        )


def _string(value: Any, path: str, *, exact: Optional[str] = None) -> str:
    if not isinstance(value, str) or not value:
        raise SchemaValidationError(f"{path} must be a non-empty string")
    if exact is not None and value != exact:
        raise SchemaValidationError(f"{path} must be {exact!r}")
    return value


def _execution_ms(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SchemaValidationError("execution_ms must be a non-negative integer")
    return value


@dataclass(frozen=True)
class SandboxExecutionRequest:
    schema_version: str
    program: Program
    programmer_input: ProgrammerInput
    binding_map: BindingMap
    limits_profile_id: str

    def __post_init__(self) -> None:
        _string(
            self.schema_version,
            "SandboxExecutionRequest.schema_version",
            exact=EXECUTION_REQUEST_SCHEMA_VERSION,
        )
        if not isinstance(self.program, Program):
            raise SchemaValidationError("program must be a Program")
        if not isinstance(self.programmer_input, ProgrammerInput):
            raise SchemaValidationError(
                "programmer_input must be a ProgrammerInput"
            )
        if not isinstance(self.binding_map, BindingMap):
            raise SchemaValidationError("binding_map must be a BindingMap")
        _string(
            self.limits_profile_id,
            "limits_profile_id",
            exact=EXECUTION_LIMITS_PROFILE_ID,
        )

    @classmethod
    def from_dict(cls, value: Any) -> "SandboxExecutionRequest":
        data = _mapping(value, "SandboxExecutionRequest")
        _exact_keys(
            data,
            {
                "schema_version",
                "program",
                "programmer_input",
                "binding_map",
                "limits_profile_id",
            },
            "SandboxExecutionRequest",
        )
        return cls(
            schema_version=data["schema_version"],
            program=Program.from_dict(data["program"]),
            programmer_input=ProgrammerInput.from_dict(data["programmer_input"]),
            binding_map=BindingMap.from_dict(data["binding_map"]),
            limits_profile_id=data["limits_profile_id"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "program": self.program.to_dict(),
            "programmer_input": self.programmer_input.to_dict(),
            "binding_map": self.binding_map.to_dict(),
            "limits_profile_id": self.limits_profile_id,
        }


@dataclass(frozen=True)
class ExecutionDatum:
    value: CanonicalDecimal
    scale: Optional[Scale]
    unit: Optional[str]

    def __post_init__(self) -> None:
        if not isinstance(self.value, CanonicalDecimal):
            raise SchemaValidationError("value must be a CanonicalDecimal")
        if self.scale is not None:
            if not isinstance(self.scale, Scale) or self.scale is Scale.OTHER:
                raise SchemaValidationError(
                    "scale must be RAW, THOUSAND, MILLION, BILLION, PERCENT, or null"
                )
        if self.unit is not None and (
            not isinstance(self.unit, str) or not self.unit
        ):
            raise SchemaValidationError("unit must be a non-empty string or null")

    @classmethod
    def from_dict(cls, value: Any) -> "ExecutionDatum":
        data = _mapping(value, "ExecutionDatum")
        _exact_keys(data, {"value", "scale", "unit"}, "ExecutionDatum")
        raw_value = data["value"]
        if not isinstance(raw_value, str):
            raise SchemaValidationError("value must be a CanonicalDecimal string")
        raw_scale = data["scale"]
        try:
            scale = None if raw_scale is None else Scale(raw_scale)
        except (TypeError, ValueError) as error:
            raise SchemaValidationError("scale is unsupported") from error
        return cls(
            value=CanonicalDecimal(raw_value),
            scale=scale,
            unit=data["unit"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "value": self.value,
            "scale": None if self.scale is None else self.scale.value,
            "unit": self.unit,
        }


@dataclass(frozen=True)
class ExecutionOutput:
    kind: ProgramOutputKind
    values: List[ExecutionDatum]

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ProgramOutputKind):
            raise SchemaValidationError("kind must be a ProgramOutputKind")
        if not isinstance(self.values, list) or not all(
            isinstance(item, ExecutionDatum) for item in self.values
        ):
            raise SchemaValidationError("values must contain ExecutionDatum values")
        object.__setattr__(self, "values", list(self.values))
        if self.kind is ProgramOutputKind.SCALAR and len(self.values) != 1:
            raise SchemaValidationError("SCALAR requires exactly one value")
        if self.kind is ProgramOutputKind.ORDERED_VALUES and not self.values:
            raise SchemaValidationError(
                "ORDERED_VALUES requires one or more ordered values"
            )

    @classmethod
    def from_dict(cls, value: Any) -> "ExecutionOutput":
        data = _mapping(value, "ExecutionOutput")
        _exact_keys(data, {"kind", "values"}, "ExecutionOutput")
        if not isinstance(data["values"], list):
            raise SchemaValidationError("values must be a list")
        try:
            kind = ProgramOutputKind(data["kind"])
        except (TypeError, ValueError) as error:
            raise SchemaValidationError("kind is unsupported") from error
        return cls(
            kind=kind,
            values=[ExecutionDatum.from_dict(item) for item in data["values"]],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind.value,
            "values": [item.to_dict() for item in self.values],
        }


@dataclass(frozen=True)
class ExecutionFailure:
    stage: ExecutionFailureStage
    code: str
    message: str

    def __post_init__(self) -> None:
        if not isinstance(self.stage, ExecutionFailureStage):
            raise SchemaValidationError("stage must be an ExecutionFailureStage")
        _string(self.code, "code")
        _string(self.message, "message")

    @classmethod
    def from_dict(cls, value: Any) -> "ExecutionFailure":
        data = _mapping(value, "ExecutionFailure")
        _exact_keys(data, {"stage", "code", "message"}, "ExecutionFailure")
        try:
            stage = ExecutionFailureStage(data["stage"])
        except (TypeError, ValueError) as error:
            raise SchemaValidationError("stage is unsupported") from error
        return cls(stage=stage, code=data["code"], message=data["message"])

    def to_dict(self) -> Dict[str, str]:
        return {
            "stage": self.stage.value,
            "code": self.code,
            "message": self.message,
        }


@dataclass(frozen=True)
class ExecutionResult:
    schema_version: str
    program_id: str
    success: bool
    output: Optional[ExecutionOutput]
    failure: Optional[ExecutionFailure]
    execution_ms: int

    def __post_init__(self) -> None:
        _string(
            self.schema_version,
            "ExecutionResult.schema_version",
            exact=EXECUTION_RESULT_SCHEMA_VERSION,
        )
        _string(self.program_id, "program_id")
        if not isinstance(self.success, bool):
            raise SchemaValidationError("success must be a boolean")
        if self.output is not None and not isinstance(self.output, ExecutionOutput):
            raise SchemaValidationError("output must be an ExecutionOutput or null")
        if self.failure is not None and not isinstance(
            self.failure, ExecutionFailure
        ):
            raise SchemaValidationError("failure must be an ExecutionFailure or null")
        object.__setattr__(self, "execution_ms", _execution_ms(self.execution_ms))
        if self.success and (self.output is None or self.failure is not None):
            raise SchemaValidationError(
                "success=true requires output and null failure"
            )
        if not self.success and (self.output is not None or self.failure is None):
            raise SchemaValidationError(
                "success=false requires null output and failure"
            )

    @classmethod
    def from_dict(cls, value: Any) -> "ExecutionResult":
        data = _mapping(value, "ExecutionResult")
        _exact_keys(
            data,
            {
                "schema_version",
                "program_id",
                "success",
                "output",
                "failure",
                "execution_ms",
            },
            "ExecutionResult",
        )
        return cls(
            schema_version=data["schema_version"],
            program_id=data["program_id"],
            success=data["success"],
            output=(
                None
                if data["output"] is None
                else ExecutionOutput.from_dict(data["output"])
            ),
            failure=(
                None
                if data["failure"] is None
                else ExecutionFailure.from_dict(data["failure"])
            ),
            execution_ms=data["execution_ms"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "program_id": self.program_id,
            "success": self.success,
            "output": None if self.output is None else self.output.to_dict(),
            "failure": None if self.failure is None else self.failure.to_dict(),
            "execution_ms": self.execution_ms,
        }
