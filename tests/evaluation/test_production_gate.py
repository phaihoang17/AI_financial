import argparse
import json
from pathlib import Path

import pytest

from src.evaluation import gold_review
from src.evaluation.production_gate import build_report, capacity_gate, evidence_gate, human_review_gate

QUESTION = "Tổng tài sản của AAA năm 2023 theo báo cáo hợp nhất là bao nhiêu?"


def _case(question_id, cell="cell-1"):
    return {"question_id": question_id, "question": QUESTION, "company": {"ticker": "AAA", "name": "AAA"},
            "period": "2023", "period_kind": "NAM", "statement_scope": "HOP_NHAT", "metric": "TOTAL_ASSETS",
            "operation": "NONE", "gold_report_id": "r", "gold_table_id": "t", "gold_paragraph_id": None,
            "gold_chunk_ids": [], "gold_source_cell_ids": [cell], "annotation_status": "AUTO_DERIVABLE",
            "gold_answer": {"value": "100", "unit": "VND", "scale": "RAW"},
            "provenance": {"source": "unit-test", "method": "AUTO_DERIVED"}}


def _response(value=100, cells=("cell-1",), status="ANSWERED", executed=True):
    attempt = {"llm_usage": [{"prompt_tokens": 10, "completion_tokens": 5}], "verification": {"passed": True},
               "execution": {"success": executed} if executed is not None else None}
    return {"question": QUESTION, "status": status, "reason_code": None, "unit": "VND", "elapsed_ms": 1000,
            "result": {"kind": "scalar", "value": value}, "sources": [{"source_cell_id": c} for c in cells],
            "attempts": [attempt]}


@pytest.fixture
def gold_root(tmp_path):
    root = tmp_path / "gold"
    for split, count in (("dev", 2), ("holdout", 2)):
        directory = root / split
        directory.mkdir(parents=True)
        cases = [_case(f"{split}-{i}") for i in range(count)]
        directory.joinpath("gold.jsonl").write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases))
        directory.joinpath("negative.jsonl").write_text(json.dumps({"question_id": f"{split}-n", "question": "Giá cổ phiếu?"}) + "\n")
        audit = {"cases": [{"question_id": c["question_id"], "operation": "LOOKUP", "statement": "BALANCE_SHEET",
                            "template": "CORPORATE", "sources": [{"cell_ids": ["cell-1", "cell-alt"]}]} for c in cases]}
        directory.joinpath("audit.json").write_text(json.dumps(audit))
    return root


def _runs(path, responses, split):
    responses[f"{split}-n"] = {"question": "Giá cổ phiếu?", "status": "UNSUPPORTED", "elapsed_ms": 1, "attempts": []}
    path.write_text(json.dumps({"measurement_source": "LIVE", "status": "COMPLETED", "responses": responses,
                                "pricing": {"cache_miss": 0.3, "cache_hit": 0.0, "output": 1.2}}))
    return path


def _args(root, tmp_path, holdout_responses, **extra):
    runs = _runs(tmp_path / "holdout-runs.json", holdout_responses, "holdout")
    return argparse.Namespace(gold_root=str(root), dev_runs=None, holdout_runs=str(runs), holdout_split="holdout", extra=None,
                              reviews=extra.get("reviews"), security=extra.get("security"),
                              stability=extra.get("stability"), api=extra.get("api"),
                              capacity=extra.get("capacity"), capacity_uncapped=None)


def test_correct_grounded_holdout_passes_quality_gates_and_missing_evidence_blocks(gold_root, tmp_path):
    report = build_report(_args(gold_root, tmp_path, {"holdout-0": _response(), "holdout-1": _response(cells=["cell-alt"])}))
    summary = report["summary"]
    for gate in ("correct_financial_answer", "code_execution_success", "provenance_correctness",
                 "unsupported_and_missing_data"):
        assert summary[gate] == "PASS", gate
    for gate in ("human_reviewed_gold", "sandbox_security", "resource_stability", "api_reliability",
                 "sandbox_capacity"):
        assert summary[gate] == "BLOCKED", gate
    assert report["production_ready"] is False


