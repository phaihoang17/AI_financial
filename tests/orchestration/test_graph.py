from __future__ import annotations

from dataclasses import replace
import json

import pytest
from langgraph.checkpoint.memory import MemorySaver

from src.evidence.schemas import CanonicalDecimal, Scale
from src.indexing.schemas import ScaleHintSource
from src.orchestration.answer_builder import build_final_answer
from src.orchestration.nodes import OrchestrationDependencies
from src.orchestration.execution_routing import classify_execution_failure
from src.orchestration.graph import OrchestrationGraph
from src.orchestration.ports import NLUStageOutput, RetrievalArtifacts
from src.orchestration.schemas import (
    ExecutionAction,
    ExecutionClassificationError,
    FinalResponseStatus,
    M9Phase,
)
from src.orchestration.state import AttemptEntryStage, AttemptRecord
from src.programmer.generator import generate_program
from src.programmer.schemas import ProgramOutputKind
from src.sandbox.executor import ExecutionInfrastructureFailureCode
from src.sandbox.interpreter import InterpreterFailureCode, interpret_execution_request
from src.sandbox.limits import ResourceLimitFailureCode
from src.sandbox.policy import ExecutionPolicyFailureCode
from src.sandbox.schemas import (
    EXECUTION_RESULT_SCHEMA_VERSION,
    ExecutionDatum,
    ExecutionFailure,
    ExecutionFailureStage,
    ExecutionOutput,
    ExecutionResult,
)
from src.sandbox.security import SecurityFailureCode
from src.supervisor.schemas import ModelTier
from src.understanding.planning_gate import FindingName, PlanningGate
from src.understanding.schemas import Operation, SchemaValidationError
from src.verification.schemas import RetryAction
from tests.orchestration.test_straight_through_graph import (
    ARTIFACT_VERSIONS,
    FakeNLU,
    MemoryBindingStore,
    fixture_pairs,
    query_understanding,
)
from tests.evidence.m5_helpers import hint
from tests.verification.helpers import text_item


class ScriptedFixture:
    def __init__(self, pair_scripts):
        self.pair_scripts = pair_scripts
        self.current = 0
        self.calls = []

    @property
    def pairs(self):
        return self.pair_scripts[self.current]

    def retrieve(self, request):
        self.current = min(len(self.calls), len(self.pair_scripts) - 1)
        self.calls.append(request)
        hints = {
            location.candidate_id: (
                hint(
                    f"scale-{location.matched_period}-{self.current}",
                    location,
                    ScaleHintSource.HEADER,
                    Scale.MILLION,
                ),
            )
            for _, location in self.pairs
        }
        return RetrievalArtifacts(
            query=request.query,
            candidates=(),
            scale_hints_by_candidate=hints,
        )

    def locate(self, query, candidates):
        return [location for _, location in self.pairs]

    def build(self, locations, candidates):
        return [item for item, _ in self.pairs]


class RecordingProgrammer:
    def __init__(self):
        self.calls = []

    def generate(self, programmer_input, model_tier):
        self.calls.append((programmer_input, model_tier))
        return generate_program(programmer_input)


class ScriptedSandbox:
    def __init__(self, outcomes=("success",)):
        self.outcomes = list(outcomes)
        self.calls = []

    def execute(self, request):
        index = min(len(self.calls), len(self.outcomes) - 1)
        outcome = self.outcomes[index]
        self.calls.append(request)
        if isinstance(outcome, tuple):
            stage, code = outcome
            return ExecutionResult(
                schema_version=EXECUTION_RESULT_SCHEMA_VERSION,
                program_id=request.program.program_id,
                success=False,
                output=None,
                failure=ExecutionFailure(stage, code, "scripted failure"),
                execution_ms=0,
            )
        result = replace(interpret_execution_request(request), execution_ms=0)
        if outcome == "wrong_kind":
            wrong_kind = (
                ProgramOutputKind.ORDERED_VALUES
                if result.output.kind is ProgramOutputKind.SCALAR
                else ProgramOutputKind.SCALAR
            )
            return replace(
                result,
                output=ExecutionOutput(wrong_kind, list(result.output.values)),
            )
        if outcome == "wrong_scale":
            return replace(
                result,
                output=ExecutionOutput(
                    result.output.kind,
                    [
                        ExecutionDatum(
                            CanonicalDecimal(result.output.values[0].value),
                            Scale.BILLION,
                            result.output.values[0].unit,
                        )
                    ],
                ),
            )
        return result


