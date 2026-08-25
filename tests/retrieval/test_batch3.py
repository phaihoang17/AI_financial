from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from src.evidence.schemas import Scale
from src.indexing.provenance_sidecar import ProvenanceSidecar
from src.indexing.schemas import ScaleHintSource, ScaleHintStatus, ScaleUnitHint, SourceSpan
from src.retrieval.batch3 import (
    Batch3RetrievalError,
    Batch3Retriever,
    CorpusChunkLookup,
    ScaleUnitHintRetriever,
    make_multi_table_subqueries,
)
from src.retrieval.corpus_candidates import iter_corpus_candidates
from src.retrieval.reranker import BGEReranker
from src.retrieval.schemas import RetrievalCandidate, RetrievalCompany, RetrievalQuery, make_candidate_id
from src.supervisor.schemas import EvidenceSource
from src.understanding.schemas import StatementScope
from tests.indexing.test_provenance_sidecar import ProvenanceSidecarTests


def make_query(*, metrics=("Revenue",), periods=("2024",), scope=StatementScope.HOP_NHAT):
    return RetrievalQuery(
        raw_question="How much is revenue?",
        company=RetrievalCompany(name="Test Company", ticker="AAA"),
        periods=list(periods),
        period_kind=None,
        statement_scope=scope,
        target_metrics=list(metrics),
        derived_target=None,
        evidence_sources=[EvidenceSource.TABLE, EvidenceSource.TEXT],
        requested_scale="MILLION",
        requested_unit="VND",
        query_texts=["How much is revenue?"],
    )


def make_candidate(name: str, source_type: EvidenceSource, *, bm25=None, vector=None, rank=1):
    representation_id = f"representation-{name}"
    return RetrievalCandidate(
        candidate_id=make_candidate_id(source_type, representation_id, f"chunk-{name}"),
        representation_id=representation_id,
        chunk_id=f"chunk-{name}",
        source_type=source_type,
        report_id="report",
        page_ids=["page"],
        table_id="table-1" if source_type is EvidenceSource.TABLE else None,
        paragraph_id=None if source_type is EvidenceSource.TABLE else f"paragraph-{name}",
        ticker="AAA",
        company_name="Test Company",
        report_year=2024,
        statement_scope=StatementScope.HOP_NHAT,
        period_labels=["2024"],
        row_paths=[],
        column_paths=[],
        content=f"document {name}",
        bm25_score=bm25,
        vector_score=vector,
        rrf_score=None,
        rerank_score=None,
        rank=rank,
    )


class ConstantTokenizer:
    def __call__(self, query, document, **kwargs):
        if isinstance(document, list):
            return {
                "input_ids": FakeTensor(len(document)),
                "attention_mask": FakeTensor(len(document)),
            }
        return {"input_ids": [0, 1, 2, 3]}


class FakeTensor:
    def __init__(self, rows):
        self.shape = (rows, 4)

    def to(self, _):
        return self


class FakeScores:
    def __init__(self, rows):
        self.rows = rows

    def __getitem__(self, _):
        return self

    def detach(self):
        return self

    def cpu(self):
        return self

    def float(self):
        return self

    def tolist(self):
        return [1.0] * self.rows


class FakeTorch:
    class _NoGrad:
        def __enter__(self):
            return None

        def __exit__(self, *_):
            return False

    @staticmethod
    def no_grad():
        return FakeTorch._NoGrad()


class ConstantModel:
    def __call__(self, **kwargs):
        return SimpleNamespace(logits=FakeScores(kwargs["input_ids"].shape[0]))


def constant_reranker():
    return BGEReranker(ConstantTokenizer(), ConstantModel(), torch_module=FakeTorch())


class FakeBackend:
    def __init__(self, *, fail_metric=None, no_result_metric=None):
        self.calls = []
        self.fail_metric = fail_metric
        self.no_result_metric = no_result_metric

    def _candidate(self, query, eligible, *, vector):
        metric = query.target_metrics[0] if query.target_metrics else "period"
        if metric == self.fail_metric:
            raise Batch3RetrievalError("TEST_SUBQUERY_FAILURE", metric)
        if metric == self.no_result_metric:
            return []
        source_type = eligible[0]
        item = make_candidate(f"{metric}-{query.periods[0] if query.periods else 'none'}", source_type)
        return [replace(item, vector_score=0.5, bm25_score=None, rank=1)] if vector else [
            replace(item, bm25_score=0.5, vector_score=None, rank=1)
        ]

    def search(self, query, *args, top_k, eligible_source_types):
        self.calls.append((query, list(eligible_source_types)))
        return self._candidate(query, eligible_source_types, vector=bool(args))


