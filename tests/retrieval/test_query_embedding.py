import math
import os
import unittest

from src.indexing.embedding_artifact_builder import APPROVED_EMBEDDING_FINGERPRINT
from src.retrieval.query_embedding import QueryEmbeddingError, embed_query_texts
from src.retrieval.schemas import RetrievalCompany, RetrievalQuery
from src.supervisor.schemas import EvidenceSource


def make_query(*texts: str) -> RetrievalQuery:
    return RetrievalQuery(
        raw_question=texts[0],
        company=RetrievalCompany(name="Test Company", ticker="AAA"),
        periods=["2024"],
        period_kind=None,
        statement_scope=None,
        target_metrics=["Doanh thu"],
        derived_target=None,
        evidence_sources=[EvidenceSource.TABLE],
        requested_scale=None,
        requested_unit=None,
        query_texts=list(texts),
    )


class RecordingQueryEncoder:
    def __init__(self, *, token_counts=None, vectors=None):
        self.token_counts = token_counts
        self.vectors = vectors
        self.calls = []

    def count_tokens(self, texts, *, max_length):
        self.calls.append(("count", list(texts), max_length))
        return self.token_counts if self.token_counts is not None else [2 for _ in texts]

    def encode(self, texts, *, batch_size, max_length):
        self.calls.append(("encode", list(texts), batch_size, max_length))
        if self.vectors is not None:
            return self.vectors
        return [[1.0] + [0.0] * 1023 for _ in texts]


class QueryEmbeddingTests(unittest.TestCase):
    def test_mocked_embeddings_are_ordered_float32_normalized_and_deterministic(self):
        query = make_query("doanh thu", "narrative")
        first = embed_query_texts(query, encoder=RecordingQueryEncoder(), batch_size=2)
        second = embed_query_texts(query, encoder=RecordingQueryEncoder(), batch_size=2)

        self.assertEqual([item.to_dict() for item in first], [item.to_dict() for item in second])
        self.assertEqual([item.query_text_index for item in first], [0, 1])
        self.assertEqual([item.query_text for item in first], list(query.query_texts))
        for item in first:
            self.assertEqual(item.dimension, 1024)
            self.assertEqual(item.dtype, "float32")
            self.assertEqual(item.embedding_fingerprint, APPROVED_EMBEDDING_FINGERPRINT)
            self.assertTrue(all(math.isfinite(value) for value in item.vector))
            self.assertAlmostEqual(math.sqrt(sum(value * value for value in item.vector)), 1.0, places=6)

    def test_over_limit_and_incompatible_fingerprint_are_typed_failures(self):
        query = make_query("doanh thu")
        with self.assertRaisesRegex(QueryEmbeddingError, "EMBEDDING_INPUT_TOO_LONG"):
            embed_query_texts(query, encoder=RecordingQueryEncoder(token_counts=[8193]))
        with self.assertRaisesRegex(QueryEmbeddingError, "EMBEDDING_FINGERPRINT_MISMATCH"):
            embed_query_texts(
                query,
                encoder=RecordingQueryEncoder(),
                embedding_fingerprint="0" * 64,
            )

    @unittest.skipUnless(
        os.environ.get("RUN_REAL_BGE_SMOKE") == "1",
        "set RUN_REAL_BGE_SMOKE=1 to run the local pinned BGE-M3 smoke",
    )
    def test_real_pinned_bge_m3_smoke(self):
        result = embed_query_texts(make_query("Doanh thu AAA năm 2024"), device="cpu", batch_size=1)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].dimension, 1024)
        self.assertEqual(result[0].dtype, "float32")
        self.assertAlmostEqual(math.sqrt(sum(value * value for value in result[0].vector)), 1.0, places=5)


if __name__ == "__main__":
    unittest.main()
