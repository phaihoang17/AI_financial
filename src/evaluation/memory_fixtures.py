"""Independent synthetic TRAIN, DEV and HELD_OUT episodes; no existing gold is read."""
from hashlib import sha256
import json

from src.evaluation.e2e_fixtures import _graph_factory, fixture_pairs, fixture_understanding
from src.memory.distillation import distill_trajectory
from src.memory.index import DevelopmentExample, validate_output_path
from src.memory.schemas import MemoryContext
from src.programmer.schemas import ProgrammerResult, ProgrammerStatus
from src.understanding.planning_gate import PlanningGate
from src.understanding.schemas import Operation

DATASET_ID = "m11-independent-synthetic-v2"


def episode(case_id, operation, periods, values):
    understanding = fixture_understanding(case_id, periods, operation)
    wording = {Operation.NONE: "LNST", Operation.GROWTH: "Mức tăng LNST",
               Operation.AGGREGATE: "Trung bình LNST", Operation.COMPARE: "So sánh LNST"}[operation]
    understanding.raw_question = f"Dữ liệu tổng hợp: {wording} hợp nhất của AAA " + " và ".join(f"năm {p}" for p in periods) + "?"
    pairs = fixture_pairs(periods, values)
    graph = _graph_factory(understanding, PlanningGate(True, [], []), [pairs], ("success",))()
    return understanding, graph


def held_out_episodes():
    return [
        ("memory-held-out-lookup", Operation.NONE, ("2025",), ("19",)),
        ("memory-held-out-growth", Operation.GROWTH, ("2024", "2025"), ("20", "26")),
        ("memory-held-out-average", Operation.AGGREGATE, ("2023", "2024", "2025"), ("17", "23", "29")),
        ("memory-held-out-compare", Operation.COMPARE, ("2024", "2025"), ("17", "23")),
    ]


def _reject_first_call(generate):
    """Fail the first programmer call so the episode records a recovered retry."""
    calls = []

    def recover(programmer_input, model_tier):
        calls.append(1)
        if len(calls) == 1:
            return ProgrammerResult(ProgrammerStatus.REJECTED, None,
                                    "FORMULA_ARITY_MISMATCH", "synthetic training failure")
        return generate(programmer_input, model_tier)

    return recover


def synthetic_training():
    held = held_out_episodes()
    held_ids = [row[0] for row in held]
    signatures = [sha256(episode(*row)[0].raw_question.encode()).hexdigest() for row in held]
    rows, entries = [], []
    for name, operation, periods, values in (
        ("memory-train-lookup", Operation.NONE, ("2019",), ("7",)),
        ("memory-train-growth", Operation.GROWTH, ("2018", "2019"), ("8", "12")),
        ("memory-train-average", Operation.AGGREGATE, ("2018", "2019"), ("7", "11")),
    ):
        understanding, graph = episode(name, operation, periods, values)
        if operation is Operation.AGGREGATE:
            graph.dependencies.programmer.generate = _reject_first_call(graph.dependencies.programmer.generate)
        state = graph.run(request_id=name, raw_question=understanding.raw_question).state
        rows.append({"split": "TRAIN", "state": state.to_dict()})
        entries.extend(distill_trajectory(state, split="TRAIN", dataset_id=DATASET_ID,
                       held_out_ids=held_ids, held_out_question_signatures=signatures))
    dev = []
    for name, operation, periods, values in (
        ("memory-dev-lookup", Operation.NONE, ("2021",), ("9",)),
        ("memory-dev-growth", Operation.GROWTH, ("2020", "2021"), ("6", "9")),
        ("memory-dev-average", Operation.AGGREGATE, ("2020", "2021", "2022"), ("9", "15", "21")),
        ("memory-dev-negative-compare", Operation.COMPARE, ("2020", "2021"), ("9", "15")),
    ):
        understanding, graph = episode(name, operation, periods, values)
        state = graph.run(request_id=name, raw_question=understanding.raw_question).state
        context = MemoryContext.from_plan(state.supervisor_result.plan)
        dev.append(DevelopmentExample(name, context, tuple(e.entry_id for e in entries if e.context.compatible_with(context))))
    return rows, tuple(entries), tuple(dev), {"ids": held_ids, "question_signatures": signatures}


def write_synthetic_training(directory):
    directory = validate_output_path(directory)
    if directory.exists():
        raise FileExistsError("synthetic training requires a new dataset directory")
    rows, entries, development, held_out = synthetic_training()
    directory.mkdir(parents=True)
    (directory / "train.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
    (directory / "dev.jsonl").write_text("".join(json.dumps({
        "source_id": e.source_id, "split": e.split, "context": e.context.to_dict(),
        "relevant_entry_ids": list(e.relevant_entry_ids)}, ensure_ascii=False) + "\n" for e in development))
    (directory / "held-out-manifest.json").write_text(json.dumps(held_out, indent=2) + "\n")
    return entries, development, held_out
