import json
from pathlib import Path
import tempfile
import unittest

from src.indexing.embedding_artifact_builder import _validate_input_manifest
from src.indexing.smoke_corpus_subset import build_smoke_subset
from tests.indexing.test_embedding_artifact_builder import EmbeddingArtifactBuilderTests


def _records(root: Path) -> list[dict]:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    rows = []
    for shard in manifest["shards"]:
        rows.extend(json.loads(line) for line in (root / shard["file"]).read_text(encoding="utf-8").splitlines())
    return rows


class SmokeCorpusSubsetTests(unittest.TestCase):
    def test_subset_is_a_valid_deterministic_corpus_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = EmbeddingArtifactBuilderTests()._input_artifact(root / "source", report_count=4)
            first = build_smoke_subset(source, root / "a", per_type=1)
            second = build_smoke_subset(source, root / "b", per_type=1)

            self.assertEqual((first / "manifest.json").read_bytes(), (second / "manifest.json").read_bytes())
            artifact = _validate_input_manifest(first, verify_hashes=True)
            rows = _records(first)
            self.assertEqual([row["source_order"] for row in rows], list(range(len(rows))))
            self.assertEqual(artifact.manifest["report_count"], len({row["report"]["report_id"] for row in rows}))

    def test_selects_lowest_chunk_ids_per_source_type(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = EmbeddingArtifactBuilderTests()._input_artifact(root / "source", report_count=4)
            subset = build_smoke_subset(source, root / "subset", per_type=2)

            selected = {row["chunk"]["chunk_id"] for row in _records(subset)}
            expected = set()
            for source_type in ("TABLE", "TEXT"):
                ids = sorted(row["chunk"]["chunk_id"] for row in _records(source) if row["chunk"]["source_type"] == source_type)
                expected.update(ids[:2])
            manifest = json.loads((subset / "manifest.json").read_text(encoding="utf-8"))
            long_table = manifest["smoke_selection"]["included_long_table_chunk_id"]
            if long_table is not None:
                self.assertIn(long_table, selected)
                self.assertEqual(len(selected), len(expected))
            else:
                self.assertEqual(selected, expected)


if __name__ == "__main__":
    unittest.main()
