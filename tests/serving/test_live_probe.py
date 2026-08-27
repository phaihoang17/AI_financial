"""Response-validation tests for the live serving probes (ADR-057).

These use an injected transport that mimics a served model so the probe's
validation/parity logic is exercised. They are NOT proof of live serving — a
real run binds the client to an ``HttpTransport`` against a deployed endpoint.
"""

from __future__ import annotations

import math
import unittest

from src.indexing.embedding_schemas import EMBEDDING_DIMENSION
from src.serving.client import OpenAICompatibleClient
from src.serving.live_probe import (
    probe_embedding_endpoint,
    probe_reranker_endpoint,
)
from src.serving.schemas import ServingEndpoint, ServingTask


def _unit(index: int) -> list[float]:
    vector = [0.0] * EMBEDDING_DIMENSION
    vector[index % EMBEDDING_DIMENSION] = 1.0
    return vector


class _FakeTransport:
    def __init__(self, response):
        self._response = response
        self.calls: list[tuple[str, dict]] = []

    def post_json(self, url, payload, *, timeout_s):
        self.calls.append((url, dict(payload)))
        return self._response


def _embed_client(response) -> OpenAICompatibleClient:
    endpoint = ServingEndpoint("bge-m3", "http://serving:8000", "BAAI/bge-m3", ServingTask.EMBED)
    return OpenAICompatibleClient(endpoint, _FakeTransport(response))


def _score_client(response) -> OpenAICompatibleClient:
    endpoint = ServingEndpoint(
        "bge-reranker", "http://serving:8001", "BAAI/bge-reranker-v2-m3", ServingTask.SCORE
    )
    return OpenAICompatibleClient(endpoint, _FakeTransport(response))


def _embed_response(vectors) -> dict:
    return {"data": [{"index": i, "embedding": v} for i, v in enumerate(vectors)]}


def _score_response(scores) -> dict:
    return {"data": [{"index": i, "score": s} for i, s in enumerate(scores)]}


class LiveEmbeddingProbeTests(unittest.TestCase):
    def test_matching_vectors_pass_with_full_cosine(self):
        refs = [_unit(0), _unit(1)]
        client = _embed_client(_embed_response(refs))
        result = probe_embedding_endpoint(
            client, sample_texts=["a", "b"], local_vectors=refs, min_cosine=0.999
        )
        self.assertTrue(result.passed)
        self.assertEqual(result.dimension, EMBEDDING_DIMENSION)
        self.assertAlmostEqual(result.min_cosine_similarity, 1.0, places=9)
        self.assertEqual(result.to_dict()["evidence"], "LIVE")

    def test_wrong_dimension_is_a_failure(self):
        client = _embed_client(_embed_response([[1.0, 0.0, 0.0]]))
        result = probe_embedding_endpoint(
            client, sample_texts=["a"], local_vectors=[_unit(0)]
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.code, "PROBE_DIMENSION_MISMATCH")

    def test_non_finite_output_is_a_failure(self):
        bad = _unit(0)
        bad[5] = math.inf
        client = _embed_client(_embed_response([bad]))
        result = probe_embedding_endpoint(
            client, sample_texts=["a"], local_vectors=[_unit(0)]
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.code, "PROBE_NON_FINITE_OUTPUT")

    def test_count_mismatch_is_a_failure(self):
        client = _embed_client(_embed_response([_unit(0)]))
        result = probe_embedding_endpoint(
            client, sample_texts=["a", "b"], local_vectors=[_unit(0), _unit(1)]
        )
        self.assertFalse(result.passed)
        # The client rejects a served count mismatch; the probe surfaces it.
        self.assertEqual(result.code, "PROBE_TRANSPORT_FAILED")
        self.assertEqual(result.detail["serving_error"], "SERVING_PROTOCOL_INVALID")

    def test_parity_below_tolerance_is_a_failure(self):
        client = _embed_client(_embed_response([_unit(7)]))  # orthogonal to the reference
        result = probe_embedding_endpoint(
            client, sample_texts=["a"], local_vectors=[_unit(0)], min_cosine=0.999
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.code, "PROBE_PARITY_BELOW_TOLERANCE")


class LiveRerankerProbeTests(unittest.TestCase):
    def test_matching_order_passes(self):
        client = _score_client(_score_response([2.0, 0.5, -1.0]))
        result = probe_reranker_endpoint(
            client,
            sample_query="q",
            sample_documents=["a", "b", "c"],
            local_scores=[1.9, 0.4, -1.2],
        )
        self.assertTrue(result.passed)
        self.assertTrue(result.order_matches_local)

    def test_order_mismatch_is_a_failure(self):
        client = _score_client(_score_response([0.1, 0.9]))
        result = probe_reranker_endpoint(
            client, sample_query="q", sample_documents=["a", "b"], local_scores=[0.9, 0.1]
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.code, "PROBE_ORDER_MISMATCH")

    def test_count_mismatch_is_a_failure(self):
        client = _score_client(_score_response([0.1]))
        result = probe_reranker_endpoint(
            client, sample_query="q", sample_documents=["a", "b"], local_scores=[0.9, 0.1]
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.code, "PROBE_TRANSPORT_FAILED")
        self.assertEqual(result.detail["serving_error"], "SERVING_PROTOCOL_INVALID")

    def test_non_finite_score_is_a_failure(self):
        client = _score_client(_score_response([0.1, math.nan]))
        result = probe_reranker_endpoint(
            client, sample_query="q", sample_documents=["a", "b"], local_scores=[0.9, 0.1]
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.code, "PROBE_NON_FINITE_OUTPUT")


if __name__ == "__main__":
    unittest.main()
