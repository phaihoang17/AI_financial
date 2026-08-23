"""Deterministic v1 parser for documented Vietnamese period expressions."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Dict, List, Optional, Tuple

from src.understanding.schemas import (
    PeriodKind,
    PeriodUnderstanding,
    SchemaValidationError,
    _require_confidence,
    _require_exact_keys,
    _require_mapping,
    _require_string,
)


_YEAR_VALUE = re.compile(r"^[0-9]{4}$")
_QUARTER_VALUE = re.compile(r"^[0-9]{4}-Q[1-4]$")
_CUMULATIVE_VALUE = re.compile(r"^[0-9]{4}-(?:[1-9]|1[0-2])M$")

_CUMULATIVE_EXPRESSION = re.compile(
    r"(?<!\w)lũy\s+kế(?:\s+(?P<number>[0-9]+))?\s+tháng"
    r"(?:(?:\s+năm\s+|\s*/\s*)(?P<year>[0-9]+))?(?!\w)",
    re.IGNORECASE,
)
_CUMULATIVE_CUE = re.compile(r"(?<!\w)lũy\s+kế(?!\w)", re.IGNORECASE)
_QUARTER_EXPRESSION = re.compile(
    r"(?<!\w)quý(?:\s+(?P<number>[0-9]+))?"
    r"(?:(?:\s*/\s*|\s+năm\s+)(?P<year>[0-9]+))?(?!\w)",
    re.IGNORECASE,
)
_YEAR_EXPRESSION = re.compile(
    r"(?<!\w)năm(?:\s+(?P<year>[0-9]+))?(?!\w)", re.IGNORECASE
)


@dataclass
class TemporalParserInput:
    text: str

    def __post_init__(self) -> None:
        self.text = _require_string(self.text, "text")

    @classmethod
    def from_dict(cls, value: Any) -> TemporalParserInput:
        data = _require_mapping(value, "TemporalParserInput")
        _require_exact_keys(data, {"text"}, "TemporalParserInput")
        return cls(text=data["text"])

    def to_dict(self) -> Dict[str, str]:
        return {"text": self.text}


def _period_value_is_valid(period: PeriodUnderstanding) -> bool:
    if period.kind is PeriodKind.NAM:
        return _YEAR_VALUE.fullmatch(period.value) is not None
    if period.kind is PeriodKind.QUY:
        return _QUARTER_VALUE.fullmatch(period.value) is not None
    return _CUMULATIVE_VALUE.fullmatch(period.value) is not None


@dataclass
class TemporalResolution:
    raw: str
    period: Optional[PeriodUnderstanding]
    confidence: float

    def __post_init__(self) -> None:
        self.raw = _require_string(self.raw, "raw")
        if not self.raw:
            raise SchemaValidationError("raw must be non-empty")
        if self.period is not None and not isinstance(
            self.period, PeriodUnderstanding
        ):
            raise SchemaValidationError(
                "period must be a PeriodUnderstanding or null"
            )
        self.confidence = _require_confidence(self.confidence, "confidence")

        if self.period is None:
            if self.confidence != 0.0:
                raise SchemaValidationError(
                    "an unresolved temporal expression requires confidence 0.0"
                )
            return
        if self.confidence != 1.0:
            raise SchemaValidationError(
                "a resolved temporal expression requires confidence 1.0"
            )
        if self.period.raw != self.raw:
            raise SchemaValidationError("period.raw must equal raw")
        if not _period_value_is_valid(self.period):
            raise SchemaValidationError("period value does not match period kind")

    @classmethod
    def from_dict(cls, value: Any, path: str = "TemporalResolution") -> TemporalResolution:
        data = _require_mapping(value, path)
        _require_exact_keys(data, {"raw", "period", "confidence"}, path)
        period = data["period"]
        return cls(
            raw=data["raw"],
            period=(
                None
                if period is None
                else PeriodUnderstanding.from_dict(period, f"{path}.period")
            ),
            confidence=data["confidence"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "raw": self.raw,
            "period": None if self.period is None else self.period.to_dict(),
            "confidence": self.confidence,
        }


@dataclass
class TemporalParseResult:
    resolutions: List[TemporalResolution]

    def __post_init__(self) -> None:
        if not isinstance(self.resolutions, list) or not all(
            isinstance(resolution, TemporalResolution)
            for resolution in self.resolutions
        ):
            raise SchemaValidationError(
                "resolutions must be a list of TemporalResolution values"
            )

    @classmethod
    def from_dict(cls, value: Any) -> TemporalParseResult:
        data = _require_mapping(value, "TemporalParseResult")
        _require_exact_keys(data, {"resolutions"}, "TemporalParseResult")
        resolutions = data["resolutions"]
        if not isinstance(resolutions, list):
            raise SchemaValidationError("resolutions must be a list")
        return cls(
            resolutions=[
                TemporalResolution.from_dict(
                    resolution, f"resolutions[{index}]"
                )
                for index, resolution in enumerate(resolutions)
            ]
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "resolutions": [resolution.to_dict() for resolution in self.resolutions]
        }


@dataclass(frozen=True)
class _Candidate:
    start: int
    end: int
    raw: str
    period: Optional[PeriodUnderstanding]


def _period(raw: str, value: str, kind: PeriodKind) -> PeriodUnderstanding:
    return PeriodUnderstanding(value=value, kind=kind, raw=raw)


def _collect_candidates(text: str) -> List[_Candidate]:
    candidates: List[_Candidate] = []

    for match in _CUMULATIVE_EXPRESSION.finditer(text):
        raw = match.group(0)
        months = match.group("number")
        year = match.group("year")
        period = None
        if (
            months is not None
            and year is not None
            and len(year) == 4
            and 1 <= int(months) <= 12
        ):
            period = _period(raw, f"{year}-{int(months)}M", PeriodKind.LUY_KE)
        candidates.append(_Candidate(match.start(), match.end(), raw, period))

    for match in _CUMULATIVE_CUE.finditer(text):
        candidates.append(
            _Candidate(match.start(), match.end(), match.group(0), None)
        )

    for match in _QUARTER_EXPRESSION.finditer(text):
        raw = match.group(0)
        quarter = match.group("number")
        year = match.group("year")
        period = None
        if (
            quarter is not None
            and year is not None
            and len(year) == 4
            and 1 <= int(quarter) <= 4
        ):
            period = _period(raw, f"{year}-Q{int(quarter)}", PeriodKind.QUY)
        candidates.append(_Candidate(match.start(), match.end(), raw, period))

    for match in _YEAR_EXPRESSION.finditer(text):
        raw = match.group(0)
        year = match.group("year")
        period = None
        if year is not None and len(year) == 4:
            period = _period(raw, year, PeriodKind.NAM)
        candidates.append(_Candidate(match.start(), match.end(), raw, period))

    return candidates


def _select_longest_non_overlapping(candidates: List[_Candidate]) -> List[_Candidate]:
    selected: List[_Candidate] = []
    for candidate in sorted(
        candidates, key=lambda item: (item.start, -(item.end - item.start))
    ):
        if any(
            candidate.start < existing.end and existing.start < candidate.end
            for existing in selected
        ):
            continue
        selected.append(candidate)
    return sorted(selected, key=lambda item: item.start)


def parse_periods(parser_input: TemporalParserInput) -> TemporalParseResult:
    if not isinstance(parser_input, TemporalParserInput):
        raise SchemaValidationError(
            "parser_input must be a TemporalParserInput"
        )
    candidates = _select_longest_non_overlapping(
        _collect_candidates(parser_input.text)
    )
    return TemporalParseResult(
        resolutions=[
            TemporalResolution(
                raw=candidate.raw,
                period=candidate.period,
                confidence=0.0 if candidate.period is None else 1.0,
            )
            for candidate in candidates
        ]
    )
