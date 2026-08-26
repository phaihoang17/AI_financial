import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from src.evaluation.oracle_reasoning import (
    ORACLE_REASONING_EVALUATION_SCHEMA_VERSION,
    OracleReasoningFailureStage,
    _execution_failure_stage,
    _generation_failure_stage,
    evaluate_oracle_reasoning_case,
    evaluate_oracle_reasoning_cases,
    outputs_equal,
)
from src.evaluation.oracle_reasoning_fixtures import (
    oracle_reasoning_fixture_cases,
)
from src.evaluation.reasoning import TraceComparison
from src.evaluation.run_reasoning_eval import main
from src.evidence.schemas import CanonicalDecimal, Scale
from src.evidence.value_binder import ValueBindingError, ValueBindingFailureCode
from src.programmer.validator import ProgramValidationFailureCode
from src.programmer.schemas import ProgramOutputKind
from src.sandbox.schemas import (
    ExecutionDatum,
    ExecutionFailureStage,
    ExecutionOutput,
)


class OracleReasoningEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.cases = oracle_reasoning_fixture_cases()

    def test_fixture_set_covers_required_reasoning_cases(self):
        self.assertEqual(
            [case.case_id for case in self.cases],
            [
                "LOOKUP",
                "MULTI_PERIOD_COMPARE",
                "GROWTH_RATE",
                "AVERAGE",
                "UNSUPPORTED_RATIO",
                "DIVISION_BY_ZERO",
                "SCALE_CONVERSION",
            ],
        )

    def test_real_m5_m6_m7_pipeline_passes_all_oracle_fixtures(self):
        report = evaluate_oracle_reasoning_cases(self.cases)

        self.assertEqual(report.case_count, 7)
        self.assertEqual(report.passed_count, 7)
        self.assertEqual(report.failed_count, 0)
        self.assertEqual(report.execution_accuracy, 1.0)
        self.assertEqual(report.trace_accuracy, 1.0)
        self.assertEqual(report.answer_accuracy, 1.0)
        payload = report.to_dict()
        self.assertEqual(
            payload["schema_version"],
            ORACLE_REASONING_EVALUATION_SCHEMA_VERSION,
        )
        self.assertEqual(
            payload["trace_distribution"],
            {"EXACT": 6, "NORMALIZED_EQUIVALENT": 1, "DIFFERENT": 0},
        )
        self.assertEqual(
            payload["failure_distribution"],
            {
                "NONE": 5,
                "PROGRAM_GENERATION": 1,
                "VALIDATION": 0,
                "BINDING": 0,
                "CONVERSION": 0,
                "ARITHMETIC": 1,
                "SANDBOX_INFRASTRUCTURE": 0,
            },
        )

    def test_expected_failures_are_typed_without_emitting_answers(self):
        report = evaluate_oracle_reasoning_cases(self.cases)
        by_id = {item.case_id: item for item in report.case_results}

        ratio = by_id["UNSUPPORTED_RATIO"]
        self.assertIs(
            ratio.failure_stage,
            OracleReasoningFailureStage.PROGRAM_GENERATION,
        )
        self.assertEqual(ratio.failure_code, "UNSUPPORTED_DERIVED_RATIO")
        self.assertIsNone(ratio.actual_output)
        self.assertIs(ratio.reasoning_result.trace_comparison, TraceComparison.EXACT)

        division = by_id["DIVISION_BY_ZERO"]
        self.assertIs(
            division.failure_stage, OracleReasoningFailureStage.ARITHMETIC
        )
        self.assertEqual(division.failure_code, "DIVISION_BY_ZERO")
        self.assertIsNone(division.actual_output)

    def test_exact_output_comparison_preserves_decimal_order_scale_and_unit(self):
        expected = ExecutionOutput(
            ProgramOutputKind.ORDERED_VALUES,
            [
                ExecutionDatum(CanonicalDecimal("1"), Scale.MILLION, "VND"),
                ExecutionDatum(CanonicalDecimal("2.0"), Scale.MILLION, "VND"),
            ],
        )
        exact = ExecutionOutput.from_dict(expected.to_dict())
        reversed_values = ExecutionOutput(
            ProgramOutputKind.ORDERED_VALUES, list(reversed(exact.values))
        )
        different_decimal = ExecutionOutput(
            ProgramOutputKind.ORDERED_VALUES,
            [
                ExecutionDatum(CanonicalDecimal("1"), Scale.MILLION, "VND"),
                ExecutionDatum(CanonicalDecimal("2"), Scale.MILLION, "VND"),
            ],
        )
        different_scale = ExecutionOutput(
            ProgramOutputKind.ORDERED_VALUES,
            [
                ExecutionDatum(CanonicalDecimal("1"), Scale.MILLION, "VND"),
                ExecutionDatum(CanonicalDecimal("2.0"), Scale.BILLION, "VND"),
            ],
        )
        different_unit = ExecutionOutput(
            ProgramOutputKind.ORDERED_VALUES,
            [
                ExecutionDatum(CanonicalDecimal("1"), Scale.MILLION, "VND"),
                ExecutionDatum(CanonicalDecimal("2.0"), Scale.MILLION, "USD"),
            ],
        )

        self.assertTrue(outputs_equal(expected, exact))
        for actual in (
            reversed_values,
            different_decimal,
            different_scale,
            different_unit,
        ):
            with self.subTest(output=actual.to_dict()):
                self.assertFalse(outputs_equal(expected, actual))

    def test_all_execution_failure_stages_have_distinct_attribution(self):
        expected = {
            ExecutionFailureStage.POLICY: OracleReasoningFailureStage.VALIDATION,
            ExecutionFailureStage.VALIDATION: OracleReasoningFailureStage.VALIDATION,
            ExecutionFailureStage.BINDING: OracleReasoningFailureStage.BINDING,
            ExecutionFailureStage.CONVERSION: OracleReasoningFailureStage.CONVERSION,
            ExecutionFailureStage.ARITHMETIC: OracleReasoningFailureStage.ARITHMETIC,
            ExecutionFailureStage.RESOURCE: OracleReasoningFailureStage.SANDBOX_INFRASTRUCTURE,
            ExecutionFailureStage.SECURITY: OracleReasoningFailureStage.SANDBOX_INFRASTRUCTURE,
            ExecutionFailureStage.INFRASTRUCTURE: OracleReasoningFailureStage.SANDBOX_INFRASTRUCTURE,
        }
        self.assertEqual(
            {stage: _execution_failure_stage(stage) for stage in expected}, expected
        )
        self.assertIs(
            _generation_failure_stage(
                ProgramValidationFailureCode.INVALID_SCHEMA.value
            ),
            OracleReasoningFailureStage.VALIDATION,
        )

    def test_m5_binding_failure_is_attributed_before_generation(self):
        error = ValueBindingError(
            ValueBindingFailureCode.EVIDENCE_MISSING, "oracle-evidence"
        )
        with patch(
            "src.evaluation.oracle_reasoning.build_binding_map",
            side_effect=error,
        ), patch(
            "src.evaluation.oracle_reasoning.generate_program"
        ) as generator:
            result = evaluate_oracle_reasoning_case(self.cases[0])
        self.assertIs(result.failure_stage, OracleReasoningFailureStage.BINDING)
        self.assertEqual(result.failure_code, "EVIDENCE_MISSING")
        generator.assert_not_called()

    def test_retrieval_entry_points_are_not_invoked(self):
        with patch(
            "src.retrieval.evidence.locate_cells",
            side_effect=AssertionError("retrieval must not run"),
        ), patch(
            "src.retrieval.evidence.build_evidence_items",
            side_effect=AssertionError("retrieval must not run"),
        ):
            report = evaluate_oracle_reasoning_cases(self.cases)
        self.assertEqual(report.failed_count, 0)

    def test_report_and_cli_are_deterministic(self):
        first = evaluate_oracle_reasoning_cases(self.cases).to_dict()
        second = evaluate_oracle_reasoning_cases(
            oracle_reasoning_fixture_cases()
        ).to_dict()
        self.assertEqual(first, second)

        streams = []
        for _ in range(2):
            stream = io.StringIO()
            with redirect_stdout(stream):
                self.assertEqual(main(["--mode", "oracle"]), 0)
            streams.append(stream.getvalue())
        self.assertEqual(streams[0], streams[1])
        payload = json.loads(streams[0])
        self.assertEqual(payload["mode"], "ORACLE")
        self.assertEqual(payload["passed_count"], 7)


if __name__ == "__main__":
    unittest.main()
