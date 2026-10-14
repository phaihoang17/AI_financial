import copy
import json

import pytest

from src.evaluation.corpus_reference_index import CorpusReferenceIndex, ReportMeta
from src.evaluation.e2e_fixtures import e2e_fixture_cases
from src.evaluation.gold_retrieval import GoldRetrievalCase
from src.evaluation.production_baseline import (
    BASELINE_RUNS_SCHEMA_VERSION,
    evaluate_production_baseline,
)
from src.evaluation.run_baseline_eval import main
from src.understanding.schemas import SchemaValidationError


@pytest.fixture
def inputs():
    # Synthetic inputs test the scorer, never serve as production run evidence.
    case = next(c for c in e2e_fixture_cases() if c.case_id == "growth-rate")
    state = case.graph_factory().run(request_id="q1", raw_question=case.raw_question).state
    gold = GoldRetrievalCase.from_dict({
        "question_id": "q1", "question": case.raw_question,
        "company": {"ticker": "AAA", "name": "Fixture Company"},
        "period": "2015", "period_kind": "NAM", "statement_scope": "HOP_NHAT",
        "metric": "LNST", "operation": "GROWTH", "gold_report_id": "report",
        "gold_table_id": "table", "gold_paragraph_id": None, "gold_chunk_ids": [],
        "gold_source_cell_ids": ["cell-2015-125"],
        "gold_answer": {"value": "25", "scale": "PERCENT", "unit": None},
        "annotation_status": "VERIFIED", "provenance": {"source": "unit-test", "method": "MANUAL"},
    })
    index = CorpusReferenceIndex("corpus", "fingerprint")
    index.reports["report"] = ReportMeta("report", "AAA", "Fixture Company", 2015, "HOP_NHAT")
    index.table_to_report["table"] = "report"
    index.source_cell_ids.update({"cell-2015-125", "other-cell"})
    runs = {"schema_version": BASELINE_RUNS_SCHEMA_VERSION, "measurement_source": "LIVE",
            "corpus_artifact_id": "corpus", "vector_artifact_id": "vector",
            "bm25_artifact_id": "bm25", "states": [state.to_dict()]}
    artifact_names = ["corpus_index_compatibility", "bm25_compatibility", "no_table_count_substitution",
        "canonical_corpus_chunk_count", "vector_count_equals_canonical_chunk_count",
        "shard_and_sqlite_integrity", "embedding_fingerprint"]
    live_names = ["live_bge_m3_embedding_parity", "live_bge_reranker_parity",
                  "deterministic_retrieval_execution"]
    checks = [{"name": n, "status": "PASS", "category": "ARTIFACT_SERVING",
               "evidence": "LIVE" if n in live_names else "ARTIFACT", "detail": {}}
              for n in artifact_names + live_names]
    checks[0]["detail"] = {"artifact_id": "vector", "corpus_fingerprint": "fingerprint"}
    checks[1]["detail"] = {"artifact_id": "bm25", "corpus_fingerprint": "fingerprint"}
    validation = {"schema_version": "m10-retrieval-production-validation-v1",
        "overall_status": "PASS", "artifact_status": "PASS", "live_serving_status": "PASS",
        "clears_gpu_pending": True, "artifact_serving_checks": checks}
    return [gold], index, runs, validation


def test_measured_baseline_scores_full_gold_and_labels_replay(inputs):
    report = evaluate_production_baseline(*inputs)
    assert report["established"] is True
    assert report["status"] == "MEASURED"
    assert report["evaluation_mode"] == "RECORDED_E2E"
    assert report["answer_accuracy"] == 1.0
    assert report["trace_accuracy"] is None
    assert report["acceptance_thresholds"] is None
    assert json.loads(json.dumps(report)) == report


