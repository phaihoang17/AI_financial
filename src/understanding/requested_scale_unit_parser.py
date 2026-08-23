"""Deterministic v1 parser for explicitly requested output scale and unit."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Dict, Optional, Set
import unicodedata

from src.understanding.schemas import (
    SchemaValidationError,
    _parse_enum,
    _require_enum,
    _require_exact_keys,
    _require_mapping,
    _require_optional_string,
    _require_string,
)


class RequestedScale(str, Enum):
    THOUSAND = "THOUSAND"
    MILLION = "MILLION"
    BILLION = "BILLION"
    PERCENT = "PERCENT"


_WORD_MAPPINGS = {
    "nghìn": RequestedScale.THOUSAND,
    "ngàn": RequestedScale.THOUSAND,
    "triệu": RequestedScale.MILLION,
    "tỷ": RequestedScale.BILLION,
    "phần trăm": RequestedScale.PERCENT,
}
_PERCENT_PATTERN = re.compile(r"(?<!%)%(?!%)")


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.split()).casefold()


def _contains_complete_phrase(text: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


@dataclass
class RequestedScaleUnitParserInput:
    text: str

    def __post_init__(self) -> None:
        self.text = _require_string(self.text, "text")

    @classmethod
    def from_dict(cls, value: Any) -> RequestedScaleUnitParserInput:
        data = _require_mapping(value, "RequestedScaleUnitParserInput")
        _require_exact_keys(
            data, {"text"}, "RequestedScaleUnitParserInput"
        )
        return cls(text=data["text"])

    def to_dict(self) -> Dict[str, str]:
        return {"text": self.text}


@dataclass
class RequestedScaleUnit:
    requested_scale: Optional[RequestedScale]
    requested_unit: Optional[str]

    def __post_init__(self) -> None:
        if self.requested_scale is not None:
            self.requested_scale = _require_enum(
                self.requested_scale, RequestedScale, "requested_scale"
            )
        self.requested_unit = _require_optional_string(
            self.requested_unit, "requested_unit"
        )

    @classmethod
    def from_dict(cls, value: Any) -> RequestedScaleUnit:
        data = _require_mapping(value, "RequestedScaleUnit")
        _require_exact_keys(
            data,
            {"requested_scale", "requested_unit"},
            "RequestedScaleUnit",
        )
        scale = data["requested_scale"]
        return cls(
            requested_scale=(
                None
                if scale is None
                else _parse_enum(scale, RequestedScale, "requested_scale")
            ),
            requested_unit=data["requested_unit"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "requested_scale": (
                None if self.requested_scale is None else self.requested_scale.value
            ),
            "requested_unit": self.requested_unit,
        }


def _find_scales(text: str) -> Set[RequestedScale]:
    scales = {
        scale
        for phrase, scale in _WORD_MAPPINGS.items()
        if _contains_complete_phrase(text, phrase)
    }
    if _PERCENT_PATTERN.search(text) is not None:
        scales.add(RequestedScale.PERCENT)
    return scales


def parse_requested_scale_unit(
    parser_input: RequestedScaleUnitParserInput,
) -> RequestedScaleUnit:
    """Parse only approved v1 scale expressions; units remain unresolved."""
    if not isinstance(parser_input, RequestedScaleUnitParserInput):
        raise SchemaValidationError(
            "parser_input must be a RequestedScaleUnitParserInput"
        )

    text = _normalize_text(parser_input.text)
    scales = _find_scales(text) if text else set()
    requested_scale = next(iter(scales)) if len(scales) == 1 else None
    return RequestedScaleUnit(
        requested_scale=requested_scale,
        requested_unit=None,
    )
