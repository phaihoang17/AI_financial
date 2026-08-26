"""Run the deterministic CPU-only TASK-106 end-to-end latency measurement.

CPU wall-clock over the deterministic fixtures only; per-stage live-model latency
remains GPU_PRODUCTION_VALIDATION_PENDING.
"""

from __future__ import annotations

import argparse
import json
from typing import Optional, Sequence

from src.evaluation.latency import measure_e2e_latency


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("fixture",), default="fixture")
    parser.add_argument("--repeats", type=int, default=1)
    args = parser.parse_args(argv)
    report = measure_e2e_latency(repeats=args.repeats)
    print(json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
