import unittest

from src.indexing.embedding_chunker import build_embedding_chunks
from src.indexing.embedding_indexer import (
    EmbeddingError,
    embed_chunks,
    make_embedding_config_fingerprint,
)

from tests.indexing.test_embedding_chunker import CharTokenizer, build_fixture


class FixedEncoder:
    def __init__(self, dimension=1024):
        self.dimension = dimension

    def encode(self, texts, *, batch_size, max_length):
        return [[1.0] + [0.0] * (self.dimension - 1) for _ in texts]


class EmbeddingIndexerTests(unittest.TestCase):
    def test_document_embedding_is_normalized_and_traceable(self):
        tables, representations = build_fixture()
        chunks = build_embedding_chunks(
            representations,
            {table.normalized_table_id: table for table in tables},
            CharTokenizer(),
        )
        fingerprint = make_embedding_config_fingerprint()
        records = embed_chunks(
            chunks,
            encoder=FixedEncoder(),
            model_fingerprint=fingerprint,
        )
        self.assertEqual(len(records), len(chunks))
        self.assertEqual(records[0].chunk_id, chunks[0].chunk_id)
        self.assertEqual(records[0].representation_id, chunks[0].representation_id)
        self.assertEqual(records[0].dimension, 1024)
        self.assertEqual(records[0].vector[0], 1.0)
        self.assertEqual(sum(value * value for value in records[0].vector), 1.0)

    def test_wrong_encoder_dimension_fails_before_persistence(self):
        tables, representations = build_fixture()
        chunks = build_embedding_chunks(
            [representations[0]],
            {tables[0].normalized_table_id: tables[0]},
            CharTokenizer(),
        )
        with self.assertRaisesRegex(EmbeddingError, "EMBEDDING_DIMENSION_MISMATCH"):
            embed_chunks(chunks, encoder=FixedEncoder(8))

    def test_duplicate_chunk_ids_are_rejected(self):
        tables, representations = build_fixture()
        chunks = build_embedding_chunks(
            [representations[0]],
            {tables[0].normalized_table_id: tables[0]},
            CharTokenizer(),
        )
        with self.assertRaisesRegex(EmbeddingError, "DUPLICATE_CHUNK_ID"):
            embed_chunks([chunks[0], chunks[0]], encoder=FixedEncoder())


if __name__ == "__main__":
    unittest.main()

