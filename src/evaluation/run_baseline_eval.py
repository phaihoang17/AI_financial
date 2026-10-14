"""Run TASK-100 fixture regression or score recorded real-corpus M9 runs."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
from pathlib import Path

from src.evaluation.baseline import evaluate_strong_strict_baseline
from src.evaluation.corpus_reference_index import (
    CorpusReferenceIndexError,
    load_or_build_reference_index,
)
from src.evaluation.gold_retrieval import GoldContractError, load_gold_file
from src.evaluation.production_baseline import blocked_baseline, evaluate_production_baseline
from src.understanding.schemas import SchemaValidationError


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("fixture", "strong-strict", "production"), default="fixture")
    parser.add_argument("--gold", default="gold/retrieval/pilot.jsonl")
    parser.add_argument("--corpus-artifact")
    parser.add_argument("--runs", help="JSON export of terminal M9 states; request_id equals question_id")
    parser.add_argument("--retrieval-validation", help="ADR-057 production validation JSON")
    args = parser.parse_args(argv)
    if args.mode != "production":
        report = evaluate_strong_strict_baseline()
        print(json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True))
        return 0 if report.failed_cases == 0 else 1
    missing = [name for name in ("corpus_artifact", "runs", "retrieval_validation")
               if getattr(args, name) is None]
    if missing:
        payload = blocked_baseline(*(f"MISSING_{name.upper()}" for name in missing))
    else:
        try:
            cases = load_gold_file(args.gold)
            index = load_or_build_reference_index(args.corpus_artifact)
            runs = json.loads(Path(args.runs).read_text(encoding="utf-8"))
            validation = json.loads(Path(args.retrieval_validation).read_text(encoding="utf-8"))
            if not isinstance(runs, dict) or not isinstance(validation, dict):
                raise SchemaValidationError("runs and retrieval validation must be JSON objects")
            payload = evaluate_production_baseline(cases, index, runs, validation)
        except (OSError, ValueError, GoldContractError, CorpusReferenceIndexError,
                SchemaValidationError) as error:
            payload = blocked_baseline(getattr(error, "code", "BASELINE_INPUT_INVALID"))
            payload["message"] = str(error)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if payload["established"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
