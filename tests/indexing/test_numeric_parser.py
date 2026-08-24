import unittest

from src.indexing.numeric_parser import parse_numeric_string
from src.indexing.schemas import NumericParseStatus


class NumericParserV1Tests(unittest.TestCase):
    def assert_parsed(self, raw, expected, percent=False):
        result = parse_numeric_string(raw)
        self.assertIs(result.status, NumericParseStatus.PARSED)
        self.assertEqual(result.raw_text, raw)
        self.assertEqual(result.normalized_lexeme, expected)
        self.assertEqual(result.decimal_value, expected)
        self.assertEqual(result.percent_literal, percent)

    def test_approved_integer_and_decimal_grammar(self):
        cases = {
            "1250": "1250",
            "1.250": "1250",
            "1 250": "1250",
            "1\u00a0250": "1250",
            "12,5": "12.5",
            "1.250,75": "1250.75",
            "+001250": "1250",
            "-1.250": "-1250",
            "(1.250)": "-1250",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assert_parsed(raw, expected)

    def test_percent_is_literal_metadata_and_does_not_divide(self):
        self.assert_parsed("12,50 %", "12.5", percent=True)

    def test_blank_and_dash_variants_are_missing(self):
        for raw in ("", "  ", "-", "–", "—", "−"):
            with self.subTest(raw=raw):
                result = parse_numeric_string(raw)
                self.assertIs(result.status, NumericParseStatus.MISSING)
                self.assertIsNone(result.decimal_value)

    def test_ambiguous_common_separator_formats_are_not_guessed(self):
        for raw in ("12.5", "1,234,567", "1,250.75"):
            with self.subTest(raw=raw):
                result = parse_numeric_string(raw)
                self.assertIs(result.status, NumericParseStatus.AMBIGUOUS)
                self.assertIsNone(result.decimal_value)

    def test_malformed_grouping_signs_and_suffixes_are_not_guessed(self):
        for raw in (
            "12.50.000",
            "1 25",
            "1 250,75",
            "1.250 VND",
            "1.250kg",
            "-(1.250)",
            "+abc",
            "( 1.250 )",
            "(1.250",
            "12%5",
        ):
            with self.subTest(raw=raw):
                result = parse_numeric_string(raw)
                self.assertIs(result.status, NumericParseStatus.MALFORMED)
                self.assertIsNone(result.decimal_value)

    def test_ordinary_text_is_not_numeric(self):
        for raw in ("Doanh thu", "Năm 2024", "VND 1.250"):
            with self.subTest(raw=raw):
                self.assertIs(
                    parse_numeric_string(raw).status,
                    NumericParseStatus.NOT_NUMERIC,
                )

    def test_ambiguous_percent_keeps_literal_provenance_without_conversion(self):
        result = parse_numeric_string("12.5%")
        self.assertIs(result.status, NumericParseStatus.AMBIGUOUS)
        self.assertTrue(result.percent_literal)
        self.assertIsNone(result.decimal_value)

    def test_decimal_value_is_a_string_and_negative_zero_is_canonical(self):
        self.assert_parsed("-0,00", "0")
        self.assertIsInstance(parse_numeric_string("1.250,75").decimal_value, str)


if __name__ == "__main__":
    unittest.main()
