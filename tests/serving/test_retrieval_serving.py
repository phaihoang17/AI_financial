import math
import unittest

from src.retrieval.query_embedding import QueryEmbeddingError, embed_query_texts
from src.retrieval.reranker import RerankerError, rerank_candidates
from src.retrieval.schemas import RetrievalCandidate, RetrievalCompany, RetrievalQuery, make_candidate_id
from src.serving.client import OpenAICompatibleClient
from src.serving.schemas import ServingEndpoint, ServingError, ServingTask
from src.serving.retrieval_serving import serving_bge_reranker, serving_query_encoder
from src.supervisor.schemas import EvidenceSource


class RoutingTransport:
    """Serves embeddings, scores, and tokenize counts for the adapters."""

    def __init__(self, *, vector=None, scores=None, token_count=3):
        self.vector = vector if vector is not None else [1.0] + [0.0] * 1023
        self.scores = scores
        self.token_count = token_count
        self.calls = []

    def post_json(self, url, payload, *, timeout_s):
        self.calls.append((url, dict(payload)))
        if url.endswith("/tokenize"):
            return {"count": self.token_count}
        if url.endswith("/v1/embeddings"):
            inputs = payload["input"]
            return {"data": [{"index": i, "embedding": list(self.vector)} for i in range(len(inputs))]}
        if url.endswith("/score"):
            docs = payload["text_2"]
            scores = self.scores if self.scores is not None else [1.0] * len(docs)
            return {"data": [{"index": i, "score": scores[i]} for i in range(len(docs))]}
        raise AssertionError(url)


def embed_client(transport):
    endpoint = ServingEndpoint(
        name="bge-m3", base_url="http://localhost:8003", model_id="BAAI/bge-m3", task=ServingTask.EMBED
    )
    return OpenAICompatibleClient(endpoint, transport)


def score_client(transport):
    endpoint = ServingEndpoint(
        name="bge-reranker-v2-m3",
        base_url="http://localhost:8004",
        model_id="BAAI/bge-reranker-v2-m3",
        task=ServingTask.SCORE,
    )
    return OpenAICompatibleClient(endpoint, transport)


def make_query(*texts):
    return RetrievalQuery(
        raw_question=texts[0],
        company=RetrievalCompany(name="Test", ticker="AAA"),
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


def candidate(name, *, rrf):
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
        bm25_score=1.0,
        vector_score=2.0,
        rrf_score=rrf,
        rerank_score=None,
        rank=1,
    )


class ServingQueryEncoderTests(unittest.TestCase):
    def test_served_vectors_flow_through_existing_embedding_contract(self):
        transport = RoutingTransport()
        encoder = serving_query_encoder(embed_client(transport))
        result = embed_query_texts(make_query("doanh thu", "narrative"), encoder=encoder, batch_size=1)
        self.assertEqual([item.query_text_index for item in result], [0, 1])
        for item in result:
            self.assertEqual(item.dimension, 1024)
            self.assertEqual(item.dtype, "float32")
            self.assertAlmostEqual(math.sqrt(sum(v * v for v in item.vector)), 1.0, places=6)

    def test_over_limit_token_count_is_rejected_by_contract(self):
        transport = RoutingTransport(token_count=8193)
        encoder = serving_query_encoder(embed_client(transport))
        with self.assertRaisesRegex(QueryEmbeddingError, "EMBEDDING_INPUT_TOO_LONG"):
            embed_query_texts(make_query("doanh thu"), encoder=encoder)

    def test_encoder_requires_embed_endpoint(self):
        with self.assertRaises(ServingError) as ctx:
            serving_query_encoder(score_client(RoutingTransport()))
        self.assertEqual(ctx.exception.code, "SERVING_TASK_MISMATCH")


class ServingRerankerTests(unittest.TestCase):
    def test_served_scores_reuse_deterministic_ranking(self):
        transport = RoutingTransport(scores=[0.1, 0.9, 0.5])
        reranker = serving_bge_reranker(score_client(transport))
        candidates = [candidate("a", rrf=0.1), candidate("b", rrf=0.2), candidate("c", rrf=0.3)]
        ranked = rerank_candidates(make_query("q"), candidates, reranker=reranker, batch_size=2)
        self.assertEqual([c.chunk_id for c in ranked], ["chunk-b", "chunk-c", "chunk-a"])
        self.assertEqual([c.rank for c in ranked], [1, 2, 3])
        self.assertAlmostEqual(ranked[0].rerank_score, 0.9)

    def test_pair_over_limit_is_typed(self):
        transport = RoutingTransport(scores=[1.0], token_count=9000)
        reranker = serving_bge_reranker(score_client(transport))
        with self.assertRaises(RerankerError) as ctx:
            rerank_candidates(make_query("q"), [candidate("a", rrf=0.1)], reranker=reranker)
        self.assertEqual(ctx.exception.code, "RERANKER_INPUT_TOO_LONG")

    def test_reranker_requires_score_endpoint(self):
        with self.assertRaises(ServingError) as ctx:
            serving_bge_reranker(embed_client(RoutingTransport()))
        self.assertEqual(ctx.exception.code, "SERVING_TASK_MISMATCH")


if __name__ == "__main__":
    unittest.main()