@pytest.mark.parametrize("answer", [
    {"value": "24", "scale": "PERCENT", "unit": None},
    {"value": "25", "scale": "RAW", "unit": None},
    {"value": "25", "scale": "PERCENT", "unit": "VND"},
    {"value": "0.25", "scale": "PERCENT", "unit": None},
])
def test_value_scale_and_unit_errors_are_measured_not_hidden(inputs, answer):
    cases, index, runs, validation = inputs
    raw = cases[0].to_dict()
    raw["gold_answer"] = answer
    report = evaluate_production_baseline([GoldRetrievalCase.from_dict(raw)], index, runs, validation)
    assert report["established"] is True  # A measured baseline need not have 100% accuracy.
    assert report["answer_accuracy"] == 0.0


def test_missing_run_is_in_denominator_and_blocks_establishment(inputs):
    cases, index, runs, validation = inputs
    raw = cases[0].to_dict()
    raw["question_id"] = "q2"
    report = evaluate_production_baseline(cases + [GoldRetrievalCase.from_dict(raw)], index, runs, validation)
    assert report["scored_case_count"] == 2
    assert report["answer_accuracy"] == 0.5
    assert report["established"] is False
    assert "E2E_RUNS_INCOMPLETE" in report["blockers"]


def test_unknown_gold_scale_is_not_assumed_raw(inputs):
    cases, index, runs, validation = inputs
    raw = cases[0].to_dict()
    raw["gold_answer"]["scale"] = None
    report = evaluate_production_baseline([GoldRetrievalCase.from_dict(raw)], index, runs, validation)
    assert report["answer_accuracy"] is None
    assert report["blockers"] == ["GOLD_SCALE_UNRESOLVED"]


@pytest.mark.parametrize("mutation", ["fixture", "fingerprint", "missing_probe", "skipped_probe", "fixture_probe"])
def test_artifact_and_live_preconditions_cannot_be_substituted(inputs, mutation):
    _, _, runs, validation = inputs
    if mutation == "fixture":
        runs["measurement_source"] = "CPU_FIXTURE"
    elif mutation == "fingerprint":
        validation["artifact_serving_checks"][0]["detail"]["corpus_fingerprint"] = "wrong"
    elif mutation == "missing_probe":
        validation["artifact_serving_checks"].pop()
    elif mutation == "skipped_probe":
        validation["artifact_serving_checks"][-1]["status"] = "SKIPPED"
    else:
        validation["artifact_serving_checks"][-1]["evidence"] = "ARTIFACT"
    report = evaluate_production_baseline(*inputs)
    assert report["established"] is False
    assert report["answer_accuracy"] is None


def test_wrong_cell_in_same_table_cannot_pass_on_coincidental_value(inputs):
    cases, index, runs, validation = inputs
    raw = cases[0].to_dict()
    raw["gold_source_cell_ids"] = ["other-cell"]
    report = evaluate_production_baseline([GoldRetrievalCase.from_dict(raw)], index, runs, validation)
    assert report["case_results"][0]["value_correct"] is True
    assert report["case_results"][0]["grounding_correct"] is False
    assert report["answer_accuracy"] == 0.0


def test_equivalent_magnitude_scales_reuse_canonical_conversion(inputs):
    cases, index, runs, validation = inputs
    case = next(c for c in e2e_fixture_cases() if c.case_id == "average")
    state = case.graph_factory().run(request_id="q1", raw_question=case.raw_question).state
    datum = state.final_response.answer.output.values[0]
    raw = cases[0].to_dict()
    raw.update(question=case.raw_question, gold_source_cell_ids=["cell-2015-30"],
               gold_answer={"value": "20", "scale": "MILLION", "unit": datum.unit})
    index.source_cell_ids.add("cell-2015-30")
    runs["states"] = [state.to_dict()]
    report = evaluate_production_baseline([GoldRetrievalCase.from_dict(raw)], index, runs, validation)
    assert report["answer_accuracy"] == 1.0


@pytest.mark.parametrize("mutation", ["bootstrap", "unknown_cell", "scope", "duplicate_gold"])
def test_bad_or_unverified_gold_cannot_establish_baseline(inputs, mutation):
    cases, index, runs, validation = inputs
    raw = cases[0].to_dict()
    if mutation == "bootstrap":
        raw["provenance"]["method"] = "BOOTSTRAP_UNVERIFIED"
    elif mutation == "unknown_cell":
        raw["gold_source_cell_ids"] = ["absent"]
    elif mutation == "scope":
        raw["statement_scope"] = "RIENG"
    else:
        cases.append(cases[0])
    if mutation != "duplicate_gold":
        cases = [GoldRetrievalCase.from_dict(raw)]
    report = evaluate_production_baseline(cases, index, runs, validation)
    assert report["blockers"] == ["GOLD_VALIDATION_FAILED"]


