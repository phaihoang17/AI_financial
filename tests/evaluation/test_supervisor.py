import json
import subprocess
import sys
import unittest

from src.evaluation.supervisor import (
    ExpectedRetrievalRequirement,
    ExpectedSupervisorResult,
    SupervisorEvaluationCase,
    SupervisorEvaluationReport,
    SupervisorField,
    SupervisorFieldComparison,
    compare_supervisor_case,
    evaluate_supervisor_case,
    evaluate_supervisor_cases,
)
from src.evaluation.supervisor_fixtures import supervisor_fixture_cases
from src.supervisor.formula_registry import FORMULA_REGISTRY, get_formula
from src.supervisor.planner import SupervisorPlanningError, validate_formula_id
from src.supervisor.schemas import (
    SupervisorAbstainReason,
    SupervisorResult,
)
from src.supervisor.table_registry import table_for_metric
from src.understanding.schemas import SchemaValidationError


def case_by_id(case_id):
    return next(case for case in supervisor_fixture_cases() if case.case_id == case_id)


class SupervisorEvaluationContractTests(unittest.TestCase):
    def test_fixture_coverage_and_stable_case_count(self):
        cases = supervisor_fixture_cases()
        self.assertEqual(len(cases), 14)
        self.assertEqual(
            [case.case_id for case in cases],
            [
                "simple-lnst-lookup",
                "single-period-quarter-lookup",
                "multi-period-compare",
                "growth-rate",
                "average",
                "growth-wrong-period-count",
                "aggregate-wrong-period-count",
                "blocked-planning-gate",
                "unknown-operation",
                "unsupported-ratio",
                "missing-statement-scope",
                "missing-table-mapping",
                "mixed-year-quarter-periods",
                "mixed-quarter-cumulative-periods",
            ],
        )

    def test_case_and_case_result_round_trip(self):
        for case in supervisor_fixture_cases():
            with self.subTest(case=case.case_id):
                self.assertEqual(
                    SupervisorEvaluationCase.from_dict(case.to_dict()).to_dict(),
                    case.to_dict(),
                )
                result = evaluate_supervisor_case(case)
                self.assertEqual(
                    type(result).from_dict(result.to_dict()).to_dict(),
                    result.to_dict(),
                )

    def test_expected_requirement_omits_and_rejects_requirement_id(self):
        requirement = case_by_id(
            "simple-lnst-lookup"
        ).expected.retrieval_requirements[0]
        self.assertNotIn("requirement_id", requirement.to_dict())
        payload = requirement.to_dict()
        payload["requirement_id"] = "ignored-is-not-accepted-in-expectations"
        with self.assertRaises(SchemaValidationError):
            ExpectedRetrievalRequirement.from_dict(payload)

    def test_field_order_is_canonical_and_exact(self):
        result = evaluate_supervisor_case(case_by_id("growth-rate"))
        self.assertEqual(
            [item.field for item in result.field_comparisons],
            list(SupervisorField),
        )
        self.assertEqual(len(result.field_comparisons), 12)

    def test_ordered_lists_are_order_sensitive(self):
        original = case_by_id("multi-period-compare")
        payload = original.to_dict()
        payload["expected"]["periods"] = ["2024", "2023"]
        changed = SupervisorEvaluationCase.from_dict(payload)
        result = compare_supervisor_case(
            changed, evaluate_supervisor_case(original).actual
        )
        failed = [item.field for item in result.field_comparisons if not item.matches]
        self.assertEqual(failed, [SupervisorField.PERIODS])

    def test_each_plan_field_can_fail_independently(self):
        original = case_by_id("simple-lnst-lookup")
        actual = evaluate_supervisor_case(original).actual
        mutations = {
            SupervisorField.QUESTION_TYPE: {"question_type": "MULTI_PERIOD"},
            SupervisorField.REASONING_MODE: {"reasoning_mode": "PROGRAM"},
            SupervisorField.FORMULA_ID: {"formula_id": "NOT_REGISTERED"},
            SupervisorField.TARGET_METRICS: {"target_metrics": ["lnst"]},
            SupervisorField.PERIODS: {"periods": ["2025"]},
            SupervisorField.TABLES_NEEDED: {"tables_needed": ["NOTES"]},
            SupervisorField.EVIDENCE_SOURCES: {"evidence_sources": ["TEXT"]},
            SupervisorField.RETRIEVAL_REQUIREMENTS: {
                "retrieval_requirements": [
                    {
                        "source_type": "TABLE",
                        "table_class": "INCOME_STATEMENT",
                        "metric": "LNST",
                        "period": "2025",
                        "required": True,
                    }
                ]
            },
            SupervisorField.REQUIRES_SCALE_RESOLUTION: {
                "requires_scale_resolution": False
            },
            SupervisorField.MODEL_TIER: {"model_tier": "STRONG"},
            SupervisorField.VERIFY_PROFILE: {"verify_profile": "STRICT"},
        }
        for field, mutation in mutations.items():
            with self.subTest(field=field.value):
                payload = original.to_dict()
                payload["expected"].update(mutation)
                changed = SupervisorEvaluationCase.from_dict(payload)
                result = compare_supervisor_case(changed, actual)
                failed = [
                    item.field
                    for item in result.field_comparisons
                    if not item.matches
                ]
                self.assertEqual(failed, [field])

    def test_no_normalization_or_synonym_expansion(self):
        original = case_by_id("simple-lnst-lookup")
        payload = original.to_dict()
        payload["expected"]["target_metrics"] = ["lnst"]
        changed = SupervisorEvaluationCase.from_dict(payload)
        result = compare_supervisor_case(
            changed, evaluate_supervisor_case(original).actual
        )
        comparison = next(
            item
            for item in result.field_comparisons
            if item.field is SupervisorField.TARGET_METRICS
        )
        self.assertFalse(comparison.matches)

    def test_abstention_requires_exact_reason_and_null_plan(self):
        case = case_by_id("blocked-planning-gate")
        actual = evaluate_supervisor_case(case).actual
        wrong_reason = SupervisorResult(
            case.planning_gate,
            None,
            True,
            SupervisorAbstainReason.INVALID_PLAN,
            actual.input_fingerprint,
        )
        result = compare_supervisor_case(case, wrong_reason)
        failed = [item.field for item in result.field_comparisons if not item.matches]
        self.assertEqual(failed, [SupervisorField.ABSTAIN])

    def test_result_rejects_tampered_comparisons_or_passed_flag(self):
        result = evaluate_supervisor_case(case_by_id("simple-lnst-lookup"))
        comparisons = list(result.field_comparisons)
        comparisons[0] = SupervisorFieldComparison(
            SupervisorField.ABSTAIN, False
        )
        with self.assertRaises(SchemaValidationError):
            type(result)(result.case, result.actual, comparisons, False)
        with self.assertRaises(SchemaValidationError):
            type(result)(
                result.case, result.actual, result.field_comparisons, False
            )

    def test_contracts_reject_unknown_fields_and_invalid_versions(self):
        payload = case_by_id("simple-lnst-lookup").to_dict()
        payload["threshold"] = 0.9
        with self.assertRaises(SchemaValidationError):
            SupervisorEvaluationCase.from_dict(payload)
        for version in (0, -1, 1.5, True):
            with self.subTest(version=version):
                invalid = case_by_id("simple-lnst-lookup").to_dict()
                invalid["version"] = version
                with self.assertRaises(SchemaValidationError):
                    SupervisorEvaluationCase.from_dict(invalid)


