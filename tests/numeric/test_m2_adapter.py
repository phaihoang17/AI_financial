import unittest
from unittest.mock import patch

from src.evidence.schemas import CanonicalDecimal
from src.indexing.numeric_parser import parse_numeric_string
from src.indexing.schemas import NumericParseStatus
from src.numeric.m2_adapter import (
    NumericBoundaryError,
    adapt_numeric_parse_result,
    parse_canonical_decimal,
)
from src.understanding.schemas import SchemaValidationError


class M2NumericAdapterTests(unittest.TestCase):
    def test_adapter_delegates_to_existing_m2_parser(self):
        with patch(
            "src.numeric.m2_adapter.numeric_parser.parse_numeric_string",
            wraps=parse_numeric_string,
        ) as parser:
            value = parse_canonical_decimal("1.234.567,8900")

        parser.assert_called_once_with("1.234.567,8900")
        self.assertEqual(value, "1234567.89")
        self.assertIsInstance(value, CanonicalDecimal)

    def test_canonical_decimals_remain_strings(self):
        for raw_text, expected in (
            ("00012", "12"),
            ("(1.250)", "-1250"),
            ("0,1250", "0.125"),
        ):
            with self.subTest(raw_text=raw_text):
                value = parse_canonical_decimal(raw_text)
                self.assertEqual(value, expected)
                self.assertIsInstance(value, str)
                self.assertNotIsInstance(value, float)

    def test_missing_ambiguous_and_malformed_statuses_propagate(self):
        for raw_text, expected_code in (
            ("-", NumericParseStatus.MISSING),
            ("1.23", NumericParseStatus.AMBIGUOUS),
            ("12,34,56", NumericParseStatus.MALFORMED),
        ):
            with self.subTest(raw_text=raw_text):
                with self.assertRaises(NumericBoundaryError) as raised:
                    parse_canonical_decimal(raw_text)
                self.assertIs(raised.exception.code, expected_code)
                self.assertEqual(raised.exception.raw_text, raw_text)

    def test_non_numeric_status_is_typed_too(self):
        with self.assertRaises(NumericBoundaryError) as raised:
            adapt_numeric_parse_result(parse_numeric_string("không có"))
        self.assertIs(raised.exception.code, NumericParseStatus.NOT_NUMERIC)

    def test_adapter_does_not_accept_float_input(self):
        with self.assertRaises(SchemaValidationError):
            parse_canonical_decimal(1.25)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
