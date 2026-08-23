"""Deterministic v1 operation detection for Vietnamese financial questions."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Dict, Set
import unicodedata

from src.understanding.schemas import (
    Operation,
    SchemaValidationError,
    _parse_enum,
    _require_enum,
    _require_exact_keys,
    _require_mapping,
    _require_string,
)


_RATIO_INDICATORS = ("roe", "tỷ lệ", "tỷ suất")
_GROWTH_INDICATORS = ("tăng bao nhiêu %",)
_AGGREGATE_INDICATORS = ("trung bình",)
_COMPARE_INDICATORS = ("so sánh", "so với")
_COMPARE_PERIOD_PATTERN = re.compile(
    r"(?<!\w)từ\s+[0-9]{4}\s+sang\s+[0-9]{4}(?!\w)"
)


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.split()).casefold()


def _contains_complete_phrase(text: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


@dataclass
class OperationDetectorInput:
    text: str

    def __post_init__(self) -> None:
        self.text = _require_string(self.text, "text")

    @classmethod
    def from_dict(cls, value: Any) -> OperationDetectorInput:
        data = _require_mapping(value, "OperationDetectorInput")
        _require_exact_keys(data, {"text"}, "OperationDetectorInput")
        return cls(text=data["text"])

    def to_dict(self) -> Dict[str, str]:
        return {"text": self.text}


@dataclass
class OperationDetection:
    operation: Operation

    def __post_init__(self) -> None:
        self.operation = _require_enum(self.operation, Operation, "operation")

    @classmethod
    def from_dict(cls, value: Any) -> OperationDetection:
        data = _require_mapping(value, "OperationDetection")
        _require_exact_keys(data, {"operation"}, "OperationDetection")
        return cls(operation=_parse_enum(data["operation"], Operation, "operation"))

    def to_dict(self) -> Dict[str, str]:
        return {"operation": self.operation.value}


def _detected_operations(text: str) -> Set[Operation]:
    operations: Set[Operation] = set()
    if any(_contains_complete_phrase(text, value) for value in _RATIO_INDICATORS):
        operations.add(Operation.RATIO)
    if any(_contains_complete_phrase(text, value) for value in _GROWTH_INDICATORS):
        operations.add(Operation.GROWTH)
    if any(
        _contains_complete_phrase(text, value) for value in _AGGREGATE_INDICATORS
    ):
        operations.add(Operation.AGGREGATE)
    if any(_contains_complete_phrase(text, value) for value in _COMPARE_INDICATORS):
        operations.add(Operation.COMPARE)
    if _COMPARE_PERIOD_PATTERN.search(text) is not None:
        operations.add(Operation.COMPARE)
    return operations


def detect_operation(detector_input: OperationDetectorInput) -> OperationDetection:
    """Detect only canonical v1 operation indicators and conflicts."""
    if not isinstance(detector_input, OperationDetectorInput):
        raise SchemaValidationError(
            "detector_input must be an OperationDetectorInput"
        )

    text = _normalize_text(detector_input.text)
    if not text:
        return OperationDetection(operation=Operation.UNKNOWN)

    operations = _detected_operations(text)
    if not operations:
        return OperationDetection(operation=Operation.NONE)
    if len(operations) == 1:
        return OperationDetection(operation=next(iter(operations)))

    if operations == {Operation.GROWTH, Operation.COMPARE}:
        has_growth_form = _contains_complete_phrase(
            text, _GROWTH_INDICATORS[0]
        )
        has_period_structure = _COMPARE_PERIOD_PATTERN.search(text) is not None
        if has_growth_form and has_period_structure:
            return OperationDetection(operation=Operation.GROWTH)

    return OperationDetection(operation=Operation.UNKNOWN)
