"""Run the deterministic CPU-only TASK-105 semantic-cache experiment."""

from __future__ import annotations

import argparse
import json
from typing import Optional, Sequence

from src.evaluation.cache_experiment import run_cache_experiment


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("fixture",), default="fixture")
    parser.parse_args(argv)
    report = run_cache_experiment()
    print(json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True))
    # The experiment is a correctness gate: any cache that changed a verified
    # answer is a failure.
    return 0 if report.correctness_preserved else 1


if __name__ == "__main__":
    raise SystemExit(main())
