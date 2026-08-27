"""Tests for the full-corpus retrieval production validation runner (ADR-057).

Real small corpus/vector/BM25 artifacts are built on CPU with the deterministic
``RecordingEncoder``. The two LIVE probes are driven through an injected
transport; a real run binds them to a deployed endpoint.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from src.evaluation.retrieval_production_validation import (
    CheckStatus,
    KNOWN_SOURCE_TABLE_COUNT,
    ProductionValidationError,
    _live_embedding_check,
    _live_reranker_check,
    run_full_corpus_retrieval_validation,
    validate_retrieval_quality,
)
from src.indexing.embedding_schemas import EMBEDDING_DIMENSION
from src.retrieval.bm25 import build_bm25_index
from src.serving.client import OpenAICompatibleClient
from src.serving.live_probe import probe_embedding_endpoint, probe_reranker_endpoint
from src.serving.schemas import ServingEndpoint, ServingTask
from tests.indexing.test_embedding_artifact_builder import (
    EmbeddingArtifactBuilderTests,
    RecordingEncoder,
)


def _unit(index: int) -> list[float]:
    vector = [0.0] * EMBEDDING_DIMENSION
    vector[index % EMBEDDING_DIMENSION] = 1.0
    return vector


class _FakeTransport:
    def __init__(self, response):
        self._response = response

    def post_json(self, url, payload, *, timeout_s):
        return self._response


def _passing_embedding_probe():
    refs = [_unit(0), _unit(1)]
    endpoint = ServingEndpoint("bge-m3", "http://serving:8000", "BAAI/bge-m3", ServingTask.EMBED)
    client = OpenAICompatibleClient(
        endpoint, _FakeTransport({"data": [{"index": i, "embedding": v} for i, v in enumerate(refs)]})
    )
    return probe_embedding_endpoint(client, sample_texts=["a", "b"], local_vectors=refs)


def _passing_reranker_probe():
    endpoint = ServingEndpoint(
        "bge-reranker", "http://serving:8001", "BAAI/bge-reranker-v2-m3", ServingTask.SCORE
    )
    client = OpenAICompatibleClient(
        endpoint, _FakeTransport({"data": [{"index": i, "score": s} for i, s in enumerate([2.0, 0.1])]})
    )
    return probe_reranker_endpoint(
        client, sample_query="q", sample_documents=["a", "b"], local_scores=[1.9, 0.2]
    )


class RetrievalProductionValidationTests(unittest.TestCase):
    def _artifacts(self, root: Path, *, report_count: int = 3):
        harness = EmbeddingArtifactBuilderTests()
        corpus = harness._input_artifact(root, report_count=report_count, records_per_shard=2)
        vectors = root / "vectors"
        harness._build(corpus, vectors, encoder=RecordingEncoder())
        bm25 = root / "bm25"
        build_bm25_index(corpus, bm25)
        corpus_manifest = json.loads((corpus / "manifest.json").read_text())
        return corpus, vectors, bm25, corpus_manifest

    def _run(self, corpus, vectors, bm25, manifest, **kwargs):
        kwargs.setdefault("expected_canonical_chunk_count", manifest["chunk_count"])
        return run_full_corpus_retrieval_validation(corpus, vectors, bm25, **kwargs)

    def _check(self, report, name):
        return next(c for c in report.artifact_serving_checks if c.name == name)

    # --- chunk/vector count gate -------------------------------------------- #

    def test_correct_chunk_vector_count_gate_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus, vectors, bm25, manifest = self._artifacts(Path(directory))
            report = self._run(corpus, vectors, bm25, manifest)
        gate = self._check(report, "vector_count_equals_canonical_chunk_count")
        self.assertEqual(gate.status, CheckStatus.PASS)
        self.assertEqual(gate.detail["vector_count"], manifest["chunk_count"])
        self.assertEqual(self._check(report, "canonical_corpus_chunk_count").status, CheckStatus.PASS)

    def test_table_count_must_never_be_substituted_for_vector_count(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus, vectors, bm25, manifest = self._artifacts(Path(directory))
            by_source_table_count = self._run(
                corpus, vectors, bm25, manifest,
                operator_expected_vector_count=KNOWN_SOURCE_TABLE_COUNT,
            )
            by_table_representation = self._run(
                corpus, vectors, bm25, manifest,
                operator_expected_vector_count=manifest["table_representation_count"],
            )
        for report in (by_source_table_count, by_table_representation):
            check = self._check(report, "no_table_count_substitution")
            self.assertEqual(check.status, CheckStatus.FAIL)
            self.assertEqual(check.code, "TABLE_COUNT_SUBSTITUTED_FOR_VECTOR_COUNT")

    def test_vector_count_mismatch_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus, vectors, bm25, manifest = self._artifacts(Path(directory))
            report = self._run(
                corpus, vectors, bm25, manifest,
                operator_expected_vector_count=manifest["chunk_count"] + 1,
            )
        gate = self._check(report, "vector_count_equals_canonical_chunk_count")
        self.assertEqual(gate.status, CheckStatus.FAIL)
        self.assertEqual(gate.code, "VECTOR_COUNT_MISMATCH")

    def test_non_canonical_corpus_chunk_count_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus, vectors, bm25, manifest = self._artifacts(Path(directory))
            # Use the real default (1,743,311); the fixture corpus has far fewer.
            report = run_full_corpus_retrieval_validation(corpus, vectors, bm25)
        check = self._check(report, "canonical_corpus_chunk_count")
        self.assertEqual(check.status, CheckStatus.FAIL)
        self.assertEqual(check.code, "CANONICAL_CHUNK_COUNT_MISMATCH")

    # --- fingerprint / shard integrity ----------------------------------- #

    def test_embedding_fingerprint_mismatch_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus, vectors, bm25, manifest = self._artifacts(Path(directory))
            path = vectors / "manifest.json"
            payload = json.loads(path.read_text())
            payload["embedding_fingerprint"] = "0" * 63
            path.write_text(json.dumps(payload), encoding="utf-8")
            report = self._run(corpus, vectors, bm25, manifest)
        check = self._check(report, "embedding_fingerprint")
        self.assertEqual(check.status, CheckStatus.FAIL)
        self.assertEqual(check.code, "EMBEDDING_FINGERPRINT_MISMATCH")

    def test_corrupt_shard_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus, vectors, bm25, manifest = self._artifacts(Path(directory))
            vector_manifest = json.loads((vectors / "manifest.json").read_text())
            shard_rel = vector_manifest["shards"][0]["file"]
            shard_path = vectors / vector_manifest["artifact_directory"] / shard_rel
            shard_path.write_bytes(b"corrupt")
            report = self._run(corpus, vectors, bm25, manifest)
        check = self._check(report, "shard_and_sqlite_integrity")
        self.assertEqual(check.status, CheckStatus.FAIL)
        self.assertEqual(check.code, "SHARD_CORRUPT_OR_MISSING")

    # --- live serving gate --------------------------------------------------- #

    def test_artifact_only_run_cannot_clear_the_gpu_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus, vectors, bm25, manifest = self._artifacts(Path(directory))
            report = self._run(corpus, vectors, bm25, manifest)
        self.assertEqual(report.artifact_status, "PASS")
        self.assertEqual(report.live_serving_status, "NOT_VALIDATED")
        self.assertFalse(report.clears_gpu_pending)
        self.assertEqual(report.overall_status, "FAIL")

    def test_full_pass_requires_live_probes_and_smoke(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus, vectors, bm25, manifest = self._artifacts(Path(directory))
            report = self._run(
                corpus, vectors, bm25, manifest,
                embedding_probe_result=_passing_embedding_probe(),
                reranker_probe_result=_passing_reranker_probe(),
                retrieval_smoke={"ran": True, "byte_identical": True, "crashes": 0, "timeouts": 0, "sample_size": 5},
            )
        self.assertEqual(report.overall_status, "PASS")
        self.assertEqual(report.live_serving_status, "PASS")
        self.assertTrue(report.clears_gpu_pending)

    def test_non_deterministic_smoke_blocks_the_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus, vectors, bm25, manifest = self._artifacts(Path(directory))
            report = self._run(
                corpus, vectors, bm25, manifest,
                embedding_probe_result=_passing_embedding_probe(),
                reranker_probe_result=_passing_reranker_probe(),
                retrieval_smoke={"ran": True, "byte_identical": False, "crashes": 0, "timeouts": 0, "sample_size": 5},
            )
        smoke = self._check(report, "deterministic_retrieval_execution")
        self.assertEqual(smoke.status, CheckStatus.FAIL)
        self.assertEqual(smoke.code, "RETRIEVAL_EXECUTION_NOT_DETERMINISTIC")
        self.assertEqual(report.live_serving_status, "FAIL")
        self.assertFalse(report.clears_gpu_pending)

    def test_missing_smoke_is_skipped_not_passed(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus, vectors, bm25, manifest = self._artifacts(Path(directory))
            report = self._run(
                corpus, vectors, bm25, manifest,
                embedding_probe_result=_passing_embedding_probe(),
                reranker_probe_result=_passing_reranker_probe(),
            )
        smoke = self._check(report, "deterministic_retrieval_execution")
        self.assertEqual(smoke.status, CheckStatus.SKIPPED)
        self.assertEqual(report.live_serving_status, "NOT_VALIDATED")
        self.assertFalse(report.clears_gpu_pending)

    def test_failed_live_probe_blocks_the_flag(self):
        endpoint = ServingEndpoint("bge-m3", "http://serving:8000", "BAAI/bge-m3", ServingTask.EMBED)
        client = OpenAICompatibleClient(
            endpoint, _FakeTransport({"data": [{"index": 0, "embedding": _unit(9)}]})
        )
        failed = probe_embedding_endpoint(client, sample_texts=["a"], local_vectors=[_unit(0)])
        with tempfile.TemporaryDirectory() as directory:
            corpus, vectors, bm25, manifest = self._artifacts(Path(directory))
            report = self._run(
                corpus, vectors, bm25, manifest,
                embedding_probe_result=failed,
                reranker_probe_result=_passing_reranker_probe(),
                retrieval_smoke={"ran": True, "byte_identical": True, "crashes": 0, "timeouts": 0, "sample_size": 5},
            )
        self.assertEqual(report.live_serving_status, "FAIL")
        self.assertFalse(report.clears_gpu_pending)

    def test_fixture_output_cannot_satisfy_a_live_gate(self):
        with self.assertRaises(ProductionValidationError) as raised:
            _live_embedding_check({"status": "PASS", "source": "FIXTURE"})
        self.assertEqual(raised.exception.code, "LIVE_EVIDENCE_REQUIRES_PROBE_RESULT")

        from src.evaluation.retrieval import RetrievalEvaluationMode, evaluate_cases
        from src.evaluation.run_retrieval_eval import fixture_cases

        fixture_report = evaluate_cases(RetrievalEvaluationMode.FIXTURE, fixture_cases())
        with self.assertRaises(ProductionValidationError) as raised:
            _live_reranker_check(fixture_report)
        self.assertEqual(raised.exception.code, "LIVE_EVIDENCE_REQUIRES_PROBE_RESULT")

    # --- retrieval quality (gold-gated) ----------------------------------- #

    def test_missing_gold_data_is_blocked(self):
        check = validate_retrieval_quality(None)
        self.assertEqual(check.status, CheckStatus.BLOCKED)
        self.assertEqual(check.code, "BLOCKED_GOLD_DATA")

    def test_questions_only_file_is_blocked_gold_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "questions.jsonl"
            path.write_text(
                '{"id": 1, "question": "x?"}\n{"id": 2, "question": "y?"}\n', encoding="utf-8"
            )
            check = validate_retrieval_quality(path)
        self.assertEqual(check.status, CheckStatus.BLOCKED)
        self.assertEqual(check.code, "BLOCKED_GOLD_DATA")

    def test_report_is_json_serializable_and_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus, vectors, bm25, manifest = self._artifacts(Path(directory))
            first = self._run(corpus, vectors, bm25, manifest).to_dict()
            second = self._run(corpus, vectors, bm25, manifest).to_dict()
        self.assertEqual(first, second)
        self.assertEqual(json.loads(json.dumps(first, sort_keys=True)), first)
        self.assertEqual(first["gpu_pending_flag"], "GPU_PRODUCTION_VALIDATION_PENDING")


if __name__ == "__main__":
    unittest.main()
