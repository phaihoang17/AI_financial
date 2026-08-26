"""Run the deterministic CPU-only TASK-111 bounded throughput/load test.

Exercises the deterministic M9 fixture graph under bounded concurrency with real
wall-clock. Accuracy/timeout degradation at production concurrency with live
models remains GPU_PRODUCTION_VALIDATION_PENDING.
"""

from __future__ import annotations

import argparse
import json
from typing import Optional, Sequence

from src.evaluation.e2e_fixtures import e2e_fixture_cases
from src.evaluation.load_test import run_load_test


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("fixture",), default="fixture")
    parser.add_argument("--total-requests", type=int, default=24)
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args(argv)

    cases = e2e_fixture_cases()

    def task(index: int) -> None:
        case = cases[index % len(cases)]
        case.graph_factory().run(request_id=f"load-{index}", raw_question=case.raw_question)

    result = run_load_test(
        task, total_requests=args.total_requests, concurrency=args.concurrency
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True))
    return 0 if result.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
