import json
from pathlib import Path
import tempfile
import unittest

from src.retrieval.compatibility import (
    ArtifactCompatibilityError,
    validate_retrieval_artifact_compatibility,
)
from tests.indexing.test_embedding_artifact_builder import (
    EmbeddingArtifactBuilderTests,
    RecordingEncoder,
)


class ArtifactCompatibilityTests(unittest.TestCase):
    def _artifacts(self, root: Path):
        harness = EmbeddingArtifactBuilderTests()
        corpus = harness._input_artifact(root, report_count=1, records_per_shard=2)
        vectors = root / "vectors"
        harness._build(corpus, vectors, encoder=RecordingEncoder())
        return corpus, vectors

    def _assert_failure(self, expected_code, mutate):
        with tempfile.TemporaryDirectory() as directory:
            corpus, vectors = self._artifacts(Path(directory))
            mutate(corpus, vectors)
            with self.assertRaises(ArtifactCompatibilityError) as raised:
                validate_retrieval_artifact_compatibility(vectors, corpus)
        self.assertEqual(raised.exception.code, expected_code)

    def test_compatible_fixture_artifact_passes_without_production_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus, vectors = self._artifacts(Path(directory))

            report = validate_retrieval_artifact_compatibility(vectors, corpus)

        self.assertEqual(report.status, "PASS")
        self.assertEqual(report.vector_count, 2)
        self.assertEqual(len(report.artifact_fingerprint), 64)

    def test_model_id_mismatch_is_a_hard_failure(self):
        def mutate(_, vectors):
            path = vectors / "manifest.json"
            payload = json.loads(path.read_text())
            payload["model_id"] = "other-model"
            path.write_text(json.dumps(payload))

        self._assert_failure("MODEL_ID_MISMATCH", mutate)

    def test_model_revision_mismatch_is_a_hard_failure(self):
        def mutate(_, vectors):
            path = vectors / "manifest.json"
            payload = json.loads(path.read_text())
            payload["model_revision"] = "0" * 40
            path.write_text(json.dumps(payload))

        self._assert_failure("MODEL_REVISION_MISMATCH", mutate)

    def test_vector_dimension_mismatch_is_a_hard_failure(self):
        def mutate(_, vectors):
            path = vectors / "manifest.json"
            payload = json.loads(path.read_text())
            payload["dimension"] = 384
            path.write_text(json.dumps(payload))

        self._assert_failure("VECTOR_DIMENSION_MISMATCH", mutate)

    def test_embedding_fingerprint_mismatch_is_a_hard_failure(self):
        def mutate(_, vectors):
            path = vectors / "manifest.json"
            payload = json.loads(path.read_text())
            payload["embedding_fingerprint"] = "0" * 64
            path.write_text(json.dumps(payload))

        self._assert_failure("EMBEDDING_FINGERPRINT_MISMATCH", mutate)

    def test_corpus_schema_mismatch_is_a_hard_failure(self):
        def mutate(corpus, _):
            path = corpus / "manifest.json"
            payload = json.loads(path.read_text())
            payload["representation_version"] = "m2a-representation-v2"
            path.write_text(json.dumps(payload))

        self._assert_failure("M2_CORPUS_SCHEMA_MISMATCH", mutate)

    def test_missing_required_metadata_is_a_hard_failure(self):
        def mutate(_, vectors):
            path = vectors / "manifest.json"
            payload = json.loads(path.read_text())
            del payload["dtype"]
            path.write_text(json.dumps(payload))

        self._assert_failure("COMPATIBILITY_METADATA_MISSING", mutate)

    def test_real_smoke_artifact_is_accepted_when_available(self):
        repository = Path(__file__).resolve().parents[2]
        corpus = repository / "artifacts/m2-vector-index-v1/smoke-input-500"
        vectors = repository / "artifacts/m2-vector-index-v1/smoke-resume"
        if not (corpus / "manifest.json").is_file() or not (vectors / "manifest.json").is_file():
            self.skipTest("local real-BGE smoke artifacts are not available")

        report = validate_retrieval_artifact_compatibility(vectors, corpus)

        self.assertEqual(report.status, "PASS")
        self.assertEqual(report.vector_count, 500)


if __name__ == "__main__":
    unittest.main()
