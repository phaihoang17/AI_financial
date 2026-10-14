"""Production readiness gate over measured evidence (ADR-072).

Every threshold below is checked from recorded evidence only; a gate whose
evidence is missing is ``BLOCKED``, never ``PASS``. The headline quality gates
are scored on the frozen holdout of ``gold/production-v1``; the development
split is reported alongside for diagnosis.

| Gate | Threshold |
|---|---|
| human-reviewed gold | 200–300 cases, ≥100 independent holdout, every case approved by a person |
| correct financial answer | ≥95% overall, ≥90% in every critical group (operation, statement, template) |
| code execution success | ≥99% of valid questions with sufficient data |
| provenance | 100% of answered cases cite exactly their audited source cells |
| unsupported / missing data | no fabricated number; every must-refuse case refused |
| sandbox security | 0 escapes / unauthorized access in the security suite |
| resource stability | no growth trend across stress and soak |
| API reliability | SLO, monitoring, alerting and rollback defined and tested |
| sandbox capacity | ≤32 concurrent M7 workers per host under NsJail, measured |
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import tomllib
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src.evaluation.gold_retrieval import load_gold_file
from src.text_to_pandas.benchmark import evaluate

GATE_SCHEMA_VERSION = "production-gate-v1"
ACCURACY_MIN, GROUP_MIN, EXECUTION_MIN = 0.95, 0.90, 0.99
GROUP_FIELDS = ("operation", "statement", "template")
GOLD_RANGE, HOLDOUT_MIN = (200, 300), 100


def _gate(name: str, status: str, detail: Mapping[str, Any]) -> Dict[str, Any]:
    return {"gate": name, "status": status, **detail}


def _load_json(path: Optional[str | Path]) -> Optional[Dict[str, Any]]:
    if path is None or not Path(path).is_file():
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


def score_split(gold: Path, negatives: Path, audit: Path, runs: Path) -> Dict[str, Any]:
    """Benchmark one split, then add group, execution, provenance and refusal metrics."""
    # Gold defects found by src/evaluation/gold_qa.py are excluded alike in every split.
    qa_path = gold.parent / "qa.json"
    defects = set(json.loads(qa_path.read_text(encoding="utf-8"))["defective_case_ids"]) if qa_path.is_file() else set()
    cases = [case for case in load_gold_file(gold) if case.question_id not in defects]
    negative_rows = [json.loads(line) for line in negatives.read_text(encoding="utf-8").splitlines() if line.strip()]
    audit_data = json.loads(audit.read_text(encoding="utf-8"))
    audit_data = {**audit_data, "cases": [row for row in audit_data["cases"] if row["question_id"] not in defects]}
    run_data = json.loads(runs.read_text(encoding="utf-8"))
    report = evaluate(cases, negative_rows, run_data, audit=audit_data)
    audited = {row["question_id"]: row for row in audit_data["cases"]}
    responses = run_data.get("responses", {})
    groups: Dict[str, Dict[str, List[bool]]] = {field: defaultdict(list) for field in GROUP_FIELDS}
    execution = {"eligible": 0, "succeeded": 0, "reached_execution": 0, "first_attempt_succeeded": 0}
    provenance = {"answered": 0, "exact": 0, "failures": []}
    for row in report["case_results"]:
        info = audited[row["question_id"]]
        for field in GROUP_FIELDS:
            groups[field][info[field]].append(row["correct"])
        response = responses.get(row["question_id"]) or {}
        # Insufficient data: the deterministic reference itself has no catalog input.
        if response.get("reason_code") != "REFERENCE_INPUT_UNAVAILABLE":
            execution["eligible"] += 1
            executed = [attempt for attempt in response.get("attempts", []) if attempt.get("execution")]
            execution["reached_execution"] += bool(executed)
            execution["succeeded"] += bool(executed and executed[-1]["execution"].get("success"))
            execution["first_attempt_succeeded"] += bool(executed and executed[0]["execution"].get("success")
                                                         and executed[0] is response["attempts"][0])
        if row["answered"]:
            provenance["answered"] += 1
            cited = {source["source_cell_id"] for source in response.get("sources", [])}
            required = [set(source["cell_ids"]) for source in info["sources"]]
            allowed = set().union(*required) if required else set()
            exact = bool(required) and all(group & cited for group in required) and cited <= allowed
            provenance["exact"] += exact
            if not exact:
                provenance["failures"].append(row["question_id"])
    group_accuracy = {field: {name: {"cases": len(values), "correct": sum(values),
                                     "accuracy": sum(values) / len(values)}
                              for name, values in sorted(by_name.items())}
                      for field, by_name in groups.items()}
    negatives_answered = [row["question_id"] for row in report["negative_results"] if row["false_answer"]]
    return {
        "verdict_adr066": report["verdict"], "run_complete": report["run_complete"],
        "telemetry_complete": report["telemetry_complete"], "catalog_id": report["catalog_id"],
        "model_id": report["model_id"], "provider": report.get("provider"),
        "source_fingerprint": report["source_fingerprint"],
        "case_count": report["gold_case_count"], "negative_case_count": report["negative_case_count"],
        "gold_defects_excluded": sorted(defects),
        "correct_count": report["correct_count"], "answered_count": report["answered_count"],
        "accuracy": report["metrics"]["answer_accuracy"], "accuracy_wilson95": report["accuracy_wilson95"],
        "group_accuracy": group_accuracy,
        "min_group_accuracy": min((item["accuracy"] for by_name in group_accuracy.values()
                                   for item in by_name.values()), default=None),
        "execution": {**execution, "rate": execution["succeeded"] / execution["eligible"]
                      if execution["eligible"] else None},
        "provenance": {**provenance, "rate": provenance["exact"] / provenance["answered"]
                       if provenance["answered"] else None},
        "wrong_answer_count": report["wrong_answer_count"],
        "negative_false_answers": negatives_answered,
        "latency_p95_s": report["metrics"]["latency_p95_s"], "cost_mean_usd": report["metrics"]["cost_mean_usd"],
        "reason_code_counts": report["reason_code_counts"],
        "case_results": report["case_results"],
    }


def human_review_gate(splits: Mapping[str, Dict[str, Any]], reviews: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    total = sum(split["case_count"] for split in splits.values())
    holdout = splits.get("holdout", {}).get("case_count", 0)
    detail = {"total_cases": total, "holdout_cases": holdout,
              "size_ok": GOLD_RANGE[0] <= total <= GOLD_RANGE[1], "holdout_ok": holdout >= HOLDOUT_MIN}
    if reviews is None:
        return _gate("human_reviewed_gold", "BLOCKED", {**detail, "approved": 0,
                     "reason": "no human review records (gold/production-v1/review/decisions.jsonl)"})
    approved = reviews.get("approved_case_ids", [])
    ids = {row["question_id"] for split in splits.values() for row in split["case_results"]}
    detail.update(approved=len(set(approved) & ids), rejected=reviews.get("rejected", 0),
                  reviewers=reviews.get("reviewers", []))
    complete = detail["approved"] == len(ids) and not detail["rejected"]
    status = "PASS" if complete and detail["size_ok"] and detail["holdout_ok"] else "FAIL"
    return _gate("human_reviewed_gold", status, detail)


def quality_gates(holdout: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not holdout.get("run_complete"):
        blocked = {"reason": "holdout run is missing or incomplete"}
        return [_gate(name, "BLOCKED", blocked) for name in
                ("correct_financial_answer", "code_execution_success", "provenance_correctness",
                 "unsupported_and_missing_data")]
    groups_ok = holdout["min_group_accuracy"] is not None and holdout["min_group_accuracy"] >= GROUP_MIN
    accuracy_ok = holdout["accuracy"] >= ACCURACY_MIN and groups_ok
    low = {f"{field}:{name}": item["accuracy"] for field, by_name in holdout["group_accuracy"].items()
           for name, item in by_name.items() if item["accuracy"] < GROUP_MIN}
    execution = holdout["execution"]
    provenance = holdout["provenance"]
    fabricated = holdout["wrong_answer_count"] + len(holdout["negative_false_answers"])
    return [
        _gate("correct_financial_answer", "PASS" if accuracy_ok else "FAIL", {
            "accuracy": holdout["accuracy"], "min": ACCURACY_MIN, "wilson95": holdout["accuracy_wilson95"],
            "min_group_accuracy": holdout["min_group_accuracy"], "group_min": GROUP_MIN,
            "groups_below_min": low, "group_accuracy": holdout["group_accuracy"]}),
        _gate("code_execution_success", "PASS" if execution["rate"] is not None and execution["rate"] >= EXECUTION_MIN
              else "FAIL", {**execution, "min": EXECUTION_MIN}),
        _gate("provenance_correctness", "PASS" if provenance["rate"] == 1.0 else "FAIL",
              {key: provenance[key] for key in ("answered", "exact", "rate", "failures")}),
        _gate("unsupported_and_missing_data", "PASS" if fabricated == 0 else "FAIL", {
            "wrong_answers": holdout["wrong_answer_count"],
            "negative_false_answers": holdout["negative_false_answers"],
            "negative_cases": holdout["negative_case_count"]}),
    ]


def evidence_gate(name: str, evidence: Optional[Dict[str, Any]], passed_key: str = "passed") -> Dict[str, Any]:
    if evidence is None:
        return _gate(name, "BLOCKED", {"reason": "evidence file missing"})
    return _gate(name, "PASS" if evidence.get(passed_key) is True else "FAIL",
                 {key: evidence[key] for key in evidence if key in (
                     "summary", "checks", "escapes", "levels_passed", "trend", "slo", "alerts", "rollback",
                     "measurement_scope", "limitations")})


def capacity_gate(capped: Optional[Dict[str, Any]], uncapped: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    settings = tomllib.loads((Path(__file__).resolve().parents[2] / "deploy" / "sandbox" / "sandbox.toml")
                             .read_text(encoding="utf-8"))
    cap = settings["capacity"]["m7"].get("linux-nsjail-v1")
    if capped is None:
        return _gate("sandbox_capacity", "BLOCKED", {"configured_cap": cap, "reason": "capped load evidence missing"})
    level = capped["levels"][0]
    detail = {"configured_cap": cap, "offered_concurrency": level["concurrency"], "outcomes": level["outcomes"],
              "p95_ms": level["benign_latency_ms"]["p95"], "leaks": level["leaks"],
              "uncapped_outcomes": None if uncapped is None else uncapped["levels"][0]["outcomes"],
              "measurement_scope": capped.get("wrapper", capped.get("isolation_mode"))}
    ok = cap is not None and cap <= 32 and capped["passed"] and level["concurrency"] > cap
    return _gate("sandbox_capacity", "PASS" if ok else "FAIL", detail)


def build_report(args: argparse.Namespace) -> Dict[str, Any]:
    # The gate's gold is the development split plus the scored holdout; any other
    # split given (e.g. a holdout already scored once, now development data) is
    # reported for diagnosis only.
    splits: Dict[str, Dict[str, Any]] = {}
    plan = [("dev", args.dev_runs), ("holdout", args.holdout_runs)]
    extra = [tuple(item.split("=", 1)) for item in getattr(args, "extra", None) or []]
    for split, runs in plan:
        directory = Path(args.gold_root) / (args.holdout_split if split == "holdout" else split)
        if runs and Path(runs).is_file():
            splits[split] = score_split(directory / "gold.jsonl", directory / "negative.jsonl",
                                        directory / "audit.json", Path(runs))
        else:
            cases = load_gold_file(directory / "gold.jsonl")
            splits[split] = {"case_count": len(cases), "case_results": [{"question_id": c.question_id} for c in cases],
                             "run_complete": False}
    diagnostics = {}
    for name, runs in extra:
        directory = Path(args.gold_root) / name
        diagnostics[name] = score_split(directory / "gold.jsonl", directory / "negative.jsonl",
                                        directory / "audit.json", Path(runs))
    gates = [human_review_gate(splits, _load_json(args.reviews))]
    gates += quality_gates(splits["holdout"])
    gates += [evidence_gate("sandbox_security", _load_json(args.security)),
              evidence_gate("resource_stability", _load_json(args.stability)),
              evidence_gate("api_reliability", _load_json(args.api)),
              capacity_gate(_load_json(args.capacity), _load_json(args.capacity_uncapped))]
    summary = {gate["gate"]: gate["status"] for gate in gates}
    strip = lambda split: {key: value for key, value in split.items() if key != "case_results"}
    return {"schema_version": GATE_SCHEMA_VERSION,
            "production_ready": all(status == "PASS" for status in summary.values()),
            "holdout_split": args.holdout_split,
            "summary": summary, "gates": gates,
            "splits": {name: strip(split) for name, split in splits.items()},
            "diagnostic_splits": {name: strip(split) for name, split in diagnostics.items()}}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gold-root", default="gold/production-v1")
    parser.add_argument("--dev-runs")
    parser.add_argument("--holdout-runs")
    parser.add_argument("--holdout-split", default="holdout", help="gold directory scored as the holdout")
    parser.add_argument("--extra", action="append", help="SPLIT=RUNS scored for diagnosis only")
    parser.add_argument("--reviews", default="gold/production-v1/review/summary.json")
    parser.add_argument("--security")
    parser.add_argument("--stability")
    parser.add_argument("--api")
    parser.add_argument("--capacity")
    parser.add_argument("--capacity-uncapped")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    report = build_report(args)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"production_ready": report["production_ready"], "summary": report["summary"]}, indent=2))
    return 0 if report["production_ready"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
