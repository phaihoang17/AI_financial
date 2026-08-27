"""Tests for the vector-build preflight and batch-size calibration (ADR-057).

CPU-only, deterministic. The preflight and calibration harness must never load
a model and must never publish or mutate an artifact.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from src.indexing.embedding_artifact_builder import EmbeddingArtifactBuilderError
from src.indexing.embedding_batch_calibration import (
    MAX_CALIBRATION_SAMPLE,
    calibrate_batch_sizes,
    load_calibration_sample,
    recommend_batch_size,
)
from src.indexing.embedding_build_preflight import preflight_vector_build

# Import as a module (not `from ... import EmbeddingArtifactBuilderTests`) so
# pytest does not re-collect that TestCase inside this file.
import tests.indexing.test_embedding_artifact_builder as _builder_tests

RecordingEncoder = _builder_tests.RecordingEncoder

_SHARD = 3


def _make_corpus(root: Path) -> Path:
    return _builder_tests.EmbeddingArtifactBuilderTests()._input_artifact(
        root, report_count=4, records_per_shard=2
    )


def _build(corpus: Path, out: Path, *, encoder, max_vectors: int = _SHARD):
    return _builder_tests.EmbeddingArtifactBuilderTests()._build(
        corpus, out, encoder=encoder, max_vectors=max_vectors
    )


class VectorBuildPreflightTests(unittest.TestCase):
    def test_fresh_when_nothing_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            corpus = _make_corpus(root)
            result = preflight_vector_build(corpus, root / "vec", max_vectors_per_shard=_SHARD)
        self.assertEqual(result["mode"], "FRESH")
        self.assertFalse(result["use_resume_flag"])
        self.assertFalse(result["staging_conflict"])
        self.assertEqual(len(result["build_id"]), 64)

    def test_already_published_after_a_completed_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            corpus = _make_corpus(root)
            out = root / "vec"
            _build(corpus, out, encoder=RecordingEncoder())
            result = preflight_vector_build(corpus, out, max_vectors_per_shard=_SHARD)
        self.assertEqual(result["mode"], "ALREADY_PUBLISHED")
        self.assertFalse(result["use_resume_flag"])

    def test_resume_when_a_compatible_checkpoint_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            corpus = _make_corpus(root)
            out = root / "vec"
            with self.assertRaises(EmbeddingArtifactBuilderError):
                _build(corpus, out, encoder=RecordingEncoder(fail_on_call=1))
            result = preflight_vector_build(corpus, out, max_vectors_per_shard=_SHARD)
        self.assertEqual(result["mode"], "RESUME")
        self.assertTrue(result["use_resume_flag"])

    def test_incompatible_checkpoint_is_a_fresh_with_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            corpus = _make_corpus(root)
            out = root / "vec"
            with self.assertRaises(EmbeddingArtifactBuilderError):
                _build(corpus, out, encoder=RecordingEncoder(fail_on_call=1))
            preview = preflight_vector_build(corpus, out, max_vectors_per_shard=_SHARD)
            checkpoint = out / ".staging" / preview["build_id"] / "checkpoint.json"
            payload = json.loads(checkpoint.read_text())
            payload["corpus_fingerprint"] = "0" * 64
            checkpoint.write_text(json.dumps(payload), encoding="utf-8")
            result = preflight_vector_build(corpus, out, max_vectors_per_shard=_SHARD)
        self.assertEqual(result["mode"], "FRESH")
        self.assertTrue(result["staging_conflict"])
        self.assertFalse(result["use_resume_flag"])

    def test_preflight_never_writes_to_output_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            corpus = _make_corpus(root)
            out = root / "vec"
            preflight_vector_build(corpus, out, max_vectors_per_shard=_SHARD)
            self.assertFalse(out.exists())


class BatchCalibrationTests(unittest.TestCase):
    def _sample(self, root: Path, n: int):
        corpus = _make_corpus(root)
        return corpus, load_calibration_sample(corpus, n)

    def test_sample_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            _, sample = self._sample(Path(directory), MAX_CALIBRATION_SAMPLE + 5_000)
        # The fixture corpus only has a handful of chunks; the cap still applies.
        self.assertLessEqual(len(sample), MAX_CALIBRATION_SAMPLE)
        self.assertGreater(len(sample), 0)

    def test_calibration_records_per_batch_and_ignores_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            corpus, sample = self._sample(root, 8)
            before = sorted(p.name for p in root.iterdir())
            results = calibrate_batch_sizes(sample, RecordingEncoder(), [2, 4], device="cpu")
            after = sorted(p.name for p in root.iterdir())
        self.assertEqual(before, after)  # nothing published/mutated
        self.assertEqual([r["batch_size"] for r in results], [2, 4])
        self.assertTrue(all(r["ok"] for r in results))
        self.assertTrue(all(r["chunks"] == len(sample) for r in results))
        self.assertTrue(all(r["peak_vram_bytes"] is None for r in results))  # cpu

    def test_failing_batch_is_recorded_not_raised(self):
        with tempfile.TemporaryDirectory() as directory:
            _, sample = self._sample(Path(directory), 8)
            results = calibrate_batch_sizes(
                sample, RecordingEncoder(fail_on_call=1), [2], device="cpu"
            )
        self.assertFalse(results[0]["ok"])
        self.assertIsNotNone(results[0]["error"])
        self.assertIsNone(results[0]["chunks_per_sec"])

    def test_recommend_prefers_smaller_without_speedup(self):
        results = [
            {"batch_size": 8, "ok": True, "chunks_per_sec": 1000.0, "peak_vram_bytes": 1},
            {"batch_size": 16, "ok": True, "chunks_per_sec": 1010.0, "peak_vram_bytes": 2},
            {"batch_size": 32, "ok": True, "chunks_per_sec": 1015.0, "peak_vram_bytes": 3},
        ]
        self.assertEqual(recommend_batch_size(results)["recommended_batch_size"], 8)

    def test_recommend_takes_larger_on_real_speedup(self):
        results = [
            {"batch_size": 8, "ok": True, "chunks_per_sec": 1000.0, "peak_vram_bytes": 1},
            {"batch_size": 16, "ok": True, "chunks_per_sec": 1400.0, "peak_vram_bytes": 2},
            {"batch_size": 32, "ok": False, "chunks_per_sec": None, "peak_vram_bytes": None},
        ]
        rec = recommend_batch_size(results)
        self.assertEqual(rec["recommended_batch_size"], 16)

    def test_recommend_none_when_all_failed(self):
        results = [{"batch_size": 8, "ok": False, "chunks_per_sec": None, "peak_vram_bytes": None}]
        self.assertIsNone(recommend_batch_size(results)["recommended_batch_size"])


if __name__ == "__main__":
    unittest.main()
