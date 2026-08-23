import copy
from dataclasses import fields
import unittest

from src.evaluation.harness import (
    EvaluationFailureStage,
    EvaluationMode,
    EvaluationResult,
    oracle_evidence_path,
    retrieved_evidence_e2e_path,
    stage_level_path,
)
from src.understanding.schemas import SchemaValidationError


CANONICAL_FIELDS = ["mode", "passed", "failure_stage", "failure_reason"]
MODES = [
    "NLU",
    "SUPERVISOR",
    "RETRIEVAL",
    "ORACLE_EVIDENCE",
    "RETRIEVED_EVIDENCE_E2E",
]
FAILURE_STAGES = [
    "NLU",
    "SUPERVISOR",
    "RETRIEVAL",
    "EVIDENCE",
    "PROGRAMMER",
    "SANDBOX",
    "VERIFICATION",
]


def passed_result(mode="NLU"):
    return {
        "mode": mode,
        "passed": True,
        "failure_stage": None,
        "failure_reason": None,
    }


class EvaluationResultTests(unittest.TestCase):
    def test_fields_match_canonical_contract(self):
        self.assertEqual(
            [field.name for field in fields(EvaluationResult)], CANONICAL_FIELDS
        )

    def test_exact_modes_are_supported(self):
        self.assertEqual([mode.value for mode in EvaluationMode], MODES)
        for mode in MODES:
            with self.subTest(mode=mode):
                payload = passed_result(mode)
                self.assertEqual(EvaluationResult.from_dict(payload).to_dict(), payload)

    def test_exact_failure_stages_are_supported(self):
        self.assertEqual(
            [stage.value for stage in EvaluationFailureStage], FAILURE_STAGES
        )
        for stage in FAILURE_STAGES:
            with self.subTest(stage=stage):
                payload = {
                    "mode": "RETRIEVED_EVIDENCE_E2E",
                    "passed": False,
                    "failure_stage": stage,
                    "failure_reason": "evaluation failed",
                }
                self.assertEqual(EvaluationResult.from_dict(payload).to_dict(), payload)

    def test_invalid_enums_are_rejected(self):
        invalid_mode = passed_result("E2E")
        with self.assertRaises(SchemaValidationError):
            EvaluationResult.from_dict(invalid_mode)

        invalid_stage = {
            "mode": "NLU",
            "passed": False,
            "failure_stage": "EXECUTION",
            "failure_reason": "invalid stage",
        }
        with self.assertRaises(SchemaValidationError):
            EvaluationResult.from_dict(invalid_stage)

    def test_passed_result_requires_null_failure_fields(self):
        for stage, reason in (("NLU", None), (None, "failure")):
            with self.subTest(stage=stage, reason=reason):
                payload = passed_result()
                payload["failure_stage"] = stage
                payload["failure_reason"] = reason
                with self.assertRaises(SchemaValidationError):
                    EvaluationResult.from_dict(payload)

    def test_failed_result_requires_stage_and_non_empty_reason(self):
        invalid_values = [
            (None, "failure"),
            ("NLU", None),
            ("NLU", ""),
            ("NLU", "   "),
        ]
        for stage, reason in invalid_values:
            with self.subTest(stage=stage, reason=reason):
                payload = {
                    "mode": "NLU",
                    "passed": False,
                    "failure_stage": stage,
                    "failure_reason": reason,
                }
                with self.assertRaises(SchemaValidationError):
                    EvaluationResult.from_dict(payload)

    def test_passed_must_be_boolean(self):
        payload = passed_result()
        payload["passed"] = 1
        with self.assertRaises(SchemaValidationError):
            EvaluationResult.from_dict(payload)

    def test_missing_and_unknown_fields_are_rejected(self):
        missing = passed_result()
        del missing["failure_reason"]
        with self.assertRaises(SchemaValidationError):
            EvaluationResult.from_dict(missing)

        unknown = passed_result()
        unknown["metric"] = 1.0
        with self.assertRaises(SchemaValidationError):
            EvaluationResult.from_dict(unknown)

    def test_serialization_is_deterministic(self):
        payload = passed_result("SUPERVISOR")
        first = EvaluationResult.from_dict(copy.deepcopy(payload))
        second = EvaluationResult.from_dict(first.to_dict())

        self.assertEqual(list(first.to_dict()), CANONICAL_FIELDS)
        self.assertEqual(second.to_dict(), payload)


class EvaluationPathTests(unittest.TestCase):
    def test_stage_level_path_accepts_only_stage_modes(self):
        for mode in ("NLU", "SUPERVISOR", "RETRIEVAL"):
            with self.subTest(mode=mode):
                result = EvaluationResult.from_dict(passed_result(mode))
                self.assertIs(stage_level_path(result), result)

        for mode in ("ORACLE_EVIDENCE", "RETRIEVED_EVIDENCE_E2E"):
            with self.subTest(mode=mode):
                result = EvaluationResult.from_dict(passed_result(mode))
                with self.assertRaises(SchemaValidationError):
                    stage_level_path(result)

    def test_oracle_and_retrieved_evidence_have_separate_paths(self):
        oracle = EvaluationResult.from_dict(passed_result("ORACLE_EVIDENCE"))
        retrieved = EvaluationResult.from_dict(
            passed_result("RETRIEVED_EVIDENCE_E2E")
        )

        self.assertIs(oracle_evidence_path(oracle), oracle)
        self.assertIs(retrieved_evidence_e2e_path(retrieved), retrieved)
        with self.assertRaises(SchemaValidationError):
            oracle_evidence_path(retrieved)
        with self.assertRaises(SchemaValidationError):
            retrieved_evidence_e2e_path(oracle)

    def test_paths_require_evaluation_results(self):
        for path in (
            stage_level_path,
            oracle_evidence_path,
            retrieved_evidence_e2e_path,
        ):
            with self.subTest(path=path.__name__):
                with self.assertRaises(SchemaValidationError):
                    path(passed_result())


if __name__ == "__main__":
    unittest.main()
