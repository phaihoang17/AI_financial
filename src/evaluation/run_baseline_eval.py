"""Run the deterministic CPU-only TASK-100 strong/strict correctness baseline."""

from __future__ import annotations

import argparse
import json
from typing import Optional, Sequence

from src.evaluation.baseline import evaluate_strong_strict_baseline


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("strong-strict",), default="strong-strict")
    parser.parse_args(argv)
    report = evaluate_strong_strict_baseline()
    print(json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True))
    return 0 if report.established else 1


if __name__ == "__main__":
    raise SystemExit(main())