class SupervisorEvaluationMetricsTests(unittest.TestCase):
    def test_fixture_metrics_are_exact(self):
        report = evaluate_supervisor_cases(supervisor_fixture_cases())
        self.assertEqual(
            (report.case_count, report.passed_count, report.failed_count),
            (14, 14, 0),
        )
        self.assertEqual(report.exact_match_rate, 1.0)
        self.assertEqual(
            report.per_field_accuracy,
            {field.value: 1.0 for field in SupervisorField},
        )
        self.assertEqual(report.abstain_precision, 1.0)
        self.assertEqual(report.abstain_recall, 1.0)
        self.assertNotIn("threshold", report.to_dict())

    def test_abstain_precision_and_recall_formulas(self):
        positive = case_by_id("blocked-planning-gate")
        true_positive = evaluate_supervisor_case(positive)

        negative = case_by_id("simple-lnst-lookup")
        valid_negative = evaluate_supervisor_case(negative)
        false_positive_actual = SupervisorResult(
            negative.planning_gate,
            None,
            True,
            SupervisorAbstainReason.INVALID_PLAN,
            valid_negative.actual.input_fingerprint,
        )
        false_positive = compare_supervisor_case(negative, false_positive_actual)

        false_negative_payload = negative.to_dict()
        false_negative_payload["case_id"] = "synthetic-false-negative"
        false_negative_payload["expected"] = ExpectedSupervisorResult.abstention(
            SupervisorAbstainReason.INVALID_PLAN
        ).to_dict()
        false_negative_case = SupervisorEvaluationCase.from_dict(
            false_negative_payload
        )
        false_negative = compare_supervisor_case(
            false_negative_case, valid_negative.actual
        )

        report = SupervisorEvaluationReport(
            (true_positive, false_positive, false_negative)
        )
        self.assertEqual(report.abstain_precision, 0.5)
        self.assertEqual(report.abstain_recall, 0.5)

    def test_cli_fixture_report(self):
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "src.evaluation.run_supervisor_eval",
                "--mode",
                "fixture",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["mode"], "FIXTURE")
        self.assertEqual(payload["case_count"], 14)
        self.assertEqual(payload["passed_count"], 14)
        self.assertEqual(payload["failed_count"], 0)
        self.assertEqual(payload["exact_match_rate"], 1.0)


