"""Score a wide live run against the ADR-066 acceptance thresholds.

Inputs are the wide LNST gold (`gold/wide/wide-lnst.jsonl`), the expected
non-answer set (`gold/wide/wide-negative.jsonl`) and the live run export of
`run_live_e2e_eval`. Every gold case is scored, missing runs count as failures,
and the verdict is ACCEPTED only when every threshold holds.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
import json
import math
from pathlib import Path
from typing import Any

from src.evaluation.gold_retrieval import GoldRetrievalCase, load_gold_file
from src.evidence.masking_schemas import SchemaLinkStatus
from src.numeric.scale_conversion import ScaleConversionStatus, convert_canonical_scale
from src.orchestration.schemas import FinalResponseStatus
from src.orchestration.state import OrchestrationState
from src.programmer.schemas import ProgramOutputKind
from src.sandbox.schemas import ExecutionDatum

WIDE_EVAL_SCHEMA_VERSION = "m10-wide-eval-v1"

#: ADR-066. Fixed before the first wide run; change only through a new ADR.
ACCEPTANCE_THRESHOLDS: dict[str, dict[str, Any]] = {
    "answer_accuracy": {"min": 0.85},
    "min_operation_accuracy": {"min": 0.75},
    "answered_precision": {"min": 0.98},
    "wrong_answer_rate": {"max": 0.02},
    "negative_false_answer_count": {"max": 0},
    "runner_error_count": {"max": 0},
    "latency_p50_s": {"max": 20.0},
    "latency_p95_s": {"max": 45.0},
    "latency_max_s": {"max": 120.0},
    "cost_mean_usd": {"max": 0.01},
    "cost_p95_usd": {"max": 0.02},
    "llm_calls_mean": {"max": 4.0},
}

#: USD per 1M tokens, DeepSeek Flash peak price list read 2026-10-13
#: (https://api-docs.deepseek.com/quick_start/pricing). Off-peak is half.
PEAK_PRICE_PER_MTOK = {"cache_hit": 0.006, "cache_miss": 0.30, "output": 1.20}

#: Growth is a percent quotient; tolerate rounding below a millionth relative.
VALUE_RELATIVE_TOLERANCE = Decimal("1e-6")


def percentile(values: Sequence[float], fraction: float) -> float | None:
    """Nearest-rank percentile."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def wilson_interval(successes: int, total: int, z: float = 1.96) -> list[float] | None:
    if total == 0:
        return None
    p = successes / total
    centre = (p + z * z / (2 * total)) / (1 + z * z / total)
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return [round(centre - half, 4), round(centre + half, 4)]


def generation_cost(event: Mapping[str, Any], prices: Mapping[str, float] = PEAK_PRICE_PER_MTOK) -> float:
    prompt = int(event.get("prompt_tokens") or 0)
    hit = int(event.get("prompt_cache_hit_tokens") or 0)
    output = int(event.get("completion_tokens") or 0)
    return ((prompt - hit) * prices["cache_miss"] + hit * prices["cache_hit"] + output * prices["output"]) / 1e6


def _value_matches(predicted: str, gold: str) -> bool:
    try:
        left, right = Decimal(predicted), Decimal(gold)
    except InvalidOperation:
        return False
    if left == right:
        return True
    return abs(left - right) <= VALUE_RELATIVE_TOLERANCE * max(abs(right), Decimal(1))


