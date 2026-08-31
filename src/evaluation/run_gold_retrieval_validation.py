"""CLI: validate a gold retrieval/answer annotation file (TASK-100).

Checks the gold set against the committed canonical corpus and (optionally)
classifies a raw questions source into AUTO_DERIVABLE / NEEDS_MANUAL_ANNOTATION
/ UNSUPPORTED. It never computes Recall@k / MRR (that needs the live retrieval
stack — see ``compute_gold_retrieval_metrics``) and never writes gold.

    python -m src.evaluation.run_gold_retrieval_validation \
        --corpus-artifact <committed m2 corpus artifact dir> \
        --gold gold/retrieval/pilot.jsonl \
        [--questions-source <raw questions jsonl>] \
        [--max-shards N] [--rebuild-index]

Exit code is 0 only when the gold file has zero ERROR-severity issues.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, List, Optional, Sequence

from src.evaluation.corpus_reference_index import (
    CorpusReferenceIndexError,
    load_or_build_reference_index,
)
from src.evaluation.gold_retrieval import (
    GoldContractError,
    classify_question,
    load_gold_file,
    validate_gold_cases,
)


def _load_jsonl(path: str | Path) -> List[dict]:
    rows: List[dict] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        rows.append(json.loads(line))
    return rows


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-artifact", required=True)
    parser.add_argument("--gold", default=None, help="gold annotation JSONL to validate")
    parser.add_argument(
        "--questions-source",
        default=None,
        help="raw questions JSONL to classify (AUTO_DERIVABLE / MANUAL / UNSUPPORTED)",
    )
    parser.add_argument("--max-shards", type=int, default=None)
    parser.add_argument("--rebuild-index", action="store_true")
    args = parser.parse_args(argv)

    try:
        index = load_or_build_reference_index(
            args.corpus_artifact, max_shards=args.max_shards, rebuild=args.rebuild_index
        )
    except CorpusReferenceIndexError as error:
        print(json.dumps({"ok": False, "code": error.code, "message": str(error)}, sort_keys=True))
        return 1

    report: dict[str, Any] = {"corpus_reference_index": index.summary()}

    if args.questions_source is not None:
        results = [classify_question(row, index) for row in _load_jsonl(args.questions_source)]
        counts: dict[str, int] = {}
        for result in results:
            counts[result.status.value] = counts.get(result.status.value, 0) + 1
        report["classification"] = {
            "total_questions": len(results),
            "counts": counts,
            "results": [result.to_dict() for result in results],
        }

    exit_code = 0
    if args.gold is not None:
        try:
            cases = load_gold_file(args.gold)
        except (GoldContractError, ValueError) as error:
            print(json.dumps({"ok": False, "code": "GOLD_FILE_INVALID", "message": str(error)}, sort_keys=True))
            return 1
        validation = validate_gold_cases(cases, index)
        report["gold_validation"] = validation.to_dict()
        exit_code = 0 if validation.ok else 1

    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
