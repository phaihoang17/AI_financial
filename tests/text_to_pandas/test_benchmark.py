from copy import deepcopy

import pytest

from src.evaluation.gold_retrieval import GoldRetrievalCase
from src.text_to_pandas.benchmark import evaluate
from src.text_to_pandas.generator import PandasCodeGenerator, ScriptedClient
from src.text_to_pandas.understanding import discover, understand_question
from tests.text_to_pandas.test_pipeline import QUESTION


@pytest.fixture
def measurement():
    case = GoldRetrievalCase.from_dict({
        "question_id": "q1", "question": QUESTION, "company": {"ticker": "AAA", "name": "AAA"},
        "period": "2023", "period_kind": "NAM", "statement_scope": "HOP_NHAT", "metric": "LNST",
        "operation": "LOOKUP", "gold_report_id": "report", "gold_table_id": "table", "gold_paragraph_id": None,
        "gold_chunk_ids": [], "gold_source_cell_ids": ["cell"],
        "gold_answer": {"value": "125", "scale": "RAW", "unit": "VND"},
        "annotation_status": "VERIFIED", "provenance": {"source": "unit-test", "method": "MANUAL"},
    })
    response = {"question": QUESTION, "status": "ANSWERED", "result": {"kind": "scalar", "value": 125},
                "unit": "VND", "reason_code": None, "sources": [{"source_cell_id": "cell"}], "elapsed_ms": 1000,
                "attempts": [{"verification": {"passed": True}, "llm_usage": [{"prompt_tokens": 100, "completion_tokens": 20}]}]}
    negative = {"question_id": "n1", "question": "Missing period?"}
    runs = {"measurement_source": "LIVE", "status": "COMPLETED", "responses": {"q1": response,
            "n1": {"question": negative["question"], "status": "NEEDS_CLARIFICATION", "elapsed_ms": 1, "attempts": []}}}
    return case, negative, runs


def test_complete_measurement_passes_all_thresholds(measurement):
    case, negative, runs = measurement
    result = evaluate([case], [negative], runs)
    assert result["verdict"] == "ACCEPTED"
    assert result["metrics"]["answer_accuracy"] == 1


@pytest.mark.parametrize("mutation", ["missing-negative", "running", "fixture", "missing-usage", "missing-tokens", "runner-error", "wrong-source", "wrong-value"])
def test_incomplete_or_incorrect_measurements_cannot_promote(measurement, mutation):
    case, negative, original = measurement
    runs = deepcopy(original)
    if mutation == "missing-negative":
        del runs["responses"]["n1"]
    elif mutation == "running":
        runs["status"] = "RUNNING"
    elif mutation == "fixture":
        runs["measurement_source"] = "FIXTURE"
    elif mutation == "missing-usage":
        runs["responses"]["q1"]["attempts"][0]["llm_usage"] = []
    elif mutation == "missing-tokens":
        runs["responses"]["q1"]["attempts"][0]["llm_usage"][0]["prompt_tokens"] = None
    elif mutation == "runner-error":
        runs["runner_errors"] = {"q2": "RuntimeError"}
    elif mutation == "wrong-source":
        runs["responses"]["q1"]["sources"] = [{"source_cell_id": "wrong"}]
    elif mutation == "wrong-value":
        runs["responses"]["q1"]["result"]["value"] = 126
    assert evaluate([case], [negative], runs)["verdict"] == "NOT_ACCEPTED"


def test_failed_validation_has_no_verifier_report(measurement):
    case, negative, runs = measurement
    runs["responses"]["q1"].update(status="FAILED", result=None)
    runs["responses"]["q1"]["attempts"][0]["verification"] = None
    assert evaluate([case], [negative], runs)["correct_count"] == 0


def test_audited_duplicate_cells_are_alternatives(measurement):
    case, negative, runs = measurement
    audit = {"cases": [{"question_id": "q1", "sources": [{"cell_ids": ["cell", "duplicate-cell"]}]}]}
    assert evaluate([case], [negative], runs, audit=audit)["correct_count"] == 1


def test_each_audited_period_needs_a_source(measurement):
    case, negative, runs = measurement
    audit = {"cases": [{"question_id": "q1", "sources": [{"cell_ids": ["cell", "duplicate-cell"]},
                                                         {"cell_ids": ["prior-year-cell"]}]}]}
    assert evaluate([case], [negative], runs, audit=audit)["correct_count"] == 0


@pytest.mark.parametrize("bad", ["not json", {"status": "OK", "code": "result=1", "result_unit": [], "expected_result_type": "scalar"}])
def test_malformed_generation_is_typed(catalog, bad):
    u = understand_question(QUESTION, catalog)
    outcome = PandasCodeGenerator(ScriptedClient([bad])).generate(u, discover(u, catalog))
    assert outcome.program is None
    assert outcome.error_code in {"GENERATION_JSON_INVALID", "GENERATION_ENVELOPE_INVALID"}


def test_cost_uses_the_run_provider_price_list(measurement):
    case, negative, original = measurement
    runs = deepcopy(original)
    runs["pricing"] = {"cache_miss": 4.0, "cache_hit": 0.2, "output": 20.0}
    priced = evaluate([case], [negative], runs)["metrics"]["cost_mean_usd"]
    assert priced == pytest.approx((100 * 4.0 + 20 * 20.0) / 1e6 / 2)
    # An unpriced provider reports no cost, so the cost thresholds fail closed.
    runs["pricing"] = None
    result = evaluate([case], [negative], runs)
    assert result["metrics"]["cost_mean_usd"] is None and result["verdict"] == "NOT_ACCEPTED"
