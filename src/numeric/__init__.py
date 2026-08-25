"""Shared deterministic numeric boundaries for M6 and later execution stages."""

from src.numeric.m2_adapter import (
    NumericBoundaryError,
    adapt_numeric_parse_result,
    parse_canonical_decimal,
)
from src.numeric.scale_conversion import (
    ScaleConversionFailureCode,
    ScaleConversionResult,
    ScaleConversionStatus,
    convert_canonical_scale,
)

__all__ = [
    "NumericBoundaryError",
    "ScaleConversionFailureCode",
    "ScaleConversionResult",
    "ScaleConversionStatus",
    "adapt_numeric_parse_result",
    "convert_canonical_scale",
    "parse_canonical_decimal",
]
