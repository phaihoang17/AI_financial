"""Canonical symbolic Program and Programmer boundary contracts for M6 v1."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
from typing import Any, Dict, List, Mapping, Optional, Set, Type, TypeVar

from src.evidence.m5_schemas import MaskedEvidenceBundle
from src.supervisor.schemas import Plan, QuestionType
from src.understanding.schemas import SchemaValidationError


PROGRAM_SCHEMA_VERSION = "m6-program-v1"


class ProgrammerStatus(str, Enum):
    GENERATED = "GENERATED"
    REJECTED = "REJECTED"


class ProgramOutputKind(str, Enum):
    SCALAR = "SCALAR"
    ORDERED_VALUES = "ORDERED_VALUES"


class ProgramOperation(str, Enum):
    IDENTITY = "IDENTITY"
    COLLECT = "COLLECT"
    APPLY_REGISTERED_FORMULA = "APPLY_REGISTERED_FORMULA"


EnumT = TypeVar("EnumT", bound=Enum)


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SchemaValidationError(f"{path} must be an object")
    return value


def _exact_keys(data: Mapping[str, Any], expected: Set[str], path: str) -> None:
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


def _string(value: Any, path: str, *, non_empty: bool = True) -> str:
    if not isinstance(value, str):
        raise SchemaValidationError(f"{path} must be a string")
    if non_empty and not value:
        raise SchemaValidationError(f"{path} must be non-empty")
    return value


def _optional_string(value: Any, path: str) -> Optional[str]:
    if value is not None and not isinstance(value, str):
        raise SchemaValidationError(f"{path} must be a string or null")
    if value == "":
        raise SchemaValidationError(f"{path} must be non-empty when present")
    return value


def _sha256_string(value: Any, path: str) -> str:
    value = _string(value, path)
    if len(value) != 64 or value != value.lower():
        raise SchemaValidationError(f"{path} must be a lowercase SHA-256")
    try:
        int(value, 16)
    except ValueError as error:
        raise SchemaValidationError(f"{path} must be a lowercase SHA-256") from error
    return value


def _string_list(value: Any, path: str, *, non_empty: bool = False) -> List[str]:
    if not isinstance(value, list):
        raise SchemaValidationError(f"{path} must be a list")
    result = [
        _string(item, f"{path}[{index}]") for index, item in enumerate(value)
    ]
    if non_empty and not result:
        raise SchemaValidationError(f"{path} must be non-empty")
    return result


def _enum(value: Any, enum_type: Type[EnumT], path: str) -> EnumT:
    if not isinstance(value, enum_type):
        raise SchemaValidationError(f"{path} must be a {enum_type.__name__}")
    return value


def _parse_enum(value: Any, enum_type: Type[EnumT], path: str) -> EnumT:
    if not isinstance(value, str):
        raise SchemaValidationError(f"{path} must be a string enum value")
    try:
        return enum_type(value)
    except ValueError as error:
        allowed = ", ".join(member.value for member in enum_type)
        raise SchemaValidationError(f"{path} must be one of: {allowed}") from error


def canonical_json(value: Any) -> str:
    """Serialize one contract payload with the repository canonical JSON rules."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


