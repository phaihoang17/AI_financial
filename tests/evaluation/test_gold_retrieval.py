import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.evaluation.corpus_reference_index import (
    CorpusReferenceIndex,
    ReportMeta,
    build_reference_index,
)
from src.evaluation.gold_retrieval import (
    GoldAnnotationStatus,
    GoldContractError,
    GoldRetrievalCase,
    classify_question,
    compute_gold_retrieval_metrics,
    dump_gold_cases,
    load_gold_file,
    score_answer_correctness,
    validate_gold_cases,
)
from src.supervisor.schemas import EvidenceSource


def _index() -> CorpusReferenceIndex:
    idx = CorpusReferenceIndex(corpus_artifact_id="corpus", source_inventory_sha256="sha")
    idx.reports["report_A"] = ReportMeta("report_A", "AAA", "CTCP A", 2015, "HOP_NHAT")
    idx.reports["report_B"] = ReportMeta("report_B", "BBB", "CTCP B", 2019, "RIENG")
    idx.tickers_by_year_scope["2015::HOP_NHAT"] = {"AAA"}
    idx.tickers_by_year_scope["2019::RIENG"] = {"BBB"}
    idx.table_to_report["table_A1"] = "report_A"
    idx.paragraph_ids.add("para_B1")
    idx.chunk_ids.update({"chunk_1", "chunk_2"})
    idx.source_cell_ids.update({"cell_1"})
    return idx


def _case(**overrides):
    base = {
        "question_id": "q1",
        "question": "ROE cua AAA nam 2015?",
        "company": {"ticker": "AAA", "name": "CTCP A"},
        "period": "2015",
        "period_kind": "NAM",
        "statement_scope": "HOP_NHAT",
        "metric": "ROE",
        "operation": "RATIO",
        "gold_report_id": "report_A",
        "gold_table_id": "table_A1",
        "gold_paragraph_id": None,
        "gold_chunk_ids": ["chunk_1"],
        "gold_source_cell_ids": ["cell_1"],
        "gold_answer": {"value": "0.12", "unit": None, "scale": None},
        "provenance": {"source": "unit-test", "method": "MANUAL", "notes": None},
        "annotation_status": "VERIFIED",
    }
    base.update(overrides)
    return base


class GoldSchemaTests(unittest.TestCase):
    def test_round_trip(self):
        cases = [GoldRetrievalCase.from_dict(_case())]
        text = dump_gold_cases(cases)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "g.jsonl"
            path.write_text("// header\n" + text, encoding="utf-8")
            reloaded = load_gold_file(path)
        self.assertEqual(reloaded[0].to_dict(), cases[0].to_dict())

    def test_unknown_key_rejected(self):
        with self.assertRaises(GoldContractError):
            load_gold_file(self._write(_case(extra="nope")))

    def test_missing_ticker_rejected(self):
        bad = _case()
        bad["company"] = {"name": "x"}
        with self.assertRaises(GoldContractError):
            load_gold_file(self._write(bad))

    def _write(self, *objs) -> Path:
        tmp = Path(tempfile.mkdtemp()) / "g.jsonl"
        tmp.write_text("\n".join(json.dumps(o) for o in objs), encoding="utf-8")
        return tmp


