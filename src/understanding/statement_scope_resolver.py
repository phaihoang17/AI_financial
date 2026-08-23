"""Deterministic v1 resolver for financial-statement scope."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Dict, Optional
import unicodedata

from src.understanding.schemas import (
    SchemaValidationError,
    StatementScope,
    StatementScopeUnderstanding,
    _parse_enum,
    _require_enum,
    _require_exact_keys,
    _require_mapping,
    _require_string,
)


_HOP_NHAT_INDICATORS = (
    "hợp nhất",
    "báo cáo tài chính hợp nhất",
    "bctc hợp nhất",
)
_RIENG_INDICATORS = (
    "riêng",
    "riêng lẻ",
    "báo cáo tài chính riêng",
    "bctc riêng",
    "công ty mẹ",
)


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.split()).casefold()


def _contains_indicator(text: str, indicator: str) -> bool:
    return (
        re.search(
            rf"(?<!\w){re.escape(_normalize_text(indicator))}(?!\w)", text
        )
        is not None
    )


@dataclass
class StatementScopeResolverInput:
    text: str
    context_scope: Optional[StatementScope]

    def __post_init__(self) -> None:
        self.text = _require_string(self.text, "text")
        if self.context_scope is not None:
            self.context_scope = _require_enum(
                self.context_scope, StatementScope, "context_scope"
            )

    @classmethod
    def from_dict(cls, value: Any) -> StatementScopeResolverInput:
        data = _require_mapping(value, "StatementScopeResolverInput")
        _require_exact_keys(
            data, {"text", "context_scope"}, "StatementScopeResolverInput"
        )
        context_scope = data["context_scope"]
        return cls(
            text=data["text"],
            context_scope=(
                None
                if context_scope is None
                else _parse_enum(context_scope, StatementScope, "context_scope")
            ),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "context_scope": (
                None if self.context_scope is None else self.context_scope.value
            ),
        }


class StatementScopeResolutionStatus(str, Enum):
    RESOLVED = "RESOLVED"
    UNRESOLVED = "UNRESOLVED"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass
class StatementScopeResolution:
    status: StatementScopeResolutionStatus
    statement_scope: StatementScopeUnderstanding

    def __post_init__(self) -> None:
        self.status = _require_enum(
            self.status, StatementScopeResolutionStatus, "status"
        )
        if not isinstance(self.statement_scope, StatementScopeUnderstanding):
            raise SchemaValidationError(
                "statement_scope must be a StatementScopeUnderstanding"
            )

        if self.status is StatementScopeResolutionStatus.RESOLVED:
            if self.statement_scope.value is None:
                raise SchemaValidationError(
                    "RESOLVED requires a non-null statement scope"
                )
            if self.statement_scope.confidence != 1.0:
                raise SchemaValidationError(
                    "RESOLVED requires confidence 1.0"
                )
            return

        if self.statement_scope.value is not None:
            raise SchemaValidationError(
                "unresolved or ambiguous scope must remain null"
            )
        if self.statement_scope.inferred:
            raise SchemaValidationError(
                "unresolved or ambiguous scope cannot be inferred"
            )
        if self.statement_scope.confidence != 0.0:
            raise SchemaValidationError(
                "unresolved or ambiguous scope requires confidence 0.0"
            )

    @classmethod
    def from_dict(cls, value: Any) -> StatementScopeResolution:
        data = _require_mapping(value, "StatementScopeResolution")
        _require_exact_keys(
            data, {"status", "statement_scope"}, "StatementScopeResolution"
        )
        return cls(
            status=_parse_enum(
                data["status"], StatementScopeResolutionStatus, "status"
            ),
            statement_scope=StatementScopeUnderstanding.from_dict(
                data["statement_scope"]
            ),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "statement_scope": self.statement_scope.to_dict(),
        }


def _resolved_scope(value: StatementScope, inferred: bool) -> StatementScopeResolution:
    return StatementScopeResolution(
        status=StatementScopeResolutionStatus.RESOLVED,
        statement_scope=StatementScopeUnderstanding(
            value=value, inferred=inferred, confidence=1.0
        ),
    )


def _empty_scope(status: StatementScopeResolutionStatus) -> StatementScopeResolution:
    return StatementScopeResolution(
        status=status,
        statement_scope=StatementScopeUnderstanding(
            value=None, inferred=False, confidence=0.0
        ),
    )


def resolve_statement_scope(
    resolver_input: StatementScopeResolverInput,
) -> StatementScopeResolution:
    if not isinstance(resolver_input, StatementScopeResolverInput):
        raise SchemaValidationError(
            "resolver_input must be a StatementScopeResolverInput"
        )

    text = _normalize_text(resolver_input.text)
    has_hop_nhat = any(
        _contains_indicator(text, indicator) for indicator in _HOP_NHAT_INDICATORS
    )
    has_rieng = any(
        _contains_indicator(text, indicator) for indicator in _RIENG_INDICATORS
    )

    if has_hop_nhat and has_rieng:
        return _empty_scope(StatementScopeResolutionStatus.AMBIGUOUS)
    if has_hop_nhat:
        return _resolved_scope(StatementScope.HOP_NHAT, inferred=False)
    if has_rieng:
        return _resolved_scope(StatementScope.RIENG, inferred=False)
    if resolver_input.context_scope is not None:
        return _resolved_scope(resolver_input.context_scope, inferred=True)
    return _empty_scope(StatementScopeResolutionStatus.UNRESOLVED)
