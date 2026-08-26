import unittest

from src.retrieval.batch3 import Batch3Retriever
from src.retrieval.parallel import (
    retrieve_multi_table_parallel,
    retrieve_sources_parallel,
)
from src.retrieval.schemas import (
    RetrievalCandidate,
    RetrievalCompany,
    RetrievalContractError,
    RetrievalQuery,
    make_candidate_id,
)
from src.supervisor.schemas import EvidenceSource
from src.understanding.schemas import StatementScope


def query(metrics, periods):
    return RetrievalQuery(
        raw_question="cau hoi",
        company=RetrievalCompany(name="Test", ticker="AAA"),
        periods=list(periods),
        period_kind=None,
        statement_scope=StatementScope.HOP_NHAT,
        target_metrics=list(metrics),
        derived_target=None,
        evidence_sources=[EvidenceSource.TABLE],
        requested_scale=None,
        requested_unit=None,
        query_texts=["cau hoi"],
    )


def _candidate(tag):
    representation_id = f"representation-{tag}"
    return RetrievalCandidate(
        candidate_id=make_candidate_id(EvidenceSource.TABLE, representation_id, f"chunk-{tag}"),
        representation_id=representation_id,
        chunk_id=f"chunk-{tag}",
        source_type=EvidenceSource.TABLE,
        report_id="report",
        page_ids=["page"],
        table_id="table",
        paragraph_id=None,
        ticker="AAA",
        company_name="Test",
        report_year=2024,
        statement_scope=StatementScope.HOP_NHAT,
        period_labels=["2024"],
        row_paths=[],
        column_paths=[],
        content=f"doc {tag}",
        bm25_score=1.0,
        vector_score=2.0,
        rrf_score=0.5,
        rerank_score=1.0,
        rank=1,
    )


class StubRetriever:
    """Deterministic retriever keyed by the subquery's metric/period."""

    def __init__(self, *, empty_for=(), fail_for=()):
        self.empty_for = set(empty_for)
        self.fail_for = set(fail_for)

    def retrieve(self, sub_query, *, top_k, eligible_source_types):
        metric = sub_query.target_metrics[0] if sub_query.target_metrics else None
        period = sub_query.periods[0] if sub_query.periods else None
        tag = f"{metric}-{period}"
        if metric in self.fail_for:
            raise RetrievalContractError("STUB_BACKEND_FAILURE", tag)
        if metric in self.empty_for:
            return []
        return [_candidate(tag)]


def _summ(result):
    return {
        "parent": result.parent_query_id,
        "complete": result.complete,
        "missing": list(result.missing_subquery_ids),
        "subqueries": [
            (r.subquery_id, r.failure_code, [c.candidate_id for c in r.candidates])
            for r in result.subquery_results
        ],
    }


class ParallelMultiTableTests(unittest.TestCase):
    def test_parallel_matches_sequential(self):
        stub = StubRetriever()
        q = query(["Doanh thu", "LNST"], ["2023", "2024"])
        sequential = Batch3Retriever.retrieve_multi_table(stub, q, top_k=5)
        parallel = retrieve_multi_table_parallel(stub, q, top_k=5)
        self.assertEqual(_summ(parallel), _summ(sequential))
        self.assertEqual(len(parallel.subquery_results), 4)

    def test_parallel_matches_sequential_with_missing_and_failures(self):
        stub = StubRetriever(empty_for={"LNST"}, fail_for={"Doanh thu"})
        q = query(["Doanh thu", "LNST"], ["2023", "2024"])
        sequential = Batch3Retriever.retrieve_multi_table(stub, q, top_k=5)
        parallel = retrieve_multi_table_parallel(stub, q, top_k=5)
        self.assertEqual(_summ(parallel), _summ(sequential))
        self.assertFalse(parallel.complete)
        self.assertEqual(len(parallel.missing_subquery_ids), 4)

    def test_deterministic_across_runs_and_worker_counts(self):
        stub = StubRetriever()
        q = query(["A", "B", "C"], ["2024"])
        one = retrieve_multi_table_parallel(stub, q, top_k=3, max_workers=1)
        many = retrieve_multi_table_parallel(stub, q, top_k=3, max_workers=8)
        self.assertEqual(_summ(one), _summ(many))

    def test_invalid_max_workers_is_rejected(self):
        stub = StubRetriever()
        q = query(["A"], ["2024"])
        with self.assertRaises(ValueError):
            retrieve_multi_table_parallel(stub, q, top_k=3, max_workers=0)


class ParallelSourcesTests(unittest.TestCase):
    def test_sources_parallel_matches_sequential_methods(self):
        narrative_sentinel = object()

        class SourceStub(StubRetriever):
            def retrieve_narrative(self, q, *, top_k):
                return narrative_sentinel

        stub = SourceStub()
        q = query(["Doanh thu"], ["2024"])
        table, text = retrieve_sources_parallel(stub, q, top_k=5)
        self.assertEqual(_summ(table), _summ(retrieve_multi_table_parallel(stub, q, top_k=5)))
        self.assertIs(text, narrative_sentinel)


if __name__ == "__main__":
    unittest.main()
