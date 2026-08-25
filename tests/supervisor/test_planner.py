import unittest

from src.supervisor.formula_registry import FORMULA_REGISTRY
from src.supervisor.planner import (
    SupervisorPlanningError,
    classify_question_type,
    derive_tables_needed,
    generate_retrieval_requirements,
    plan_required_periods,
    select_formula,
    supervise_batch1,
    validate_formula_id,
)
from src.supervisor.schemas import (
    EvidenceSource,
    FormulaDefinition,
    FormulaPeriodRule,
    MetricTableMapping,
    QuestionType,
    ReasoningMode,
    RetrievalRequirement,
    SupervisorAbstainReason,
    SupervisorResult,
    TableClass,
)
from src.understanding.planning_gate import FindingName, PlanningGate
from src.understanding.schemas import (
    CompanyUnderstanding,
    MetricUnderstanding,
    Operation,
    PeriodKind,
    PeriodUnderstanding,
    QueryUnderstanding,
    SchemaValidationError,
    StatementScope,
    StatementScopeUnderstanding,
)


def understanding(
    operation=Operation.NONE,
    periods=(("2024", PeriodKind.NAM),),
    metrics=("LNST",),
    scope=StatementScope.HOP_NHAT,
):
    return QueryUnderstanding(
        raw_question="fixture question",
        company=CompanyUnderstanding("AAA", "Test Company", "AAA", 1.0),
        periods=[PeriodUnderstanding(value, kind, value) for value, kind in periods],
        statement_scope=StatementScopeUnderstanding(scope, False, 1.0 if scope else 0.0),
        metrics=[MetricUnderstanding(metric, metric, 1.0) for metric in metrics],
        operation=operation,
        requested_scale=None,
        requested_unit=None,
        missing_information=[],
        ambiguities=[],
        confidence=1.0,
    )


ALLOWED = PlanningGate(True, [], [])


class QuestionTypeTests(unittest.TestCase):
    def test_operation_mapping(self):
        self.assertIs(classify_question_type(Operation.NONE), QuestionType.LOOKUP)
        self.assertIs(classify_question_type(Operation.GROWTH), QuestionType.MULTI_PERIOD)
        self.assertIs(classify_question_type(Operation.AGGREGATE), QuestionType.AGGREGATE)
        self.assertIs(classify_question_type(Operation.COMPARE), QuestionType.MULTI_PERIOD)

    def test_supported_and_unsupported_ratio_and_unknown(self):
        ratio = FormulaDefinition(
            "TEST_ROE", Operation.RATIO, QuestionType.DERIVED_RATIO, "ROE",
            ["LNST"], FormulaPeriodRule.SINGLE, ReasoningMode.PROGRAM,
            [EvidenceSource.TABLE],
        )
        selected = select_formula(
            understanding(Operation.RATIO, metrics=("ROE",)), (ratio,)
        )
        self.assertIs(classify_question_type(Operation.RATIO, ratio_formula=selected), QuestionType.DERIVED_RATIO)
        for operation, reason in (
            (Operation.RATIO, SupervisorAbstainReason.UNSUPPORTED_RATIO),
            (Operation.UNKNOWN, SupervisorAbstainReason.UNKNOWN_OPERATION),
        ):
            with self.subTest(operation=operation), self.assertRaises(SupervisorPlanningError) as caught:
                classify_question_type(operation)
            self.assertIs(caught.exception.reason, reason)


class RequiredTableTests(unittest.TestCase):
    def test_mapped_metric_deduplicated_tables_and_missing_mapping(self):
        query = understanding(periods=(("2023", PeriodKind.NAM), ("2024", PeriodKind.NAM)))
        period_plan = plan_required_periods(query.periods, operation=query.operation, formula=None)
        requirements = generate_retrieval_requirements(query, period_plan, formula=None)
        self.assertEqual(derive_tables_needed(requirements), [TableClass.INCOME_STATEMENT])
        self.assertEqual([item.metric for item in requirements], ["LNST", "LNST"])
        with self.assertRaises(SupervisorPlanningError) as caught:
            bad = understanding(metrics=("UNMAPPED",))
            generate_retrieval_requirements(
                bad,
                plan_required_periods(bad.periods, operation=bad.operation, formula=None),
                formula=None,
            )
        self.assertIs(caught.exception.reason, SupervisorAbstainReason.MISSING_TABLE_MAPPING)


