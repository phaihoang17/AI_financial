from dataclasses import fields
import unittest

from src.evaluation.harness import EvaluationResult
from src.evaluation.slices import (
    EvaluationEvidenceSource,
    EvaluationSlice,
    ReasoningDepth,
    SlicedEvaluationResult,
)
from src.supervisor.schemas import EvidenceSource
from src.understanding.schemas import SchemaValidationError


SLICE_FIELDS = ["evidence_source", "reasoning_depth", "scale_unit_sensitive"]
SLICED_RESULT_FIELDS = ["result", "slice"]


def slice_payload():
    return {
        "evidence_source": "TABLE",
        "reasoning_depth": "ONE_STEP",
        "scale_unit_sensitive": False,
    }


def sliced_result_payload():
    return {
        "result": {
            "mode": "ORACLE_EVIDENCE",
            "passed": True,
            "failure_stage": None,
            "failure_reason": None,
        },
        "slice": slice_payload(),
    }


class EvaluationSliceTests(unittest.TestCase):
    def test_fields_match_canonical_contracts(self):
        self.assertEqual(
            [field.name for field in fields(EvaluationSlice)], SLICE_FIELDS
        )
        self.assertEqual(
            [field.name for field in fields(SlicedEvaluationResult)],
            SLICED_RESULT_FIELDS,
        )

    def test_all_evidence_source_slices_are_supported(self):
        expected = ["TABLE", "TEXT", "HYBRID"]
        self.assertEqual(
            [source.value for source in EvaluationEvidenceSource], expected
        )
        for source in expected:
            with self.subTest(source=source):
                payload = slice_payload()
                payload["evidence_source"] = source
                self.assertEqual(EvaluationSlice.from_dict(payload).to_dict(), payload)

    def test_all_reasoning_depth_slices_are_supported(self):
        expected = ["ONE_STEP", "TWO_STEP", "THREE_PLUS_STEPS"]
        self.assertEqual([depth.value for depth in ReasoningDepth], expected)
        for depth in expected:
            with self.subTest(depth=depth):
                payload = slice_payload()
                payload["reasoning_depth"] = depth
                self.assertEqual(EvaluationSlice.from_dict(payload).to_dict(), payload)

    def test_scale_unit_sensitive_is_strict_boolean(self):
        for value in (False, True):
            payload = slice_payload()
            payload["scale_unit_sensitive"] = value
            self.assertEqual(EvaluationSlice.from_dict(payload).to_dict(), payload)

        payload = slice_payload()
        payload["scale_unit_sensitive"] = 1
        with self.assertRaises(SchemaValidationError):
            EvaluationSlice.from_dict(payload)

    def test_invalid_enums_are_rejected(self):
        invalid_source = slice_payload()
        invalid_source["evidence_source"] = "BOTH"
        with self.assertRaises(SchemaValidationError):
            EvaluationSlice.from_dict(invalid_source)

        invalid_depth = slice_payload()
        invalid_depth["reasoning_depth"] = "FOUR_STEPS"
        with self.assertRaises(SchemaValidationError):
            EvaluationSlice.from_dict(invalid_depth)

    def test_missing_and_unknown_fields_are_rejected(self):
        missing = slice_payload()
        del missing["reasoning_depth"]
        with self.assertRaises(SchemaValidationError):
            EvaluationSlice.from_dict(missing)

        unknown = slice_payload()
        unknown["case_id"] = "case-1"
        with self.assertRaises(SchemaValidationError):
            EvaluationSlice.from_dict(unknown)

    def test_hybrid_is_evaluation_only(self):
        self.assertEqual(EvaluationEvidenceSource.HYBRID.value, "HYBRID")
        self.assertEqual([source.value for source in EvidenceSource], ["TABLE", "TEXT"])


class SlicedEvaluationResultTests(unittest.TestCase):
    def test_slice_attaches_without_changing_evaluation_result(self):
        payload = sliced_result_payload()
        sliced = SlicedEvaluationResult.from_dict(payload)

        self.assertIsInstance(sliced.result, EvaluationResult)
        self.assertEqual(sliced.to_dict(), payload)
        self.assertEqual(
            [field.name for field in fields(EvaluationResult)],
            ["mode", "passed", "failure_stage", "failure_reason"],
        )

    def test_invalid_nested_contracts_are_rejected(self):
        invalid_result = sliced_result_payload()
        invalid_result["result"]["metric"] = 1.0
        with self.assertRaises(SchemaValidationError):
            SlicedEvaluationResult.from_dict(invalid_result)

        invalid_slice = sliced_result_payload()
        invalid_slice["slice"]["evidence_source"] = "BOTH"
        with self.assertRaises(SchemaValidationError):
            SlicedEvaluationResult.from_dict(invalid_slice)

    def test_missing_and_unknown_fields_are_rejected(self):
        missing = sliced_result_payload()
        del missing["slice"]
        with self.assertRaises(SchemaValidationError):
            SlicedEvaluationResult.from_dict(missing)

        unknown = sliced_result_payload()
        unknown["aggregate"] = {}
        with self.assertRaises(SchemaValidationError):
            SlicedEvaluationResult.from_dict(unknown)

    def test_serialization_round_trip_is_deterministic(self):
        payload = sliced_result_payload()
        sliced = SlicedEvaluationResult.from_dict(payload)

        self.assertEqual(list(sliced.to_dict()), SLICED_RESULT_FIELDS)
        self.assertEqual(
            SlicedEvaluationResult.from_dict(sliced.to_dict()).to_dict(), payload
        )


if __name__ == "__main__":
    unittest.main()