class FakeSidecar:
    def __init__(self, links):
        self.links = links

    def get_links_by_paragraph_id(self, paragraph_id):
        return self.links.get(paragraph_id, [])


class Link:
    def __init__(self, table_id):
        self.table_id = table_id


class FullLink(Link):
    def __init__(self, table_id, paragraph_id):
        super().__init__(table_id)
        self.paragraph_id = paragraph_id


class FakeProvenanceRepository:
    def __init__(self, hints_by_source, hints_by_table, table_links, paragraph_links):
        self.hints_by_source = hints_by_source
        self.hints_by_table = hints_by_table
        self.table_links = table_links
        self.paragraph_links = paragraph_links

    def get_hints_by_source_ref(self, source_ref):
        return self.hints_by_source.get(source_ref, [])

    def get_hints_by_table_id(self, table_id):
        return self.hints_by_table.get(table_id, [])

    def get_links_by_table_id(self, table_id):
        return self.table_links.get(table_id, [])

    def get_links_by_paragraph_id(self, paragraph_id):
        return self.paragraph_links.get(paragraph_id, [])


class FakeChunkLookup:
    def __init__(self, chunks):
        self.chunks = chunks

    def for_candidates(self, candidates):
        return self.chunks


class Batch3PipelineTests(unittest.TestCase):
    def test_cartesian_decomposition_order_and_table_only_execution(self):
        query = make_query(metrics=("Revenue", "Profit"), periods=("2023", "2024"))
        subqueries = make_multi_table_subqueries(query)
        backend = FakeBackend()
        service = Batch3Retriever(backend, backend, lambda _: [], reranker=constant_reranker())

        result = service.retrieve_multi_table(query, top_k=3)

        self.assertEqual(
            [(item.metric, item.period) for item in subqueries],
            [("Revenue", "2023"), ("Revenue", "2024"), ("Profit", "2023"), ("Profit", "2024")],
        )
        self.assertTrue(result.complete)
        self.assertEqual(result.missing_subquery_ids, [])
        self.assertEqual(len(result.subquery_results), 4)
        self.assertTrue(all(call[1] == [EvidenceSource.TABLE] for call in backend.calls))

    def test_metric_only_period_only_zero_result_and_typed_partial_failure(self):
        self.assertEqual(
            [(item.metric, item.period) for item in make_multi_table_subqueries(make_query(metrics=("Revenue",), periods=()))],
            [("Revenue", None)],
        )
        self.assertEqual(
            [(item.metric, item.period) for item in make_multi_table_subqueries(make_query(metrics=(), periods=("2024",)))],
            [(None, "2024")],
        )
        query = make_query(metrics=("Revenue", "Missing", "Failure"), periods=("2024",))
        backend = FakeBackend(fail_metric="Failure", no_result_metric="Missing")
        result = Batch3Retriever(backend, backend, lambda _: [], reranker=constant_reranker()).retrieve_multi_table(query, top_k=2)

        self.assertFalse(result.complete)
        self.assertEqual(len(result.missing_subquery_ids), 2)
        failures = {item.failure_code for item in result.subquery_results}
        self.assertIn("TEST_SUBQUERY_FAILURE", failures)
        self.assertTrue(any(not item.candidates and item.failure_code is None for item in result.subquery_results))

    def test_scope_is_required_and_narrative_is_text_only_with_no_synthetic_link(self):
        with self.assertRaisesRegex(Batch3RetrievalError, "MULTI_TABLE_SCOPE_REQUIRED"):
            make_multi_table_subqueries(make_query(scope=None))

        backend = FakeBackend()
        sidecar = FakeSidecar({"paragraph-period-2024": [Link("table-linked")]})
        service = Batch3Retriever(backend, backend, lambda _: [], reranker=constant_reranker(), sidecar=sidecar)
        result = service.retrieve_narrative(make_query(metrics=(), periods=("2024",)), top_k=2)

        self.assertTrue(all(candidate.source_type is EvidenceSource.TEXT for candidate in result.candidates))
        self.assertEqual(result.linked_table_ids_by_candidate[result.candidates[0].candidate_id], ["table-linked"])
        self.assertTrue(all(call[1] == [EvidenceSource.TEXT] for call in backend.calls))
        unlinked = Batch3Retriever(
            FakeBackend(), FakeBackend(), lambda _: [], reranker=constant_reranker(), sidecar=FakeSidecar({})
        ).retrieve_narrative(make_query(metrics=(), periods=("2024",)), top_k=2)
        self.assertEqual(unlinked.linked_table_ids_by_candidate[unlinked.candidates[0].candidate_id], [])


