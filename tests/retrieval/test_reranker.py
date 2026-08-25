from dataclasses import replace
import math
import os
from types import SimpleNamespace
import unittest

from src.retrieval.reranker import (
    BGEReranker,
    BGE_RERANKER_MODEL_ID,
    BGE_RERANKER_REVISION,
    BGE_RERANKER_TOKENIZER_CLASS,
    RERANKER_CONFIG_FINGERPRINT,
    RERANKER_MAX_PAIR_TOKENS,
    RerankerError,
    load_bge_reranker,
    make_reranker_config_fingerprint,
    rerank_candidates,
)
from src.retrieval.schemas import RetrievalCandidate, RetrievalCompany, RetrievalQuery, make_candidate_id
from src.supervisor.schemas import EvidenceSource


def query() -> RetrievalQuery:
    return RetrievalQuery(
        raw_question="raw question only",
        company=RetrievalCompany(name="Test", ticker="AAA"),
        periods=["2024"],
        period_kind=None,
        statement_scope=None,
        target_metrics=["metric"],
        derived_target=None,
        evidence_sources=[EvidenceSource.TABLE],
        requested_scale=None,
        requested_unit=None,
        query_texts=["different lexical query"],
    )


def candidate(name: str, *, rrf: float, bm25=1.0, vector=2.0) -> RetrievalCandidate:
    representation_id = f"representation-{name}"
    return RetrievalCandidate(
        candidate_id=make_candidate_id(EvidenceSource.TABLE, representation_id, f"chunk-{name}"),
        representation_id=representation_id,
        chunk_id=f"chunk-{name}",
        source_type=EvidenceSource.TABLE,
        report_id="report",
        page_ids=["page"],
        table_id="table",
        paragraph_id=None,
        ticker="AAA",
        company_name="Test",
        report_year=2024,
        statement_scope=None,
        period_labels=["2024"],
        row_paths=[],
        column_paths=[],
        content=f"document {name}",
        bm25_score=bm25,
        vector_score=vector,
        rrf_score=rrf,
        rerank_score=None,
        rank=1,
    )


class FakeTokenizer:
    def __init__(self, lengths=None):
        self.lengths = lengths or {}
        self.calls = []

    def __call__(self, query, document, **kwargs):
        self.calls.append((query, document, kwargs))
        if isinstance(document, list):
            lengths = [self.lengths.get(value, 4) for value in document]
            return {
                "input_ids": FakeTensor([[0] * max(lengths) for _ in document]),
                "attention_mask": FakeTensor([[1] * max(lengths) for _ in document]),
            }
        return {"input_ids": [0] * self.lengths.get(document, 4)}


class FakeTensor:
    def __init__(self, values):
        self.values = values
        self.shape = (len(values), len(values[0]) if values else 0)

    def to(self, _):
        return self


class FakeScores:
    def __init__(self, values):
        self.values = values

    def __getitem__(self, _):
        return self

    def detach(self):
        return self

    def cpu(self):
        return self

    def float(self):
        return self

    def tolist(self):
        return list(self.values)


class FakeTorch:
    class _NoGrad:
        def __enter__(self):
            return None

        def __exit__(self, *_):
            return False

    @staticmethod
    def no_grad():
        return FakeTorch._NoGrad()


class FakeModel:
    def __init__(self, scores):
        self.scores = list(scores)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        length = kwargs["input_ids"].shape[0]
        scores = self.scores[:length]
        self.scores = self.scores[length:]
        return SimpleNamespace(logits=FakeScores(scores))


def fake_reranker(tokenizer, model):
    return BGEReranker(tokenizer, model, torch_module=FakeTorch())


class RerankerTests(unittest.TestCase):
    def test_pinned_fingerprint_and_raw_question_pairing(self):
        tokenizer = FakeTokenizer()
        model = FakeModel([0.25])
        result = rerank_candidates(query(), [candidate("a", rrf=0.1)], reranker=fake_reranker(tokenizer, model))

        self.assertEqual(make_reranker_config_fingerprint(), RERANKER_CONFIG_FINGERPRINT)
        self.assertEqual(BGE_RERANKER_MODEL_ID, "BAAI/bge-reranker-v2-m3")
        self.assertEqual(BGE_RERANKER_REVISION, "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e")
        self.assertEqual(BGE_RERANKER_TOKENIZER_CLASS, "XLMRobertaTokenizerFast")
        self.assertEqual(result[0].rerank_score, 0.25)
        self.assertEqual(tokenizer.calls[0][0], "raw question only")
        self.assertFalse(tokenizer.calls[0][2]["truncation"])
        self.assertTrue(tokenizer.calls[0][2]["add_special_tokens"])

    def test_raw_logits_prior_scores_and_deterministic_order_are_preserved(self):
        first = candidate("a", rrf=0.1, bm25=3.0, vector=4.0)
        second = candidate("b", rrf=0.2, bm25=5.0, vector=6.0)
        result = rerank_candidates(
            query(), [first, second], reranker=fake_reranker(FakeTokenizer(), FakeModel([1.0, 1.0]))
        )

        self.assertEqual(result[0].candidate_id, second.candidate_id)
        self.assertEqual(result[0].rerank_score, 1.0)
        self.assertEqual(result[0].rrf_score, 0.2)
        self.assertEqual(result[0].bm25_score, 5.0)
        self.assertEqual(result[0].vector_score, 6.0)
        self.assertEqual([item.rank for item in result], [1, 2])

    def test_8192_is_accepted_8193_is_rejected_without_truncation(self):
        accepted = candidate("accepted", rrf=0.1)
        rejected = candidate("rejected", rrf=0.1)
        tokenizer = FakeTokenizer(
            {accepted.content: RERANKER_MAX_PAIR_TOKENS, rejected.content: RERANKER_MAX_PAIR_TOKENS + 1}
        )
        rerank_candidates(query(), [accepted], reranker=fake_reranker(tokenizer, FakeModel([0.0])))
        with self.assertRaisesRegex(RerankerError, "RERANKER_INPUT_TOO_LONG"):
            rerank_candidates(query(), [rejected], reranker=fake_reranker(tokenizer, FakeModel([0.0])))
        self.assertTrue(all(call[2]["truncation"] is False for call in tokenizer.calls))

    def test_missing_rrf_and_unpinned_config_are_typed_failures(self):
        with self.assertRaisesRegex(RerankerError, "RERANKER_RRF_SCORE_MISSING"):
            rerank_candidates(
                query(),
                [replace(candidate("a", rrf=0.1), rrf_score=None)],
                reranker=fake_reranker(FakeTokenizer(), FakeModel([0.0])),
            )
        with self.assertRaisesRegex(RerankerError, "RERANKER_CONFIG_MISMATCH"):
            BGEReranker(FakeTokenizer(), FakeModel([0.0]), config_fingerprint="0" * 64)

    @unittest.skipUnless(
        os.environ.get("RUN_REAL_RERANKER_SMOKE") == "1",
        "set RUN_REAL_RERANKER_SMOKE=1 to run the pinned reranker smoke",
    )
    def test_real_pinned_reranker_smoke(self):
        try:
            reranker = load_bge_reranker(device="cpu")
        except RerankerError as error:
            self.skipTest(f"pinned reranker unavailable: {error.code}")
        result = rerank_candidates(
            query(), [candidate("real", rrf=0.1)], reranker=reranker, batch_size=1
        )
        self.assertEqual(len(result), 1)
        self.assertTrue(math.isfinite(result[0].rerank_score))


if __name__ == "__main__":
    unittest.main()
