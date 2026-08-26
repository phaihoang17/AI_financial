"""Run the deterministic CPU-only M9 retrieved-evidence fixture evaluation."""

from __future__ import annotations

import argparse
import json

from src.evaluation.e2e import evaluate_e2e_cases
from src.evaluation.e2e_fixtures import e2e_fixture_cases


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("fixture",), default="fixture")
    parser.parse_args()
    report = evaluate_e2e_cases(e2e_fixture_cases()).to_dict()
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["failed_cases"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
