"""Run deterministic, CPU-only TASK-067 oracle reasoning evaluation."""

from __future__ import annotations

import argparse
import json
from typing import Optional, Sequence

from src.evaluation.oracle_reasoning import evaluate_oracle_reasoning_cases
from src.evaluation.oracle_reasoning_fixtures import (
    oracle_reasoning_fixture_cases,
)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("oracle",), default="oracle")
    args = parser.parse_args(argv)
    report = evaluate_oracle_reasoning_cases(oracle_reasoning_fixture_cases())
    payload = {"mode": args.mode.upper(), **report.to_dict()}
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if report.failed_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
