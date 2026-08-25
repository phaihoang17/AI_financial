from dataclasses import replace
import unittest

from src.retrieval.rrf import RRF_K, RRFFusionError, fuse_rrf
from src.retrieval.schemas import RetrievalCandidate, make_candidate_id
from src.supervisor.schemas import EvidenceSource


def candidate(
    name: str, *, bm25=None, vector=None, rank=1, content=None
) -> RetrievalCandidate:
    representation_id = f"representation-{name}"
    return RetrievalCandidate(
        candidate_id=make_candidate_id(EvidenceSource.TABLE, representation_id, f"chunk-{name}"),
        representation_id=representation_id,
        chunk_id=f"chunk-{name}",
        source_type=EvidenceSource.TABLE,
        report_id="report-1",
        page_ids=["page-1"],
        table_id="table-1",
        paragraph_id=None,
        ticker="AAA",
        company_name="Test",
        report_year=2024,
        statement_scope=None,
        period_labels=["2024"],
        row_paths=[],
        column_paths=[],
        content=content or f"content {name}",
        bm25_score=bm25,
        vector_score=vector,
        rrf_score=None,
        rerank_score=None,
        rank=rank,
    )


class RRFFusionTests(unittest.TestCase):
    def test_exact_formula_score_preservation_and_final_top_k(self):
        a_bm25 = candidate("a", bm25=2.0, rank=1)
        b_bm25 = candidate("b", bm25=1.0, rank=2)
        b_vector = candidate("b", vector=0.9, rank=1)
        c_vector = candidate("c", vector=0.8, rank=2)

        result = fuse_rrf([a_bm25, b_bm25], [b_vector, c_vector], top_k=2)

        self.assertEqual([item.candidate_id for item in result], [b_bm25.candidate_id, a_bm25.candidate_id])
        self.assertAlmostEqual(result[0].rrf_score, 1 / (RRF_K + 2) + 1 / (RRF_K + 1))
        self.assertEqual(result[0].bm25_score, 1.0)
        self.assertEqual(result[0].vector_score, 0.9)
        self.assertEqual(result[1].bm25_score, 2.0)
        self.assertIsNone(result[1].vector_score)
        self.assertEqual([item.rank for item in result], [1, 2])

    def test_one_or_zero_backend_is_valid(self):
        only = candidate("only", bm25=1.0, rank=1)

        one = fuse_rrf([only], [], top_k=3)
        empty = fuse_rrf([], [], top_k=3)

        self.assertEqual(len(one), 1)
        self.assertAlmostEqual(one[0].rrf_score, 1 / (RRF_K + 1))
        self.assertEqual(empty, [])

    def test_duplicate_invalid_rank_and_provenance_mismatch_are_typed(self):
        first = candidate("a", bm25=1.0, rank=1)
        with self.assertRaisesRegex(RRFFusionError, "RRF_DUPLICATE_CANDIDATE"):
            fuse_rrf([first, first], [], top_k=2)
        with self.assertRaisesRegex(RRFFusionError, "RRF_RANK_INVALID"):
            fuse_rrf([replace(first, rank=2)], [], top_k=2)
        with self.assertRaisesRegex(RRFFusionError, "RRF_PROVENANCE_MISMATCH"):
            fuse_rrf(
                [first], [replace(first, bm25_score=None, vector_score=1.0, content="other")], top_k=2
            )

    def test_ties_sort_by_candidate_id(self):
        a = candidate("a", vector=1.0, rank=1)
        b = candidate("b", bm25=1.0, rank=1)

        result = fuse_rrf([b], [a], top_k=2)

        self.assertEqual(
            [item.candidate_id for item in result],
            sorted([a.candidate_id, b.candidate_id]),
        )


if __name__ == "__main__":
    unittest.main()
