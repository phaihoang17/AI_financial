import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.indexing.embedding_chunker import CHUNKING_CONFIG_FINGERPRINT
from src.indexing.embedding_indexer import embed_chunks, make_embedding_config_fingerprint
from src.indexing.vector_index_store import (
    IndexStorageError,
    build_sharded_index,
    load_sharded_manifest,
)

from tests.indexing.test_embedding_chunker import CharTokenizer, build_fixture


class FixedEncoder:
    def encode(self, texts, *, batch_size, max_length):
        return [[1.0] + [0.0] * 1023 for _ in texts]


class VectorIndexStoreTests(unittest.TestCase):
    def build_artifact(self, root: Path):
        tables, representations = build_fixture()
        from src.indexing.embedding_chunker import build_embedding_chunks
        chunks = build_embedding_chunks(
            representations,
            {table.normalized_table_id: table for table in tables},
            CharTokenizer(),
        )
        fingerprint = make_embedding_config_fingerprint()
        records = embed_chunks(chunks, encoder=FixedEncoder(), model_fingerprint=fingerprint)
        manifest = build_sharded_index(
            records,
            chunks,
            representations,
            root,
            model_fingerprint=fingerprint,
            chunking_config_fingerprint=CHUNKING_CONFIG_FINGERPRINT,
        )
        return manifest

    def test_build_is_traceable_and_loadable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self.build_artifact(root)
            loaded = load_sharded_manifest(root)
            self.assertEqual(loaded["artifact_id"], manifest["artifact_id"])
            self.assertEqual(loaded["vector_count"], loaded["chunk_count"])
            with self.assertRaisesRegex(IndexStorageError, "MODEL_FINGERPRINT_MISMATCH"):
                load_sharded_manifest(root, expected_model_fingerprint="0" * 64)
            metadata = root / manifest["artifact_directory"] / "metadata.sqlite"
            connection = sqlite3.connect(metadata)
            try:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0],
                    manifest["vector_count"],
                )
                row = connection.execute(
                    "SELECT embedding_id, chunk_id, representation_id, shard_id FROM embeddings"
                ).fetchone()
                self.assertEqual(row[2], connection.execute("SELECT representation_id FROM chunks WHERE chunk_id=?", (row[1],)).fetchone()[0])
            finally:
                connection.close()

    def test_corrupted_shard_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self.build_artifact(root)
            shard = root / manifest["artifact_directory"] / manifest["shards"][0]["file"]
            shard.write_bytes(shard.read_bytes() + b"corrupt")
            with self.assertRaisesRegex(IndexStorageError, "SHARD_CORRUPT_OR_MISSING"):
                load_sharded_manifest(root)

    def test_duplicate_embedding_ids_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tables, representations = build_fixture()
            from src.indexing.embedding_chunker import build_embedding_chunks
            chunks = build_embedding_chunks(
                representations,
                {table.normalized_table_id: table for table in tables},
                CharTokenizer(),
            )
            fingerprint = make_embedding_config_fingerprint()
            records = embed_chunks(chunks, encoder=FixedEncoder(), model_fingerprint=fingerprint)
            with self.assertRaisesRegex(IndexStorageError, "DUPLICATE_EMBEDDING_ID"):
                build_sharded_index(
                    records + [records[0]], chunks, representations, root,
                    model_fingerprint=fingerprint,
                    chunking_config_fingerprint=CHUNKING_CONFIG_FINGERPRINT,
                )


if __name__ == "__main__":
    unittest.main()