class GoldValidationTests(unittest.TestCase):
    def _load(self, *objs):
        tmp = Path(tempfile.mkdtemp()) / "g.jsonl"
        tmp.write_text("\n".join(json.dumps(o) for o in objs), encoding="utf-8")
        return load_gold_file(tmp)

    def test_clean_case_passes(self):
        report = validate_gold_cases(self._load(_case()), _index())
        self.assertTrue(report.ok, report.to_dict())
        self.assertEqual(report.gold_bearing_count, 1)

    def test_duplicate_question_id_is_error(self):
        report = validate_gold_cases(self._load(_case(), _case()), _index())
        self.assertFalse(report.ok)
        self.assertTrue(any(i.code == "DUPLICATE_QUESTION_ID" for i in report.issues))

    def test_missing_evidence_for_gold_status_is_error(self):
        bare = _case(
            question_id="q2",
            gold_report_id=None,
            gold_table_id=None,
            gold_paragraph_id=None,
            gold_chunk_ids=[],
            gold_source_cell_ids=[],
            gold_answer=None,
            annotation_status="AUTO_DERIVABLE",
        )
        report = validate_gold_cases(self._load(bare), _index())
        self.assertFalse(report.ok)
        self.assertTrue(any(i.code == "MISSING_EVIDENCE" for i in report.issues))

    def test_bootstrap_cannot_back_gold_status(self):
        boot = _case(question_id="q3")
        boot["provenance"] = {"source": "guess", "method": "BOOTSTRAP_UNVERIFIED", "notes": None}
        report = validate_gold_cases(self._load(boot), _index())
        self.assertFalse(report.ok)
        self.assertTrue(any(i.code == "BOOTSTRAP_NOT_GOLD" for i in report.issues))

    def test_unknown_corpus_ids_are_errors(self):
        bad = _case(
            question_id="q4",
            gold_report_id="report_ZZZ",
            gold_table_id="table_ZZZ",
            gold_chunk_ids=["chunk_missing"],
            gold_source_cell_ids=["cell_missing"],
        )
        report = validate_gold_cases(self._load(bad), _index())
        codes = {i.code for i in report.issues}
        self.assertIn("REPORT_NOT_IN_CORPUS", codes)
        self.assertIn("TABLE_NOT_IN_CORPUS", codes)
        self.assertIn("CHUNK_NOT_IN_CORPUS", codes)
        self.assertIn("SOURCE_CELL_NOT_IN_CORPUS", codes)

    def test_table_report_mismatch_is_error(self):
        idx = _index()
        idx.table_to_report["table_B1"] = "report_B"
        bad = _case(question_id="q5", gold_table_id="table_B1")
        report = validate_gold_cases(self._load(bad), idx)
        self.assertTrue(any(i.code == "TABLE_REPORT_MISMATCH" for i in report.issues))

    def test_no_index_emits_warning_not_error(self):
        report = validate_gold_cases(self._load(_case()), None)
        self.assertTrue(report.ok)
        self.assertTrue(any(i.code == "NO_CORPUS_INDEX" for i in report.issues))


class ClassificationTests(unittest.TestCase):
    def test_unsupported_unknown_ticker(self):
        result = classify_question({"question_id": "x", "company": {"ticker": "ZZZ"}}, _index())
        self.assertIs(result.status, GoldAnnotationStatus.UNSUPPORTED)

    def test_unsupported_no_matching_report(self):
        result = classify_question(
            {"question_id": "x", "company": {"ticker": "AAA"}, "period": "1999"}, _index()
        )
        self.assertIs(result.status, GoldAnnotationStatus.UNSUPPORTED)

    def test_auto_derivable_with_resolvable_pointer(self):
        result = classify_question(
            {
                "question_id": "x",
                "company": {"ticker": "AAA"},
                "period": "2015",
                "statement_scope": "HOP_NHAT",
                "report_id": "report_A",
                "table_id": "table_A1",
            },
            _index(),
        )
        self.assertIs(result.status, GoldAnnotationStatus.AUTO_DERIVABLE)

    def test_needs_manual_without_pointer(self):
        result = classify_question(
            {"question_id": "x", "company": {"ticker": "AAA"}, "period": "2015"}, _index()
        )
        self.assertIs(result.status, GoldAnnotationStatus.NEEDS_MANUAL_ANNOTATION)


def _cand(source_type, report_id, table_id=None, paragraph_id=None):
    return SimpleNamespace(
        source_type=source_type, report_id=report_id, table_id=table_id, paragraph_id=paragraph_id
    )


