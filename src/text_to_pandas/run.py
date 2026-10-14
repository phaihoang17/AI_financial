"""Opt-in live Text-to-Pandas demo and resumable benchmark; never switches defaults."""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path
import sys
import threading
from time import monotonic

from src.evaluation.run_live_e2e_eval import load_questions
from src.serving.providers import DEFAULT_PROVIDERS_FILE, provider_client, provider_report
from src.text_to_pandas.catalog import DatasetCatalog
from src.text_to_pandas.generator import PandasCodeGenerator
from src.text_to_pandas.pipeline import PandasPipeline


def source_fingerprint():
    root = Path(__file__).resolve().parents[2]
    paths = sorted((root / "src").rglob("*.py"))
    digest = sha256()
    for path in paths:
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--provider", help="LLM provider in --llm-config (default: LLM_PROVIDER, then the file default)")
    parser.add_argument("--llm-config", default=str(DEFAULT_PROVIDERS_FILE))
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--question")
    choice.add_argument("--questions")
    parser.add_argument("--history-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--concurrency", type=int, default=1,
                        help="questions answered in parallel (load measurement; 1 keeps the sequential order)")
    args = parser.parse_args(argv)
    if args.concurrency < 1:
        raise ValueError("--concurrency must be at least 1")
    catalog = DatasetCatalog(args.catalog)
    client = provider_client(args.provider, config_path=args.llm_config, env_file=args.env_file)
    provider = provider_report(client)
    pipeline = PandasPipeline(catalog, PandasCodeGenerator(client), history_root=args.history_root,
                              max_retries=args.max_retries)
    questions = load_questions(args.questions) if args.questions else [("demo", args.question)]
    report = {"schema_version": "t2p-live-runs-v1", "measurement_source": "LIVE",
              "catalog_id": catalog.catalog_id, "model_id": client.endpoint.model_id,
              "provider": provider["name"], "provider_protocol": provider["protocol"],
              "pricing": provider["pricing"],
              "source_fingerprint": source_fingerprint(), "max_retries": args.max_retries,
              "questions_sha256": sha256(json.dumps(questions, ensure_ascii=False).encode()).hexdigest(),
              "concurrency": args.concurrency, "status": "RUNNING", "responses": {}, "runner_errors": {}}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if args.resume and output.is_file():
        previous = json.loads(output.read_text())
        identity = ("catalog_id", "model_id", "source_fingerprint", "max_retries", "questions_sha256")
        if (any(previous.get(key) != report[key] for key in identity)
                or previous.get("concurrency", 1) != args.concurrency
                or previous.get("provider", "deepseek") != report["provider"]):
            raise ValueError("resume requires the same catalog, provider, model, code, retry budget, questions and "
                             "concurrency")
        report["responses"] = previous["responses"]
    elif output.exists():
        raise ValueError("output already exists; use --resume or a new output path")

    def save():
        temporary = output.with_suffix(output.suffix + ".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(output)

    save()
    lock = threading.Lock()

    def answer(item):
        question_id, question = item
        with lock:
            if report["status"] == "SOURCE_CHANGED":
                return
            if source_fingerprint() != report["source_fingerprint"]:
                report["status"] = "SOURCE_CHANGED"
                save()
                return
        try:
            response = pipeline.ask(question, request_id=question_id)
        except Exception as error:
            # Keep the exception type only: provider messages may contain secrets.
            with lock:
                report["runner_errors"][question_id] = type(error).__name__
                save()
            print(f"{question_id}: RUNNER_ERROR {type(error).__name__}", file=sys.stderr, flush=True)
            return
        with lock:
            report["responses"][question_id] = response
            save()
        print(f"{question_id}: {response['status']} {response['reason_code'] or ''}", file=sys.stderr, flush=True)

    pending = [item for item in questions if item[0] not in report["responses"]]
    started = monotonic()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        list(pool.map(answer, pending))
    if source_fingerprint() != report["source_fingerprint"]:
        report["status"] = "SOURCE_CHANGED"
        save()
    if report["status"] == "SOURCE_CHANGED":
        raise RuntimeError("source changed during the run; keep this evidence and start a new measured run")
    report["wall_clock_s"] = round(monotonic() - started, 3)
    report["status"] = "COMPLETED"
    report["outcome_counts"] = dict(Counter(response["status"] for response in report["responses"].values()))
    save()
    print(json.dumps({"status": report["status"], "output": str(output), "outcome_counts": report["outcome_counts"],
                      "runner_errors": report["runner_errors"]}))
    return 1 if report["runner_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
