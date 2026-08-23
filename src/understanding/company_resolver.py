"""Deterministic v1 company/ticker alias resolution."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Tuple

from src.understanding.schemas import (
    CompanyUnderstanding,
    SchemaValidationError,
    _parse_enum,
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
    return value.strip().casefold()


@dataclass
class CompanyAlias:
    alias: str
    name: str
    ticker: str

    def __post_init__(self) -> None:
        self.alias = _require_non_empty_string(self.alias, "alias")
        self.name = _require_non_empty_string(self.name, "name")
        self.ticker = _require_non_empty_string(self.ticker, "ticker")

    @classmethod
    def from_dict(cls, value: Any) -> CompanyAlias:
        data = _require_mapping(value, "CompanyAlias")
        _require_exact_keys(data, {"alias", "name", "ticker"}, "CompanyAlias")
        return cls(alias=data["alias"], name=data["name"], ticker=data["ticker"])

    def to_dict(self) -> Dict[str, str]:
        return {"alias": self.alias, "name": self.name, "ticker": self.ticker}


@dataclass
class CompanyCandidate:
    name: str
    ticker: str

    def __post_init__(self) -> None:
        self.name = _require_non_empty_string(self.name, "candidate.name")
        self.ticker = _require_non_empty_string(self.ticker, "candidate.ticker")

    @classmethod
    def from_dict(cls, value: Any, path: str = "CompanyCandidate") -> CompanyCandidate:
        data = _require_mapping(value, path)
        _require_exact_keys(data, {"name", "ticker"}, path)
        return cls(name=data["name"], ticker=data["ticker"])

    def to_dict(self) -> Dict[str, str]:
        return {"name": self.name, "ticker": self.ticker}


class CompanyResolutionStatus(str, Enum):
    RESOLVED = "RESOLVED"
    UNRESOLVED = "UNRESOLVED"
    AMBIGUOUS = "AMBIGUOUS"


def _candidate_key(candidate: CompanyCandidate) -> Tuple[str, str]:
    return (
        _normalize_match_value(candidate.name),
        _normalize_match_value(candidate.ticker),
    )


@dataclass
class CompanyResolution:
    status: CompanyResolutionStatus
    company: CompanyUnderstanding
    candidates: List[CompanyCandidate]

    def __post_init__(self) -> None:
        self.status = _require_enum(
            self.status, CompanyResolutionStatus, "status"
        )
        if not isinstance(self.company, CompanyUnderstanding):
            raise SchemaValidationError("company must be a CompanyUnderstanding")
        if not isinstance(self.company.raw, str):
            raise SchemaValidationError("company.raw must preserve the string input")
        if not isinstance(self.candidates, list) or not all(
            isinstance(candidate, CompanyCandidate) for candidate in self.candidates
        ):
            raise SchemaValidationError(
                "candidates must be a list of CompanyCandidate values"
            )

        candidate_keys = [_candidate_key(candidate) for candidate in self.candidates]
        if len(candidate_keys) != len(set(candidate_keys)):
            raise SchemaValidationError("candidates must contain distinct companies")

        if self.status is CompanyResolutionStatus.RESOLVED:
            if len(self.candidates) != 1:
                raise SchemaValidationError(
                    "RESOLVED requires exactly one candidate"
                )
            candidate = self.candidates[0]
            if (
                self.company.name != candidate.name
                or self.company.ticker != candidate.ticker
                or self.company.confidence != 1.0
            ):
                raise SchemaValidationError(
                    "RESOLVED company must match its candidate with confidence 1.0"
                )
            return

        if self.company.name is not None or self.company.ticker is not None:
            raise SchemaValidationError(
                "unresolved company identity must remain null"
            )
        if self.company.confidence != 0.0:
            raise SchemaValidationError(
                "unresolved company confidence must be 0.0"
            )
        if self.status is CompanyResolutionStatus.UNRESOLVED:
            if self.candidates:
                raise SchemaValidationError("UNRESOLVED requires no candidates")
        elif len(self.candidates) < 2:
            raise SchemaValidationError(
                "AMBIGUOUS requires at least two distinct candidates"
            )

    @classmethod
    def from_dict(cls, value: Any) -> CompanyResolution:
        data = _require_mapping(value, "CompanyResolution")
        _require_exact_keys(
            data, {"status", "company", "candidates"}, "CompanyResolution"
        )
        candidates = data["candidates"]
        if not isinstance(candidates, list):
            raise SchemaValidationError("candidates must be a list")
        return cls(
            status=_parse_enum(
                data["status"], CompanyResolutionStatus, "status"
            ),
            company=CompanyUnderstanding.from_dict(data["company"]),
            candidates=[
                CompanyCandidate.from_dict(candidate, f"candidates[{index}]")
                for index, candidate in enumerate(candidates)
            ],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "company": self.company.to_dict(),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }


def resolve_company(raw: str, aliases: List[CompanyAlias]) -> CompanyResolution:
    """Resolve by trimmed case-insensitive alias or ticker equality only."""
    raw = _require_string(raw, "raw")
    if not isinstance(aliases, list) or not all(
        isinstance(alias, CompanyAlias) for alias in aliases
    ):
        raise SchemaValidationError("aliases must be a list of CompanyAlias values")

    normalized_raw = _normalize_match_value(raw)
    matches: Dict[Tuple[str, str], CompanyCandidate] = {}
    for alias in aliases:
        if normalized_raw not in {
            _normalize_match_value(alias.alias),
            _normalize_match_value(alias.ticker),
        }:
            continue
        candidate = CompanyCandidate(name=alias.name, ticker=alias.ticker)
        matches.setdefault(_candidate_key(candidate), candidate)

    candidates = sorted(
        matches.values(),
        key=lambda candidate: (
            _normalize_match_value(candidate.ticker),
            _normalize_match_value(candidate.name),
            candidate.ticker,
            candidate.name,
        ),
    )

    if len(candidates) == 1:
        candidate = candidates[0]
        return CompanyResolution(
            status=CompanyResolutionStatus.RESOLVED,
            company=CompanyUnderstanding(
                raw=raw,
                name=candidate.name,
                ticker=candidate.ticker,
                confidence=1.0,
            ),
            candidates=candidates,
        )
    if not candidates:
        return CompanyResolution(
            status=CompanyResolutionStatus.UNRESOLVED,
            company=CompanyUnderstanding(
                raw=raw, name=None, ticker=None, confidence=0.0
            ),
            candidates=[],
        )
    return CompanyResolution(
        status=CompanyResolutionStatus.AMBIGUOUS,
        company=CompanyUnderstanding(
            raw=raw, name=None, ticker=None, confidence=0.0
        ),
        candidates=candidates,
    )