def score_case(case: GoldRetrievalCase, state: OrchestrationState | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "question_id": case.question_id, "operation": case.operation, "outcome": None,
        "reason_code": None, "answered": False, "value_correct": False, "unit_correct": False,
        "grounding_correct": False, "verification_passed": False, "answer_correct": False,
        "attempts": 0, "failure_stage": None,
    }
    if state is None:
        result["outcome"] = "MISSING_RUN"
        return result
    if state.raw_question != case.question:
        raise ValueError(f"question mismatch for {case.question_id}")
    result["outcome"] = state.outcome.value
    result["reason_code"] = state.final_response.reason_code
    result["attempts"] = len(state.attempts)
    if state.failure_attribution:
        result["failure_stage"] = state.failure_attribution.stage.value
    attempt = state.attempts[-1] if state.attempts else None
    result["verification_passed"] = bool(attempt and attempt.verification_report
                                         and attempt.verification_report.passed)
    answer = state.final_response.answer
    if state.outcome is not FinalResponseStatus.PASS or answer is None:
        return result
    result["answered"] = True
    if answer.output.kind is not ProgramOutputKind.SCALAR or not answer.output.values:
        return result
    gold = case.gold_answer
    gold_datum = ExecutionDatum.from_dict(gold.to_dict())
    datum = answer.output.values[0]
    converted = convert_canonical_scale(datum.value, source_scale=datum.scale, source_unit=datum.unit,
                                        requested_output_scale=gold_datum.scale)
    result["predicted"] = datum.to_dict()
    result["value_correct"] = (converted.status is ScaleConversionStatus.SUCCESS
                               and _value_matches(str(converted.canonical_value), str(gold_datum.value)))
    result["unit_correct"] = datum.unit == gold.unit
    plan = state.supervisor_result.plan if state.supervisor_result else None
    used_ids = {ref.evidence_id for ref in answer.evidence}
    used_cells = {link.matched_source_cell_id for link in attempt.schema_links
                  if link.evidence_id in used_ids and link.status is SchemaLinkStatus.RESOLVED}
    periods = 1 if case.period else 2
    result["grounding_correct"] = bool(
        plan and plan.company.ticker == case.company_ticker
        and (case.statement_scope is None or plan.statement_scope.value == case.statement_scope)
        and len(used_cells.intersection(case.gold_source_cell_ids)) >= periods)
    result["answer_correct"] = all(result[key] for key in (
        "value_correct", "unit_correct", "grounding_correct", "verification_passed"))
    return result


def _check(name: str, value: float | None) -> dict[str, Any]:
    rule = ACCEPTANCE_THRESHOLDS[name]
    if value is None:
        passed = False
    elif "min" in rule:
        passed = value >= rule["min"]
    else:
        passed = value <= rule["max"]
    return {"name": name, "value": value, **rule, "passed": passed}


