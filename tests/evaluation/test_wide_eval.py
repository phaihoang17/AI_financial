import pytest

from src.evaluation.e2e_fixtures import e2e_fixture_cases
from src.evaluation.gold_retrieval import GoldRetrievalCase
from src.evaluation.wide_eval import (
    ACCEPTANCE_THRESHOLDS,
    evaluate,
    generation_cost,
    percentile,
    wilson_interval,
)


@pytest.fixture
def run():
    # Synthetic fixture states exercise the scorer; they are never run evidence.
    case = next(c for c in e2e_fixture_cases() if c.case_id == "growth-rate")
    state = case.graph_factory().run(request_id="q1", raw_question=case.raw_question).state
    gold = {
        "question_id": "q1", "question": case.raw_question,
        "company": {"ticker": "AAA", "name": "Fixture Company"},
        "period": "2015", "period_kind": "NAM", "statement_scope": "HOP_NHAT",
        "metric": "LNST", "operation": "GROWTH", "gold_report_id": "report",
        "gold_table_id": "table", "gold_paragraph_id": None, "gold_chunk_ids": [],
        "gold_source_cell_ids": ["cell-2015-125"],
        "gold_answer": {"value": "25", "scale": "PERCENT", "unit": None},
        "annotation_status": "VERIFIED", "provenance": {"source": "unit-test", "method": "MANUAL"},
    }
    runs = {"measurement_source": "LIVE", "states": [state.to_dict()],
            "seconds_by_question": {"q1": 10.0, "n1": 2.0},
            "generations_by_question": {"q1": [{"prompt_tokens": 1000, "prompt_cache_hit_tokens": 400,
                                                "completion_tokens": 100}], "n1": []}}
    return gold, runs


def _negative(outcome_id="n1"):
    return [{"question_id": outcome_id, "question": "ROE?", "expected": "NO_ANSWER", "kind": "UNSUPPORTED_METRIC"}]


def test_correct_answer_is_scored_and_metrics_are_computed(run):
    gold, runs = run
    report = evaluate([GoldRetrievalCase.from_dict(gold)], _negative(), runs)
    assert report["correct_count"] == 1
    assert report["metrics"]["answer_accuracy"] == 1.0
    assert report["metrics"]["answered_precision"] == 1.0
    assert report["metrics"]["latency_p50_s"] == 2.0
    assert report["metrics"]["llm_calls_mean"] == 0.5
    assert {c["name"] for c in report["threshold_checks"]} == set(ACCEPTANCE_THRESHOLDS)
    # The negative question has no recorded state, so the run is incomplete.
    assert report["run_complete"] is False


@pytest.mark.parametrize("answer", [
    {"value": "24", "scale": "PERCENT", "unit": None},
    {"value": "25", "scale": "PERCENT", "unit": "VND"},
])
def test_wrong_answers_count_against_precision(run, answer):
    gold, runs = run
    gold["gold_answer"] = answer
    report = evaluate([GoldRetrievalCase.from_dict(gold)], [], runs)
    assert report["wrong_answer_count"] == 1
    assert report["metrics"]["answered_precision"] == 0.0
    assert report["verdict"] == "NOT_ACCEPTED"


def test_ungrounded_answer_is_not_correct(run):
    gold, runs = run
    gold["gold_source_cell_ids"] = ["another-cell"]
    report = evaluate([GoldRetrievalCase.from_dict(gold)], [], runs)
    assert report["case_results"][0]["value_correct"] is True
    assert report["case_results"][0]["grounding_correct"] is False
    assert report["correct_count"] == 0


def test_missing_run_counts_as_failure(run):
    gold, runs = run
    runs["states"] = []
    report = evaluate([GoldRetrievalCase.from_dict(gold)], [], runs)
    assert report["case_results"][0]["outcome"] == "MISSING_RUN"
    assert report["metrics"]["answer_accuracy"] == 0.0


def test_answered_negative_is_a_false_answer(run):
    gold, runs = run
    report = evaluate([], _negative("q1"), runs)
    assert report["metrics"]["negative_false_answer_count"] == 1
    assert "negative_false_answer_count" in report["failed_thresholds"]


def test_cost_percentile_and_interval_helpers():
    cost = generation_cost({"prompt_tokens": 1_000_000, "prompt_cache_hit_tokens": 0, "completion_tokens": 0})
    assert cost == pytest.approx(0.30)
    assert generation_cost({"prompt_tokens": 10, "prompt_cache_hit_tokens": 10, "completion_tokens": 0}) < 1e-6
    assert percentile([5, 1, 3, 2, 4], 0.5) == 3
    assert percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.95) == 10
    assert percentile([], 0.5) is None
    low, high = wilson_interval(85, 100)
    assert low < 0.85 < high
    assert wilson_interval(0, 0) is None
