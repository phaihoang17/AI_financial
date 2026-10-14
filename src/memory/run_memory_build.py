"""Distill independent TRAIN/DEV trajectories and publish a calibrated memory index."""
import argparse
import json
from pathlib import Path

from src.memory.distillation import distill_trajectory
from src.memory.index import DevelopmentExample, build_index, publish_index, validate_output_path
from src.memory.schemas import MemoryContext
from src.orchestration.state import OrchestrationState
from src.serving.client import HttpTransport, OpenAICompatibleClient
from src.serving.retrieval_serving import ServingQueryEncoder
from src.serving.schemas import ServingEndpoint
from src.understanding.schemas import _require_exact_keys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("trajectories", "development", "held-out-manifest", "serving-config", "dataset-id", "output"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args(argv)
    output = validate_output_path(args.output)
    if output.exists():
        raise FileExistsError("memory publication requires a new artifact path")
    held_out = json.loads(Path(args.held_out_manifest).read_text())
    _require_exact_keys(held_out, {"ids", "question_signatures"}, "HeldOutManifest")
    entries = []
    for line in Path(args.trajectories).read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        _require_exact_keys(row, {"split", "state"}, "TrainingTrajectory")
        entries.extend(distill_trajectory(OrchestrationState.from_dict(row["state"]), split=row["split"],
                       dataset_id=args.dataset_id, held_out_ids=held_out["ids"],
                       held_out_question_signatures=held_out["question_signatures"]))
    development = []
    for line in Path(args.development).read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        _require_exact_keys(row, {"source_id", "split", "context", "relevant_entry_ids"}, "DevelopmentExample")
        development.append(DevelopmentExample(row["source_id"], MemoryContext.from_dict(row["context"]),
                           tuple(row["relevant_entry_ids"]), row["split"]))
    config = json.loads(Path(args.serving_config).read_text())
    encoder = ServingQueryEncoder(OpenAICompatibleClient(ServingEndpoint.from_dict(config["embedding"]), HttpTransport()))
    index = build_index(entries, development, encoder=encoder, dataset_id=args.dataset_id,
                        held_out_ids=held_out["ids"], held_out_question_signatures=held_out["question_signatures"])
    publish_index(index, output)
    print(json.dumps({"status": "PUBLISHED", "artifact_id": index.artifact_id,
                      "entries": len(index.entries), "calibration": index.calibration.to_dict()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
