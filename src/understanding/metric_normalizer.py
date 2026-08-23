"""Deterministic v1 normalization for documented financial metrics."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional
import unicodedata

from src.understanding.schemas import (
    MetricUnderstanding,
    SchemaValidationError,
    _parse_enum,
    _require_confidence,
    _require_enum,
    _require_exact_keys,
    _require_mapping,
    _require_string,
)


def _require_non_empty_string(value: Any, path: str) -> str:
    text = _require_string(value, path)
    if not text.strip():
        raise SchemaValidationError(f"{path} must be non-empty")
    return text


def _normalize_match_value(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.split()).casefold()


@dataclass
class CanonicalMetric:
    canonical: str

    def __post_init__(self) -> None:
        self.canonical = _require_non_empty_string(
            self.canonical, "canonical"
        )

    @classmethod
    def from_dict(cls, value: Any, path: str = "CanonicalMetric") -> CanonicalMetric:
        data = _require_mapping(value, path)
        _require_exact_keys(data, {"canonical"}, path)
        return cls(canonical=data["canonical"])

    def to_dict(self) -> Dict[str, str]:
        return {"canonical": self.canonical}


@dataclass
class MetricSynonym:
    synonym: str
    canonical: str

    def __post_init__(self) -> None:
        self.synonym = _require_non_empty_string(self.synonym, "synonym")
        self.canonical = _require_non_empty_string(
            self.canonical, "canonical"
        )

    @classmethod
    def from_dict(cls, value: Any) -> MetricSynonym:
        data = _require_mapping(value, "MetricSynonym")
        _require_exact_keys(data, {"synonym", "canonical"}, "MetricSynonym")
        return cls(synonym=data["synonym"], canonical=data["canonical"])

    def to_dict(self) -> Dict[str, str]:
        return {"synonym": self.synonym, "canonical": self.canonical}


class MetricResolutionStatus(str, Enum):
    RESOLVED = "RESOLVED"
    UNRESOLVED = "UNRESOLVED"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass
class MetricResolution:
    status: MetricResolutionStatus
    raw: str
    metric: Optional[MetricUnderstanding]
    candidates: List[CanonicalMetric]
    confidence: float

    def __post_init__(self) -> None:
        self.status = _require_enum(
            self.status, MetricResolutionStatus, "status"
        )
        self.raw = _require_string(self.raw, "raw")
        if self.metric is not None and not isinstance(
            self.metric, MetricUnderstanding
        ):
            raise SchemaValidationError(
                "metric must be a MetricUnderstanding or null"
            )
        if not isinstance(self.candidates, list) or not all(
            isinstance(candidate, CanonicalMetric)
            for candidate in self.candidates
        ):
            raise SchemaValidationError(
                "candidates must be a list of CanonicalMetric values"
            )
        self.confidence = _require_confidence(self.confidence, "confidence")

        candidate_keys = [
            _normalize_match_value(candidate.canonical)
            for candidate in self.candidates
        ]
        if len(candidate_keys) != len(set(candidate_keys)):
            raise SchemaValidationError(
                "candidates must be deduplicated by canonical metric"
            )

        if self.status is MetricResolutionStatus.RESOLVED:
            if len(self.candidates) != 1 or self.metric is None:
                raise SchemaValidationError(
                    "RESOLVED requires one candidate and a metric"
                )
            candidate = self.candidates[0]
            if self.metric.raw != self.raw:
                raise SchemaValidationError("metric.raw must equal raw")
            if self.metric.canonical != candidate.canonical:
                raise SchemaValidationError(
                    "metric.canonical must match the candidate"
                )
            if self.confidence != 1.0 or self.metric.confidence != 1.0:
                raise SchemaValidationError(
                    "RESOLVED requires result and metric confidence 1.0"
                )
            return

        if self.metric is not None:
            raise SchemaValidationError(
                "UNRESOLVED and AMBIGUOUS require a null metric"
            )
        if self.confidence != 0.0:
            raise SchemaValidationError(
                "UNRESOLVED and AMBIGUOUS require confidence 0.0"
            )
        if self.status is MetricResolutionStatus.UNRESOLVED:
            if self.candidates:
                raise SchemaValidationError("UNRESOLVED requires no candidates")
        elif len(self.candidates) < 2:
            raise SchemaValidationError(
                "AMBIGUOUS requires at least two distinct candidates"
            )

    @classmethod
    def from_dict(cls, value: Any) -> MetricResolution:
        data = _require_mapping(value, "MetricResolution")
        _require_exact_keys(
            data,
            {"status", "raw", "metric", "candidates", "confidence"},
            "MetricResolution",
        )
        metric = data["metric"]
        candidates = data["candidates"]
        if not isinstance(candidates, list):
            raise SchemaValidationError("candidates must be a list")
        return cls(
            status=_parse_enum(
                data["status"], MetricResolutionStatus, "status"
            ),
            raw=data["raw"],
            metric=(
                None
                if metric is None
                else MetricUnderstanding.from_dict(metric, "metric")
            ),
            candidates=[
                CanonicalMetric.from_dict(candidate, f"candidates[{index}]")
                for index, candidate in enumerate(candidates)
            ],
            confidence=data["confidence"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "raw": self.raw,
            "metric": None if self.metric is None else self.metric.to_dict(),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "confidence": self.confidence,
        }


def initial_metric_registry() -> List[MetricSynonym]:
    """Return the documented v1 registry without shared mutable state."""
    return [
        MetricSynonym(synonym="LNST", canonical="LNST"),
        MetricSynonym(synonym="lãi ròng", canonical="LNST"),
        MetricSynonym(synonym="lợi nhuận sau thuế", canonical="LNST"),
    ]


def resolve_metric(raw: str, synonyms: List[MetricSynonym]) -> MetricResolution:
    raw = _require_string(raw, "raw")
    if not isinstance(synonyms, list) or not all(
        isinstance(synonym, MetricSynonym) for synonym in synonyms
    ):
        raise SchemaValidationError(
            "synonyms must be a list of MetricSynonym values"
        )

    normalized_raw = _normalize_match_value(raw)
    matches: Dict[str, CanonicalMetric] = {}
    for synonym in synonyms:
        if _normalize_match_value(synonym.synonym) != normalized_raw:
            continue
        key = _normalize_match_value(synonym.canonical)
        candidate = CanonicalMetric(canonical=synonym.canonical)
        existing = matches.get(key)
        if existing is None or candidate.canonical < existing.canonical:
            matches[key] = candidate

    candidates = sorted(
        matches.values(),
        key=lambda candidate: (
            _normalize_match_value(candidate.canonical),
            candidate.canonical,
        ),
    )

    if len(candidates) == 1:
        candidate = candidates[0]
        return MetricResolution(
            status=MetricResolutionStatus.RESOLVED,
            raw=raw,
            metric=MetricUnderstanding(
                raw=raw, canonical=candidate.canonical, confidence=1.0
            ),
            candidates=candidates,
            confidence=1.0,
        )
    if not candidates:
        return MetricResolution(
            status=MetricResolutionStatus.UNRESOLVED,
            raw=raw,
            metric=None,
            candidates=[],
            confidence=0.0,
        )
    return MetricResolution(
        status=MetricResolutionStatus.AMBIGUOUS,
        raw=raw,
        metric=None,
        candidates=candidates,
        confidence=0.0,
    )