class RequiredPeriodTests(unittest.TestCase):
    def test_homogeneous_period_kinds_and_no_invention(self):
        for periods, kind in (
            ((("2023", PeriodKind.NAM), ("2024", PeriodKind.NAM)), PeriodKind.NAM),
            ((("2024-Q1", PeriodKind.QUY), ("2024-Q2", PeriodKind.QUY)), PeriodKind.QUY),
            ((("2024-6M", PeriodKind.LUY_KE), ("2024-9M", PeriodKind.LUY_KE)), PeriodKind.LUY_KE),
        ):
            query = understanding(Operation.COMPARE, periods=periods)
            result = plan_required_periods(query.periods, operation=query.operation, formula=None)
            self.assertIs(result.period_kind, kind)
            self.assertEqual(result.periods, [value for value, _ in periods])

    def test_mixed_growth_and_aggregate_period_rules(self):
        mixed = understanding(periods=(("2024", PeriodKind.NAM), ("2024-Q1", PeriodKind.QUY)))
        with self.assertRaises(SupervisorPlanningError) as caught:
            plan_required_periods(mixed.periods, operation=mixed.operation, formula=None)
        self.assertIs(caught.exception.reason, SupervisorAbstainReason.MIXED_PERIOD_KIND_UNSUPPORTED)

        growth = select_formula(understanding(Operation.GROWTH))
        with self.assertRaises(SupervisorPlanningError) as caught:
            one = understanding(Operation.GROWTH)
            plan_required_periods(one.periods, operation=one.operation, formula=growth)
        self.assertIs(caught.exception.reason, SupervisorAbstainReason.INVALID_PERIOD_REQUIREMENT)

        average = select_formula(understanding(Operation.AGGREGATE))
        two = understanding(Operation.AGGREGATE, periods=(("2023", PeriodKind.NAM), ("2024", PeriodKind.NAM)))
        self.assertEqual(plan_required_periods(two.periods, operation=two.operation, formula=average).periods, ["2023", "2024"])


class FormulaPlannerTests(unittest.TestCase):
    def test_registry_selection_and_unknown_rejection(self):
        growth = select_formula(understanding(Operation.GROWTH))
        average = select_formula(understanding(Operation.AGGREGATE))
        self.assertEqual((growth.formula_id, average.formula_id), ("GROWTH_RATE", "AVERAGE"))
        self.assertIsNone(select_formula(understanding(Operation.NONE)))
        self.assertIsNone(select_formula(understanding(Operation.COMPARE)))
        with self.assertRaises(SupervisorPlanningError):
            validate_formula_id("NOT_REGISTERED")
        self.assertEqual([item.formula_id for item in FORMULA_REGISTRY], ["GROWTH_RATE", "AVERAGE"])
        self.assertEqual(
            FormulaDefinition.from_dict(growth.to_dict()).to_dict(), growth.to_dict()
        )

    def test_requirement_identity_and_registry_contract_round_trip(self):
        requirement = RetrievalRequirement.create(
            EvidenceSource.TABLE, TableClass.INCOME_STATEMENT, "LNST", "2024"
        )
        self.assertEqual(
            RetrievalRequirement.from_dict(requirement.to_dict()), requirement
        )
        mapping = MetricTableMapping("LNST", TableClass.INCOME_STATEMENT)
        self.assertEqual(MetricTableMapping.from_dict(mapping.to_dict()), mapping)


