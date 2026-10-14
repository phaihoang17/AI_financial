"""Run synthetic held-out memory ablation and served-tier comparison."""
import argparse
import json
from pathlib import Path

from src.evaluation.memory_experiment import FixtureProgrammerClient, run_cheap_routing_experiment, run_memory_ablation
from src.memory.index import MemoryIndex, validate_output_path
from src.serving.client import HttpTransport, OpenAICompatibleClient
from src.serving.deepseek import deepseek_client
from src.serving.retrieval_serving import ServingQueryEncoder
from src.serving.schemas import ServingEndpoint


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memory-index", required=True)
    parser.add_argument("--serving-config", required=True)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--live-programmer", action="store_true")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    output = validate_output_path(args.output)
    if output.exists() or output == Path(args.memory_index).resolve():
        raise ValueError("evaluation output must be a new file distinct from the memory index")
    config = json.loads(Path(args.serving_config).read_text())
    encoder = ServingQueryEncoder(OpenAICompatibleClient(ServingEndpoint.from_dict(config["embedding"]), HttpTransport()))
    memory = MemoryIndex.load(args.memory_index, encoder=encoder)
    client = deepseek_client(args.env_file) if args.live_programmer else FixtureProgrammerClient()
    report = {"memory": run_memory_ablation(client, memory=memory, index_path=args.memory_index),
              "routing": run_cheap_routing_experiment(client)}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": "MEASURED", "output": args.output,
                      "memory_accuracy_delta": report["memory"]["memory_accuracy_delta"],
                      "routing_accuracy_delta": report["routing"]["accuracy_delta"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
