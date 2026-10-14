from pathlib import Path
import tempfile
import unittest

from src.indexing.embedding_artifact_builder import _iter_input_records, _validate_input_manifest
from src.retrieval.batch3 import CorpusChunkLookup
from src.retrieval.corpus_candidates import CorpusCandidateError, CorpusRecordLocator
from src.retrieval.corpus_candidates import candidate_from_representation_chunk
from src.retrieval.evidence import EvidenceProvenanceRepository
from src.retrieval.vector_search import VectorSearcher
from tests.indexing.test_embedding_artifact_builder import EmbeddingArtifactBuilderTests, RecordingEncoder


class CorpusRecordLocatorTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        root = Path(self._directory.name)
        harness = EmbeddingArtifactBuilderTests()
        self.corpus_root = harness._input_artifact(root, report_count=3)
        vectors = root / "vectors"
        harness._build(self.corpus_root, vectors, max_vectors=2, encoder=RecordingEncoder())
        self.metadata = VectorSearcher(vectors, self.corpus_root).metadata_path
        self.corpus = _validate_input_manifest(self.corpus_root)
        self.full_pass = list(_iter_input_records(self.corpus))

    def tearDown(self):
        self._directory.cleanup()

    def test_positional_reads_equal_the_full_corpus_pass(self):
        locator = CorpusRecordLocator(self.corpus, self.metadata)
        keys = {(r.chunk.chunk_id, r.representation.representation_id): r for r in self.full_pass[::2]}
        found = locator.records(keys)
        self.assertEqual(set(found), set(keys))
        for key, record in found.items():
            self.assertEqual(record, keys[key])

    def test_unknown_or_mismatched_keys_are_absent(self):
        locator = CorpusRecordLocator(self.corpus, self.metadata)
        record = self.full_pass[0]
        self.assertEqual(locator.records({("missing-chunk", "rep")}), {})
        self.assertEqual(locator.records({(record.chunk.chunk_id, "other-representation")}), {})

    def test_corrupted_line_is_rejected(self):
        record = self.full_pass[0]
        path = self.corpus_root / record.source_shard
        path.write_bytes(path.read_bytes().replace(b'"source_order":0', b'"source_order":0 ', 1))
        locator = CorpusRecordLocator(self.corpus, self.metadata)
        with self.assertRaises(CorpusCandidateError):
            locator.records({(record.chunk.chunk_id, record.representation.representation_id)})

    def test_chunk_lookup_and_repository_use_positions(self):
        candidates = [candidate_from_representation_chunk(r.representation, r.chunk) for r in self.full_pass]
        lookup = CorpusChunkLookup(str(self.corpus_root), vector_metadata_path=str(self.metadata))
        legacy = CorpusChunkLookup(str(self.corpus_root))
        self.assertEqual(lookup.for_candidates(candidates), legacy.for_candidates(candidates))
        repository = EvidenceProvenanceRepository(self.corpus_root, self.corpus_root, vector_metadata_path=self.metadata)
        repository.preload(candidates)
        self.assertEqual(len(repository._records), len(self.full_pass))


if __name__ == "__main__":
    unittest.main()