def make_graph(
    current,
    pair_scripts,
    *,
    gate=None,
    sandbox_outcomes=("success",),
    checkpointer=None,
):
    gate = PlanningGate(True, [], []) if gate is None else gate
    fixture = ScriptedFixture(pair_scripts)
    programmer = RecordingProgrammer()
    sandbox = ScriptedSandbox(sandbox_outcomes)
    bindings = MemoryBindingStore()
    dependencies = OrchestrationDependencies(
        nlu=FakeNLU(NLUStageOutput(current, gate)),
        retrieval=fixture,
        cell_locator=fixture,
        evidence_builder=fixture,
        programmer=programmer,
        sandbox=sandbox,
        binding_store=bindings,
        top_k=10,
        artifact_versions=ARTIFACT_VERSIONS,
    )
    return OrchestrationGraph(dependencies, checkpointer=checkpointer), dependencies


def run(graph, current, *, request_id="request-orchestration", config=None):
    return graph.run(
        request_id=request_id,
        raw_question=current.raw_question,
        config=config,
    )


def test_clarification_before_retrieval_uses_only_actionable_gate_findings():
    current = query_understanding()
    gate = PlanningGate(False, [FindingName.COMPANY], [FindingName.PERIOD])
    graph, deps = make_graph(current, [fixture_pairs(("2015",), ("12.5",))], gate=gate)

    result = run(graph, current)

    assert result.state.outcome is FinalResponseStatus.CLARIFICATION
    assert result.state.final_response.reason_code == "MISSING_COMPANY+AMBIGUOUS_PERIOD"
    assert result.state.failure_attribution.stage.value == "NLU"
    assert deps.retrieval.calls == []
    assert deps.programmer.calls == []
    assert deps.sandbox.calls == []


def test_supervisor_early_abstain_is_terminal_before_retrieval():
    current = query_understanding(scope_missing=True)
    graph, deps = make_graph(current, [fixture_pairs(("2015",), ("12.5",))])

    result = run(graph, current)

    assert result.state.outcome is FinalResponseStatus.ABSTAIN
    assert result.state.final_response.reason_code == "MISSING_STATEMENT_SCOPE"
    assert result.state.failure_attribution.stage.value == "SUPERVISOR"
    assert deps.retrieval.calls == []


def test_verification_pass_builds_terminal_answer_with_exact_output_and_citations():
    current = query_understanding()
    graph, _ = make_graph(current, [fixture_pairs(("2015",), ("12.5",))])

    result = run(graph, current)

    state = result.state
    attempt = state.attempts[-1]
    assert result.successful
    assert state.phase is M9Phase.TERMINAL
    assert attempt.retry_directive.action is RetryAction.PASS
    assert state.final_response.answer.output.to_dict() == attempt.execution_result.output.to_dict()
    assert [item.evidence_id for item in state.final_response.answer.evidence] == [
        item.evidence_id for item in attempt.programmer_result.program.inputs
    ]


def test_permanent_verification_failure_abstains_without_retry():
    current = query_understanding()
    graph, deps = make_graph(
        current,
        [fixture_pairs(("2015",), ("12.5",))],
        sandbox_outcomes=("wrong_scale",),
    )

    result = run(graph, current)

    assert result.state.outcome is FinalResponseStatus.ABSTAIN
    assert len(result.state.attempts) == 1
    assert result.state.retry_state.retries_used == 0
    assert result.state.failure_attribution.stage.value == "VERIFICATION"
    assert len(deps.sandbox.calls) == 1


def test_verification_retrieval_retry_preserves_policy_and_replaces_artifacts():
    current = query_understanding()
    good = fixture_pairs(("2015",), ("12.5",))
    item, location = good[0]
    bad = [(replace(item, report_year=2014), replace(location, report_year=2014))]
    graph, deps = make_graph(current, [bad, good])

    result = run(graph, current)

    assert result.successful
    assert len(result.state.attempts) == 2
    first, second = result.state.attempts
    assert first.retry_directive.action is RetryAction.RETRY_RETRIEVAL
    assert second.entry_stage is AttemptEntryStage.RETRIEVAL
    assert first.retrieval_query.to_dict() == second.retrieval_query.to_dict()
    assert result.state.plan_fingerprint == deps.retrieval.calls[0].plan_fingerprint
    assert result.state.retrieval_policy_fingerprint == deps.retrieval.calls[1].retrieval_policy_fingerprint
    assert first.evidence_items[0].report_year == 2014
    assert second.evidence_items[0].report_year == 2015


