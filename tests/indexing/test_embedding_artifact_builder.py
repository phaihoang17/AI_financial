import hashlib
import json
from dataclasses import replace
from pathlib import Path
import sqlite3
import tempfile
import unittest

from src.indexing.embedding_artifact_builder import (
    APPROVED_EMBEDDING_FINGERPRINT,
    EmbeddingArtifactBuilderError,
    _build_id,
    build_streaming_embedding_artifact,
    validate_published_artifact,
)
from src.indexing.embedding_chunker import CHUNKING_CONFIG_FINGERPRINT, build_embedding_chunks
from src.indexing.embedding_schemas import EMBEDDING_CHUNK_SCHEMA_VERSION
from src.indexing.embedding_indexer import EMBEDDING_ALGORITHM_VERSION
from src.indexing.corpus_artifact import CORPUS_ARTIFACT_SCHEMA_VERSION, CORPUS_RECORD_SCHEMA_VERSION
from src.indexing.embedding_chunker import BGE_M3_MODEL_ID, BGE_M3_REVISION
from src.indexing.schemas import M2A_SCHEMA_VERSION, NORMALIZATION_VERSION, REPRESENTATION_VERSION

from tests.indexing.test_embedding_chunker import CharTokenizer, build_fixture


class RecordingEncoder:
    def __init__(self, *, fail_on_call=None):
        self.calls = []
        self.fail_on_call = fail_on_call

    def encode(self, texts, *, batch_size, max_length):
        self.calls.append((len(texts), batch_size, max_length))
        if self.fail_on_call is not None and len(self.calls) >= self.fail_on_call:
            raise RuntimeError("test interruption")
        return [[1.0] + [0.0] * 1023 for _ in texts]


