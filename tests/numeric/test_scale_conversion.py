import unittest

from src.evidence.schemas import CanonicalDecimal, Scale
from src.numeric.scale_conversion import (
    ScaleConversionFailureCode,
    ScaleConversionResult,
    ScaleConversionStatus,
    convert_canonical_scale,
)
from src.understanding.requested_scale_unit_parser import RequestedScale


class ScaleConversionTests(unittest.TestCase):
    def assert_success(self, result, value, scale, unit=None):
        self.assertIs(result.status, ScaleConversionStatus.SUCCESS)
        self.assertEqual(result.canonical_value, value)
        self.assertIsInstance(result.canonical_value, CanonicalDecimal)
        self.assertIs(result.output_scale, scale)
        self.assertEqual(result.output_unit, unit)
        self.assertIsNone(result.failure_code)

    def assert_rejected(self, result, code):
        self.assertIs(result.status, ScaleConversionStatus.REJECTED)
        self.assertIs(result.failure_code, code)
        self.assertIsNone(result.canonical_value)

    def test_raw_thousand_million_and_billion_conversions(self):
        cases = (
            ("1", Scale.RAW, Scale.THOUSAND, "0.001"),
            ("1", Scale.THOUSAND, Scale.RAW, "1000"),
            ("2.5", Scale.RAW, Scale.MILLION, "0.0000025"),
            ("2.5", Scale.MILLION, Scale.RAW, "2500000"),
            ("3", Scale.RAW, Scale.BILLION, "0.000000003"),
            ("3", Scale.BILLION, Scale.RAW, "3000000000"),
        )
        for value, source, output, expected in cases:
            with self.subTest(source=source, output=output):
                self.assert_success(
                    convert_canonical_scale(
                        CanonicalDecimal(value),
                        source_scale=source,
                        requested_output_scale=output,
                    ),
                    expected,
                    output,
                )

    def test_million_and_billion_conversion_both_directions(self):
        self.assert_success(
            convert_canonical_scale(
                CanonicalDecimal("2.5"),
                source_scale=Scale.MILLION,
                requested_output_scale=Scale.BILLION,
            ),
            "0.0025",
            Scale.BILLION,
        )
        self.assert_success(
            convert_canonical_scale(
                CanonicalDecimal("2.5"),
                source_scale=Scale.BILLION,
                requested_output_scale=RequestedScale.MILLION,
            ),
            "2500",
            Scale.MILLION,
        )

    def test_null_requested_scale_preserves_value_scale_and_unit(self):
        value = CanonicalDecimal("1200.00")
        result = convert_canonical_scale(
            value,
            source_scale=Scale.THOUSAND,
            source_unit="VND",
            requested_output_scale=None,
        )
        self.assert_success(result, "1200.00", Scale.THOUSAND, "VND")
        self.assertIs(result.canonical_value, value)

    def test_same_scale_and_unit_are_identity(self):
        self.assert_success(
            convert_canonical_scale(
                CanonicalDecimal("12.50"),
                source_scale=Scale.MILLION,
                source_unit="VND",
                requested_output_scale=Scale.MILLION,
                requested_output_unit="VND",
            ),
            "12.50",
            Scale.MILLION,
            "VND",
        )

    def test_percent_identity_is_allowed(self):
        self.assert_success(
            convert_canonical_scale(
                CanonicalDecimal("12.5"),
                source_scale=Scale.PERCENT,
                requested_output_scale=RequestedScale.PERCENT,
            ),
            "12.5",
            Scale.PERCENT,
        )

    def test_percent_and_magnitude_conversions_are_rejected(self):
        for source, output in (
            (Scale.PERCENT, Scale.RAW),
            (Scale.RAW, Scale.PERCENT),
        ):
            with self.subTest(source=source, output=output):
                self.assert_rejected(
                    convert_canonical_scale(
                        CanonicalDecimal("1"),
                        source_scale=source,
                        requested_output_scale=output,
                    ),
                    ScaleConversionFailureCode.UNSUPPORTED_SCALE_CONVERSION,
                )

    def test_conversion_requires_resolved_source_scale(self):
        self.assert_rejected(
            convert_canonical_scale(
                CanonicalDecimal("1"),
                source_scale=None,
                requested_output_scale=Scale.MILLION,
            ),
            ScaleConversionFailureCode.SOURCE_SCALE_REQUIRED,
        )

    def test_different_or_unknown_source_unit_is_rejected(self):
        for source_unit in ("USD", None):
            with self.subTest(source_unit=source_unit):
                self.assert_rejected(
                    convert_canonical_scale(
                        CanonicalDecimal("1"),
                        source_scale=Scale.RAW,
                        source_unit=source_unit,
                        requested_output_unit="VND",
                    ),
                    ScaleConversionFailureCode.UNSUPPORTED_UNIT_CONVERSION,
                )

    def test_conversion_is_exact_beyond_default_decimal_precision(self):
        result = convert_canonical_scale(
            CanonicalDecimal("123456789012345678901234567890.123456789"),
            source_scale=Scale.RAW,
            requested_output_scale=Scale.BILLION,
        )
        self.assert_success(
            result,
            "123456789012345678901.234567890123456789",
            Scale.BILLION,
        )
        self.assertNotIsInstance(result.canonical_value, float)

    def test_plain_string_and_float_values_are_typed_rejections(self):
        for value in ("1.5", 1.5):
            with self.subTest(value=value):
                self.assert_rejected(
                    convert_canonical_scale(  # type: ignore[arg-type]
                        value,
                        source_scale=Scale.RAW,
                    ),
                    ScaleConversionFailureCode.INVALID_INPUT,
                )

    def test_result_round_trip_has_exact_schema(self):
        result = convert_canonical_scale(
            CanonicalDecimal("2"),
            source_scale=Scale.MILLION,
            requested_output_scale=Scale.BILLION,
        )
        self.assertEqual(
            ScaleConversionResult.from_dict(result.to_dict()).to_dict(),
            result.to_dict(),
        )


if __name__ == "__main__":
    unittest.main()