class EarlyAbstainAndIntegrationTests(unittest.TestCase):
    def test_valid_plan_and_deterministic_supervisor_result(self):
        first = supervise_batch1(understanding(), ALLOWED)
        second = supervise_batch1(understanding(), ALLOWED)
        self.assertFalse(first.abstain)
        self.assertEqual(first.input_fingerprint, second.input_fingerprint)
        self.assertEqual(first.to_dict(), SupervisorResult.from_dict(first.to_dict()).to_dict())
        self.assertEqual(first.plan.tables_needed, [TableClass.INCOME_STATEMENT])
        self.assertEqual(len(first.plan.retrieval_requirements), 1)
        self.assertTrue(first.plan.requires_scale_resolution)

    def test_supervisor_result_contradictions_are_rejected(self):
        result = supervise_batch1(understanding(), ALLOWED)
        with self.assertRaises(SchemaValidationError):
            SupervisorResult(ALLOWED, result.plan, True, SupervisorAbstainReason.INVALID_PLAN, result.input_fingerprint)
        with self.assertRaises(SchemaValidationError):
            SupervisorResult(ALLOWED, None, True, None, result.input_fingerprint)

    def test_early_abstain_reasons(self):
        blocked = PlanningGate(False, [FindingName.COMPANY], [])
        cases = (
            (understanding(), blocked, {}, SupervisorAbstainReason.PLANNING_GATE_BLOCKED),
            (understanding(Operation.UNKNOWN), PlanningGate(False, [], []), {}, SupervisorAbstainReason.UNKNOWN_OPERATION),
            (understanding(scope=None), ALLOWED, {}, SupervisorAbstainReason.MISSING_STATEMENT_SCOPE),
            (understanding(Operation.GROWTH, periods=(("2024", PeriodKind.NAM),)), ALLOWED, {}, SupervisorAbstainReason.INVALID_PERIOD_REQUIREMENT),
            (understanding(periods=(("2024", PeriodKind.NAM), ("2024-Q1", PeriodKind.QUY))), ALLOWED, {}, SupervisorAbstainReason.MIXED_PERIOD_KIND_UNSUPPORTED),
            (understanding(metrics=("UNMAPPED",)), ALLOWED, {}, SupervisorAbstainReason.MISSING_TABLE_MAPPING),
            (understanding(Operation.GROWTH, periods=(("2023", PeriodKind.NAM), ("2024", PeriodKind.NAM))), ALLOWED, {"formula_registry": ()}, SupervisorAbstainReason.MISSING_FORMULA),
            (understanding(Operation.RATIO, metrics=("ROE",)), ALLOWED, {}, SupervisorAbstainReason.UNSUPPORTED_RATIO),
        )
        for query, gate, kwargs, reason in cases:
            with self.subTest(reason=reason):
                result = supervise_batch1(query, gate, **kwargs)
                self.assertTrue(result.abstain)
                self.assertIsNone(result.plan)
                self.assertIs(result.abstain_reason, reason)

    def test_explicit_growth_requirements_are_not_blind_cartesian(self):
        query = understanding(
            Operation.GROWTH,
            periods=(("2023", PeriodKind.NAM), ("2024", PeriodKind.NAM)),
        )
        result = supervise_batch1(query, ALLOWED)
        self.assertFalse(result.abstain)
        self.assertEqual(
            [(item.metric, item.period) for item in result.plan.retrieval_requirements],
            [("LNST", "2023"), ("LNST", "2024")],
        )

    def test_direct_compare_is_cheap_but_strict_and_keeps_retry_budget(self):
        query = understanding(
            Operation.COMPARE,
            periods=(("2023", PeriodKind.NAM), ("2024", PeriodKind.NAM)),
        )
        result = supervise_batch1(query, ALLOWED)
        self.assertFalse(result.abstain)
        self.assertIs(result.plan.reasoning_mode, ReasoningMode.DIRECT)
        self.assertEqual(result.plan.model_tier.value, "CHEAP")
        self.assertEqual(result.plan.verify_profile.value, "STRICT")
        self.assertEqual(result.plan.max_retries, 2)

    def test_registered_growth_is_program_strong_strict_table_only(self):
        query = understanding(
            Operation.GROWTH,
            periods=(("2023", PeriodKind.NAM), ("2024", PeriodKind.NAM)),
        )
        result = supervise_batch1(query, ALLOWED)
        self.assertFalse(result.abstain)
        self.assertIs(result.plan.reasoning_mode, ReasoningMode.PROGRAM)
        self.assertEqual(result.plan.model_tier.value, "STRONG")
        self.assertEqual(result.plan.verify_profile.value, "STRICT")
        self.assertEqual(result.plan.evidence_sources, [EvidenceSource.TABLE])


if __name__ == "__main__":
    unittest.main()
