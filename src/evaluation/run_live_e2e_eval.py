"""Run raw questions through real M9 ports; export terminal states for scoring."""

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import time

from src.evaluation.production_baseline import BASELINE_RUNS_SCHEMA_VERSION
from src.orchestration.live import build_live_graph
from src.serving.deepseek import deepseek_client


def load_questions(path):
    questions = []
    for number, line in enumerate(Path(path).read_text().splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("//"):
            continue
        row = json.loads(line)
        case_id = row.get("question_id", row.get("id"))
        question = row.get("question")
        if not isinstance(case_id, (str, int)) or isinstance(case_id, bool) or not str(case_id).strip():
            raise ValueError(f"Missing question ID at line {number}")
        if not isinstance(question, str) or not question.strip():
            raise ValueError(f"Missing question text at line {number}")
        questions.append((str(case_id), question))
    if not questions or len({case_id for case_id, _ in questions}) != len(questions):
        raise ValueError("Questions must be nonempty with unique IDs")
    return questions


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--questions", required=True)
    for name in ("corpus", "vector", "bm25", "provenance", "table-class"):
        parser.add_argument(f"--{name}-artifact", required=True)
    parser.add_argument("--raw-corpus", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args(argv)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    def save(report):
        temporary = output.with_name(output.name + ".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        temporary.replace(output)

    try:
        client = deepseek_client(args.env_file)
        questions = load_questions(args.questions)
        graph = build_live_graph(
            client, corpus_root=args.corpus_artifact, vector_root=args.vector_artifact,
            bm25_root=args.bm25_artifact, raw_root=args.raw_corpus,
            provenance_root=args.provenance_artifact, table_class_root=args.table_class_artifact,
            top_k=args.top_k,
        )
    except Exception as error:
        save({"status": "BLOCKED", "established": False,
              "error": f"{type(error).__name__}: {error}"})
        print(json.dumps({"status": "BLOCKED", "output": str(output)}))
        return 2
    versions = graph.dependencies.artifact_versions
    report = {"schema_version": BASELINE_RUNS_SCHEMA_VERSION,
              "measurement_source": "LIVE", "status": "RUNNING", "established": False,
              "corpus_artifact_id": versions["corpus"],
              "vector_artifact_id": versions["vector"], "bm25_artifact_id": versions["bm25"],
              "model_id": client.endpoint.model_id, "states": [], "seconds_by_question": {}}
    save(report)
    for case_id, question in questions:
        start = time.monotonic()
        state = graph.run(request_id=case_id, raw_question=question).state
        report["states"].append(state.to_dict())
        report["seconds_by_question"][case_id] = time.monotonic() - start
        for attempt in state.attempts:
            if attempt.binding_ref is not None:
                graph.dependencies.binding_store.discard(attempt.binding_ref)
        save(report)
        print(f"{case_id}: {state.outcome.value}", file=sys.stderr, flush=True)
    report["status"] = "COMPLETED"
    report["outcome_counts"] = dict(Counter(state["outcome"] for state in report["states"]))
    save(report)
    print(json.dumps({"status": "COMPLETED", "output": str(output),
                      "outcome_counts": report["outcome_counts"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