@pytest.mark.parametrize("mutation,gate", [
    ({"value": 101}, "correct_financial_answer"),
    ({"executed": False}, "code_execution_success"),
    ({"cells": ["cell-1", "unrelated"]}, "provenance_correctness"),
    ({"value": 101}, "unsupported_and_missing_data"),
])
def test_each_quality_failure_fails_its_gate(gold_root, tmp_path, mutation, gate):
    report = build_report(_args(gold_root, tmp_path, {"holdout-0": _response(**mutation), "holdout-1": _response()}))
    assert report["summary"][gate] == "FAIL"


def test_refusing_a_must_refuse_case_is_required(gold_root, tmp_path):
    args = _args(gold_root, tmp_path, {"holdout-0": _response(), "holdout-1": _response()})
    runs = json.loads(Path(args.holdout_runs).read_text())
    runs["responses"]["holdout-n"]["status"] = "ANSWERED"
    Path(args.holdout_runs).write_text(json.dumps(runs))
    assert build_report(args)["summary"]["unsupported_and_missing_data"] == "FAIL"


def test_human_review_needs_every_case_approved_by_a_named_reviewer(gold_root, tmp_path):
    decisions = gold_root / "review" / "decisions.jsonl"
    for split in ("dev", "holdout"):
        for i in range(2):
            gold_review.record_decision(decisions, f"{split}-{i}", "APPROVE", "Reviewer One")
    with pytest.raises(ValueError):
        gold_review.record_decision(decisions, "dev-0", "APPROVE", " ")
    with pytest.raises(ValueError):
        gold_review.record_decision(decisions, "dev-0", "CORRECT", "Reviewer One")
    summary = gold_review.summarize(gold_root)
    assert summary["complete"] and summary["reviewers"] == ["Reviewer One"]
    splits = {"dev": {"case_count": 2, "case_results": [{"question_id": f"dev-{i}"} for i in range(2)]},
              "holdout": {"case_count": 2, "case_results": [{"question_id": f"holdout-{i}"} for i in range(2)]}}
    gate = human_review_gate(splits, summary)
    assert gate["approved"] == 4 and gate["status"] == "FAIL"  # 4 cases is below the 200-case minimum
    gold_review.record_decision(decisions, "dev-1", "REJECT", "Reviewer Two", note="wrong column")
    assert gold_review.summarize(gold_root)["rejected"] == 1


def test_evidence_and_capacity_gates_read_their_reports(tmp_path):
    assert evidence_gate("sandbox_security", {"passed": True, "escapes": 0})["status"] == "PASS"
    assert evidence_gate("sandbox_security", {"passed": False, "escapes": 1})["status"] == "FAIL"
    capped = {"passed": True, "wrapper": "nsjail", "levels": [{"concurrency": 64, "outcomes": {"BENIGN_CORRECT": 128},
              "benign_latency_ms": {"p95": 1045.0}, "leaks": {}}]}
    gate = capacity_gate(capped, None)
    assert gate["status"] == "PASS" and gate["configured_cap"] == 32


def test_page_decisions_ingest_once_per_reviewer_and_time(gold_root):
    rows = [{"question_id": "dev-0", "decision": "APPROVE", "reviewer_id": "u_1", "reviewed_at": "2026-10-14T01:00:00Z"},
            {"question_id": "dev-1", "decision": "CORRECT", "corrected_value": "99", "reviewer_id": "u_1",
             "reviewed_at": "2026-10-14T01:01:00Z"}]
    assert gold_review.ingest(gold_root, rows, source="artifact-user") == 2
    assert gold_review.ingest(gold_root, rows, source="artifact-user") == 0
    summary = gold_review.summarize(gold_root)
    assert summary["reviewers"] == ["artifact-user:u_1"] and summary["rejected"] == 1