def test_strong_programmer_retry_preserves_evidence_and_binding():
    periods = ("2014", "2015")
    current = query_understanding(periods, Operation.GROWTH)
    graph, deps = make_graph(
        current,
        [fixture_pairs(periods, ("100", "125"))],
        sandbox_outcomes=("wrong_kind", "success"),
    )

    result = run(graph, current)

    assert result.successful
    first, second = result.state.attempts
    assert first.retry_directive.action is RetryAction.RETRY_PROGRAMMER
    assert second.entry_stage is AttemptEntryStage.PROGRAMMER
    assert second.model_tier is ModelTier.STRONG
    assert second.binding_ref == first.binding_ref
    assert [item.to_dict() for item in second.evidence_items] == [
        item.to_dict() for item in first.evidence_items
    ]
    assert second.programmer_result.program.program_id == first.programmer_result.program.program_id
    assert len(deps.retrieval.calls) == 1
    assert len(deps.programmer.calls) == 2


def test_cheap_to_strong_escalates_once_and_never_downgrades():
    current = query_understanding()
    graph, deps = make_graph(
        current,
        [fixture_pairs(("2015",), ("12.5",))],
        sandbox_outcomes=("wrong_kind", "success"),
    )

    result = run(graph, current)

    assert result.successful
    assert [item.model_tier for item in result.state.attempts] == [
        ModelTier.CHEAP,
        ModelTier.STRONG,
    ]
    assert result.state.attempts[0].retry_directive.action is RetryAction.ESCALATE_STRONG
    assert result.state.retry_state.strong_escalated
    assert [tier for _, tier in deps.programmer.calls] == [ModelTier.CHEAP, ModelTier.STRONG]


def test_sandbox_only_retry_preserves_program_and_skips_verification_on_failure():
    current = query_understanding()
    graph, deps = make_graph(
        current,
        [fixture_pairs(("2015",), ("12.5",))],
        sandbox_outcomes=(
            (ExecutionFailureStage.RESOURCE, ResourceLimitFailureCode.WALL_CLOCK_TIMEOUT.value),
            "success",
        ),
    )

    result = run(graph, current)

    assert result.successful
    first, second = result.state.attempts
    assert first.execution_directive.action is ExecutionAction.RETRY_SANDBOX
    assert first.verification_report is None
    assert second.entry_stage is AttemptEntryStage.SANDBOX
    assert second.programmer_result.program.program_id == first.programmer_result.program.program_id
    assert len(deps.programmer.calls) == 1
    assert len(deps.sandbox.calls) == 2


def test_final_retry_is_allowed_and_next_repairable_failure_exhausts_budget():
    current = query_understanding()
    graph, deps = make_graph(
        current,
        [fixture_pairs(("2015",), ("12.5",))],
        sandbox_outcomes=("wrong_kind",),
    )

    result = run(graph, current)

    assert result.state.outcome is FinalResponseStatus.ABSTAIN
    assert len(result.state.attempts) == 2
    assert result.state.attempts[0].retry_directive.action is RetryAction.ESCALATE_STRONG
    assert result.state.attempts[1].retry_directive.action is RetryAction.ABSTAIN
    assert result.state.retry_state.retries_used == result.state.retry_state.max_retries == 1
    assert result.state.retry_state.terminal
    assert len(deps.sandbox.calls) == 2


def test_security_failure_abstains_immediately_and_never_verifies():
    current = query_understanding()
    graph, _ = make_graph(
        current,
        [fixture_pairs(("2015",), ("12.5",))],
        sandbox_outcomes=((ExecutionFailureStage.SECURITY, SecurityFailureCode.NETWORK_ACCESS_DENIED.value),),
    )

    result = run(graph, current)

    attempt = result.state.attempts[0]
    assert result.state.outcome is FinalResponseStatus.ABSTAIN
    assert attempt.execution_directive.action is ExecutionAction.ABSTAIN
    assert attempt.verification_report is None
    assert result.state.retry_state.retries_used == 0


def test_unknown_execution_code_fails_closed_without_directive_or_fallback():
    current = query_understanding()
    graph, _ = make_graph(
        current,
        [fixture_pairs(("2015",), ("12.5",))],
        sandbox_outcomes=((ExecutionFailureStage.INFRASTRUCTURE, "UNKNOWN_CODE"),),
    )

    result = run(graph, current)

    attempt = result.state.attempts[0]
    assert result.state.outcome is FinalResponseStatus.ABSTAIN
    assert result.state.final_response.reason_code == "UNKNOWN_CODE"
    assert result.state.failure_attribution.code == "UNKNOWN_CODE"
    assert attempt.execution_result.failure.code == "UNKNOWN_CODE"
    assert attempt.execution_directive is None
    assert attempt.verification_report is None


