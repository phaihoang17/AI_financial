import copy
from dataclasses import fields
import math
import unittest

from src.sandbox.schemas import ExecutionResult
from src.understanding.schemas import SchemaValidationError


CANONICAL_FIELDS = [
    "success",
    "result",
    "error_type",
    "error_message",
    "execution_ms",
]


def successful_result():
    return {
        "success": True,
        "result": 12.5,
        "error_type": None,
        "error_message": None,
        "execution_ms": 14,
    }


class ExecutionResultSchemaTests(unittest.TestCase):
    def test_field_names_match_canonical_contract(self):
        self.assertEqual([field.name for field in fields(ExecutionResult)], CANONICAL_FIELDS)

    def test_successful_numeric_string_and_null_results(self):
        for result in (12.5, 12, "12.5", None):
            with self.subTest(result=result):
                payload = successful_result()
                payload["result"] = result
                execution = ExecutionResult.from_dict(payload)
                self.assertEqual(execution.to_dict(), payload)

    def test_failed_execution_with_error_details(self):
        payload = {
            "success": False,
            "result": None,
            "error_type": "SYNTAX_ERROR",
            "error_message": "invalid syntax",
            "execution_ms": 3,
        }

        execution = ExecutionResult.from_dict(payload)

        self.assertEqual(execution.to_dict(), payload)

    def test_boolean_result_is_rejected(self):
        payload = successful_result()
        payload["result"] = True

        with self.assertRaises(SchemaValidationError):
            ExecutionResult.from_dict(payload)

    def test_non_finite_numeric_results_are_rejected(self):
        for result in (math.nan, math.inf, -math.inf):
            with self.subTest(result=result):
                payload = successful_result()
                payload["result"] = result
                with self.assertRaises(SchemaValidationError):
                    ExecutionResult.from_dict(payload)

    def test_execution_ms_must_be_non_negative_integer(self):
        for execution_ms in (-1, 1.5, True, "14"):
            with self.subTest(execution_ms=execution_ms):
                payload = successful_result()
                payload["execution_ms"] = execution_ms
                with self.assertRaises(SchemaValidationError):
                    ExecutionResult.from_dict(payload)

    def test_invalid_success_type_is_rejected(self):
        payload = successful_result()
        payload["success"] = 1

        with self.assertRaises(SchemaValidationError):
            ExecutionResult.from_dict(payload)

    def test_missing_and_unknown_fields_are_rejected(self):
        missing = successful_result()
        del missing["result"]
        with self.assertRaises(SchemaValidationError):
            ExecutionResult.from_dict(missing)

        unknown = successful_result()
        unknown["extra"] = "not allowed"
        with self.assertRaises(SchemaValidationError):
            ExecutionResult.from_dict(unknown)

    def test_serialization_round_trip(self):
        payload = successful_result()

        first = ExecutionResult.from_dict(copy.deepcopy(payload))
        second = ExecutionResult.from_dict(first.to_dict())

        self.assertEqual(second.to_dict(), payload)


if __name__ == "__main__":
    unittest.main()
