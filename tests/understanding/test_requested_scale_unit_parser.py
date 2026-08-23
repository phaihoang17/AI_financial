import unittest

from src.understanding.requested_scale_unit_parser import (
    RequestedScale,
    RequestedScaleUnit,
    RequestedScaleUnitParserInput,
    parse_requested_scale_unit,
)
from src.understanding.schemas import QueryUnderstanding, SchemaValidationError


class RequestedScaleUnitParserTests(unittest.TestCase):
    def parse(self, text):
        return parse_requested_scale_unit(
            RequestedScaleUnitParserInput(text=text)
        )

    def test_contract_fields_and_round_trip(self):
        parser_input = RequestedScaleUnitParserInput.from_dict(
            {"text": "đơn vị triệu"}
        )
        self.assertEqual(parser_input.to_dict(), {"text": "đơn vị triệu"})

        result = RequestedScaleUnit.from_dict(
            {"requested_scale": "MILLION", "requested_unit": None}
        )
        self.assertEqual(result.requested_scale, RequestedScale.MILLION)
        self.assertEqual(
            result.to_dict(),
            {"requested_scale": "MILLION", "requested_unit": None},
        )

        with self.assertRaises(SchemaValidationError):
            RequestedScaleUnit.from_dict(
                {
                    "requested_scale": "RAW",
                    "requested_unit": None,
                }
            )
        with self.assertRaises(SchemaValidationError):
            RequestedScaleUnitParserInput.from_dict(
                {"text": "triệu", "extra": True}
            )

    def test_approved_word_mappings(self):
        cases = {
            "đơn vị nghìn": RequestedScale.THOUSAND,
            "đơn vị ngàn": RequestedScale.THOUSAND,
            "đơn vị triệu": RequestedScale.MILLION,
            "đơn vị tỷ": RequestedScale.BILLION,
            "tính theo phần trăm": RequestedScale.PERCENT,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                result = self.parse(text)
                self.assertEqual(result.requested_scale, expected)
                self.assertIsNone(result.requested_unit)

    def test_percent_symbol_mapping(self):
        for text in ("bao nhiêu %", "tăng 10%", "％"):
            with self.subTest(text=text):
                self.assertEqual(
                    self.parse(text).requested_scale, RequestedScale.PERCENT
                )

    def test_empty_or_unsupported_input_returns_null_fields(self):
        for text in ("", " \t\n ", "LNST của AAA", "đơn vị VND"):
            with self.subTest(text=text):
                self.assertEqual(
                    self.parse(text),
                    RequestedScaleUnit(
                        requested_scale=None, requested_unit=None
                    ),
                )

    def test_repeated_same_scale_indicators_are_valid(self):
        result = self.parse("nghìn hoặc ngàn, vẫn tính theo nghìn")
        self.assertEqual(result.requested_scale, RequestedScale.THOUSAND)
        self.assertIsNone(result.requested_unit)

        percent = self.parse("% hay phần trăm")
        self.assertEqual(percent.requested_scale, RequestedScale.PERCENT)
        self.assertIsNone(percent.requested_unit)

    def test_multiple_different_scales_are_unresolved(self):
        for text in ("nghìn hay triệu", "tỷ và %", "triệu phần trăm"):
            with self.subTest(text=text):
                self.assertEqual(
                    self.parse(text),
                    RequestedScaleUnit(
                        requested_scale=None, requested_unit=None
                    ),
                )

    def test_normalization_is_nfkc_casefolded_trimmed_and_collapsed(self):
        self.assertEqual(
            self.parse("  PHẦN\u3000TRĂM  ").requested_scale,
            RequestedScale.PERCENT,
        )
        self.assertEqual(
            self.parse("  TRIỆU  ").requested_scale,
            RequestedScale.MILLION,
        )

    def test_complete_tokens_only_without_fuzzy_or_typo_matching(self):
        unsupported = (
            "nghin",
            "trieu",
            "ty",
            "phầntrăm",
            "siêutriệu",
            "nghìnđồng",
            "nghìn_ty",
        )
        for text in unsupported:
            with self.subTest(text=text):
                self.assertIsNone(self.parse(text).requested_scale)

    def test_currencies_and_units_are_not_parsed_in_v1(self):
        for text in ("VND", "đồng", "USD", "đô la"):
            with self.subTest(text=text):
                result = self.parse(text)
                self.assertIsNone(result.requested_scale)
                self.assertIsNone(result.requested_unit)

        result = self.parse("triệu VND")
        self.assertEqual(result.requested_scale, RequestedScale.MILLION)
        self.assertIsNone(result.requested_unit)

    def test_result_populates_existing_query_understanding_fields(self):
        result = self.parse("đơn vị triệu")
        payload = {
            "raw_question": "đơn vị triệu",
            "company": {
                "raw": None,
                "name": None,
                "ticker": None,
                "confidence": 0.0,
            },
            "periods": [],
            "statement_scope": {
                "value": None,
                "inferred": False,
                "confidence": 0.0,
            },
            "metrics": [],
            "operation": "none",
            **result.to_dict(),
            "missing_information": [],
            "ambiguities": [],
            "confidence": 0.0,
        }
        query = QueryUnderstanding.from_dict(payload)
        self.assertEqual(query.requested_scale, "MILLION")
        self.assertIsNone(query.requested_unit)

    def test_parser_rejects_invalid_input_type(self):
        with self.assertRaises(SchemaValidationError):
            parse_requested_scale_unit("triệu")


if __name__ == "__main__":
    unittest.main()