def evaluate(cases: Sequence[GoldRetrievalCase], negatives: Sequence[Mapping[str, Any]],
             runs: Mapping[str, Any], *, audit: Mapping[str, Any] | None = None) -> dict[str, Any]:
    states = {raw["request_id"]: OrchestrationState.from_dict(raw) for raw in runs.get("states", [])}
    results = [score_case(case, states.get(case.question_id)) for case in cases]
    strata = {}
    if audit:
        for entry in audit.get("cases", []):
            sources = entry["sources"]
            strata[entry["question_id"]] = {
                "template": "BANK" if any(s["template"] == "BANK" for s in sources) else "TT200",
                "source_scale": "/".join(sorted({s["source_scale"] for s in sources})),
            }
    for result in results:
        result.update(strata.get(result["question_id"], {}))

    negative_results = []
    for row in negatives:
        state = states.get(row["question_id"])
        outcome = state.outcome.value if state else "MISSING_RUN"
        negative_results.append({"question_id": row["question_id"], "kind": row["kind"], "outcome": outcome,
                                 "reason_code": state.final_response.reason_code if state else None,
                                 "false_answer": outcome == FinalResponseStatus.PASS.value})

    def rate(rows: Sequence[Mapping[str, Any]], key: str) -> float | None:
        return sum(bool(r[key]) for r in rows) / len(rows) if rows else None

    def breakdown(field: str) -> dict[str, Any]:
        groups: dict[str, list[Mapping[str, Any]]] = {}
        for result in results:
            groups.setdefault(str(result.get(field)), []).append(result)
        return {name: {"cases": len(rows), "correct": sum(r["answer_correct"] for r in rows),
                       "answered": sum(r["answered"] for r in rows),
                       "accuracy": rate(rows, "answer_correct")} for name, rows in sorted(groups.items())}

    answered = [r for r in results if r["answered"]]
    correct = sum(r["answer_correct"] for r in results)
    wrong = sum(r["answered"] and not r["answer_correct"] for r in results)
    by_operation = breakdown("operation")

    question_ids = [c.question_id for c in cases] + [n["question_id"] for n in negatives]
    seconds = runs.get("seconds_by_question", {})
    latencies = [float(seconds[q]) for q in question_ids if q in seconds]
    generations = runs.get("generations_by_question", {})
    costs = [sum(generation_cost(e) for e in generations[q]) for q in question_ids if q in generations]
    calls = [len(generations[q]) for q in question_ids if q in generations]
    tokens = Counter()
    for q in question_ids:
        for event in generations.get(q, []):
            tokens["prompt"] += int(event.get("prompt_tokens") or 0)
            tokens["cache_hit"] += int(event.get("prompt_cache_hit_tokens") or 0)
            tokens["completion"] += int(event.get("completion_tokens") or 0)

    metrics = {
        "answer_accuracy": correct / len(results) if results else None,
        "min_operation_accuracy": min((v["accuracy"] for v in by_operation.values() if v["accuracy"] is not None),
                                      default=None),
        "answered_precision": (sum(r["answer_correct"] for r in answered) / len(answered)) if answered else None,
        "wrong_answer_rate": wrong / len(results) if results else None,
        "negative_false_answer_count": sum(r["false_answer"] for r in negative_results),
        "runner_error_count": len(runs.get("runner_errors", {})),
        "latency_p50_s": percentile(latencies, 0.50),
        "latency_p95_s": percentile(latencies, 0.95),
        "latency_max_s": max(latencies) if latencies else None,
        "cost_mean_usd": sum(costs) / len(costs) if costs else None,
        "cost_p95_usd": percentile(costs, 0.95),
        "llm_calls_mean": sum(calls) / len(calls) if calls else None,
    }
    checks = [_check(name, metrics[name]) for name in ACCEPTANCE_THRESHOLDS]
    complete = len(states) >= len(question_ids) and not runs.get("runner_errors")
    return {
        "schema_version": WIDE_EVAL_SCHEMA_VERSION,
        "verdict": "ACCEPTED" if all(c["passed"] for c in checks) else "NOT_ACCEPTED",
        "run_complete": complete,
        "measurement_source": runs.get("measurement_source"),
        "retrieval_models": runs.get("retrieval_models"),
        "model_id": runs.get("model_id"),
        "artifact_ids": {k: runs.get(k) for k in ("corpus_artifact_id", "vector_artifact_id", "bm25_artifact_id")},
        "gold_case_count": len(results), "negative_case_count": len(negative_results),
        "answered_count": len(answered), "correct_count": correct, "wrong_answer_count": wrong,
        "answer_accuracy_wilson95": wilson_interval(correct, len(results)),
        "metrics": metrics, "threshold_checks": checks,
        "failed_thresholds": [c["name"] for c in checks if not c["passed"]],
        "accuracy_by_operation": by_operation,
        "accuracy_by_template": breakdown("template"),
        "accuracy_by_source_scale": breakdown("source_scale"),
        "outcome_counts": dict(Counter(r["outcome"] for r in results)),
        "reason_code_counts": dict(Counter(r["reason_code"] for r in results if not r["answer_correct"])),
        "negative_outcomes": dict(Counter(f'{r["kind"]}:{r["outcome"]}' for r in negative_results)),
        "token_totals": dict(tokens),
        "price_per_mtok_usd": PEAK_PRICE_PER_MTOK,
        "case_results": results, "negative_results": negative_results,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", default="gold/wide/wide-lnst.jsonl")
    parser.add_argument("--negatives", default="gold/wide/wide-negative.jsonl")
    parser.add_argument("--audit", default="gold/wide/wide-lnst-audit.json")
    parser.add_argument("--runs", required=True)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    cases = load_gold_file(args.gold)
    negatives = [json.loads(line) for line in Path(args.negatives).read_text(encoding="utf-8").splitlines() if line.strip()]
    audit = json.loads(Path(args.audit).read_text(encoding="utf-8")) if Path(args.audit).is_file() else None
    report = evaluate(cases, negatives, json.loads(Path(args.runs).read_text(encoding="utf-8")), audit=audit)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    summary = {k: report[k] for k in ("verdict", "run_complete", "gold_case_count", "answered_count",
                                      "correct_count", "wrong_answer_count", "failed_thresholds")}
    print(json.dumps({**summary, "metrics": report["metrics"]}, ensure_ascii=False))
    return 0 if report["verdict"] == "ACCEPTED" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