class EmbeddingArtifactBuilderTests(unittest.TestCase):
    def _input_artifact(self, root: Path, *, report_count=4, records_per_shard=2) -> Path:
        tables, representations = build_fixture()
        base_chunks = build_embedding_chunks(
            representations,
            {table.normalized_table_id: table for table in tables},
            CharTokenizer(),
        )
        reports = []
        records = []
        for report_index in range(report_count):
            report_id = f"report-{report_index}"
            source_ref = f"AAA/2024/report-{report_index}.txt"
            content_sha = hashlib.sha256(source_ref.encode()).hexdigest()
            report = {
                "report_id": report_id,
                "source_ref": source_ref,
                "ticker": "AAA",
                "company_name": "Test Company",
                "report_year": 2024,
                "statement_scope": None,
                "content_sha256": content_sha,
                "byte_size": len(source_ref),
            }
            reports.append(report)
            for representation_index, base_representation in enumerate(representations):
                representation = replace(
                    base_representation,
                    representation_id=f"representation-{report_index}-{representation_index}",
                    report_id=report_id,
                    source_ref=f"source-{report_index}-{representation_index}",
                )
                base_chunk = next(
                    chunk for chunk in base_chunks
                    if chunk.representation_id == base_representation.representation_id
                )
                chunk = replace(
                    base_chunk,
                    chunk_id=hashlib.sha256(f"chunk-{report_index}-{representation_index}".encode()).hexdigest(),
                    representation_id=representation.representation_id,
                )
                source_cell_ids = (
                    list(chunk.primary_source_cell_ids)
                    if representation.source_type.value == "TABLE"
                    else []
                )
                report_record = {
                    "report_id": report_id,
                    "source_ref": source_ref,
                    "content_sha256": content_sha,
                    "ticker": "AAA",
                    "company_name": "Test Company",
                    "report_year": 2024,
                    "statement_scope": None,
                }
                records.append({
                    "record_schema_version": CORPUS_RECORD_SCHEMA_VERSION,
                    "source_order": len(records),
                    "report": report_record,
                    "representation": representation.to_dict(),
                    "source_cell_ids": source_cell_ids,
                    "chunk": chunk.to_dict(),
                })

        artifact = root / "m2-input"
        shards_dir = artifact / "shards"
        shards_dir.mkdir(parents=True)
        shard_entries = []
        for shard_index, start in enumerate(range(0, len(records), records_per_shard)):
            shard_records = records[start:start + records_per_shard]
            payload = b"".join(
                json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
                for record in shard_records
            )
            shard_path = shards_dir / f"shard-{shard_index:06d}.jsonl"
            shard_path.write_bytes(payload)
            shard_entries.append({
                "shard_id": shard_path.stem,
                "file": f"shards/{shard_path.name}",
                "record_count": len(shard_records),
                "table_chunk_count": sum(item["chunk"]["source_type"] == "TABLE" for item in shard_records),
                "text_chunk_count": sum(item["chunk"]["source_type"] == "TEXT" for item in shard_records),
                "byte_count": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            })
        corpus_fingerprint = hashlib.sha256(
            json.dumps(reports, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        manifest = {
            "manifest_schema_version": CORPUS_ARTIFACT_SCHEMA_VERSION,
            "record_schema_version": CORPUS_RECORD_SCHEMA_VERSION,
            "artifact_id": "m2-input-fixture",
            "artifact_status": "COMMITTED",
            "corpus_id": "test-corpus",
            "source_inventory_sha256": corpus_fingerprint,
            "reports": reports,
            "source_order": "relative_posix_source_ref",
            "record_order": "report_source_order_then_representation_then_chunk_index",
            "m2a_schema_version": M2A_SCHEMA_VERSION,
            "normalization_version": NORMALIZATION_VERSION,
            "representation_version": REPRESENTATION_VERSION,
            "chunk_schema_version": EMBEDDING_CHUNK_SCHEMA_VERSION,
            "chunking_config_fingerprint": CHUNKING_CONFIG_FINGERPRINT,
            "tokenizer": {"model_id": BGE_M3_MODEL_ID, "revision": BGE_M3_REVISION, "max_tokens": 7168},
            "format": "jsonl-utf8-canonical",
            "max_records_per_shard": records_per_shard,
            "max_shard_bytes": 1000000,
            "report_count": report_count,
            "representation_count": report_count * 2,
            "table_representation_count": report_count,
            "text_representation_count": report_count,
            "chunk_count": len(records),
            "table_chunk_count": report_count,
            "text_chunk_count": report_count,
            "source_cell_count": report_count * len(base_chunks[0].primary_source_cell_ids),
            "emitted_source_cell_count": report_count * len(base_chunks[0].primary_source_cell_ids),
            "shard_count": len(shard_entries),
            "shards": shard_entries,
            "omitted_representation_count": 0,
            "omitted_source_cell_count": 0,
        }
        (artifact / "manifest.json").write_bytes(
            json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode()
        )
        return artifact

    def _build(self, input_artifact: Path, output: Path, *, batch_size=2, max_vectors=3, encoder=None, resume=False):
        return build_streaming_embedding_artifact(
            input_artifact,
            output,
            device="cpu",
            batch_size=batch_size,
            max_vectors_per_shard=max_vectors,
            resume=resume,
            encoder=encoder or RecordingEncoder(),
        )

    def test_streams_multiple_input_shards_and_batches(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_artifact = self._input_artifact(root, report_count=4, records_per_shard=2)
            encoder = RecordingEncoder()
            output = root / "vectors"
            manifest = self._build(input_artifact, output, batch_size=3, max_vectors=100, encoder=encoder)
            self.assertGreater(len(json.loads((input_artifact / "manifest.json").read_text())["shards"]), 1)
            self.assertEqual([call[0] for call in encoder.calls], [3, 3, 2])
            self.assertEqual(manifest["vector_count"], 8)

    def test_faiss_shard_boundary_and_sqlite_mapping(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_artifact = self._input_artifact(root, report_count=4)
            output = root / "vectors"
            manifest = self._build(input_artifact, output, max_vectors=3)
            self.assertEqual([item["vector_count"] for item in manifest["shards"]], [3, 3, 2])
            db = output / manifest["artifact_directory"] / "metadata.sqlite"
            with sqlite3.connect(db) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0], 8)
                row = connection.execute(
                    "SELECT embedding_id, chunk_id, representation_id, source_type, report_id, "
                    "page_ids_json, table_id, paragraph_id, ticker, company_name, report_year, "
                    "statement_scope, period_labels_json, faiss_shard_id, local_vector_id "
                    "FROM embeddings ORDER BY source_order LIMIT 1"
                ).fetchone()
            self.assertEqual(row[3], "TABLE")
            self.assertEqual(row[4], "report-0")
            self.assertEqual(row[8:11], ("AAA", None, 2024))
            self.assertEqual(row[13], "shard-00000")
            self.assertEqual(row[14], 0)

    def test_interruption_resume_and_incomplete_shard_replay(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_artifact = self._input_artifact(root, report_count=4)
            output = root / "vectors"
            with self.assertRaisesRegex(EmbeddingArtifactBuilderError, "EMBEDDING_BATCH_FAILED"):
                self._build(input_artifact, output, max_vectors=3, encoder=RecordingEncoder(fail_on_call=3))
            staging = next((output / ".staging").iterdir())
            self.assertTrue((staging / "checkpoints" / "shard-00000.json").is_file())
            manifest = self._build(input_artifact, output, max_vectors=3, encoder=RecordingEncoder(), resume=True)
            self.assertEqual(manifest["vector_count"], 8)
            self.assertEqual(validate_published_artifact(output, input_artifact=input_artifact)["sqlite_row_count"], 8)
            self.assertFalse((output / ".staging").exists() and any((output / ".staging").iterdir()))

    def test_duplicate_ids_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_artifact = self._input_artifact(root, report_count=4)
            manifest_path = input_artifact / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            first = input_artifact / manifest["shards"][0]["file"]
            second = input_artifact / manifest["shards"][1]["file"]
            first_value = json.loads(first.read_text().splitlines()[0])
            lines = second.read_text().splitlines()
            duplicate = json.loads(lines[0])
            duplicate["chunk"] = first_value["chunk"]
            duplicate["chunk"]["representation_id"] = duplicate["representation"]["representation_id"]
            lines[0] = json.dumps(duplicate, ensure_ascii=False, separators=(",", ":"))
            second.write_text("\n".join(lines) + "\n", encoding="utf-8")
            manifest["shards"][1]["sha256"] = hashlib.sha256(second.read_bytes()).hexdigest()
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            with self.assertRaisesRegex(EmbeddingArtifactBuilderError, "DUPLICATE_ID"):
                self._build(input_artifact, root / "vectors", batch_size=1)

    def test_incompatible_fingerprint_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_artifact = self._input_artifact(root)
            manifest_path = input_artifact / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["chunking_config_fingerprint"] = "0" * 64
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(EmbeddingArtifactBuilderError, "CHUNKING_FINGERPRINT_MISMATCH"):
                self._build(input_artifact, root / "vectors")

    def test_corrupt_input_shard_is_rejected_before_embedding(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_artifact = self._input_artifact(root)
            manifest = json.loads((input_artifact / "manifest.json").read_text())
            shard = input_artifact / manifest["shards"][0]["file"]
            shard.write_bytes(shard.read_bytes() + b"corrupt")
            encoder = RecordingEncoder()
            with self.assertRaisesRegex(EmbeddingArtifactBuilderError, "INPUT_SHARD_HASH_MISMATCH"):
                self._build(input_artifact, root / "vectors", encoder=encoder)
            self.assertEqual(encoder.calls, [])

    def test_corrupt_completed_faiss_shard_is_not_reused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_artifact = self._input_artifact(root)
            output = root / "vectors"
            with self.assertRaises(EmbeddingArtifactBuilderError):
                self._build(input_artifact, output, max_vectors=3, encoder=RecordingEncoder(fail_on_call=3))
            staging = next((output / ".staging").iterdir())
            faiss_path = staging / "shards" / "shard-00000.faiss"
            faiss_path.write_bytes(faiss_path.read_bytes() + b"corrupt")
            with self.assertRaisesRegex(EmbeddingArtifactBuilderError, "COMPLETED_FAISS_CORRUPT"):
                self._build(input_artifact, output, max_vectors=3, encoder=RecordingEncoder(), resume=True)

    def test_sqlite_integrity_failure_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_artifact = self._input_artifact(root)
            output = root / "vectors"
            manifest = self._build(input_artifact, output)
            artifact = output / manifest["artifact_directory"]
            db = artifact / "metadata.sqlite"
            db.write_bytes(b"not sqlite")
            top_manifest_path = output / "manifest.json"
            top_manifest = json.loads(top_manifest_path.read_text())
            top_manifest["metadata"]["sha256"] = hashlib.sha256(db.read_bytes()).hexdigest()
            top_manifest_path.write_text(json.dumps(top_manifest), encoding="utf-8")
            with self.assertRaisesRegex(EmbeddingArtifactBuilderError, "SQLITE_INTEGRITY_FAILED"):
                validate_published_artifact(output, input_artifact=input_artifact)

    def test_final_publication_is_atomic_and_current_is_valid(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_artifact = self._input_artifact(root)
            output = root / "vectors"
            manifest = self._build(input_artifact, output)
            self.assertEqual((output / "CURRENT").read_text().strip(), manifest["artifact_id"])
            self.assertTrue((output / manifest["artifact_directory"] / "manifest.json").is_file())
            self.assertFalse((output / ".staging").exists() and any((output / ".staging").iterdir()))
            self.assertEqual(validate_published_artifact(output, input_artifact=input_artifact)["integrity"], "PASS")

    def test_deterministic_rebuild_reuses_same_immutable_artifact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_artifact = self._input_artifact(root)
            output = root / "vectors"
            first = self._build(input_artifact, output)
            second_encoder = RecordingEncoder(fail_on_call=1)
            second = self._build(input_artifact, output, encoder=second_encoder, resume=True)
            self.assertEqual(first["artifact_id"], second["artifact_id"])
            self.assertEqual(second_encoder.calls, [])
            self.assertEqual(first["embedding_fingerprint"], APPROVED_EMBEDDING_FINGERPRINT)


if __name__ == "__main__":
    unittest.main()
