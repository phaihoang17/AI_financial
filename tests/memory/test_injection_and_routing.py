from dataclasses import replace
import json
from unittest.mock import patch

import pytest

from src.evaluation.memory_experiment import FixtureProgrammerClient, run_memory_ablation, run_cheap_routing_experiment
from src.evaluation.memory_fixtures import synthetic_training
from src.memory.index import build_index, publish_index
from src.orchestration.api_stages import APIProgrammer
from src.supervisor.schemas import ModelTier
from tests.memory.test_experience import FixtureEncoder
from tests.programmer.helpers import lookup_programmer_input, average_programmer_input


class RecordingClient(FixtureProgrammerClient):
    def __init__(self):
        self.calls = []

    def generate(self, messages, **params):
        self.calls.append((json.loads(messages[1]["content"]), params))
        return super().generate(messages, **params)


@pytest.fixture
def memory():
    _, entries, dev, _ = synthetic_training()
    return build_index(entries, dev, encoder=FixtureEncoder(), dataset_id="synthetic")


def test_served_client_selection_activation_and_strong_escalation():
    cheap, strong = RecordingClient(), RecordingClient()
    stage = APIProgrammer(cheap, strong_client=strong)
    programmer_input = lookup_programmer_input()
    assert stage.generate(programmer_input, ModelTier.CHEAP).program is not None
    assert len(cheap.calls) == 1 and not strong.calls
    stage.generate(programmer_input, ModelTier.STRONG)
    assert strong.calls[-1][1]["thinking"] == {"type": "enabled"}
    with patch("src.supervisor.cheap_routing.CHEAP_ROUTING_ACTIVATED", False):
        stage.generate(programmer_input, ModelTier.CHEAP)
    assert len(strong.calls) == 2
    stage.generate(average_programmer_input(), ModelTier.CHEAP)
    assert len(strong.calls) == 3 and len(cheap.calls) == 1
    assert programmer_input.plan.model_tier is ModelTier.CHEAP
    assert strong.calls[1][0]["plan"]["verify_profile"] == programmer_input.plan.verify_profile.value


def test_memory_only_adds_allowlisted_advice_and_keeps_masked_contract(memory):
    client = RecordingClient()
    programmer_input = lookup_programmer_input()
    before = programmer_input.to_dict()
    result = APIProgrammer(client, memory=memory).generate(programmer_input, ModelTier.CHEAP)
    assert result.program is not None
    payload, _ = client.calls[-1]
    assert "experience_advice" in payload
    for key, value in before.items():
        assert payload[key] == value
    assert programmer_input.to_dict() == before
    assert len(payload["experience_advice"]) <= 3
    assert "bindings" not in payload


def test_unavailable_memory_falls_back_without_extra_generative_call(memory):
    encoder = FixtureEncoder()
    encoder.count_tokens = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("unavailable"))
    unavailable = replace(memory, encoder=encoder)
    client = RecordingClient()
    result = APIProgrammer(client, memory=unavailable).generate(lookup_programmer_input(), ModelTier.CHEAP)
    assert result.program is not None
    assert len(client.calls) == 1
    assert "experience_advice" not in client.calls[0][0]


def test_memory_cannot_make_invalid_program_pass_validation(memory):
    client = RecordingClient()
    good = client.generate

    def invalid(messages, **params):
        proposal = json.loads(good(messages, **params))
        proposal["inputs"][0]["placeholder"] = "invented-placeholder"
        return json.dumps(proposal)

    client.generate = invalid
    result = APIProgrammer(client, memory=memory).generate(lookup_programmer_input(), ModelTier.CHEAP)
    assert result.failure_code == "UNKNOWN_PLACEHOLDER"
    assert result.program is None


def test_ablation_and_tier_experiment_are_read_only_and_honestly_labelled(memory, tmp_path):
    path = tmp_path / "memory.json"
    publish_index(memory, path)
    report = run_memory_ablation(FixtureProgrammerClient(), memory=memory, index_path=path)
    assert report["index_unchanged"]
    assert not report["production_quality_measured"]
    assert report["generative_measurement"] == "CPU_FIXTURE"
    assert [arm["arm"] for arm in report["arms"]] == ["BASE", "TOOLS_ONLY", "TOOLS_MEMORY"]
    assert all(arm["answer_accuracy"] == 1 for arm in report["arms"])
    assert all(arm["program_trace_match_rate"] == 1 for arm in report["arms"])
    assert all(arm["completion_tokens"] is None for arm in report["arms"])
    routing = run_cheap_routing_experiment(FixtureProgrammerClient())
    assert routing["eligible_case_count"] == 2
    assert routing["accuracy_delta"] == 0
