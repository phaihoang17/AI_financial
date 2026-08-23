import json
from pathlib import Path
import unittest

from src.evaluation.nlu import (
    NLUEvaluationCase,
    NLUEvaluationField,
    NLUEvaluationResult,
    NLUFieldComparison,
    evaluate_nlu_case,
)
from src.understanding.schemas import QueryUnderstanding, SchemaValidationError


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "nlu_v1.json"


def load_cases():
    with FIXTURE_PATH.open(encoding="utf-8") as fixture_file:
        return [
            NLUEvaluationCase.from_dict(item) for item in json.load(fixture_file)
        ]


def actual_from(case, **changes):
    payload = case.expected.to_dict()
    payload.update(changes)
    return QueryUnderstanding.from_dict(payload)


class NLUEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.case = load_cases()[0]

    def test_small_approved_fixture_set_is_valid(self):
        cases = load_cases()
        self.assertEqual(len(cases), 3)
        self.assertEqual(
            [case.case_id for case in cases],
            [
                "lookup-resolved",
                "growth-resolved",
                "hard-identities-missing",
            ],
        )

    def test_case_requires_only_question_equal_expected_raw_question(self):
        payload = self.case.to_dict()
        payload["question"] = "different"
        with self.assertRaises(SchemaValidationError):
            NLUEvaluationCase.from_dict(payload)

    def test_actual_raw_question_does_not_invalidate_or_affect_scoring(self):
        actual = actual_from(self.case, raw_question="rewritten by upstream")
        result = evaluate_nlu_case(self.case, actual)
        self.assertEqual(result.actual.raw_question, "rewritten by upstream")
        self.assertTrue(all(item.matches for item in result.field_comparisons))
        self.assertEqual(
            NLUEvaluationResult.from_dict(result.to_dict()).to_dict(),
            result.to_dict(),
        )

    def test_overall_confidence_is_not_scored(self):
        actual = actual_from(self.case, confidence=0.0)
        result = evaluate_nlu_case(self.case, actual)
        self.assertTrue(all(item.matches for item in result.field_comparisons))

    def test_requested_scale_and_unit_are_not_scored_in_v1(self):
        actual = actual_from(
            self.case, requested_scale="MILLION", requested_unit="VND"
        )
        result = evaluate_nlu_case(self.case, actual)
        self.assertTrue(all(item.matches for item in result.field_comparisons))

    def test_exact_approved_fields_are_compared_in_canonical_order(self):
        expected_fields = list(NLUEvaluationField)
        result = evaluate_nlu_case(self.case, self.case.expected)
        self.assertEqual(
            [comparison.field for comparison in result.field_comparisons],
            expected_fields,
        )
        self.assertEqual(len(result.field_comparisons), 7)

    def test_each_approved_field_can_fail_independently(self):
        mutations = {
            NLUEvaluationField.COMPANY: {
                "company": {
                    "raw": "AAB",
                    "name": "Other",
                    "ticker": "AAB",
                    "confidence": 1.0,
                }
            },
            NLUEvaluationField.PERIODS: {"periods": []},
            NLUEvaluationField.STATEMENT_SCOPE: {
                "statement_scope": {
                    "value": "RIENG",
                    "inferred": False,
                    "confidence": 1.0,
                }
            },
            NLUEvaluationField.METRICS: {"metrics": []},
            NLUEvaluationField.OPERATION: {"operation": "compare"},
            NLUEvaluationField.MISSING_INFORMATION: {
                "missing_information": ["period"]
            },
            NLUEvaluationField.AMBIGUITIES: {"ambiguities": ["company"]},
        }

        for changed_field, changes in mutations.items():
            with self.subTest(field=changed_field.value):
                result = evaluate_nlu_case(
                    self.case, actual_from(self.case, **changes)
                )
                failed = [
                    comparison.field
                    for comparison in result.field_comparisons
                    if not comparison.matches
                ]
                self.assertEqual(failed, [changed_field])

    def test_no_evaluator_side_normalization(self):
        payload = self.case.expected.to_dict()
        payload["metrics"][0]["raw"] = " lnst "
        result = evaluate_nlu_case(
            self.case, QueryUnderstanding.from_dict(payload)
        )
        comparison = next(
            item
            for item in result.field_comparisons
            if item.field is NLUEvaluationField.METRICS
        )
        self.assertFalse(comparison.matches)

    def test_result_rejects_missing_duplicate_reordered_or_false_comparisons(self):
        result = evaluate_nlu_case(self.case, self.case.expected)
        valid = result.field_comparisons
        invalid_lists = (
            valid[:-1],
            valid + [valid[-1]],
            [valid[1], valid[0], *valid[2:]],
            [
                NLUFieldComparison(field=valid[0].field, matches=False),
                *valid[1:],
            ],
        )
        for comparisons in invalid_lists:
            with self.subTest(comparisons=comparisons):
                with self.assertRaises(SchemaValidationError):
                    NLUEvaluationResult(
                        case=self.case,
                        actual=self.case.expected,
                        field_comparisons=comparisons,
                    )

    def test_contracts_reject_unknown_fields_and_aggregate_outputs(self):
        case_payload = self.case.to_dict()
        case_payload["score"] = 1.0
        with self.assertRaises(SchemaValidationError):
            NLUEvaluationCase.from_dict(case_payload)

        result_payload = evaluate_nlu_case(
            self.case, self.case.expected
        ).to_dict()
        result_payload["accuracy"] = 1.0
        with self.assertRaises(SchemaValidationError):
            NLUEvaluationResult.from_dict(result_payload)

    def test_invalid_evaluator_inputs_are_rejected(self):
        with self.assertRaises(SchemaValidationError):
            evaluate_nlu_case(self.case, {})
        with self.assertRaises(SchemaValidationError):
            evaluate_nlu_case({}, self.case.expected)


if __name__ == "__main__":
    unittest.main()
