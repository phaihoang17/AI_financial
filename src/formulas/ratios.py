"""Financial ratio definitions and Decimal reference arithmetic (ADR-069).

This module is the single source of truth for derived-ratio semantics used by
the Text-to-Pandas path: the code-generation prompt renders ``formula_text``
from these definitions and verification recomputes with the same definitions.
Growth and average semantics come from the existing M4/M6 FormulaRegistry
implementation metadata, so no competing GROWTH_RATE/AVERAGE definition exists.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, localcontext

from src.sandbox.decimal_policy import make_execution_decimal_context
from src.supervisor.formula_registry import get_formula_implementation

END_OF_YEAR = "END_OF_YEAR"
AVERAGE_OPENING_CLOSING = "AVERAGE_OPENING_CLOSING"


@dataclass(frozen=True)
class RatioDefinition:
    ratio_id: str
    name_vi: str
    numerator: str
    denominator: str
    denominator_basis: str
    multiplier: str
    unit: str
    synonyms: tuple[str, ...]

    @property
    def formula_text(self) -> str:
        denominator = (
            f"({self.denominator}[Y] + {self.denominator}[Y-1]) / 2"
            if self.denominator_basis == AVERAGE_OPENING_CLOSING
            else f"{self.denominator}[Y]"
        )
        scale = f" * {self.multiplier}" if self.multiplier != "1" else ""
        return f"{self.ratio_id}[Y] = {self.numerator}[Y] / {denominator}{scale}  (unit {self.unit})"

    @property
    def required_metrics(self) -> tuple[str, str]:
        return (self.numerator, self.denominator)

    def to_dict(self) -> dict[str, object]:
        return {
            "ratio_id": self.ratio_id,
            "name_vi": self.name_vi,
            "formula": self.formula_text,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "denominator_basis": self.denominator_basis,
            "unit": self.unit,
        }


RATIOS: tuple[RatioDefinition, ...] = (
    RatioDefinition("ROE", "Tỷ suất lợi nhuận trên vốn chủ sở hữu bình quân", "LNST", "EQUITY",
                    AVERAGE_OPENING_CLOSING, "100", "PERCENT",
                    ("roe", "tỷ suất lợi nhuận trên vốn chủ sở hữu",
                     "tỷ suất lợi nhuận trên vốn chủ sở hữu bình quân", "return on equity")),
    RatioDefinition("ROA", "Tỷ suất lợi nhuận trên tổng tài sản bình quân", "LNST", "TOTAL_ASSETS",
                    AVERAGE_OPENING_CLOSING, "100", "PERCENT",
                    ("roa", "tỷ suất lợi nhuận trên tổng tài sản",
                     "tỷ suất lợi nhuận trên tổng tài sản bình quân", "return on assets")),
    RatioDefinition("GROSS_MARGIN", "Biên lợi nhuận gộp", "GROSS_PROFIT", "NET_REVENUE",
                    END_OF_YEAR, "100", "PERCENT", ("biên lợi nhuận gộp", "biên gộp", "tỷ suất lợi nhuận gộp",
                                                    "tỷ suất lợi nhuận gộp trên doanh thu thuần", "gross margin")),
    RatioDefinition("NET_MARGIN", "Biên lợi nhuận ròng", "LNST", "NET_REVENUE",
                    END_OF_YEAR, "100", "PERCENT",
                    ("biên lợi nhuận ròng", "biên lợi nhuận sau thuế", "biên ròng", "tỷ suất lợi nhuận ròng",
                     "tỷ suất lợi nhuận sau thuế trên doanh thu thuần", "net margin")),
    RatioDefinition("DEBT_TO_EQUITY", "Tỷ lệ nợ phải trả trên vốn chủ sở hữu", "LIABILITIES", "EQUITY",
                    END_OF_YEAR, "1", "TIMES",
                    ("nợ trên vốn chủ sở hữu", "nợ phải trả trên vốn chủ sở hữu", "nợ/vốn chủ sở hữu", "d/e",
                     "debt to equity")),
    RatioDefinition("CURRENT_RATIO", "Hệ số thanh toán hiện hành", "CURRENT_ASSETS", "CURRENT_LIABILITIES",
                    END_OF_YEAR, "1", "TIMES",
                    ("hệ số thanh toán hiện hành", "khả năng thanh toán hiện hành", "current ratio")),
)

RATIOS_BY_ID: dict[str, RatioDefinition] = {ratio.ratio_id: ratio for ratio in RATIOS}


def _decimal(value: object) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def compute_ratio(
    ratio: RatioDefinition,
    numerator: object,
    denominator: object,
    opening_denominator: object | None = None,
) -> Decimal | None:
    """Reference value; ``None`` when an input is missing or the denominator is zero."""
    with localcontext(make_execution_decimal_context()):
        if numerator is None or denominator is None:
            return None
        base = _decimal(denominator)
        if ratio.denominator_basis == AVERAGE_OPENING_CLOSING:
            if opening_denominator is None:
                return None
            base = (base + _decimal(opening_denominator)) / 2
        if base.is_zero():
            return None
        return _decimal(numerator) / base * Decimal(ratio.multiplier)


def growth_rate(previous: object, current: object) -> Decimal | None:
    """Registered GROWTH_RATE: ordered (previous, current), times its approved constant."""
    implementation = get_formula_implementation("GROWTH_RATE")
    if implementation is None or implementation.input_order != ["previous", "current"]:
        raise RuntimeError("GROWTH_RATE implementation metadata is unavailable")
    with localcontext(make_execution_decimal_context()):
        before = _decimal(previous)
        if before.is_zero():
            return None
        return (_decimal(current) - before) / before * Decimal(implementation.constants[0])


def average(values: Sequence[object]) -> Decimal | None:
    """Registered AVERAGE: arithmetic mean of at least ``min_arity`` values."""
    implementation = get_formula_implementation("AVERAGE")
    if implementation is None:
        raise RuntimeError("AVERAGE implementation metadata is unavailable")
    if len(values) < implementation.min_arity:
        return None
    with localcontext(make_execution_decimal_context()):
        return sum((_decimal(value) for value in values), Decimal("0")) / Decimal(len(values))
