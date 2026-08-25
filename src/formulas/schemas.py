"""Versioned, non-executing formula implementation contracts for M6."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Dict, List, Mapping, Optional, Set

from src.understanding.schemas import SchemaValidationError


FORMULA_IMPLEMENTATION_SCHEMA_VERSION = "m6-formula-implementation-v1"
_CANONICAL_DECIMAL = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?\Z")


class FormulaImplementationKind(str, Enum):
    PERCENT_GROWTH = "PERCENT_GROWTH"
    ARITHMETIC_MEAN = "ARITHMETIC_MEAN"


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


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise SchemaValidationError(f"{path} must be a non-empty string")
    return value


def _arity(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SchemaValidationError(f"{path} must be a positive integer")
    return value


@dataclass(frozen=True)
class FormulaImplementation:
    """Deterministic formula semantics metadata; it performs no arithmetic."""

    schema_version: str
    formula_id: str
    implementation_kind: FormulaImplementationKind
    input_order: List[str]
    min_arity: int
    max_arity: Optional[int]
    constants: List[str]

    def __post_init__(self) -> None:
        if self.schema_version != FORMULA_IMPLEMENTATION_SCHEMA_VERSION:
            raise SchemaValidationError(
                "FormulaImplementation.schema_version is unsupported"
            )
        object.__setattr__(self, "formula_id", _string(self.formula_id, "formula_id"))
        if not isinstance(self.implementation_kind, FormulaImplementationKind):
            raise SchemaValidationError(
                "implementation_kind must be a FormulaImplementationKind"
            )
        if not isinstance(self.input_order, list):
            raise SchemaValidationError("input_order must be a list")
        input_order = [
            _string(item, f"input_order[{index}]")
            for index, item in enumerate(self.input_order)
        ]
        if not input_order or len(input_order) != len(set(input_order)):
            raise SchemaValidationError(
                "input_order must be non-empty and deduplicated"
            )
        object.__setattr__(self, "input_order", input_order)
        object.__setattr__(self, "min_arity", _arity(self.min_arity, "min_arity"))
        if self.max_arity is not None:
            object.__setattr__(
                self, "max_arity", _arity(self.max_arity, "max_arity")
            )
            if self.max_arity < self.min_arity:
                raise SchemaValidationError("max_arity must be >= min_arity")
        if not isinstance(self.constants, list):
            raise SchemaValidationError("constants must be a list")
        constants: List[str] = []
        for index, constant in enumerate(self.constants):
            if not isinstance(constant, str) or not _CANONICAL_DECIMAL.fullmatch(
                constant
            ):
                raise SchemaValidationError(
                    f"constants[{index}] must be a canonical decimal string"
                )
            constants.append(constant)
        if len(constants) != len(set(constants)):
            raise SchemaValidationError("constants must be deduplicated")
        object.__setattr__(self, "constants", constants)

        if self.implementation_kind is FormulaImplementationKind.PERCENT_GROWTH:
            if (
                self.input_order != ["previous", "current"]
                or self.min_arity != 2
                or self.max_arity != 2
                or self.constants != ["100"]
            ):
                raise SchemaValidationError(
                    "PERCENT_GROWTH requires previous/current, arity 2, and constant 100"
                )
        elif (
            self.input_order != ["values"]
            or self.min_arity < 2
            or self.max_arity is not None
            or self.constants
        ):
            raise SchemaValidationError(
                "ARITHMETIC_MEAN requires variadic values with minimum arity 2"
            )

    def accepts_arity(self, arity: int) -> bool:
        return arity >= self.min_arity and (
            self.max_arity is None or arity <= self.max_arity
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "formula_id": self.formula_id,
            "implementation_kind": self.implementation_kind.value,
            "input_order": list(self.input_order),
            "min_arity": self.min_arity,
            "max_arity": self.max_arity,
            "constants": list(self.constants),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "FormulaImplementation":
        data = _mapping(value, "FormulaImplementation")
        _exact_keys(
            data,
            {
                "schema_version",
                "formula_id",
                "implementation_kind",
                "input_order",
                "min_arity",
                "max_arity",
                "constants",
            },
            "FormulaImplementation",
        )
        try:
            kind = FormulaImplementationKind(data["implementation_kind"])
        except (TypeError, ValueError) as error:
            raise SchemaValidationError(
                "implementation_kind is unsupported"
            ) from error
        return cls(
            schema_version=data["schema_version"],
            formula_id=data["formula_id"],
            implementation_kind=kind,
            input_order=data["input_order"],
            min_arity=data["min_arity"],
            max_arity=data["max_arity"],
            constants=data["constants"],
        )
