"""ADR-063: vectors are reused only for byte-identical content from a verified source."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from src.indexing.embedding_artifact_builder import EmbeddingArtifactBuilderError
from src.indexing.vector_reuse import ContentReuseEncoder
from tests.indexing import test_embedding_artifact_builder as builder_tests
from tests.indexing.test_embedding_artifact_builder import RecordingEncoder


class ContentEncoder(RecordingEncoder):
    """A distinct one-hot vector per text, so reuse is observable."""

    def encode(self, texts, *, batch_size, max_length):
        self.calls.append((len(texts), batch_size, max_length))
        vectors = []
        for text in texts:
            vector = [0.0] * 1024
            vector[int(hashlib.sha256(text.encode()).hexdigest(), 16) % 1024] = 1.0
            vectors.append(vector)
        return vectors


def contents(corpus: Path):
    manifest = json.loads((corpus / "manifest.json").read_text())
    return [
        json.loads(line)["chunk"]["content"]
        for shard in manifest["shards"]
        for line in (corpus / shard["file"]).read_text().splitlines()
    ]


class VectorReuseTests(unittest.TestCase):
    def _source(self, root: Path):
        harness = builder_tests.EmbeddingArtifactBuilderTests()
        corpus = harness._input_artifact(root, report_count=2, records_per_shard=2)
        manifest = harness._build(corpus, root / "vectors", encoder=ContentEncoder())
        return harness, corpus, root / "vectors" / manifest["artifact_directory"]

    def test_identical_content_is_reused_and_only_new_content_is_encoded(self):
        with tempfile.TemporaryDirectory() as temporary:
            harness, corpus, vectors = self._source(Path(temporary))
            texts = contents(corpus)
            inner = RecordingEncoder()
            reuse = ContentReuseEncoder(vectors, corpus, inner)
            self.assertEqual([list(v) for v in reuse.encode(texts, batch_size=4, max_length=8192)],
                             ContentEncoder().encode(texts, batch_size=4, max_length=8192))
            self.assertEqual((reuse.reused, reuse.encoded, inner.calls), (len(texts), 0, []))
            reuse.encode(["unseen", texts[0]], batch_size=4, max_length=8192)
            self.assertEqual((reuse.reused, reuse.encoded, inner.calls), (len(texts) + 1, 1, [(1, 1, 8192)]))
            # The real builder accepts it unchanged; any call to the inner encoder would fail.
            harness._build(corpus, Path(temporary) / "rebuilt",
                           encoder=ContentReuseEncoder(vectors, corpus, RecordingEncoder(fail_on_call=1)))

    def test_a_corpus_the_source_was_not_built_from_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, corpus, vectors = self._source(Path(temporary))
            manifest = corpus / "manifest.json"
            manifest.write_text(manifest.read_text() + " ")
            with self.assertRaises(EmbeddingArtifactBuilderError) as raised:
                ContentReuseEncoder(vectors, corpus, RecordingEncoder())
            self.assertEqual(raised.exception.code, "REUSE_SOURCE_INVALID")


if __name__ == "__main__":
    unittest.main()
