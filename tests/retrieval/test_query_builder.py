import unittest

from src.retrieval.query_builder import build_retrieval_query
from src.supervisor.schemas import (
    EvidenceSource,
    ModelTier,
    Plan,
    PlanCompany,
    QuestionType,
    ReasoningMode,
    RetrievalRequirement,
    TableClass,
    VerifyProfile,
)
from src.understanding.schemas import (
    CompanyUnderstanding,
    MetricUnderstanding,
    Operation,
    PeriodKind,
    PeriodUnderstanding,
    QueryUnderstanding,
    StatementScope,
    StatementScopeUnderstanding,
)


def make_plan(*, evidence_sources):
    requirements = []
    for source in evidence_sources:
        requirements.append(
            RetrievalRequirement.create(
                source,
                TableClass.INCOME_STATEMENT if source is EvidenceSource.TABLE else None,
                "LNST" if source is EvidenceSource.TABLE else None,
                "2024" if source is EvidenceSource.TABLE else None,
            )
        )
    return Plan(
        question_type=QuestionType.LOOKUP,
        company=PlanCompany(name="CTCP Nhựa An Phát Xanh", ticker="AAA"),
        periods=["2024"],
        period_kind=PeriodKind.NAM,
        statement_scope=StatementScope.HOP_NHAT,
        target_metrics=["LNST"],
        derived_target=None,
        formula_id=None,
        tables_needed=[TableClass.INCOME_STATEMENT],
        retrieval_requirements=requirements,
        evidence_sources=evidence_sources,
        reasoning_mode=ReasoningMode.DIRECT,
        requires_scale_resolution=True,
        model_tier=ModelTier.CHEAP,
        verify_profile=VerifyProfile.LIGHT,
        max_retries=0,
        confidence=0.9,
        abstain=False,
        abstain_reason=None,
    )


def make_understanding(*, question="Doanh thu AAA năm 2024 là bao nhiêu?"):
    return QueryUnderstanding(
        raw_question=question,
        company=CompanyUnderstanding(raw="AAA", name="CTCP Nhựa An Phát Xanh", ticker="AAA", confidence=1.0),
        periods=[
            PeriodUnderstanding(value="2024", kind=PeriodKind.NAM, raw="năm 2024"),
            PeriodUnderstanding(value="2024-Q4", kind=PeriodKind.QUY, raw="quý 4/2024"),
        ],
        statement_scope=StatementScopeUnderstanding(
            value=StatementScope.HOP_NHAT, inferred=False, confidence=1.0
        ),
        metrics=[MetricUnderstanding(raw="doanh thu", canonical="Doanh thu", confidence=1.0)],
        operation=Operation.NONE,
        requested_scale="MILLION",
        requested_unit="VND",
        missing_information=[],
        ambiguities=[],
        confidence=1.0,
    )


class RetrievalQueryBuilderTests(unittest.TestCase):
    def test_builds_deterministic_query_from_upstream_contracts(self):
        plan = make_plan(evidence_sources=[EvidenceSource.TABLE, EvidenceSource.TEXT])
        understanding = make_understanding()

        query = build_retrieval_query(plan, understanding)

        self.assertEqual(query.raw_question, understanding.raw_question)
        self.assertEqual(query.company.to_dict(), plan.company.to_dict())
        self.assertEqual(query.periods, ["2024"])
        self.assertEqual(query.target_metrics, ["LNST"])
        self.assertIsNone(query.derived_target)
        self.assertEqual(query.requested_scale, "MILLION")
        self.assertEqual(query.requested_unit, "VND")
        self.assertEqual(
            query.query_texts,
            [
                "Doanh thu AAA năm 2024 là bao nhiêu?",
                "LNST",
                "2024",
                "AAA CTCP Nhựa An Phát Xanh",
            ],
        )

    def test_preserves_table_text_and_hybrid_evidence_source_constraints(self):
        for requested in (
            [EvidenceSource.TABLE],
            [EvidenceSource.TEXT, EvidenceSource.TABLE],
        ):
            with self.subTest(requested=requested):
                query = build_retrieval_query(make_plan(evidence_sources=requested), make_understanding())
                self.assertEqual(query.evidence_sources, requested)
                self.assertEqual(query.eligible_source_types, requested)

    def test_deduplicates_only_exact_query_texts_in_first_occurrence_order(self):
        question = "LNST"
        plan = make_plan(evidence_sources=[EvidenceSource.TABLE])

        query = build_retrieval_query(plan, make_understanding(question=question))

        self.assertEqual(
            query.query_texts,
            ["LNST", "2024", "AAA CTCP Nhựa An Phát Xanh"],
        )

    def test_query_texts_are_unmodified_upstream_values_not_llm_paraphrases(self):
        plan = make_plan(evidence_sources=[EvidenceSource.TABLE])
        understanding = make_understanding()

        query = build_retrieval_query(plan, understanding)

        allowed = {
            understanding.raw_question,
            *plan.target_metrics,
            plan.derived_target,
            *plan.periods,
            f"{plan.company.ticker} {plan.company.name}",
        }
        self.assertTrue(set(query.query_texts).issubset(allowed))

    def test_preserves_period_and_source_order(self):
        plan = make_plan(evidence_sources=[EvidenceSource.TEXT, EvidenceSource.TABLE])

        query = build_retrieval_query(plan, make_understanding())

        self.assertEqual(query.periods, ["2024"])
        self.assertEqual(query.evidence_sources, [EvidenceSource.TEXT, EvidenceSource.TABLE])
        self.assertEqual(query.query_texts[-2:], ["2024", "AAA CTCP Nhựa An Phát Xanh"])


if __name__ == "__main__":
    unittest.main()
