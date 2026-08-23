from dataclasses import fields
import unittest

from src.understanding.schemas import PeriodKind, SchemaValidationError
from src.understanding.temporal_parser import (
    TemporalParseResult,
    TemporalParserInput,
    TemporalResolution,
    parse_periods,
)


class TemporalParserContractTests(unittest.TestCase):
    def test_fields_match_canonical_contracts(self):
        self.assertEqual(
            [field.name for field in fields(TemporalParserInput)], ["text"]
        )
        self.assertEqual(
            [field.name for field in fields(TemporalResolution)],
            ["raw", "period", "confidence"],
        )
        self.assertEqual(
            [field.name for field in fields(TemporalParseResult)], ["resolutions"]
        )

    def test_missing_and_unknown_fields_are_rejected(self):
        with self.assertRaises(SchemaValidationError):
            TemporalParserInput.from_dict({})
        with self.assertRaises(SchemaValidationError):
            TemporalParserInput.from_dict({"text": "năm 2015", "extra": True})

    def test_resolution_invariants_are_enforced(self):
        with self.assertRaises(SchemaValidationError):
            TemporalResolution(raw="quý 3", period=None, confidence=1.0)
        with self.assertRaises(SchemaValidationError):
            TemporalResolution(raw="", period=None, confidence=0.0)


class TemporalParserTests(unittest.TestCase):
    def test_year(self):
        result = parse_periods(TemporalParserInput("năm 2015"))
        resolution = result.resolutions[0]

        self.assertEqual(resolution.period.value, "2015")
        self.assertEqual(resolution.period.kind, PeriodKind.NAM)
        self.assertEqual(resolution.confidence, 1.0)

    def test_supported_quarter_forms(self):
        for text in ("quý 3/2015", "quý 3 năm 2015"):
            with self.subTest(text=text):
                resolution = parse_periods(TemporalParserInput(text)).resolutions[0]
                self.assertEqual(resolution.period.value, "2015-Q3")
                self.assertEqual(resolution.period.kind, PeriodKind.QUY)

    def test_supported_cumulative_forms(self):
        for text in ("lũy kế 9 tháng năm 2015", "lũy kế 9 tháng/2015"):
            with self.subTest(text=text):
                resolution = parse_periods(TemporalParserInput(text)).resolutions[0]
                self.assertEqual(resolution.period.value, "2015-9M")
                self.assertEqual(resolution.period.kind, PeriodKind.LUY_KE)

    def test_incomplete_expressions_are_unresolved(self):
        for text in ("năm", "quý 3", "lũy kế 9 tháng"):
            with self.subTest(text=text):
                resolution = parse_periods(TemporalParserInput(text)).resolutions[0]
                self.assertIsNone(resolution.period)
                self.assertEqual(resolution.confidence, 0.0)

    def test_invalid_quarter_and_month_are_unresolved(self):
        for text in ("quý 5/2015", "lũy kế 13 tháng năm 2015"):
            with self.subTest(text=text):
                resolution = parse_periods(TemporalParserInput(text)).resolutions[0]
                self.assertIsNone(resolution.period)
                self.assertEqual(resolution.confidence, 0.0)

    def test_nearby_year_is_never_inferred_for_cumulative_period(self):
        result = parse_periods(
            TemporalParserInput("năm 2015 và lũy kế 9 tháng")
        )

        self.assertEqual(result.resolutions[0].period.value, "2015")
        self.assertIsNone(result.resolutions[1].period)

    def test_multiple_periods_preserve_source_order_and_exact_raw_text(self):
        text = "So sánh NĂM 2014 với quý 3/2015"
        result = parse_periods(TemporalParserInput(text))

        self.assertEqual(
            [resolution.raw for resolution in result.resolutions],
            ["NĂM 2014", "quý 3/2015"],
        )
        self.assertEqual(
            [resolution.period.value for resolution in result.resolutions],
            ["2014", "2015-Q3"],
        )
        self.assertEqual(
            [resolution.period.raw for resolution in result.resolutions],
            ["NĂM 2014", "quý 3/2015"],
        )

    def test_longest_expression_prevents_nested_year_result(self):
        result = parse_periods(TemporalParserInput("quý 3 năm 2015"))

        self.assertEqual(len(result.resolutions), 1)
        self.assertEqual(result.resolutions[0].period.value, "2015-Q3")

    def test_no_temporal_expression_returns_empty_result(self):
        result = parse_periods(TemporalParserInput("LNST của AAA"))
        self.assertEqual(result.resolutions, [])

    def test_serialization_round_trip_is_deterministic(self):
        result = parse_periods(TemporalParserInput("năm 2015 và quý 2/2016"))
        payload = result.to_dict()

        self.assertEqual(list(payload), ["resolutions"])
        self.assertEqual(TemporalParseResult.from_dict(payload).to_dict(), payload)


if __name__ == "__main__":
    unittest.main()
