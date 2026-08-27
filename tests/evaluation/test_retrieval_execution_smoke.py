"""Tests for the deterministic retrieval-execution smoke core (ADR-057).

The ``Batch3Retriever`` construction needs real artifacts + pinned weights and
is exercised only on the GPU host; here the retriever is a stand-in so the
twice-run comparison / crash / timeout accounting is verified.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from src.evaluation.run_retrieval_execution_smoke import (
    RETRIEVAL_EXECUTION_SMOKE_SCHEMA_VERSION,
    RetrievalSmokeError,
    load_smoke_queries,
    run_retrieval_execution_smoke,
)
from src.retrieval.schemas import RetrievalCompany, RetrievalQuery
from src.supervisor.schemas import EvidenceSource
from src.understanding.schemas import StatementScope


def _query() -> RetrievalQuery:
    return RetrievalQuery(
        raw_question="ROE cua AAA nam 2015",
        company=RetrievalCompany(name="Nhua An Phat", ticker="AAA"),
        periods=["2015"],
        period_kind=None,
        statement_scope=StatementScope.HOP_NHAT,
        target_metrics=["Doanh thu"],
        derived_target=None,
        evidence_sources=[EvidenceSource.TABLE],
        requested_scale=None,
        requested_unit=None,
        query_texts=["ROE cua AAA nam 2015"],
        eligible_source_types=[EvidenceSource.TABLE],
    )


class _Candidate:
    def __init__(self, tag: str) -> None:
        self.tag = tag

    def to_dict(self) -> dict:
        return {"candidate_id": self.tag, "rank": 1}


class _Stable:
    def retrieve(self, query, *, top_k, eligible_source_types):
        assert eligible_source_types  # non-empty
        return [_Candidate("a"), _Candidate("b")]


class _Flaky:
    def __init__(self) -> None:
        self.n = 0

    def retrieve(self, query, *, top_k, eligible_source_types):
        self.n += 1
        return [_Candidate(f"run{self.n}")]


class _Crash:
    def retrieve(self, *args, **kwargs):
        raise RuntimeError("backend down")


class RetrievalExecutionSmokeTests(unittest.TestCase):
    def test_stable_retriever_is_byte_identical(self):
        result = run_retrieval_execution_smoke(_Stable(), [_query(), _query()])
        self.assertTrue(result["ran"])
        self.assertTrue(result["byte_identical"])
        self.assertEqual(result["crashes"], 0)
        self.assertEqual(result["timeouts"], 0)
        self.assertEqual(result["sample_size"], 2)
        self.assertEqual(result["schema_version"], RETRIEVAL_EXECUTION_SMOKE_SCHEMA_VERSION)

    def test_nondeterministic_retriever_is_not_byte_identical(self):
        result = run_retrieval_execution_smoke(_Flaky(), [_query()])
        self.assertFalse(result["byte_identical"])
        self.assertEqual(result["crashes"], 0)

    def test_crash_is_counted_and_fails_identity(self):
        result = run_retrieval_execution_smoke(_Crash(), [_query()])
        self.assertEqual(result["crashes"], 1)
        self.assertFalse(result["byte_identical"])
        self.assertFalse(result["per_query"][0]["ok"])

    def test_timeout_is_counted(self):
        result = run_retrieval_execution_smoke(
            _Stable(), [_query()], per_query_timeout_s=-1.0
        )
        self.assertEqual(result["timeouts"], 1)
        self.assertFalse(result["per_query"][0]["ok"])

    def test_eligible_source_types_falls_back_to_evidence_sources(self):
        # Directly constructed without eligible_source_types (defaults to None
        # then RetrievalQuery normalises it from evidence_sources).
        query = RetrievalQuery(
            raw_question="q",
            company=RetrievalCompany(name="Nhua An Phat", ticker="AAA"),
            periods=["2015"],
            period_kind=None,
            statement_scope=None,
            target_metrics=["Doanh thu"],
            derived_target=None,
            evidence_sources=[EvidenceSource.TABLE],
            requested_scale=None,
            requested_unit=None,
            query_texts=["q"],
        )
        result = run_retrieval_execution_smoke(_Stable(), [query])
        self.assertTrue(result["byte_identical"])

    def test_smoke_result_is_the_shape_the_runner_gates_on(self):
        result = run_retrieval_execution_smoke(_Stable(), [_query()])
        for key in ("ran", "byte_identical", "crashes", "timeouts", "sample_size"):
            self.assertIn(key, result)

    def test_load_queries_parses_jsonl(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "q.jsonl"
            path.write_text(
                json.dumps(_query().to_dict()) + "\n\n" + json.dumps(_query().to_dict()) + "\n",
                encoding="utf-8",
            )
            queries = load_smoke_queries(path)
        self.assertEqual(len(queries), 2)
        self.assertIsInstance(queries[0], RetrievalQuery)

    def test_load_queries_rejects_empty_and_malformed(self):
        with tempfile.TemporaryDirectory() as directory:
            empty = Path(directory) / "empty.jsonl"
            empty.write_text("\n  \n", encoding="utf-8")
            with self.assertRaises(RetrievalSmokeError) as raised:
                load_smoke_queries(empty)
            self.assertEqual(raised.exception.code, "SMOKE_QUERIES_EMPTY")

            bad = Path(directory) / "bad.jsonl"
            bad.write_text('{"raw_question": "x"}\n', encoding="utf-8")
            with self.assertRaises(RetrievalSmokeError) as raised:
                load_smoke_queries(bad)
            self.assertEqual(raised.exception.code, "SMOKE_QUERIES_INVALID")


if __name__ == "__main__":
    unittest.main()
