import copy
import unittest

from src.indexing.schemas import HeaderPathEntry
from src.retrieval.schemas import (
    RetrievalCandidate,
    RetrievalCompany,
    RetrievalContractError,
    RetrievalObservation,
    RetrievalQuery,
    make_candidate_id,
    rank_candidates,
)
from src.supervisor.schemas import EvidenceSource
from src.understanding.schemas import PeriodKind, StatementScope


def candidate_payload(*, source_type="TABLE", chunk_id="chunk-1"):
    source = EvidenceSource(source_type)
    is_table = source is EvidenceSource.TABLE
    representation_id = "representation-1"
    return {
        "candidate_id": make_candidate_id(source, representation_id, chunk_id),
        "representation_id": representation_id,
        "chunk_id": chunk_id,
        "source_type": source_type,
        "report_id": "report-1",
        "page_ids": ["page-1"],
        "table_id": "table-1" if is_table else None,
        "paragraph_id": None if is_table else "paragraph-1",
        "ticker": "AAA",
        "company_name": "An Phat",
        "report_year": 2024,
        "statement_scope": "HOP_NHAT",
        "period_labels": ["2024"],
        "row_paths": [[{"header_id": "row-1", "label": "Doanh thu"}]] if is_table else [],
        "column_paths": [[{"header_id": "column-1", "label": "2024"}]] if is_table else [],
        "content": "canonical candidate content",
        "bm25_score": 0.1,
        "vector_score": 0.2,
        "rrf_score": None,
        "rerank_score": None,
        "rank": None,
    }


def query_payload():
    return {
        "raw_question": "Doanh thu AAA năm 2024 là bao nhiêu?",
        "company": {"name": "An Phat", "ticker": "AAA"},
        "periods": ["2024", "2024-Q4"],
        "period_kind": "NAM",
        "statement_scope": "HOP_NHAT",
        "target_metrics": ["Doanh thu"],
        "derived_target": None,
        "evidence_sources": ["TABLE", "TEXT"],
        "requested_scale": "MILLION",
        "requested_unit": "VND",
        "query_texts": ["Doanh thu AAA năm 2024 là bao nhiêu?", "Doanh thu"],
        "eligible_source_types": ["TABLE", "TEXT"],
    }


