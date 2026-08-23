"""Evaluation-only slice contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict

from src.evaluation.harness import EvaluationResult
from src.understanding.schemas import (
    SchemaValidationError,
    _parse_enum,
    _require_bool,
    _require_enum,
    _require_exact_keys,
    _require_mapping,
)


class EvaluationEvidenceSource(str, Enum):
    TABLE = "TABLE"
    TEXT = "TEXT"
    HYBRID = "HYBRID"


class ReasoningDepth(str, Enum):
    ONE_STEP = "ONE_STEP"
    TWO_STEP = "TWO_STEP"
    THREE_PLUS_STEPS = "THREE_PLUS_STEPS"


@dataclass
class EvaluationSlice:
    evidence_source: EvaluationEvidenceSource
    reasoning_depth: ReasoningDepth
    scale_unit_sensitive: bool

    def __post_init__(self) -> None:
        self.evidence_source = _require_enum(
            self.evidence_source,
            EvaluationEvidenceSource,
            "evidence_source",
        )
        self.reasoning_depth = _require_enum(
            self.reasoning_depth, ReasoningDepth, "reasoning_depth"
        )
        self.scale_unit_sensitive = _require_bool(
            self.scale_unit_sensitive, "scale_unit_sensitive"
        )

    @classmethod
    def from_dict(cls, value: Any) -> EvaluationSlice:
        data = _require_mapping(value, "EvaluationSlice")
        _require_exact_keys(
            data,
            {"evidence_source", "reasoning_depth", "scale_unit_sensitive"},
            "EvaluationSlice",
        )
        return cls(
            evidence_source=_parse_enum(
                data["evidence_source"],
                EvaluationEvidenceSource,
                "evidence_source",
            ),
            reasoning_depth=_parse_enum(
                data["reasoning_depth"], ReasoningDepth, "reasoning_depth"
            ),
            scale_unit_sensitive=data["scale_unit_sensitive"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evidence_source": self.evidence_source.value,
            "reasoning_depth": self.reasoning_depth.value,
            "scale_unit_sensitive": self.scale_unit_sensitive,
        }


@dataclass
class SlicedEvaluationResult:
    result: EvaluationResult
    slice: EvaluationSlice

    def __post_init__(self) -> None:
        if not isinstance(self.result, EvaluationResult):
            raise SchemaValidationError("result must be an EvaluationResult")
        if not isinstance(self.slice, EvaluationSlice):
            raise SchemaValidationError("slice must be an EvaluationSlice")

    @classmethod
    def from_dict(cls, value: Any) -> SlicedEvaluationResult:
        data = _require_mapping(value, "SlicedEvaluationResult")
        _require_exact_keys(
            data, {"result", "slice"}, "SlicedEvaluationResult"
        )
        return cls(
            result=EvaluationResult.from_dict(data["result"]),
            slice=EvaluationSlice.from_dict(data["slice"]),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {"result": self.result.to_dict(), "slice": self.slice.to_dict()}
