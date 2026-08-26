"""Run the deterministic CPU-only TASK-102 retry/escalation measurement.

Measurement only: retry/escalation routing stays in the deterministic M8 engine.
"""

from __future__ import annotations

import argparse
import json
from typing import Optional, Sequence

from src.evaluation.e2e import evaluate_e2e_cases
from src.evaluation.e2e_fixtures import e2e_fixture_cases
from src.evaluation.retry_metrics import retry_escalation_metrics


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("fixture",), default="fixture")
    parser.parse_args(argv)
    cases = e2e_fixture_cases()
    confidence_by_case = {
        case.case_id: case.expected_plan.confidence
        for case in cases
        if case.expected_plan is not None
    }
    report = evaluate_e2e_cases(cases)
    metrics = retry_escalation_metrics(
        report.case_results, confidence_by_case=confidence_by_case
    )
    print(json.dumps(metrics.to_dict(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
