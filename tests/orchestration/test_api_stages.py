import json
from unittest.mock import Mock

import pytest

from src.evaluation.e2e_fixtures import e2e_fixture_cases
from src.orchestration.api_stages import APINLU, APIProgrammer
from src.programmer.schemas import ProgrammerStatus
from src.supervisor.schemas import ModelTier
from src.understanding.company_resolver import CompanyAlias
from src.understanding.planning_gate import FindingName
from src.understanding.schemas import SchemaValidationError, StatementScope


def nlu(proposal):
    client = Mock()
    client.generate.return_value = json.dumps(proposal)
    return APINLU(client, [CompanyAlias("Company A", "Company A", "AAA")])


def test_api_nlu_keeps_period_scope_scale_deterministic():
    stage = nlu({"company": "AAA", "metrics": ["LNST"], "operation": "none"})
    question = "LNST hợp nhất của AAA năm 2023 là bao nhiêu triệu đồng?"
    output = stage.understand(question)
    assert output.planning_gate.allowed
    assert output.query_understanding.raw_question == question
    assert output.query_understanding.periods[0].value == "2023"
    assert output.query_understanding.statement_scope.value is StatementScope.HOP_NHAT
    assert output.query_understanding.requested_scale == "MILLION"
    assert stage.client.generate.call_args.kwargs["thinking"] == {"type": "disabled"}


def test_unknown_metric_does_not_become_supported_and_scope_is_not_guessed():
    output = nlu({"company": "AAA", "metrics": ["Lãi tiền gửi"], "operation": "none"}).understand(
        "Lãi tiền gửi AAA năm 2023?"
    )
    assert not output.planning_gate.allowed
    assert output.planning_gate.missing_information == [FindingName.METRIC]
    assert output.query_understanding.metrics == []
    assert output.query_understanding.statement_scope.value is None


def test_api_cannot_set_planning_gate():
    with pytest.raises(SchemaValidationError):
        nlu({"company": "AAA", "metrics": [], "operation": "none", "allowed": True}).understand("q")


def test_api_mean_routes_strong_strict_and_rejects_noncanonical_operation():
    from src.supervisor.planner import supervise_batch1
    from src.supervisor.schemas import VerifyProfile

    question = "Trung bình LNST hợp nhất của AAA năm 2022 và năm 2023?"
    stage = nlu({"company": "AAA", "metrics": ["LNST"], "operation": "aggregate"})
    output = stage.understand(question)
    plan = supervise_batch1(output.query_understanding, output.planning_gate).plan
    assert plan.formula_id == "AVERAGE"
    assert plan.periods == ["2022", "2023"]
    assert plan.model_tier is ModelTier.STRONG
    assert plan.verify_profile is VerifyProfile.STRICT
    stage.client.generate.return_value = json.dumps(
        {"company": "AAA", "metrics": ["LNST"], "operation": "average"}
    )
    with pytest.raises(ValueError):
        stage.understand(question)


def test_programmer_validates_symbolic_proposal_and_uses_strong_thinking():
    state = e2e_fixture_cases()[0].graph_factory().run(request_id="test", raw_question=e2e_fixture_cases()[0].raw_question).state
    attempt = state.attempts[-1]
    expected = attempt.programmer_result.program
    proposal = {key: value for key, value in expected.to_dict().items()
                if key in {"inputs", "steps", "output_ref", "output_kind"}}
    client = Mock()
    client.generate.return_value = json.dumps(proposal)
    result = APIProgrammer(client).generate(attempt.programmer_input, ModelTier.STRONG)
    assert result.status is ProgrammerStatus.GENERATED
    assert result.program.to_dict() == expected.to_dict()
    assert client.generate.call_args.kwargs["thinking"] == {"type": "enabled"}
    assert client.generate.call_args.kwargs["reasoning_effort"] == "high"
    proposal["inputs"][0]["placeholder"] = "123456789"
    client.generate.return_value = json.dumps(proposal)
    rejected = APIProgrammer(client).generate(attempt.programmer_input, ModelTier.STRONG)
    assert rejected.status is ProgrammerStatus.REJECTED
    assert rejected.failure_code == "UNKNOWN_PLACEHOLDER"


def test_average_prompt_uses_registry_and_rejects_one_ref():
    from tests.orchestration.test_graph import make_graph, run
    from tests.orchestration.test_straight_through_graph import query_understanding, fixture_pairs
    from src.understanding.schemas import Operation
    current = query_understanding(("2014", "2015"), Operation.AGGREGATE)
    graph, _ = make_graph(current, [fixture_pairs(("2014", "2015"), ("10", "20"))])
    state = run(graph, current).state
    attempt = state.attempts[-1]
    proposal = {key: value for key, value in attempt.programmer_result.program.to_dict().items()
                if key in {"inputs", "steps", "output_ref", "output_kind"}}
    client = Mock()
    client.generate.return_value = json.dumps(proposal)
    stage = APIProgrammer(client)
    assert stage.generate(attempt.programmer_input, ModelTier.STRONG).status is ProgrammerStatus.GENERATED
    payload = json.loads(client.generate.call_args.args[0][1]["content"])
    assert payload["formula_input_contract"]["min_arity"] == 2
    proposal["steps"][-1]["input_refs"] = proposal["steps"][-1]["input_refs"][:1]
    client.generate.return_value = json.dumps(proposal)
    assert stage.generate(attempt.programmer_input, ModelTier.STRONG).failure_code == "FORMULA_ARITY_MISMATCH"


@pytest.mark.parametrize("response,code", [("{", "PROGRAMMER_JSON_INVALID"), ("[]", "INVALID_SCHEMA")])
def test_programmer_rejects_invalid_json_without_repair(response, code):
    case = e2e_fixture_cases()[0]
    attempt = case.graph_factory().run(request_id="invalid", raw_question=case.raw_question).state.attempts[-1]
    client = Mock()
    client.generate.return_value = response
    result = APIProgrammer(client).generate(attempt.programmer_input, ModelTier.STRONG)
    assert result.status is ProgrammerStatus.REJECTED
    assert result.failure_code == code
    assert client.generate.call_count == 1


def test_programmer_preserves_truncation_code_from_client():
    from src.serving.schemas import ServingError
    case = e2e_fixture_cases()[0]
    attempt = case.graph_factory().run(request_id="truncated", raw_question=case.raw_question).state.attempts[-1]
    client = Mock()
    client.generate.side_effect = ServingError("SERVING_RESPONSE_TRUNCATED", "token limit")
    result = APIProgrammer(client).generate(attempt.programmer_input, ModelTier.STRONG)
    assert result.failure_code == "SERVING_RESPONSE_TRUNCATED"
    assert result.program is None
    assert client.generate.call_count == 1
