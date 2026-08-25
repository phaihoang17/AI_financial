import json
from pathlib import Path
import tempfile
import unittest

from src.retrieval.bm25 import BM25Index, BM25IndexError, build_bm25_index
from src.retrieval.schemas import RetrievalCompany, RetrievalQuery
from src.supervisor.schemas import EvidenceSource
from tests.indexing.test_embedding_artifact_builder import EmbeddingArtifactBuilderTests


def make_query(*query_texts: str) -> RetrievalQuery:
    return RetrievalQuery(
        raw_question=query_texts[0] if query_texts else "",
        company=RetrievalCompany(name="Test Company", ticker="AAA"),
        periods=["2024"],
        period_kind=None,
        statement_scope=None,
        target_metrics=["Doanh thu"],
        derived_target=None,
        evidence_sources=[EvidenceSource.TABLE, EvidenceSource.TEXT],
        requested_scale=None,
        requested_unit=None,
        query_texts=list(query_texts),
    )


class BM25IndexTests(unittest.TestCase):
    def _index(self, root: Path, *, reports: int = 2) -> tuple[Path, Path]:
        corpus = EmbeddingArtifactBuilderTests()._input_artifact(root, report_count=reports)
        index = root / "bm25"
        build_bm25_index(corpus, index)
        return corpus, index

    def test_deterministic_build_and_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus, index = self._index(Path(directory))
            first = json.loads((index / "manifest.json").read_text())
            second = build_bm25_index(corpus, index)

        self.assertEqual(first, second)
        self.assertEqual(first["bm25_schema_version"], "m3-bm25-index-v1")
        self.assertEqual(first["backend"], "sqlite-fts5")
        self.assertEqual(first["candidate_count"], 4)

    def test_table_text_diacritics_multiple_queries_duplicates_and_top_k(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus, index = self._index(Path(directory))
            search = BM25Index(index, corpus)

            table = search.search(make_query("Chỉ"), top_k=10)
            text = search.search(make_query("Narrative"), top_k=10)
            combined = search.search(make_query("Chỉ", "Narrative", "Chỉ"), top_k=3)
            accent_mismatch = search.search(make_query("Chi"), top_k=10)

        self.assertTrue(table)
        self.assertTrue(text)
        self.assertTrue(all(item.source_type is EvidenceSource.TABLE for item in table))
        self.assertTrue(all(item.source_type is EvidenceSource.TEXT for item in text))
        self.assertEqual(len({item.candidate_id for item in combined}), len(combined))
        self.assertEqual(len(combined), 3)
        self.assertEqual(
            [(item.bm25_score, item.candidate_id) for item in combined],
            sorted(
                [(item.bm25_score, item.candidate_id) for item in combined],
                key=lambda value: (-float(value[0]), value[1]),
            ),
        )
        self.assertEqual(accent_mismatch, [])

    def test_empty_query_returns_no_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus, index = self._index(Path(directory))
            result = BM25Index(index, corpus).search(make_query(" \t\n "), top_k=1)

        self.assertEqual(result, [])

    def test_source_eligibility_is_exact_and_applies_before_top_k(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus, index = self._index(Path(directory))
            search = BM25Index(index, corpus)
            table = search.search(make_query("Chỉ", "Narrative"), top_k=1, eligible_source_types=[EvidenceSource.TABLE])
            text = search.search(make_query("Chỉ", "Narrative"), top_k=1, eligible_source_types=[EvidenceSource.TEXT])
            both = search.search(make_query("Chỉ", "Narrative"), top_k=4, eligible_source_types=[EvidenceSource.TABLE, EvidenceSource.TEXT])
            with self.assertRaisesRegex(BM25IndexError, "SOURCE_ELIGIBILITY_EMPTY"):
                search.search(make_query("Chỉ"), top_k=1, eligible_source_types=[])

        self.assertEqual(len(table), 1)
        self.assertEqual(len(text), 1)
        self.assertIs(table[0].source_type, EvidenceSource.TABLE)
        self.assertIs(text[0].source_type, EvidenceSource.TEXT)
        self.assertEqual({item.source_type for item in both}, {EvidenceSource.TABLE, EvidenceSource.TEXT})

    def test_incompatible_or_corrupt_index_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            corpus, index = self._index(root)
            manifest_path = index / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["bm25_schema_version"] = "m3-bm25-index-v0"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(BM25IndexError, "BM25_ARTIFACT_MISMATCH"):
                BM25Index(index, corpus)

            # Rebuild a clean artifact, then make its database unreadable.
            index = root / "clean-bm25"
            build_bm25_index(corpus, index)
            manifest = json.loads((index / "manifest.json").read_text())
            database = index / manifest["artifact_directory"] / manifest["database"]["file"]
            database.write_bytes(b"not a sqlite database")
            with self.assertRaisesRegex(BM25IndexError, "BM25_INDEX_CORRUPT_OR_MISSING"):
                BM25Index(index, corpus)


if __name__ == "__main__":
    unittest.main()
