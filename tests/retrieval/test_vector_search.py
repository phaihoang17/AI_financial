import json
from pathlib import Path
import tempfile
import unittest

from src.indexing.embedding_artifact_builder import APPROVED_EMBEDDING_FINGERPRINT
from src.retrieval.query_embedding import QueryEmbedding, make_query_embedding_id, make_retrieval_query_id
from src.retrieval.schemas import RetrievalCompany, RetrievalQuery
from src.retrieval.vector_search import VectorSearchError, VectorSearcher
from src.supervisor.schemas import EvidenceSource
from src.understanding.schemas import StatementScope
from tests.indexing.test_embedding_artifact_builder import EmbeddingArtifactBuilderTests, RecordingEncoder


def make_query(
    *, ticker: str = "AAA", year: str = "2024", scope=None, texts=("Doanh thu",)
) -> RetrievalQuery:
    return RetrievalQuery(
        raw_question=texts[0],
        company=RetrievalCompany(name="Test Company", ticker=ticker),
        periods=[year],
        period_kind=None,
        statement_scope=scope,
        target_metrics=["Doanh thu"],
        derived_target=None,
        evidence_sources=[EvidenceSource.TABLE, EvidenceSource.TEXT],
        requested_scale=None,
        requested_unit=None,
        query_texts=list(texts),
    )


def embeddings_for(query: RetrievalQuery) -> list[QueryEmbedding]:
    query_id = make_retrieval_query_id(query)
    result = []
    for index, text in enumerate(query.query_texts):
        vector = [1.0] + [0.0] * 1023
        result.append(
            QueryEmbedding(
                query_embedding_id=make_query_embedding_id(
                    query_id=query_id,
                    query_text=text,
                    query_text_index=index,
                    embedding_fingerprint=APPROVED_EMBEDDING_FINGERPRINT,
                ),
                query_id=query_id,
                query_text=text,
                query_text_index=index,
                vector=vector,
                dimension=1024,
                dtype="float32",
                embedding_fingerprint=APPROVED_EMBEDDING_FINGERPRINT,
            )
        )
    return result


class VectorSearchTests(unittest.TestCase):
    def _artifacts(self, root: Path) -> tuple[Path, Path]:
        harness = EmbeddingArtifactBuilderTests()
        corpus = harness._input_artifact(root, report_count=2)
        vectors = root / "vectors"
        harness._build(corpus, vectors, max_vectors=2, encoder=RecordingEncoder())
        return corpus, vectors

    def test_fixture_search_preserves_provenance_shards_top_k_and_ties(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus, vectors = self._artifacts(Path(directory))
            query = make_query(texts=("Doanh thu", "revenue"))
            result = VectorSearcher(vectors, corpus).search_with_diagnostics(
                query, embeddings_for(query), top_k=3
            )

        self.assertEqual(len(result.candidates), 3)
        self.assertEqual(len({item.candidate_id for item in result.candidates}), 3)
        self.assertEqual({item.source_type for item in result.candidates}, {EvidenceSource.TABLE, EvidenceSource.TEXT})
        self.assertTrue(all(item.vector_score is not None for item in result.candidates))
        self.assertTrue(all(item.bm25_score is None for item in result.candidates))
        self.assertEqual(
            [item.candidate_id for item in result.candidates],
            sorted(item.candidate_id for item in result.candidates),
        )
        self.assertEqual(len(result.observations), 3)
        self.assertEqual({item.query_text_index for item in result.observations}, {0})

    def test_metadata_filter_is_exact_and_never_relaxed(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus, vectors = self._artifacts(Path(directory))
            searcher = VectorSearcher(vectors, corpus)
            no_ticker = make_query(ticker="ZZZ")
            wrong_year = make_query(year="2023")
            explicit_scope = make_query(scope=StatementScope.HOP_NHAT)

            results = [
                searcher.search(no_ticker, embeddings_for(no_ticker), top_k=10),
                searcher.search(wrong_year, embeddings_for(wrong_year), top_k=10),
                searcher.search(explicit_scope, embeddings_for(explicit_scope), top_k=10),
            ]

        self.assertEqual(results, [[], [], []])

    def test_source_eligibility_is_exact_and_applies_before_top_k(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus, vectors = self._artifacts(Path(directory))
            searcher = VectorSearcher(vectors, corpus)
            query = make_query()
            embeddings = embeddings_for(query)
            table = searcher.search(query, embeddings, top_k=1, eligible_source_types=[EvidenceSource.TABLE])
            text = searcher.search(query, embeddings, top_k=1, eligible_source_types=[EvidenceSource.TEXT])
            both = searcher.search(query, embeddings, top_k=4, eligible_source_types=[EvidenceSource.TABLE, EvidenceSource.TEXT])
            with self.assertRaisesRegex(VectorSearchError, "SOURCE_ELIGIBILITY_EMPTY"):
                searcher.search(query, embeddings, top_k=1, eligible_source_types=[])

        self.assertEqual(len(table), 1)
        self.assertEqual(len(text), 1)
        self.assertIs(table[0].source_type, EvidenceSource.TABLE)
        self.assertIs(text[0].source_type, EvidenceSource.TEXT)
        self.assertEqual({item.source_type for item in both}, {EvidenceSource.TABLE, EvidenceSource.TEXT})

    def test_missing_shard_and_incompatible_manifest_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            corpus, vectors = self._artifacts(root)
            manifest = json.loads((vectors / "manifest.json").read_text())
            shard = vectors / manifest["artifact_directory"] / manifest["shards"][0]["file"]
            shard.unlink()
            with self.assertRaisesRegex(VectorSearchError, "VECTOR_ARTIFACT_INVALID"):
                VectorSearcher(vectors, corpus)

            corpus, vectors = self._artifacts(root / "second")
            manifest_path = vectors / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["embedding_fingerprint"] = "0" * 64
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(VectorSearchError, "EMBEDDING_FINGERPRINT_MISMATCH"):
                VectorSearcher(vectors, corpus)

    def test_real_500_vector_artifact_search(self):
        repository = Path(__file__).resolve().parents[2]
        corpus = repository / "artifacts/m2-vector-index-v1/smoke-input-500"
        vectors = repository / "artifacts/m2-vector-index-v1/smoke-resume"
        if not (corpus / "manifest.json").is_file() or not (vectors / "manifest.json").is_file():
            self.skipTest("local real-BGE smoke artifacts are unavailable")
        query = make_query(year="2016", scope=StatementScope.RIENG, texts=("lợi nhuận",))

        result = VectorSearcher(vectors, corpus).search(query, embeddings_for(query), top_k=5)

        self.assertTrue(result)
        self.assertLessEqual(len(result), 5)
        self.assertTrue(all(item.ticker == "AAA" for item in result))
        self.assertTrue(all(item.report_year == 2016 for item in result))
        self.assertTrue(all(item.statement_scope is StatementScope.RIENG for item in result))


if __name__ == "__main__":
    unittest.main()
