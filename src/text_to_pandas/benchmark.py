"""Score live Pandas runs against independent wide gold and ADR-066 thresholds."""

import argparse
from collections import Counter
import json
from pathlib import Path

from src.evaluation.gold_retrieval import load_gold_file
from src.evaluation.wide_eval import (
    ACCEPTANCE_THRESHOLDS,
    PEAK_PRICE_PER_MTOK,
    _check,
    _value_matches,
    generation_cost,
    percentile,
    wilson_interval,
)


def evaluate(cases, negatives, runs, *, audit=None):
    responses = runs.get("responses", {})
    audited = {row["question_id"]: row for row in (audit or {}).get("cases", [])}
    results = []
    for case in cases:
        response = responses.get(case.question_id)
        if response and response["question"] != case.question:
            raise ValueError(f"question mismatch for {case.question_id}")
        answered = bool(response and response["status"] == "ANSWERED")
        datum = response.get("result") or {} if response else {}
        value_ok = answered and datum.get("kind") == "scalar" and _value_matches(str(datum.get("value")), case.gold_answer.value)
        source_ids = {source["source_cell_id"] for source in response.get("sources", [])} if response else set()
        if audit is not None:
            # Multiple cells within one audited source are alternatives (e.g.
            # a carried-forward statement row), not multiple required inputs.
            groups = audited.get(case.question_id, {}).get("sources", [])
            grounded = bool(groups) and all(set(group["cell_ids"]).intersection(source_ids) for group in groups)
        else:
            grounded = len(set(case.gold_source_cell_ids).intersection(source_ids)) >= (1 if case.period else 2)
        unit_ok = answered and response["unit"] == ("PERCENT" if case.gold_answer.scale == "PERCENT" else case.gold_answer.unit)
        verified = bool(response and response["attempts"] and (response["attempts"][-1].get("verification") or {}).get("passed"))
        results.append({"question_id": case.question_id, "operation": case.operation, "answered": answered,
                        "value_correct": bool(value_ok), "unit_correct": bool(unit_ok), "grounding_correct": grounded,
                        "correct": bool(value_ok and unit_ok and grounded and verified),
                        "status": response["status"] if response else "MISSING_RUN",
                        "reason_code": response["reason_code"] if response else "MISSING_RUN"})
    negative_results = []
    for case in negatives:
        response = responses.get(case["question_id"])
        if response and response["question"] != case["question"]:
            raise ValueError(f"question mismatch for {case['question_id']}")
        negative_results.append({"question_id": case["question_id"],
                                 "false_answer": bool(response and response["status"] == "ANSWERED"),
                                 "status": response["status"] if response else "MISSING_RUN"})
    ids = {case.question_id for case in cases} | {case["question_id"] for case in negatives}
    measured = [responses[question_id] for question_id in ids if question_id in responses]
    latency = [response["elapsed_ms"] / 1000 for response in measured]
    events = [[event for attempt in response["attempts"] for event in attempt["llm_usage"]] for response in measured]
    # Cost uses the run's provider price list; a run without one (an unpriced
    # provider) reports no cost, so the cost thresholds cannot pass silently.
    pricing = runs["pricing"] if "pricing" in runs else PEAK_PRICE_PER_MTOK
    costs = ([sum(generation_cost(event, pricing) for event in question_events) for question_events in events]
             if pricing else [])
    by_operation = {}
    for operation in sorted({row["operation"] for row in results}):
        rows = [row for row in results if row["operation"] == operation]
        by_operation[operation] = {"cases": len(rows), "correct": sum(row["correct"] for row in rows),
                                   "accuracy": sum(row["correct"] for row in rows) / len(rows)}
    answered, correct = sum(row["answered"] for row in results), sum(row["correct"] for row in results)
    metrics = {"answer_accuracy": correct / len(results) if results else None,
               "min_operation_accuracy": min((row["accuracy"] for row in by_operation.values()), default=None),
               "answered_precision": correct / answered if answered else None,
               "wrong_answer_rate": (answered - correct) / len(results) if results else None,
               "negative_false_answer_count": sum(row["false_answer"] for row in negative_results),
               "runner_error_count": len(runs.get("runner_errors", {})),
               "latency_p50_s": percentile(latency, 0.5), "latency_p95_s": percentile(latency, 0.95),
               "latency_max_s": max(latency, default=None),
               "cost_mean_usd": sum(costs) / len(costs) if costs else None,
               "cost_p95_usd": percentile(costs, 0.95),
               "llm_calls_mean": sum(map(len, events)) / len(events) if events else None}
    checks = [_check(name, metrics[name]) for name in ACCEPTANCE_THRESHOLDS]
    complete = ids.issubset(responses) and not runs.get("runner_errors") and runs.get("status") == "COMPLETED"
    telemetry_complete = all(
        len(response["attempts"]) == len(question_events)
        and all(all(type(event.get(key)) is int and event[key] >= 0 for key in ("prompt_tokens", "completion_tokens"))
                and 0 <= (event.get("prompt_cache_hit_tokens") or 0) <= event["prompt_tokens"]
                for event in question_events)
        for response, question_events in zip(measured, events))
    live = runs.get("measurement_source") == "LIVE"
    accepted = complete and telemetry_complete and live and all(check["passed"] for check in checks)
    tokens = Counter()
    for question_events in events:
        for event in question_events:
            for key in ("prompt_tokens", "completion_tokens", "prompt_cache_hit_tokens"):
                tokens[key] += event.get(key) or 0
    return {"schema_version": "t2p-benchmark-v1", "verdict": "ACCEPTED" if accepted else "NOT_ACCEPTED",
            "run_complete": complete, "telemetry_complete": telemetry_complete, "measurement_source": runs.get("measurement_source"),
            "catalog_id": runs.get("catalog_id"), "model_id": runs.get("model_id"),
            "provider": runs.get("provider", "deepseek"),
            "source_fingerprint": runs.get("source_fingerprint"),
            "gold_case_count": len(results), "negative_case_count": len(negatives),
            "answered_count": answered, "correct_count": correct, "wrong_answer_count": answered - correct,
            "metrics": metrics, "accuracy_by_operation": by_operation,
            "accuracy_wilson95": wilson_interval(correct, len(results)), "token_totals": dict(tokens),
            "threshold_checks": checks, "failed_thresholds": [check["name"] for check in checks if not check["passed"]],
            "reason_code_counts": dict(Counter(row["reason_code"] for row in results if not row["correct"])),
            "case_results": results, "negative_results": negative_results,
            "default_changed": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", default="gold/wide/wide-lnst.jsonl")
    parser.add_argument("--negatives", default="gold/wide/t2p-negative.jsonl")
    parser.add_argument("--runs", required=True)
    parser.add_argument("--audit", default="gold/wide/wide-lnst-audit.json")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    cases = load_gold_file(args.gold)
    negatives = [json.loads(line) for line in Path(args.negatives).read_text().splitlines() if line.strip()]
    report = evaluate(cases, negatives, json.loads(Path(args.runs).read_text()),
                      audit=json.loads(Path(args.audit).read_text()))
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ("verdict", "run_complete", "correct_count", "wrong_answer_count", "metrics")}))
    return 0 if report["verdict"] == "ACCEPTED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
