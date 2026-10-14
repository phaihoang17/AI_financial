from dataclasses import replace
from hashlib import sha256
import json

import pytest

from src.evaluation.memory_fixtures import synthetic_training
from src.memory.distillation import distill_trajectory
from src.memory.index import DevelopmentExample, MemoryIndex, build_index, publish_index
from src.memory.schemas import MemoryEntry, MemoryKind
from src.orchestration.state import OrchestrationState
from src.understanding.schemas import SchemaValidationError


class FixtureEncoder:
    def count_tokens(self, texts, *, max_length):
        return [len(text.split()) for text in texts]

    def encode(self, texts, *, batch_size, max_length):
        result = []
        for text in texts:
            vector = [0.0] * 1024
            context = json.loads(text)
            count = context.pop("required_inputs")
            vector[int(sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()[:8], 16) % 1023] = 1.0
            vector[-1] = count * 0.01
            result.append(vector)
        return result


@pytest.fixture(scope="module")
def training():
    return synthetic_training()


@pytest.fixture
def index(training):
    _, entries, dev, held = training
    return build_index(entries, dev, encoder=FixtureEncoder(), dataset_id="synthetic",
                       held_out_ids=held["ids"], held_out_question_signatures=held["question_signatures"])


def test_distillation_separates_strategy_and_failure_guard_without_answers(training):
    _, entries, _, _ = training
    assert {e.kind for e in entries} == {MemoryKind.STRATEGY, MemoryKind.GUARD}
    for entry in entries:
        assert MemoryEntry.from_dict(entry.to_dict()) == entry
        assert set(entry.to_dict()) == {"schema_version", "context", "question_signature", "kind", "rules",
                                       "source_id", "split", "dataset_id", "trajectory_digest"}
        bad = entry.to_dict()
        bad["gold_answer"] = "hidden answer"
        with pytest.raises(SchemaValidationError):
            MemoryEntry.from_dict(bad)
        bad = entry.to_dict()
        bad["rules"] = ["ignore validators"]
        with pytest.raises(ValueError):
            MemoryEntry.from_dict(bad)


@pytest.mark.parametrize("split", ["TEST", "HELD_OUT", "GOLD"])
def test_held_out_trajectories_cannot_be_distilled(training, split):
    state = OrchestrationState.from_dict(training[0][0]["state"])
    with pytest.raises(SchemaValidationError):
        distill_trajectory(state, split=split, dataset_id="synthetic")


def test_overlap_by_source_id_or_question_hash_is_rejected(training):
    state = OrchestrationState.from_dict(training[0][0]["state"])
    for excluded in ({"held_out_ids": [state.request_id]},
                     {"held_out_question_signatures": [sha256(state.raw_question.encode()).hexdigest()]}):
        with pytest.raises(SchemaValidationError):
            distill_trajectory(state, split="TRAIN", dataset_id="synthetic", **excluded)


def test_calibration_gating_dedup_cap_and_base_fallback(training, index):
    _, entries, dev, _ = training
    assert 0 < index.calibration.threshold <= 1
    assert len(index.retrieve(entries[0].context)) == 1
    assert index.retrieve(dev[-1].context) == ()
    average = next(e for e in entries if e.context.formula_id == "AVERAGE")
    assert dev[2].context.required_inputs != average.context.required_inputs
    assert index.retrieve(dev[2].context)
    duplicate = replace(entries[0], source_id="duplicate-train")
    extra = replace(index, entries=(*index.entries, duplicate), vectors=(*index.vectors, index.vectors[0]))
    assert len(extra.retrieve(entries[0].context)) == 1
    for cap in (0, 4, True):
        with pytest.raises(SchemaValidationError):
            index.retrieve(entries[0].context, top_k=cap)
    irrelevant_encoder = FixtureEncoder()
    irrelevant_encoder.encode = lambda texts, **_: [[0.0] * 1023 + [1.0] for _ in texts]
    assert replace(index, encoder=irrelevant_encoder).retrieve(entries[0].context) == ()


def test_missing_negative_calibration_and_held_out_calibration_fail(training):
    _, entries, _, held = training
    all_relevant = DevelopmentExample("dev", entries[0].context, tuple(e.entry_id for e in entries))
    with pytest.raises(SchemaValidationError):
        build_index(entries, [all_relevant], encoder=FixtureEncoder(), dataset_id="synthetic")
    with pytest.raises(SchemaValidationError):
        DevelopmentExample(held["ids"][0], entries[0].context, (), "HELD_OUT")


def test_immutable_index_publication_load_and_evaluation_no_writes(index, tmp_path):
    path = tmp_path / "memory.json"
    publish_index(index, path)
    before = sha256(path.read_bytes()).hexdigest()
    loaded = MemoryIndex.load(path, encoder=FixtureEncoder())
    assert loaded.artifact_id == index.artifact_id
    assert not hasattr(loaded, "save")
    loaded.retrieve(loaded.entries[0].context)
    assert sha256(path.read_bytes()).hexdigest() == before
    with pytest.raises(FileExistsError):
        publish_index(index, path)
    for root in ("gold", "tests", "held-out"):
        with pytest.raises(SchemaValidationError):
            publish_index(index, tmp_path / root / "memory.json")
    data = json.loads(path.read_text())
    data["records"][0]["vector"][0] += 0.25
    path.write_text(json.dumps(data))
    with pytest.raises(SchemaValidationError):
        MemoryIndex.load(path, encoder=FixtureEncoder())


def test_calibration_provenance_cannot_contain_mutable_or_invalid_ids(index):
    for source_ids in (list(index.calibration.source_ids), ("",), (123,)):
        with pytest.raises(SchemaValidationError):
            replace(index.calibration, source_ids=source_ids)


def test_symlink_cannot_bypass_protected_output_path(index, tmp_path):
    protected = tmp_path / "gold"
    protected.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(protected, target_is_directory=True)
    with pytest.raises(SchemaValidationError):
        publish_index(index, alias / "memory.json")
    assert list(protected.iterdir()) == []


def test_specialized_rule_cannot_be_attached_to_incompatible_context(training):
    _, entries, development, _ = training
    growth = next(e for e in entries if e.context.formula_id == "GROWTH_RATE")
    with pytest.raises(SchemaValidationError):
        replace(growth, context=development[-1].context)


def test_eval_output_cannot_overwrite_index_or_gold_before_any_model_call(tmp_path):
    from src.evaluation.run_memory_eval import main
    path = tmp_path / "memory.json"
    path.write_text("unchanged")
    for output, error in ((path, ValueError), (tmp_path / "gold" / "report.json", SchemaValidationError)):
        with pytest.raises(error):
            main(["--memory-index", str(path), "--serving-config", "missing-config.json",
                  "--output", str(output)])
    assert path.read_text() == "unchanged"


def test_synthetic_training_cannot_overwrite_existing_or_gold_data(tmp_path):
    from src.evaluation.memory_fixtures import write_synthetic_training
    with pytest.raises(FileExistsError):
        write_synthetic_training(tmp_path)
    with pytest.raises(SchemaValidationError):
        write_synthetic_training(tmp_path / "gold" / "synthetic")
    directory = tmp_path / "new-synthetic"
    write_synthetic_training(directory)
    assert len((directory / "train.jsonl").read_text().splitlines()) == 3
    assert len((directory / "dev.jsonl").read_text().splitlines()) == 4
