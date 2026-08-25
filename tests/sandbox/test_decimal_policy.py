from decimal import (
    Decimal,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
    Rounded,
    localcontext,
)
import unittest

from src.evidence.schemas import CanonicalDecimal
from src.sandbox.decimal_policy import (
    EXECUTION_DECIMAL_PRECISION,
    EXECUTION_DECIMAL_ROUNDING,
    make_execution_decimal_context,
    serialize_execution_decimal,
)
from src.understanding.schemas import SchemaValidationError


class ExecutionDecimalPolicyTests(unittest.TestCase):
    def test_context_has_approved_precision_rounding_and_traps(self):
        context = make_execution_decimal_context()
        self.assertEqual(context.prec, 50)
        self.assertEqual(context.prec, EXECUTION_DECIMAL_PRECISION)
        self.assertEqual(context.rounding, EXECUTION_DECIMAL_ROUNDING)
        self.assertTrue(context.traps[DivisionByZero])
        self.assertTrue(context.traps[InvalidOperation])
        self.assertTrue(context.traps[Overflow])
        self.assertFalse(context.traps[Inexact])
        self.assertFalse(context.traps[Rounded])

        with self.assertRaises(DivisionByZero):
            context.divide(Decimal("1"), Decimal("0"))

    def test_repeating_division_uses_fifty_significant_digits(self):
        with localcontext(make_execution_decimal_context()) as context:
            value = Decimal("1") / Decimal("3")
            self.assertTrue(context.flags[Inexact])
            self.assertTrue(context.flags[Rounded])
        serialized = serialize_execution_decimal(value)
        self.assertEqual(serialized, "0." + "3" * 50)
        self.assertIsInstance(serialized, CanonicalDecimal)

    def test_canonical_fixed_point_serialization(self):
        cases = (
            ("1E+3", "1000"),
            ("1.23000", "1.23"),
            ("-0.000", "0"),
            ("0E-50", "0"),
            ("-12.500", "-12.5"),
        )
        for value, expected in cases:
            with self.subTest(value=value):
                result = serialize_execution_decimal(Decimal(value))
                self.assertEqual(result, expected)
                self.assertNotIn("E", result.upper())

    def test_float_and_non_finite_values_are_rejected(self):
        for value in (1.5, Decimal("NaN"), Decimal("Infinity")):
            with self.subTest(value=value):
                with self.assertRaises(SchemaValidationError):
                    serialize_execution_decimal(value)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
