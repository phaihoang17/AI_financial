from dataclasses import fields
import unittest

from src.evaluation.reasoning import (
    ReasoningEvaluationResult,
    TraceComparison,
    compare_traces,
)
from src.understanding.schemas import SchemaValidationError


CANONICAL_FIELDS = [
    "execution_correct",
    "trace_comparison",
    "answer_correct",
]


class ReasoningEvaluationResultTests(unittest.TestCase):
    def test_fields_match_canonical_contract(self):
        self.assertEqual(
            [field.name for field in fields(ReasoningEvaluationResult)],
            CANONICAL_FIELDS,
        )

    def test_correctness_outcomes_are_independent(self):
        for execution_correct in (False, True):
            for trace_comparison in TraceComparison:
                for answer_correct in (False, True):
                    with self.subTest(
                        execution_correct=execution_correct,
                        trace_comparison=trace_comparison,
                        answer_correct=answer_correct,
                    ):
                        result = ReasoningEvaluationResult(
                            execution_correct=execution_correct,
                            trace_comparison=trace_comparison,
                            answer_correct=answer_correct,
                        )
                        self.assertEqual(
                            result.to_dict(),
                            {
                                "execution_correct": execution_correct,
                                "trace_comparison": trace_comparison.value,
                                "answer_correct": answer_correct,
                            },
                        )

    def test_invalid_enum_and_boolean_values_are_rejected(self):
        invalid_enum = {
            "execution_correct": True,
            "trace_comparison": "EQUIVALENT",
            "answer_correct": True,
        }
        with self.assertRaises(SchemaValidationError):
            ReasoningEvaluationResult.from_dict(invalid_enum)

        for field_name in ("execution_correct", "answer_correct"):
            with self.subTest(field_name=field_name):
                payload = {
                    "execution_correct": True,
                    "trace_comparison": "EXACT",
                    "answer_correct": True,
                }
                payload[field_name] = 1
                with self.assertRaises(SchemaValidationError):
                    ReasoningEvaluationResult.from_dict(payload)

    def test_missing_and_unknown_fields_are_rejected(self):
        missing = {
            "execution_correct": True,
            "trace_comparison": "EXACT",
        }
        with self.assertRaises(SchemaValidationError):
            ReasoningEvaluationResult.from_dict(missing)

        unknown = {
            "execution_correct": True,
            "trace_comparison": "EXACT",
            "answer_correct": True,
            "score": 1.0,
        }
        with self.assertRaises(SchemaValidationError):
            ReasoningEvaluationResult.from_dict(unknown)

    def test_serialization_round_trip_is_deterministic(self):
        payload = {
            "execution_correct": False,
            "trace_comparison": "NORMALIZED_EQUIVALENT",
            "answer_correct": True,
        }
        result = ReasoningEvaluationResult.from_dict(payload)

        self.assertEqual(list(result.to_dict()), CANONICAL_FIELDS)
        self.assertEqual(
            ReasoningEvaluationResult.from_dict(result.to_dict()).to_dict(), payload
        )


class TraceComparisonTests(unittest.TestCase):
    def test_exact_match_does_not_call_hook(self):
        def hook(expected, actual):
            raise AssertionError("hook must not run for exact traces")

        self.assertEqual(compare_traces("a", "a", hook), TraceComparison.EXACT)

    def test_hook_can_mark_non_identical_traces_equivalent(self):
        def ignore_whitespace(expected, actual):
            return "".join(expected.split()) == "".join(actual.split())

        self.assertEqual(
            compare_traces("a + b", "a+b", ignore_whitespace),
            TraceComparison.NORMALIZED_EQUIVALENT,
        )

    def test_non_equivalent_traces_are_different(self):
        self.assertEqual(compare_traces("a", "b"), TraceComparison.DIFFERENT)
        self.assertEqual(
            compare_traces("a", "b", lambda expected, actual: False),
            TraceComparison.DIFFERENT,
        )

    def test_invalid_hook_and_hook_result_are_rejected(self):
        with self.assertRaises(SchemaValidationError):
            compare_traces("a", "b", "not callable")
        with self.assertRaises(SchemaValidationError):
            compare_traces("a", "b", lambda expected, actual: 1)


if __name__ == "__main__":
    unittest.main()