@pytest.mark.parametrize(
    ("stage", "code", "action"),
    [
        (ExecutionFailureStage.POLICY, ExecutionPolicyFailureCode.TOO_MANY_STEPS.value, ExecutionAction.RETRY_PROGRAMMER),
        (ExecutionFailureStage.POLICY, ExecutionPolicyFailureCode.INVALID_REQUEST.value, ExecutionAction.ABSTAIN),
        (ExecutionFailureStage.CONVERSION, "SOURCE_SCALE_REQUIRED", ExecutionAction.RETRY_RETRIEVAL),
        (ExecutionFailureStage.ARITHMETIC, InterpreterFailureCode.INVALID_FORMULA.value, ExecutionAction.RETRY_PROGRAMMER),
        (ExecutionFailureStage.ARITHMETIC, InterpreterFailureCode.DIVISION_BY_ZERO.value, ExecutionAction.ABSTAIN),
        (ExecutionFailureStage.RESOURCE, ResourceLimitFailureCode.MEMORY_LIMIT.value, ExecutionAction.RETRY_SANDBOX),
        (ExecutionFailureStage.INFRASTRUCTURE, ExecutionInfrastructureFailureCode.WORKER_CRASHED.value, ExecutionAction.RETRY_SANDBOX),
        (ExecutionFailureStage.INFRASTRUCTURE, ExecutionInfrastructureFailureCode.INVALID_WORKER_RESULT.value, ExecutionAction.ABSTAIN),
    ],
)
def test_execution_directive_mapping(stage, code, action):
    assert classify_execution_failure(stage, code) is action


def test_execution_classifier_rejects_unknown_code():
    with pytest.raises(ExecutionClassificationError):
        classify_execution_failure(ExecutionFailureStage.RESOURCE, "NEW_CODE")


def test_binding_map_remains_private_in_terminal_checkpoint():
    current = query_understanding()
    saver = MemorySaver()
    graph, _ = make_graph(
        current,
        [fixture_pairs(("2015",), ("12.5",))],
        checkpointer=saver,
    )
    config = {"configurable": {"thread_id": "orchestration-private"}}

    result = run(graph, current, config=config)
    encoded = json.dumps(graph.graph.get_state(config).values, sort_keys=True)

    assert result.successful
    assert "binding_map" not in encoded.casefold()
    assert '"bindings"' not in encoded.casefold()


def test_answer_builder_excludes_evidence_not_used_by_program():
    current = query_understanding()
    graph, _ = make_graph(current, [fixture_pairs(("2015",), ("12.5",))])
    state = run(graph, current).state
    attempt = state.attempts[-1]
    unused = text_item()

    answer = build_final_answer(
        state.supervisor_result.plan,
        attempt.programmer_result.program,
        attempt.execution_result,
        attempt.verification_report,
        [*attempt.evidence_items, unused],
        attempt.schema_links,
    )

    assert [item.evidence_id for item in answer.evidence] == [
        attempt.programmer_result.program.inputs[0].evidence_id
    ]
    assert unused.evidence_id not in {item.evidence_id for item in answer.evidence}


def test_terminal_state_rejects_every_later_transition():
    current = query_understanding()
    graph, _ = make_graph(current, [fixture_pairs(("2015",), ("12.5",))])
    state = run(graph, current).state

    with pytest.raises(SchemaValidationError, match="terminal state"):
        state.append_attempt(
            AttemptRecord(1, AttemptEntryStage.SANDBOX, ModelTier.CHEAP),
            next_retry_state=state.retry_state,
        )
    with pytest.raises(SchemaValidationError, match="terminal state"):
        state.terminate(state.final_response)


def test_replay_is_deterministic_and_retry_loop_is_bounded():
    current = query_understanding()
    pairs = [fixture_pairs(("2015",), ("12.5",))]
    first_graph, _ = make_graph(current, pairs, sandbox_outcomes=("wrong_kind",))
    second_graph, _ = make_graph(current, pairs, sandbox_outcomes=("wrong_kind",))

    first = run(first_graph, current)
    second = run(second_graph, current)

    assert first.state.to_dict() == second.state.to_dict()
    assert len(first.state.attempts) <= first.state.retry_state.max_retries + 1