class SupervisorSafetyAndRegistryTests(unittest.TestCase):
    def test_successful_plans_never_invent_identity_scope_metric_period_or_table(self):
        for case in supervisor_fixture_cases():
            result = evaluate_supervisor_case(case).actual
            if result.abstain:
                self.assertIsNone(result.plan)
                continue
            with self.subTest(case=case.case_id):
                plan = result.plan
                understanding = case.query_understanding
                self.assertEqual(plan.company.name, understanding.company.name)
                self.assertEqual(plan.company.ticker, understanding.company.ticker)
                self.assertIs(
                    plan.statement_scope, understanding.statement_scope.value
                )
                requested_metrics = [
                    item.canonical for item in understanding.metrics
                ]
                self.assertEqual(plan.target_metrics, requested_metrics)
                requested_periods = []
                for item in understanding.periods:
                    if item.value not in requested_periods:
                        requested_periods.append(item.value)
                self.assertEqual(plan.periods, requested_periods)
                for requirement in plan.retrieval_requirements:
                    self.assertIn(requirement.metric, requested_metrics)
                    self.assertIn(requirement.period, requested_periods)
                    self.assertIs(
                        requirement.table_class,
                        table_for_metric(requirement.metric),
                    )

    def test_formula_registry_supervisor_plan_consistency(self):
        fixture_by_formula = {
            case.expected.formula_id: case
            for case in supervisor_fixture_cases()
            if case.expected.formula_id is not None
        }
        self.assertEqual(set(fixture_by_formula), {"GROWTH_RATE", "AVERAGE"})
        for formula in FORMULA_REGISTRY:
            with self.subTest(formula=formula.formula_id):
                result = evaluate_supervisor_case(
                    fixture_by_formula[formula.formula_id]
                ).actual
                plan = result.plan
                self.assertEqual(plan.formula_id, formula.formula_id)
                self.assertIs(plan.question_type, formula.question_type)
                self.assertIs(plan.reasoning_mode, formula.reasoning_mode)
                self.assertEqual(plan.derived_target, formula.derived_target)
                self.assertEqual(plan.evidence_sources, formula.evidence_sources)

    def test_table_mapping_consistency_and_unknown_formula_rejection(self):
        self.assertIs(
            table_for_metric("LNST"),
            case_by_id("simple-lnst-lookup").expected.tables_needed[0],
        )
        self.assertIsNone(table_for_metric("UNMAPPED"))
        self.assertIsNone(get_formula("NOT_REGISTERED"))
        with self.assertRaises(SupervisorPlanningError) as caught:
            validate_formula_id("NOT_REGISTERED")
        self.assertIn("MISSING_FORMULA", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
