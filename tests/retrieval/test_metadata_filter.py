import unittest

from src.retrieval.metadata_filter import derive_report_years, filter_by_report_metadata
from src.retrieval.schemas import (
    RetrievalCandidate,
    RetrievalCompany,
    RetrievalContractError,
    RetrievalQuery,
    make_candidate_id,
)
from src.supervisor.schemas import EvidenceSource
from src.understanding.schemas import StatementScope


def query(*, ticker="AAA", periods=None, statement_scope=StatementScope.HOP_NHAT):
    return RetrievalQuery(
        raw_question="question",
        company=RetrievalCompany(name="An Phat", ticker=ticker),
        periods=["2024"] if periods is None else periods,
        period_kind=None,
        statement_scope=statement_scope,
        target_metrics=["Doanh thu"],
        derived_target=None,
        evidence_sources=[EvidenceSource.TABLE],
        requested_scale=None,
        requested_unit=None,
        query_texts=["question"],
    )


def candidate(
    candidate_number,
    *,
    ticker="AAA",
    report_year=2024,
    statement_scope=StatementScope.HOP_NHAT,
):
    representation_id = f"representation-{candidate_number}"
    chunk_id = f"chunk-{candidate_number}"
    return RetrievalCandidate(
        candidate_id=make_candidate_id(EvidenceSource.TABLE, representation_id, chunk_id),
        representation_id=representation_id,
        chunk_id=chunk_id,
        source_type=EvidenceSource.TABLE,
        report_id=f"report-{candidate_number}",
        page_ids=[f"page-{candidate_number}"],
        table_id=f"table-{candidate_number}",
        paragraph_id=None,
        ticker=ticker,
        company_name="An Phat",
        report_year=report_year,
        statement_scope=statement_scope,
        period_labels=[str(report_year)],
        row_paths=[],
        column_paths=[],
        content=f"content-{candidate_number}",
        bm25_score=None,
        vector_score=None,
        rrf_score=None,
        rerank_score=None,
        rank=None,
    )


class ReportMetadataFilterTests(unittest.TestCase):
    def test_exact_ticker_match_and_mismatch(self):
        matching = candidate(1, ticker="AAA")
        mismatch = candidate(2, ticker="AAB")

        result = filter_by_report_metadata(query(), [matching, mismatch])

        self.assertEqual(result, [matching])

    def test_explicit_scope_rejects_null_candidate_scope(self):
        explicit = query(statement_scope=StatementScope.HOP_NHAT)
        scoped = candidate(1, statement_scope=StatementScope.HOP_NHAT)
        unresolved = candidate(2, statement_scope=None)

        result = filter_by_report_metadata(explicit, [scoped, unresolved])

        self.assertEqual(result, [scoped])

    def test_unresolved_scope_does_not_filter_candidates(self):
        result = filter_by_report_metadata(
            query(statement_scope=None),
            [
                candidate(1, statement_scope=StatementScope.HOP_NHAT),
                candidate(2, statement_scope=StatementScope.RIENG),
                candidate(3, statement_scope=None),
            ],
        )

        self.assertEqual([item.report_id for item in result], ["report-1", "report-2", "report-3"])

    def test_year_quarter_and_cumulative_periods_filter_by_their_report_year(self):
        for period in ("2024", "2024-Q4", "2024-9M"):
            with self.subTest(period=period):
                result = filter_by_report_metadata(
                    query(periods=[period]),
                    [candidate(1, report_year=2024), candidate(2, report_year=2023)],
                )
                self.assertEqual([item.report_year for item in result], [2024])

    def test_multiple_years_are_allowed_once_in_source_order(self):
        periods = ["2025-Q1", "2024-9M", "2025", "2024"]

        self.assertEqual(derive_report_years(periods), [2025, 2024])
        result = filter_by_report_metadata(
            query(periods=periods),
            [candidate(1, report_year=2024), candidate(2, report_year=2025), candidate(3, report_year=2023)],
        )

        self.assertEqual([item.report_year for item in result], [2024, 2025])

    def test_zero_matches_are_returned_without_filter_relaxation(self):
        result = filter_by_report_metadata(
            query(), [candidate(1, ticker="AAB"), candidate(2, report_year=2023)]
        )

        self.assertEqual(result, [])

    def test_candidate_source_order_is_preserved_after_filtering(self):
        first = candidate(3)
        second = candidate(1)
        third = candidate(2, report_year=2023)

        result = filter_by_report_metadata(query(), [first, second, third])

        self.assertEqual(result, [first, second])

    def test_unresolved_ticker_does_not_add_a_ticker_filter(self):
        result = filter_by_report_metadata(
            query(ticker=""), [candidate(1, ticker="AAA"), candidate(2, ticker="BBB")]
        )

        self.assertEqual([item.ticker for item in result], ["AAA", "BBB"])

    def test_invalid_period_is_a_hard_failure_not_a_relaxed_filter(self):
        with self.assertRaisesRegex(RetrievalContractError, "REPORT_YEAR_UNRESOLVED"):
            filter_by_report_metadata(query(periods=["Q4-2024"]), [candidate(1)])


if __name__ == "__main__":
    unittest.main()
