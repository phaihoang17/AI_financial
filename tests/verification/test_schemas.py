import copy
from dataclasses import fields
import unittest

from src.understanding.schemas import SchemaValidationError
from src.verification.schemas import VerificationResult


CANONICAL_FIELDS = ["passed", "failure_category", "failure_reason"]


class VerificationResultSchemaTests(unittest.TestCase):
    def test_field_names_match_canonical_contract(self):
        self.assertEqual(
            [field.name for field in fields(VerificationResult)], CANONICAL_FIELDS
        )

    def test_passed_result_requires_null_failure_fields(self):
        payload = {
            "passed": True,
            "failure_category": None,
            "failure_reason": None,
        }

        result = VerificationResult.from_dict(payload)

        self.assertEqual(result.to_dict(), payload)

    def test_each_failure_category_is_valid(self):
        categories = [
            "GROUNDING",
            "INSUFFICIENT_EVIDENCE",
            "NUMERIC",
            "SCALE_UNIT",
            "FINANCIAL_LOGIC",
        ]
        for category in categories:
            with self.subTest(category=category):
                payload = {
                    "passed": False,
                    "failure_category": category,
                    "failure_reason": "verification failed",
                }
                result = VerificationResult.from_dict(payload)
                self.assertEqual(result.to_dict(), payload)

    def test_invalid_failure_category_is_rejected(self):
        payload = {
            "passed": False,
            "failure_category": "EXECUTION",
            "failure_reason": "runtime failure",
        }

        with self.assertRaises(SchemaValidationError):
            VerificationResult.from_dict(payload)

    def test_passed_result_rejects_failure_category(self):
        payload = {
            "passed": True,
            "failure_category": "GROUNDING",
            "failure_reason": None,
        }

        with self.assertRaises(SchemaValidationError):
            VerificationResult.from_dict(payload)

    def test_passed_result_rejects_failure_reason(self):
        payload = {
            "passed": True,
            "failure_category": None,
            "failure_reason": "unexpected reason",
        }

        with self.assertRaises(SchemaValidationError):
            VerificationResult.from_dict(payload)

    def test_failed_result_requires_category(self):
        payload = {
            "passed": False,
            "failure_category": None,
            "failure_reason": "evidence missing",
        }

        with self.assertRaises(SchemaValidationError):
            VerificationResult.from_dict(payload)

    def test_failed_result_requires_non_empty_reason(self):
        for reason in (None, "", "   "):
            with self.subTest(reason=reason):
                payload = {
                    "passed": False,
                    "failure_category": "GROUNDING",
                    "failure_reason": reason,
                }
                with self.assertRaises(SchemaValidationError):
                    VerificationResult.from_dict(payload)

    def test_invalid_passed_type_is_rejected(self):
        payload = {
            "passed": 1,
            "failure_category": None,
            "failure_reason": None,
        }

        with self.assertRaises(SchemaValidationError):
            VerificationResult.from_dict(payload)

    def test_missing_and_unknown_fields_are_rejected(self):
        missing = {
            "passed": True,
            "failure_category": None,
        }
        with self.assertRaises(SchemaValidationError):
            VerificationResult.from_dict(missing)

        unknown = {
            "passed": True,
            "failure_category": None,
            "failure_reason": None,
            "extra": "not allowed",
        }
        with self.assertRaises(SchemaValidationError):
            VerificationResult.from_dict(unknown)

    def test_serialization_round_trip(self):
        payload = {
            "passed": False,
            "failure_category": "SCALE_UNIT",
            "failure_reason": "unit provenance is missing",
        }

        first = VerificationResult.from_dict(copy.deepcopy(payload))
        second = VerificationResult.from_dict(first.to_dict())

        self.assertEqual(second.to_dict(), payload)


if __name__ == "__main__":
    unittest.main()
