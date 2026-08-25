"""Canonical Decimal context and serialization for M7 execution values."""

from __future__ import annotations

from decimal import (
    Context,
    Decimal,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
    ROUND_HALF_EVEN,
    Rounded,
)

from src.evidence.schemas import CanonicalDecimal
from src.understanding.schemas import SchemaValidationError


EXECUTION_DECIMAL_PRECISION = 50
EXECUTION_DECIMAL_ROUNDING = ROUND_HALF_EVEN


def make_execution_decimal_context() -> Context:
    """Return a fresh canonical context so callers cannot mutate shared state."""
    context = Context(
        prec=EXECUTION_DECIMAL_PRECISION,
        rounding=EXECUTION_DECIMAL_ROUNDING,
    )
    context.traps[DivisionByZero] = True
    context.traps[InvalidOperation] = True
    context.traps[Overflow] = True
    context.traps[Inexact] = False
    context.traps[Rounded] = False
    return context


def serialize_execution_decimal(value: Decimal) -> CanonicalDecimal:
    """Serialize a finite Decimal without exponent notation or float conversion."""
    if not isinstance(value, Decimal):
        raise SchemaValidationError("execution value must be a Decimal")
    if not value.is_finite():
        raise SchemaValidationError("execution value must be finite")
    if value.is_zero():
        return CanonicalDecimal("0")
    serialized = format(value, "f")
    if "." in serialized:
        serialized = serialized.rstrip("0").rstrip(".")
    return CanonicalDecimal(serialized)