class ScaleUnitHintRetrievalTests(unittest.TestCase):
    def test_real_persisted_sidecar_returns_direct_and_linked_hints(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw_root, corpus, sidecar_root = ProvenanceSidecarTests()._artifacts(
                root, text="Đơn vị: tỷ."
            )
            sidecar = ProvenanceSidecar(sidecar_root, corpus)
            candidates = list(iter_corpus_candidates(corpus))
            tables = [item for item in candidates if item.source_type is EvidenceSource.TABLE]
            text = next(item for item in candidates if item.source_type is EvidenceSource.TEXT)
            result = ScaleUnitHintRetriever(sidecar, CorpusChunkLookup(str(corpus))).retrieve([*tables, text])

        table_hints = [hint for table in tables for hint in result[table.candidate_id]]
        text_hints = result[text.candidate_id]
        self.assertTrue(any(item.association.value == "DIRECT" for item in table_hints))
        self.assertTrue(any(item.association.value == "LINKED" for item in table_hints))
        self.assertTrue(any(item.source_kind.value == "TEXT" and item.association.value == "DIRECT" for item in text_hints))
        self.assertTrue(any(item.association.value == "LINKED" for item in text_hints))
        for table in tables:
            hints = result[table.candidate_id]
            self.assertEqual(
                [(item.source_span.start, item.source_span.end, item.hint_id) for item in hints],
                sorted((item.source_span.start, item.source_span.end, item.hint_id) for item in hints),
            )
        self.assertIn(Scale.BILLION, {item.scale_candidate for item in table_hints})

    def test_direct_header_cell_caption_text_linked_conflicts_and_requested_scale_are_preserved(self):
        table = make_candidate("hints", EvidenceSource.TABLE)
        text = make_candidate("text", EvidenceSource.TEXT)

        def hint(name, kind, source_ref, start, scale):
            return ScaleUnitHint(
                hint_id=f"hint-{name}", report_id="report", page_id="page", table_id="table-1" if kind is not ScaleHintSource.TEXT else None,
                source_kind=kind, source_ref=source_ref, source_span=SourceSpan(start, start + 1),
                raw_hint_text=name, normalized_hint_text=name, scale_candidate=scale,
                unit_candidate=None, status=ScaleHintStatus.AMBIGUOUS,
            )

        header = hint("header", ScaleHintSource.HEADER, "header-cell", 4, Scale.MILLION)
        cell = hint("cell", ScaleHintSource.CELL, "data-cell", 2, Scale.THOUSAND)
        caption = hint("caption", ScaleHintSource.CAPTION, "table-1", 1, Scale.BILLION)
        narrative = hint("text", ScaleHintSource.TEXT, text.paragraph_id, 3, Scale.PERCENT)
        link = FullLink("table-1", text.paragraph_id)
        repository = FakeProvenanceRepository(
            {
                "header-cell": [header], "data-cell": [cell], "table-1": [caption],
                text.paragraph_id: [narrative],
            },
            {"table-1": [header, cell, caption]},
            {"table-1": [link]}, {text.paragraph_id: [link]},
        )
        chunks = {table.candidate_id: SimpleNamespace(
            primary_source_cell_ids=["header-cell", "data-cell"], context_source_cell_ids=[]
        )}
        result = ScaleUnitHintRetriever(repository, FakeChunkLookup(chunks)).retrieve([table, text])

        direct_table = [item for item in result[table.candidate_id] if item.association.value == "DIRECT"]
        self.assertEqual({item.source_kind.value for item in direct_table}, {"HEADER", "CELL", "CAPTION"})
        self.assertTrue(any(item.association.value == "LINKED" for item in result[table.candidate_id]))
        self.assertTrue(any(item.association.value == "DIRECT" for item in result[text.candidate_id]))
        self.assertTrue(any(item.association.value == "LINKED" for item in result[text.candidate_id]))
        self.assertEqual(
            {item.scale_candidate for item in direct_table}, {Scale.THOUSAND, Scale.MILLION, Scale.BILLION}
        )
        self.assertEqual(
            [(item.source_span.start, item.source_span.end, item.hint_id) for item in result[table.candidate_id]],
            sorted((item.source_span.start, item.source_span.end, item.hint_id) for item in result[table.candidate_id]),
        )


if __name__ == "__main__":
    unittest.main()