def test_unverified_answers_do_not_enter_accuracy(inputs):
    cases, index, runs, validation = inputs
    raw = cases[0].to_dict()
    raw.update(question_id="q2", annotation_status="NEEDS_MANUAL_ANNOTATION")
    report = evaluate_production_baseline(cases + [GoldRetrievalCase.from_dict(raw)], index, runs, validation)
    assert report["scored_case_count"] == 1
    assert report["skipped_case_count"] == 1
    assert report["answer_accuracy"] == 1.0


def test_production_cli_scores_export_and_rejects_invalid_input(inputs, tmp_path, monkeypatch, capsys):
    from src.evaluation.gold_retrieval import dump_gold_cases

    cases, index, runs, validation = inputs
    gold = tmp_path / "gold.jsonl"
    gold.write_text(dump_gold_cases(cases))
    export = tmp_path / "runs.json"
    export.write_text(json.dumps(runs))
    retrieval = tmp_path / "retrieval.json"
    retrieval.write_text(json.dumps(validation))
    monkeypatch.setattr("src.evaluation.run_baseline_eval.load_or_build_reference_index", lambda _: index)
    args = ["--mode", "production", "--gold", str(gold), "--corpus-artifact", "corpus",
            "--runs", str(export), "--retrieval-validation", str(retrieval)]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["answer_accuracy"] == 1.0
    export.write_text("not json")
    assert main(args) == 1
    assert json.loads(capsys.readouterr().out)["established"] is False


def test_lookup_does_not_satisfy_strong_strict_baseline(inputs):
    cases, index, runs, validation = inputs
    case = next(c for c in e2e_fixture_cases() if c.case_id == "lookup")
    state = case.graph_factory().run(request_id="q1", raw_question=case.raw_question).state
    raw = cases[0].to_dict()
    raw["question"] = case.raw_question
    runs["states"] = [state.to_dict()]
    report = evaluate_production_baseline([GoldRetrievalCase.from_dict(raw)], index, runs, validation)
    assert "STRONG_STRICT_CONFIGURATION_REQUIRED" in report["blockers"]


def test_abstention_is_incorrect_and_preserves_native_failure(inputs):
    cases, index, runs, validation = inputs
    case = next(c for c in e2e_fixture_cases() if c.case_id == "security-abstain")
    state = case.graph_factory().run(request_id="q1", raw_question=case.raw_question).state
    raw = cases[0].to_dict()
    raw["question"] = case.raw_question
    runs["states"] = [state.to_dict()]
    report = evaluate_production_baseline([GoldRetrievalCase.from_dict(raw)], index, runs, validation)
    assert report["answer_accuracy"] == 0.0
    assert report["execution_success_rate"] == 0.0
    assert report["verification_pass_rate"] == 0.0
    assert report["case_results"][0]["failure_code"] == state.failure_attribution.code


@pytest.mark.parametrize("mutation", ["duplicate", "nonterminal", "question", "unknown_request"])
def test_bad_checkpoint_export_is_rejected(inputs, mutation):
    _, _, runs, _ = inputs
    if mutation == "duplicate":
        runs["states"].append(copy.deepcopy(runs["states"][0]))
    elif mutation == "nonterminal":
        runs["states"][0]["phase"] = "NLU"
    elif mutation == "question":
        runs["states"][0]["raw_question"] = "different question"
    else:
        runs["states"][0]["request_id"] = "unknown"
    with pytest.raises(SchemaValidationError):
        evaluate_production_baseline(*inputs)


def test_production_cli_exits_nonzero_for_missing_prerequisites(capsys):
    assert main(["--mode", "production"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["established"] is False
    assert "MISSING_RUNS" in report["blockers"]