@dataclass(frozen=True)
class ProgramInput:
    input_id: str
    placeholder: str
    evidence_id: str
    requirement_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "input_id", _string(self.input_id, "input_id"))
        object.__setattr__(
            self, "placeholder", _string(self.placeholder, "placeholder")
        )
        object.__setattr__(
            self, "evidence_id", _string(self.evidence_id, "evidence_id")
        )
        object.__setattr__(
            self,
            "requirement_id",
            _string(self.requirement_id, "requirement_id"),
        )

    def to_dict(self) -> Dict[str, str]:
        return {
            "input_id": self.input_id,
            "placeholder": self.placeholder,
            "evidence_id": self.evidence_id,
            "requirement_id": self.requirement_id,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "ProgramInput":
        data = _mapping(value, "ProgramInput")
        _exact_keys(
            data,
            {"input_id", "placeholder", "evidence_id", "requirement_id"},
            "ProgramInput",
        )
        return cls(
            input_id=data["input_id"],
            placeholder=data["placeholder"],
            evidence_id=data["evidence_id"],
            requirement_id=data["requirement_id"],
        )


@dataclass(frozen=True)
class ProgramStep:
    step_id: str
    operation: ProgramOperation
    input_refs: List[str]
    formula_id: Optional[str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_id", _string(self.step_id, "step_id"))
        object.__setattr__(
            self, "operation", _enum(self.operation, ProgramOperation, "operation")
        )
        object.__setattr__(
            self,
            "input_refs",
            _string_list(self.input_refs, "input_refs", non_empty=True),
        )
        object.__setattr__(
            self, "formula_id", _optional_string(self.formula_id, "formula_id")
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "operation": self.operation.value,
            "input_refs": list(self.input_refs),
            "formula_id": self.formula_id,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "ProgramStep":
        data = _mapping(value, "ProgramStep")
        _exact_keys(
            data,
            {"step_id", "operation", "input_refs", "formula_id"},
            "ProgramStep",
        )
        return cls(
            step_id=data["step_id"],
            operation=_parse_enum(data["operation"], ProgramOperation, "operation"),
            input_refs=data["input_refs"],
            formula_id=data["formula_id"],
        )


def _program_identity_payload(
    *,
    formula_registry_fingerprint: str,
    question_type: QuestionType,
    formula_id: Optional[str],
    inputs: List[ProgramInput],
    steps: List[ProgramStep],
    output_ref: str,
    output_kind: ProgramOutputKind,
) -> Dict[str, Any]:
    return {
        "schema_version": PROGRAM_SCHEMA_VERSION,
        "formula_registry_fingerprint": formula_registry_fingerprint,
        "question_type": question_type.value,
        "formula_id": formula_id,
        "inputs": [item.to_dict() for item in inputs],
        "steps": [step.to_dict() for step in steps],
        "output_ref": output_ref,
        "output_kind": output_kind.value,
    }


def make_program_id(
    *,
    formula_registry_fingerprint: str,
    question_type: QuestionType,
    formula_id: Optional[str],
    inputs: List[ProgramInput],
    steps: List[ProgramStep],
    output_ref: str,
    output_kind: ProgramOutputKind,
) -> str:
    """Hash only symbolic Program fields; execution bindings cannot participate."""
    payload = _program_identity_payload(
        formula_registry_fingerprint=formula_registry_fingerprint,
        question_type=question_type,
        formula_id=formula_id,
        inputs=inputs,
        steps=steps,
        output_ref=output_ref,
        output_kind=output_kind,
    )
    return sha256(canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Program:
    schema_version: str
    program_id: str
    formula_registry_fingerprint: str
    question_type: QuestionType
    formula_id: Optional[str]
    inputs: List[ProgramInput]
    steps: List[ProgramStep]
    output_ref: str
    output_kind: ProgramOutputKind

    def __post_init__(self) -> None:
        if self.schema_version != PROGRAM_SCHEMA_VERSION:
            raise SchemaValidationError("Program.schema_version is unsupported")
        object.__setattr__(self, "program_id", _sha256_string(self.program_id, "program_id"))
        object.__setattr__(
            self,
            "formula_registry_fingerprint",
            _sha256_string(
                self.formula_registry_fingerprint,
                "formula_registry_fingerprint",
            ),
        )
        object.__setattr__(
            self, "question_type", _enum(self.question_type, QuestionType, "question_type")
        )
        object.__setattr__(
            self, "formula_id", _optional_string(self.formula_id, "formula_id")
        )
        if not isinstance(self.inputs, list) or not all(
            isinstance(item, ProgramInput) for item in self.inputs
        ):
            raise SchemaValidationError("inputs must contain ProgramInput values")
        if not self.inputs:
            raise SchemaValidationError("inputs must be non-empty")
        object.__setattr__(self, "inputs", list(self.inputs))
        if not isinstance(self.steps, list) or not all(
            isinstance(step, ProgramStep) for step in self.steps
        ):
            raise SchemaValidationError("steps must contain ProgramStep values")
        if not self.steps:
            raise SchemaValidationError("steps must be non-empty")
        object.__setattr__(self, "steps", list(self.steps))
        object.__setattr__(self, "output_ref", _string(self.output_ref, "output_ref"))
        object.__setattr__(
            self, "output_kind", _enum(self.output_kind, ProgramOutputKind, "output_kind")
        )

    @classmethod
    def create(
        cls,
        *,
        formula_registry_fingerprint: str,
        question_type: QuestionType,
        formula_id: Optional[str],
        inputs: List[ProgramInput],
        steps: List[ProgramStep],
        output_ref: str,
        output_kind: ProgramOutputKind,
    ) -> "Program":
        program_id = make_program_id(
            formula_registry_fingerprint=formula_registry_fingerprint,
            question_type=question_type,
            formula_id=formula_id,
            inputs=inputs,
            steps=steps,
            output_ref=output_ref,
            output_kind=output_kind,
        )
        return cls(
            schema_version=PROGRAM_SCHEMA_VERSION,
            program_id=program_id,
            formula_registry_fingerprint=formula_registry_fingerprint,
            question_type=question_type,
            formula_id=formula_id,
            inputs=inputs,
            steps=steps,
            output_ref=output_ref,
            output_kind=output_kind,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "program_id": self.program_id,
            "formula_registry_fingerprint": self.formula_registry_fingerprint,
            "question_type": self.question_type.value,
            "formula_id": self.formula_id,
            "inputs": [item.to_dict() for item in self.inputs],
            "steps": [step.to_dict() for step in self.steps],
            "output_ref": self.output_ref,
            "output_kind": self.output_kind.value,
        }

    def canonical_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, value: Any) -> "Program":
        data = _mapping(value, "Program")
        _exact_keys(
            data,
            {
                "schema_version",
                "program_id",
                "formula_registry_fingerprint",
                "question_type",
                "formula_id",
                "inputs",
                "steps",
                "output_ref",
                "output_kind",
            },
            "Program",
        )
        inputs = data["inputs"]
        steps = data["steps"]
        if not isinstance(inputs, list):
            raise SchemaValidationError("inputs must be a list")
        if not isinstance(steps, list):
            raise SchemaValidationError("steps must be a list")
        return cls(
            schema_version=data["schema_version"],
            program_id=data["program_id"],
            formula_registry_fingerprint=data["formula_registry_fingerprint"],
            question_type=_parse_enum(
                data["question_type"], QuestionType, "question_type"
            ),
            formula_id=data["formula_id"],
            inputs=[ProgramInput.from_dict(item) for item in inputs],
            steps=[ProgramStep.from_dict(step) for step in steps],
            output_ref=data["output_ref"],
            output_kind=_parse_enum(
                data["output_kind"], ProgramOutputKind, "output_kind"
            ),
        )


@dataclass(frozen=True)
class ProgrammerInput:
    plan: Plan
    masked_evidence: MaskedEvidenceBundle
    formula_registry_fingerprint: str

    def __post_init__(self) -> None:
        if not isinstance(self.plan, Plan):
            raise SchemaValidationError("plan must be an executable Plan")
        if not isinstance(self.masked_evidence, MaskedEvidenceBundle):
            raise SchemaValidationError(
                "masked_evidence must be a MaskedEvidenceBundle"
            )
        object.__setattr__(
            self,
            "formula_registry_fingerprint",
            _sha256_string(
                self.formula_registry_fingerprint,
                "formula_registry_fingerprint",
            ),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan": self.plan.to_dict(),
            "masked_evidence": self.masked_evidence.to_dict(),
            "formula_registry_fingerprint": self.formula_registry_fingerprint,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "ProgrammerInput":
        data = _mapping(value, "ProgrammerInput")
        _exact_keys(
            data,
            {
                "plan",
                "masked_evidence",
                "formula_registry_fingerprint",
            },
            "ProgrammerInput",
        )
        return cls(
            plan=Plan.from_dict(data["plan"]),
            masked_evidence=MaskedEvidenceBundle.from_dict(
                data["masked_evidence"]
            ),
            formula_registry_fingerprint=data["formula_registry_fingerprint"],
        )


@dataclass(frozen=True)
class ProgrammerResult:
    status: ProgrammerStatus
    program: Optional[Program]
    failure_code: Optional[str]
    failure_message: Optional[str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _enum(self.status, ProgrammerStatus, "status"))
        if self.program is not None and not isinstance(self.program, Program):
            raise SchemaValidationError("program must be a Program or null")
        object.__setattr__(
            self, "failure_code", _optional_string(self.failure_code, "failure_code")
        )
        object.__setattr__(
            self,
            "failure_message",
            _optional_string(self.failure_message, "failure_message"),
        )
        if self.status is ProgrammerStatus.GENERATED:
            if (
                self.program is None
                or self.failure_code is not None
                or self.failure_message is not None
            ):
                raise SchemaValidationError(
                    "GENERATED requires program and null failure fields"
                )
        elif (
            self.program is not None
            or self.failure_code is None
            or self.failure_message is None
        ):
            raise SchemaValidationError(
                "REJECTED requires null program and non-null failure fields"
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "program": None if self.program is None else self.program.to_dict(),
            "failure_code": self.failure_code,
            "failure_message": self.failure_message,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "ProgrammerResult":
        data = _mapping(value, "ProgrammerResult")
        _exact_keys(
            data,
            {"status", "program", "failure_code", "failure_message"},
            "ProgrammerResult",
        )
        return cls(
            status=_parse_enum(data["status"], ProgrammerStatus, "status"),
            program=(
                None if data["program"] is None else Program.from_dict(data["program"])
            ),
            failure_code=data["failure_code"],
            failure_message=data["failure_message"],
        )
