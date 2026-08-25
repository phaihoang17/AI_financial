"""M6/M7 adapter over the existing M2 numeric parser contract."""

from __future__ import annotations

from src.evidence.schemas import CanonicalDecimal
from src.indexing import numeric_parser
from src.indexing.schemas import NumericParseResult, NumericParseStatus
from src.understanding.schemas import SchemaValidationError


class NumericBoundaryError(SchemaValidationError):
    """Expose an unsuccessful M2 parse status as a typed boundary failure."""

    def __init__(self, code: NumericParseStatus, raw_text: str) -> None:
        self.code = code
        self.raw_text = raw_text
        super().__init__(f"numeric parsing failed with {code.value}: {raw_text!r}")


def adapt_numeric_parse_result(result: NumericParseResult) -> CanonicalDecimal:
    """Return the parsed canonical string or preserve the exact M2 failure type."""
    if not isinstance(result, NumericParseResult):
        raise SchemaValidationError("result must be a NumericParseResult")
    if result.status is not NumericParseStatus.PARSED:
        raise NumericBoundaryError(result.status, result.raw_text)
    if result.decimal_value is None:  # Defensive; NumericParseResult already enforces this.
        raise SchemaValidationError("PARSED result must contain decimal_value")
    return CanonicalDecimal(result.decimal_value)


def parse_canonical_decimal(raw_text: str) -> CanonicalDecimal:
    """Delegate parsing to M2; this adapter defines no additional numeric grammar."""
    return adapt_numeric_parse_result(numeric_parser.parse_numeric_string(raw_text))
