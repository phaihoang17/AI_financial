import unittest

from src.supervisor.router import (
    derive_evidence_sources,
    plan_requirement_source_types,
    requires_scale_resolution,
    route_model_tier,
    route_reasoning_mode,
    route_verification_profile,
)
from src.supervisor.schemas import (
    EvidenceSource,
    FormulaDefinition,
    FormulaPeriodRule,
    ModelTier,
    QuestionType,
    ReasoningMode,
    RetrievalRequirement,
    TableClass,
    VerifyProfile,
)
from src.understanding.schemas import Operation, SchemaValidationError


def table_requirement(period="2024", *, required=True):
    return RetrievalRequirement.create(
        EvidenceSource.TABLE,
        TableClass.INCOME_STATEMENT,
        "LNST",
        period,
        required,
    )


def text_requirement(*, required=True):
    return RetrievalRequirement.create(
        EvidenceSource.TEXT, None, None, None, required
    )


def growth_formula(*, sources=(EvidenceSource.TABLE,)):
    return FormulaDefinition(
        "TEST_GROWTH",
        Operation.GROWTH,
        QuestionType.MULTI_PERIOD,
        "GROWTH",
        [],
        FormulaPeriodRule.EXACT_TWO,
        ReasoningMode.PROGRAM,
        list(sources),
    )


class ReasoningAndModelRoutingTests(unittest.TestCase):
    def test_direct_routes_cheap_and_program_routes_strong(self):
        self.assertIs(route_model_tier(ReasoningMode.DIRECT), ModelTier.CHEAP)
        self.assertIs(route_model_tier(ReasoningMode.PROGRAM), ModelTier.STRONG)

    def test_registry_override_can_only_upgrade(self):
        self.assertIs(
            route_model_tier(
                ReasoningMode.DIRECT,
                registry_required_tier=ModelTier.STRONG,
            ),
            ModelTier.STRONG,
        )
        self.assertIs(
            route_model_tier(
                ReasoningMode.PROGRAM,
                registry_required_tier=ModelTier.CHEAP,
            ),
            ModelTier.STRONG,
        )

    def test_reasoning_mode_uses_formula_precedence(self):
        self.assertIs(
            route_reasoning_mode(QuestionType.LOOKUP, formula=None),
            ReasoningMode.DIRECT,
        )
        self.assertIs(
            route_reasoning_mode(QuestionType.MULTI_PERIOD, formula=None),
            ReasoningMode.DIRECT,
        )
        self.assertIs(
            route_reasoning_mode(
                QuestionType.MULTI_PERIOD, formula=growth_formula()
            ),
            ReasoningMode.PROGRAM,
        )

    def test_table_transform_is_disabled(self):
        with self.assertRaises(SchemaValidationError):
            route_model_tier(ReasoningMode.TABLE_TRANSFORM)


class VerificationRoutingTests(unittest.TestCase):
    def test_only_single_direct_lookup_is_light(self):
        one = [table_requirement()]
        self.assertIs(
            route_verification_profile(
                QuestionType.LOOKUP, ReasoningMode.DIRECT, None, one
            ),
            VerifyProfile.LIGHT,
        )
        strict_cases = (
            (QuestionType.LOOKUP, ReasoningMode.PROGRAM, None, one),
            (QuestionType.LOOKUP, ReasoningMode.DIRECT, "FORMULA", one),
            (
                QuestionType.LOOKUP,
                ReasoningMode.DIRECT,
                None,
                [table_requirement(), text_requirement()],
            ),
            (QuestionType.MULTI_PERIOD, ReasoningMode.DIRECT, None, one),
            (QuestionType.DERIVED_RATIO, ReasoningMode.PROGRAM, "RATIO", one),
            (QuestionType.AGGREGATE, ReasoningMode.PROGRAM, "AVERAGE", one),
        )
        for question_type, mode, formula_id, requirements in strict_cases:
            with self.subTest(
                question_type=question_type,
                mode=mode,
                formula_id=formula_id,
            ):
                self.assertIs(
                    route_verification_profile(
                        question_type, mode, formula_id, requirements
                    ),
                    VerifyProfile.STRICT,
                )


class EvidenceSourceAndScaleRoutingTests(unittest.TestCase):
    def test_evidence_source_precedence_is_formula_then_requirements_then_policy(self):
        self.assertEqual(
            plan_requirement_source_types(
                formula=growth_formula(),
                explicit_requirements=[text_requirement()],
            ),
            [EvidenceSource.TABLE],
        )
        self.assertEqual(
            plan_requirement_source_types(
                formula=None,
                explicit_requirements=[text_requirement(), table_requirement()],
            ),
            [EvidenceSource.TEXT, EvidenceSource.TABLE],
        )
        self.assertEqual(
            plan_requirement_source_types(formula=None),
            [EvidenceSource.TABLE],
        )

    def test_final_sources_are_exact_ordered_requirement_sources(self):
        requirements = [
            table_requirement("2023"),
            text_requirement(),
            table_requirement("2024"),
        ]
        self.assertEqual(
            derive_evidence_sources(requirements),
            [EvidenceSource.TABLE, EvidenceSource.TEXT],
        )

    def test_scale_resolution_flag_preserves_batch1_rule(self):
        self.assertTrue(
            requires_scale_resolution(
                [EvidenceSource.TABLE], requested_scale=None, requested_unit=None
            )
        )
        self.assertTrue(
            requires_scale_resolution(
                [EvidenceSource.TEXT],
                requested_scale="MILLION",
                requested_unit=None,
            )
        )
        self.assertTrue(
            requires_scale_resolution(
                [EvidenceSource.TEXT], requested_scale=None, requested_unit="VND"
            )
        )
        self.assertFalse(
            requires_scale_resolution(
                [EvidenceSource.TEXT], requested_scale=None, requested_unit=None
            )
        )


if __name__ == "__main__":
    unittest.main()
