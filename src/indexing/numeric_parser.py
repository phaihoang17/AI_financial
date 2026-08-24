"""Strict, lossless numeric-string parsing for TASK-024 v1."""

from __future__ import annotations

import re

from src.indexing.cell_normalizer import normalize_cell_text
from src.indexing.schemas import NumericParseResult, NumericParseStatus
from src.understanding.schemas import SchemaValidationError


_MISSING_LEXEMES = {"", "-", "‒", "–", "—", "―", "−"}
_PLAIN_INTEGER = re.compile(r"[0-9]+\Z")
_PERIOD_GROUPED_INTEGER = re.compile(r"[0-9]{1,3}(?:\.[0-9]{3})+\Z")
_SPACE_GROUPED_INTEGER = re.compile(r"[0-9]{1,3}(?: [0-9]{3})+\Z")
_COMMA_DECIMAL = re.compile(r"([0-9]+),([0-9]+)\Z")
_PERIOD_GROUPED_COMMA_DECIMAL = re.compile(
    r"([0-9]{1,3}(?:\.[0-9]{3})+),([0-9]+)\Z"
)
_COMMON_PERIOD_DECIMAL = re.compile(r"[0-9]+\.[0-9]+\Z")
_COMMON_COMMA_GROUPED = re.compile(
    r"[0-9]{1,3}(?:,[0-9]{3}){2,}(?:\.[0-9]+)?\Z"
)
_COMMON_COMMA_GROUPED_PERIOD_DECIMAL = re.compile(
    r"[0-9]{1,3}(?:,[0-9]{3})+\.[0-9]+\Z"
)


def _result(
    status: NumericParseStatus,
    raw_text: str,
    *,
    decimal_value: str | None = None,
    percent_literal: bool = False,
) -> NumericParseResult:
    return NumericParseResult(
        status=status,
        raw_text=raw_text,
        normalized_lexeme=decimal_value,
        decimal_value=decimal_value,
        percent_literal=percent_literal,
    )


def _canonical_decimal(integer_digits: str, fraction_digits: str, negative: bool) -> str:
    integer = integer_digits.lstrip("0") or "0"
    fraction = fraction_digits.rstrip("0")
    value = integer if not fraction else f"{integer}.{fraction}"
    if negative and (integer != "0" or fraction):
        return f"-{value}"
    return value


def _is_ambiguous_common_notation(value: str) -> bool:
    if _COMMON_COMMA_GROUPED.fullmatch(value):
        return True
    if _COMMON_COMMA_GROUPED_PERIOD_DECIMAL.fullmatch(value):
        return True
    if _COMMON_PERIOD_DECIMAL.fullmatch(value):
        return not bool(_PERIOD_GROUPED_INTEGER.fullmatch(value))
    return False


def parse_numeric_string(raw_text: str) -> NumericParseResult:
    """Parse only the approved comma-decimal, period/space-grouped v1 grammar."""

    if not isinstance(raw_text, str):
        raise SchemaValidationError("numeric raw_text must be a string")

    value = normalize_cell_text(raw_text)
    if value in _MISSING_LEXEMES:
        return _result(NumericParseStatus.MISSING, raw_text)

    percent_literal = False
    had_numeric_wrapper = False
    if value.endswith("%"):
        percent_literal = True
        had_numeric_wrapper = True
        value = value[:-1].rstrip()
        if not value:
            return _result(NumericParseStatus.MALFORMED, raw_text)
    elif "%" in value:
        return _result(NumericParseStatus.MALFORMED, raw_text)

    negative = False
    if value.startswith("(") or value.endswith(")"):
        had_numeric_wrapper = True
        if not (value.startswith("(") and value.endswith(")")):
            return _result(
                NumericParseStatus.MALFORMED,
                raw_text,
                percent_literal=percent_literal,
            )
        value = value[1:-1]
        negative = True
        if not value or value[0] in "+-" or "(" in value or ")" in value:
            return _result(
                NumericParseStatus.MALFORMED,
                raw_text,
                percent_literal=percent_literal,
            )
    elif value.startswith(("+", "-")):
        had_numeric_wrapper = True
        negative = value[0] == "-"
        value = value[1:]
        if not value:
            return _result(
                NumericParseStatus.MALFORMED,
                raw_text,
                percent_literal=percent_literal,
            )

    integer_digits: str | None = None
    fraction_digits = ""
    if _PLAIN_INTEGER.fullmatch(value):
        integer_digits = value
    elif _PERIOD_GROUPED_INTEGER.fullmatch(value):
        integer_digits = value.replace(".", "")
    elif _SPACE_GROUPED_INTEGER.fullmatch(value):
        integer_digits = value.replace(" ", "")
    else:
        decimal_match = _COMMA_DECIMAL.fullmatch(value)
        grouped_decimal_match = _PERIOD_GROUPED_COMMA_DECIMAL.fullmatch(value)
        if grouped_decimal_match is not None:
            integer_digits = grouped_decimal_match.group(1).replace(".", "")
            fraction_digits = grouped_decimal_match.group(2)
        elif decimal_match is not None:
            integer_digits = decimal_match.group(1)
            fraction_digits = decimal_match.group(2)

    if integer_digits is not None:
        decimal_value = _canonical_decimal(
            integer_digits, fraction_digits, negative
        )
        return _result(
            NumericParseStatus.PARSED,
            raw_text,
            decimal_value=decimal_value,
            percent_literal=percent_literal,
        )

    if _is_ambiguous_common_notation(value):
        return _result(
            NumericParseStatus.AMBIGUOUS,
            raw_text,
            percent_literal=percent_literal,
        )
    if (
        had_numeric_wrapper
        or value[0].isdigit()
        or value[0] in "+-("
        or value[0] == "−"
    ):
        return _result(
            NumericParseStatus.MALFORMED,
            raw_text,
            percent_literal=percent_literal,
        )
    return _result(NumericParseStatus.NOT_NUMERIC, raw_text)
