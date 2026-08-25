"""Run the deterministic, CPU-only TASK-047 fixture evaluation."""

from __future__ import annotations

import argparse
import json
from typing import Optional, Sequence

from src.evaluation.supervisor import evaluate_supervisor_cases
from src.evaluation.supervisor_fixtures import supervisor_fixture_cases


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("fixture",), default="fixture")
    args = parser.parse_args(argv)
    report = evaluate_supervisor_cases(supervisor_fixture_cases())
    payload = {"mode": args.mode.upper(), **report.to_dict()}
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if report.failed_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
