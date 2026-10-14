"""CLI runner for TASK-06D: Static Programmer vs TABLE_TRANSFORM fallback comparison."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import json

from src.evaluation.table_transform_comparison import (
    build_complex_table_fixtures,
    render_comparison_markdown,
    run_comparison_evaluation,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run TASK-06D comparison evaluation")
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="json",
        help="Output format (default: json)",
    )
    args = parser.parse_args(argv)

    fixtures = build_complex_table_fixtures()
    report = run_comparison_evaluation(fixtures)

    if args.format == "markdown":
        print(render_comparison_markdown(report))
    else:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
