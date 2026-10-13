from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.evaluation import run_live_retrieval_eval as runner
from src.evaluation.gold_retrieval import GoldRetrievalCase


def case():
    return GoldRetrievalCase.from_dict({
        "question_id": "q1", "question": "Doanh thu AAA năm 2023?",
        "company": {"ticker": "AAA", "name": "Company A"},
        "period": "2023", "period_kind": "NAM", "statement_scope": "HOP_NHAT",
        "metric": "Doanh thu", "operation": "LOOKUP",
        "gold_report_id": "expected-report", "gold_table_id": "expected-table",
        "gold_chunk_ids": ["expected-chunk"], "gold_source_cell_ids": ["expected-cell"],
        "gold_answer": {"value": "123456", "unit": "VND", "scale": "RAW"},
        "provenance": {"source": "test", "method": "MANUAL"},
        "annotation_status": "VERIFIED",
    })


def test_query_is_independent_of_gold_answer_and_evidence():
    original = case()
    changed = replace(original, gold_answer=None, gold_report_id="other-report",
                      gold_table_id="other-table", gold_chunk_ids=("other-chunk",),
                      gold_source_cell_ids=("other-cell",))
    assert runner.annotated_query(original).to_dict() == runner.annotated_query(changed).to_dict()
    assert "123456" not in str(runner.annotated_query(original).to_dict())


def test_backend_crash_remains_zero_recall_in_denominator():
    backend = SimpleNamespace(artifact_fingerprint="bm25", search=lambda *a, **kw: [])
    vector = SimpleNamespace(artifact_fingerprint="vector")
    vector.search = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("broken shard"))
    with patch.object(runner, "BM25Index", return_value=backend), \
         patch.object(runner, "VectorSearcher", return_value=vector), \
         patch.object(runner, "load_bge_m3_encoder"), \
         patch.object(runner, "embed_query_texts", return_value=[]), \
         patch.object(runner, "load_bge_reranker"):
        report = runner.run([case()], corpus_root="c", vector_root="v", bm25_root="b")
    assert report["status"] == "COMPLETED_WITH_ERRORS"
    assert report["errors"][0]["stage"] == "vector_rrf"
    assert report["metrics_by_stage"]["rerank"]["evaluated_case_count"] == 1
    assert report["metrics_by_stage"]["rerank"]["recall_at_k"]["10"] == 0
    assert report["is_e2e"] is False
    assert report["establishes_task100_baseline"] is False


def test_invalid_input_does_not_load_models():
    with patch.object(runner, "load_bge_m3_encoder") as loader:
        with pytest.raises(ValueError):
            runner.run([], corpus_root="c", vector_root="v", bm25_root="b")
        with pytest.raises(ValueError):
            runner.run([case(), case()], corpus_root="c", vector_root="v", bm25_root="b")
        loader.assert_not_called()