class RetrievalContractTests(unittest.TestCase):
    def test_retrieval_query_round_trips_with_deterministic_serialization(self):
        payload = query_payload()

        query = RetrievalQuery.from_dict(payload)

        self.assertEqual(query.to_dict(), payload)
        self.assertEqual(query.to_json(), query.to_json())
        self.assertEqual(query.period_kind, PeriodKind.NAM)
        self.assertEqual(query.statement_scope, StatementScope.HOP_NHAT)

    def test_retrieval_query_preserves_list_order_without_hidden_normalization(self):
        payload = query_payload()
        payload["periods"] = ["2024-Q4", "2024", "2024-Q4"]
        payload["evidence_sources"] = ["TEXT", "TABLE"]
        payload["target_metrics"] = ["Lợi nhuận", "Doanh thu"]

        query = RetrievalQuery.from_dict(payload)

        self.assertEqual(query.periods, payload["periods"])
        self.assertEqual([source.value for source in query.evidence_sources], payload["evidence_sources"])
        self.assertEqual(query.target_metrics, payload["target_metrics"])

    def test_source_eligibility_is_canonical_deduplicated_and_legacy_payloads_default(self):
        payload = query_payload()
        payload["eligible_source_types"] = ["TEXT", "TABLE", "TEXT"]

        query = RetrievalQuery.from_dict(payload)
        legacy = query_payload()
        legacy.pop("eligible_source_types")
        legacy_query = RetrievalQuery.from_dict(legacy)

        self.assertEqual(
            [source.value for source in query.eligible_source_types], ["TEXT", "TABLE"]
        )
        self.assertEqual(
            [source.value for source in legacy_query.eligible_source_types], ["TABLE", "TEXT"]
        )

    def test_candidate_id_is_stable_and_independent_of_score_and_rank(self):
        first_payload = candidate_payload()
        second_payload = copy.deepcopy(first_payload)
        second_payload.update(
            {
                "bm25_score": 9.9,
                "vector_score": 0.01,
                "rrf_score": 0.6,
                "rerank_score": 0.7,
                "rank": 4,
            }
        )

        first = RetrievalCandidate.from_dict(first_payload)
        second = RetrievalCandidate.from_dict(second_payload)

        self.assertEqual(first.candidate_id, second.candidate_id)
        self.assertEqual(
            first.candidate_id,
            make_candidate_id(EvidenceSource.TABLE, "representation-1", "chunk-1"),
        )

    def test_candidate_uses_representation_identity_when_chunk_is_absent(self):
        payload = candidate_payload(chunk_id=None)
        payload["candidate_id"] = make_candidate_id(
            EvidenceSource.TABLE, payload["representation_id"], None
        )

        candidate = RetrievalCandidate.from_dict(payload)

        self.assertEqual(
            candidate.candidate_id,
            make_candidate_id(EvidenceSource.TABLE, "representation-1", None),
        )

    def test_table_and_text_provenance_shapes_are_preserved(self):
        table = RetrievalCandidate.from_dict(candidate_payload(source_type="TABLE"))
        text = RetrievalCandidate.from_dict(candidate_payload(source_type="TEXT"))

        self.assertEqual(table.row_paths[0][0], HeaderPathEntry("row-1", "Doanh thu"))
        self.assertEqual(table.table_id, "table-1")
        self.assertIsNone(table.paragraph_id)
        self.assertEqual(text.paragraph_id, "paragraph-1")
        self.assertIsNone(text.table_id)
        self.assertEqual(text.row_paths, [])

    def test_score_fields_remain_independent(self):
        payload = candidate_payload()
        payload.update(
            {
                "bm25_score": 0.11,
                "vector_score": 0.22,
                "rrf_score": 0.33,
                "rerank_score": 0.44,
            }
        )

        candidate = RetrievalCandidate.from_dict(payload)

        self.assertEqual(
            [
                candidate.bm25_score,
                candidate.vector_score,
                candidate.rrf_score,
                candidate.rerank_score,
            ],
            [0.11, 0.22, 0.33, 0.44],
        )

    def test_candidate_rejects_score_or_rank_derived_identity(self):
        payload = candidate_payload()
        payload["candidate_id"] = "candidate-ranked-1"

        with self.assertRaisesRegex(RetrievalContractError, "CANDIDATE_ID_MISMATCH"):
            RetrievalCandidate.from_dict(payload)

    def test_candidate_rejects_invalid_provenance_shape(self):
        payload = candidate_payload()
        payload["paragraph_id"] = "paragraph-1"

        with self.assertRaisesRegex(RetrievalContractError, "PROVENANCE_SHAPE_INVALID"):
            RetrievalCandidate.from_dict(payload)

    def test_canonical_ranking_breaks_equal_scores_by_candidate_id(self):
        first_payload = candidate_payload(chunk_id="chunk-b")
        second_payload = candidate_payload(chunk_id="chunk-a")
        first_payload["candidate_id"] = make_candidate_id(
            EvidenceSource.TABLE, first_payload["representation_id"], first_payload["chunk_id"]
        )
        second_payload["candidate_id"] = make_candidate_id(
            EvidenceSource.TABLE, second_payload["representation_id"], second_payload["chunk_id"]
        )
        first_payload["bm25_score"] = 0.5
        second_payload["bm25_score"] = 0.5

        ranked = rank_candidates(
            [RetrievalCandidate.from_dict(first_payload), RetrievalCandidate.from_dict(second_payload)],
            score_field="bm25_score",
        )

        self.assertEqual(
            [candidate.candidate_id for candidate in ranked],
            sorted([first_payload["candidate_id"], second_payload["candidate_id"]]),
        )
        self.assertEqual([candidate.rank for candidate in ranked], [1, 2])
        self.assertEqual([candidate.bm25_score for candidate in ranked], [0.5, 0.5])

    def test_observation_exposes_only_the_required_later_logging_fields(self):
        observation = RetrievalObservation(
            query_id="query-1",
            stage="metadata_filter",
            candidate_id="candidate-1",
            rank=None,
            score=None,
            filter_reason="REPORT_YEAR_MISMATCH",
            artifact_fingerprint="artifact-fingerprint",
            model_fingerprint="model-fingerprint",
        )

        self.assertEqual(
            observation.to_dict(),
            {
                "query_id": "query-1",
                "stage": "metadata_filter",
                "candidate_id": "candidate-1",
                "rank": None,
                "score": None,
                "filter_reason": "REPORT_YEAR_MISMATCH",
                "artifact_fingerprint": "artifact-fingerprint",
                "model_fingerprint": "model-fingerprint",
            },
        )

    def test_direct_query_contract_allows_unresolved_optional_period_kind_and_scope(self):
        query = RetrievalQuery(
            raw_question="question",
            company=RetrievalCompany(name="", ticker=""),
            periods=[],
            period_kind=None,
            statement_scope=None,
            target_metrics=[],
            derived_target=None,
            evidence_sources=[],
            requested_scale=None,
            requested_unit=None,
            query_texts=["question"],
            eligible_source_types=[EvidenceSource.TABLE],
        )

        self.assertIsNone(query.period_kind)
        self.assertIsNone(query.statement_scope)


if __name__ == "__main__":
    unittest.main()
