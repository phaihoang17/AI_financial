import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.indexing.corpus_artifact import (
    CORPUS_ARTIFACT_SCHEMA_VERSION,
    build_corpus_artifact,
    verify_corpus_artifact,
)


class CharTokenizer:
    def __call__(self, text, **kwargs):
        if isinstance(text, list):
            return {"input_ids": [[ord(char) for char in item] for item in text]}
        return {"input_ids": [ord(char) for char in text]}


class CorpusArtifactTests(unittest.TestCase):
    def _corpus(self, root: Path) -> tuple[Path, Path]:
        corpus = root / "financial_statements"
        report_dir = corpus / "AAA" / "2024" / "AAA_financial_statements_2024_consolidated"
        report_dir.mkdir(parents=True)
        (report_dir / "AAA_financial_statements_2024_consolidated_extracted.txt").write_text(
            "===== PAGE 1 =====\nBefore.\n"
            "<table><tr><td>Chỉ tiêu</td><td>2024</td></tr>"
            "<tr><td>Doanh thu</td><td>1.250</td></tr></table>\nAfter.\n",
            encoding="utf-8",
        )
        metadata = root / "code_stock.csv"
        metadata.write_text("Mã CK,Tên công ty\nAAA,Test Company\n", encoding="utf-8")
        return corpus, metadata

    def test_builds_and_verifies_canonical_shards(self):
        with tempfile.TemporaryDirectory() as temporary:
            corpus, metadata = self._corpus(Path(temporary))
            artifact_root = Path(temporary) / "artifacts"
            with patch("src.indexing.corpus_artifact.load_bge_m3_tokenizer", return_value=CharTokenizer()):
                artifact = build_corpus_artifact(
                    corpus,
                    artifact_root,
                    company_metadata=metadata,
                    max_records_per_shard=2,
                    max_shard_bytes=100_000,
                )
            result = verify_corpus_artifact(artifact)
            self.assertEqual(result["integrity"], "PASS")
            self.assertEqual(result["report_count"], 1)
            self.assertGreater(result["table_chunk_count"], 0)
            self.assertGreater(result["text_chunk_count"], 0)
            manifest = json.loads((artifact / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["manifest_schema_version"], CORPUS_ARTIFACT_SCHEMA_VERSION)
            self.assertEqual(manifest["chunk_count"], sum(item["record_count"] for item in manifest["shards"]))
            self.assertTrue(all((artifact / item["file"]).stat().st_mode & 0o222 == 0 for item in manifest["shards"]))

    def test_resume_rejects_incomplete_or_incompatible_state_without_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            corpus, metadata = self._corpus(Path(temporary))
            artifact_root = Path(temporary) / "artifacts"
            with patch("src.indexing.corpus_artifact.load_bge_m3_tokenizer", return_value=CharTokenizer()):
                artifact = build_corpus_artifact(
                    corpus,
                    artifact_root,
                    company_metadata=metadata,
                    max_records_per_shard=100,
                    max_shard_bytes=100_000,
                )
                again = build_corpus_artifact(
                    corpus,
                    artifact_root,
                    company_metadata=metadata,
                    max_records_per_shard=100,
                    max_shard_bytes=100_000,
                )
            self.assertEqual(artifact, again)


if __name__ == "__main__":
    unittest.main()
