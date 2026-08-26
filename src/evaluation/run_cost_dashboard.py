"""Run the deterministic CPU-only TASK-110 cost/token dashboard.

CPU fixtures invoke no model, so token/cost telemetry is reported as
LIVE_PENDING. Escalation count and cache hit rate are real CPU-measurable
fields; token/cost require live serving (GPU_PRODUCTION_VALIDATION_PENDING).
"""

from __future__ import annotations

import argparse
import json
from typing import Optional, Sequence

from src.evaluation.cache_experiment import run_cache_experiment
from src.evaluation.cost_dashboard import build_cost_dashboard
from src.evaluation.e2e import evaluate_e2e_cases
from src.evaluation.e2e_fixtures import e2e_fixture_cases
from src.evaluation.retry_metrics import retry_escalation_metrics


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("fixture",), default="fixture")
    parser.parse_args(argv)

    report = evaluate_e2e_cases(e2e_fixture_cases())
    metrics = retry_escalation_metrics(report.case_results)
    cache = run_cache_experiment()
    query_hit_rate = next(
        (item.hit_rate for item in cache.policy_results if item.policy == "QUERY"), None
    )
    dashboard = build_cost_dashboard(
        invocations=[],  # no live model runs on CPU fixtures
        queries=max(1, len(report.case_results)),
        escalation_requests=metrics.escalated_cases,
        cache_hit_rate=query_hit_rate,
    )
    print(json.dumps(dashboard.to_dict(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