class MetricsTests(unittest.TestCase):
    def _load_one(self, obj):
        tmp = Path(tempfile.mkdtemp()) / "g.jsonl"
        tmp.write_text(json.dumps(obj), encoding="utf-8")
        return load_gold_file(tmp)

    def test_recall_and_mrr_over_gold_bearing_only(self):
        cases = self._load_one(_case())

        def retrieve_fn(_case):
            return [
                _cand(EvidenceSource.TEXT, "report_X"),
                _cand(EvidenceSource.TABLE, "report_A", table_id="table_A1"),
            ]

        metrics = compute_gold_retrieval_metrics(cases, retrieve_fn, k_values=(1, 5))
        self.assertEqual(metrics.evaluated_case_count, 1)
        self.assertEqual(metrics.recall_at_k[1], 0.0)
        self.assertEqual(metrics.recall_at_k[5], 1.0)
        self.assertAlmostEqual(metrics.mean_reciprocal_rank, 0.5)

    def test_non_gold_bearing_is_skipped(self):
        cases = self._load_one(
            _case(
                annotation_status="NEEDS_MANUAL_ANNOTATION",
                gold_answer=None,
            )
        )
        metrics = compute_gold_retrieval_metrics(cases, lambda c: [], k_values=(1,))
        self.assertEqual(metrics.evaluated_case_count, 0)
        self.assertEqual(metrics.skipped_case_count, 1)

    def test_answer_correctness_verified_only(self):
        cases = self._load_one(_case())
        scored = score_answer_correctness(cases, {"q1": "0.12000"})
        self.assertEqual(scored["scored_case_count"], 1)
        self.assertEqual(scored["correct_count"], 1)


class CorpusReferenceIndexTests(unittest.TestCase):
    def test_build_from_tiny_corpus(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "shards").mkdir()
            manifest = {
                "artifact_id": "abc",
                "source_inventory_sha256": "def",
                "reports": [
                    {
                        "report_id": "report_A",
                        "ticker": "AAA",
                        "company_name": "CTCP A",
                        "report_year": 2015,
                        "statement_scope": "HOP_NHAT",
                    }
                ],
            }
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            rec_table = {
                "report": {"report_id": "report_A", "ticker": "AAA", "report_year": 2015, "statement_scope": "HOP_NHAT"},
                "representation": {"representation_id": "rep_1", "source_type": "TABLE", "table_id": "table_A1", "paragraph_id": None, "report_id": "report_A"},
                "source_cell_ids": ["cell_1", "cell_2"],
                "chunk": {"chunk_id": "chunk_1"},
            }
            rec_text = {
                "report": {"report_id": "report_A", "ticker": "AAA", "report_year": 2015, "statement_scope": "HOP_NHAT"},
                "representation": {"representation_id": "rep_2", "source_type": "TEXT", "table_id": None, "paragraph_id": "para_1", "report_id": "report_A"},
                "source_cell_ids": [],
                "chunk": {"chunk_id": "chunk_2"},
            }
            (root / "shards" / "shard-000000.jsonl").write_text(
                json.dumps(rec_table) + "\n" + json.dumps(rec_text) + "\n", encoding="utf-8"
            )
            idx = build_reference_index(root)
        self.assertTrue(idx.has_report("report_A"))
        self.assertEqual(idx.table_report("table_A1"), "report_A")
        self.assertTrue(idx.has_paragraph("para_1"))
        self.assertTrue(idx.has_chunk("chunk_1") and idx.has_chunk("chunk_2"))
        self.assertTrue(idx.has_source_cell("cell_2"))
        self.assertTrue(idx.known_ticker("AAA"))
        self.assertEqual([m.report_id for m in idx.reports_for("AAA", 2015, "HOP_NHAT")], ["report_A"])
        round_trip = CorpusReferenceIndex.from_cache_dict(idx.to_cache_dict())
        self.assertEqual(round_trip.summary(), idx.summary())


if __name__ == "__main__":
    unittest.main()
