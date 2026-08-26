from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json

import pytest
from langgraph.checkpoint.memory import MemorySaver

from src.evidence.m5_schemas import BindingMap
from src.evidence.schemas import CanonicalDecimal, Scale
from src.indexing.schemas import ScaleHintSource
from src.orchestration.nodes import (
    OrchestrationDependencies,
    OrchestrationFailure,
    OrchestrationFailureCode,
)
from src.orchestration.graph import STRAIGHT_THROUGH_NODE_SEQUENCE, StraightThroughOrchestrationGraph
from src.orchestration.ports import NLUStageOutput, RetrievalArtifacts
from src.programmer.generator import generate_program
from src.programmer.schemas import Program, ProgrammerResult, ProgrammerStatus
from src.retrieval.evidence import MatchBasis
from src.retrieval.schemas import RetrievalContractError
from src.sandbox.interpreter import interpret_execution_request
from src.sandbox.schemas import (
    EXECUTION_RESULT_SCHEMA_VERSION,
    ExecutionDatum,
    ExecutionFailure,
    ExecutionFailureStage,
    ExecutionOutput,
    ExecutionResult,
)
from src.supervisor.schemas import ModelTier, TableClass
from src.understanding.planning_gate import FindingName, PlanningGate
from src.understanding.schemas import (
    Operation,
    PeriodKind,
    PeriodUnderstanding,
    QueryUnderstanding,
    StatementScopeUnderstanding,
)
from src.verification.schemas import VerificationReport
from tests.evidence.m5_helpers import grounded_cell, hint, understanding


ARTIFACT_VERSIONS = {
    "bm25": "fixture-bm25-v1",
    "embedding": "fixture-bge-m3-v1",
    "reranker": "fixture-reranker-v1",
}


def query_understanding(
    periods=("2015",), operation=Operation.NONE, *, scope_missing=False
):
    base = understanding()
    return QueryUnderstanding(
        raw_question=f"LNST {' '.join(periods)} {operation.value}",
        company=base.company,
        periods=[
            PeriodUnderstanding(period, PeriodKind.NAM, period)
            for period in periods
        ],
        statement_scope=(
            StatementScopeUnderstanding(None, False, 0.0)
            if scope_missing
            else base.statement_scope
        ),
        metrics=base.metrics,
        operation=operation,
        requested_scale=None,
        requested_unit=None,
        missing_information=[],
        ambiguities=[],
        confidence=1.0,
    )


def fixture_pairs(periods, values):
    pairs = []
    for period, value in zip(periods, values):
        item, location = grounded_cell(
            period,
            column_labels=(period,),
            raw_value=value,
            decimal_value=value,
        )
        item = replace(
            item,
            period=period,
            metric="LNST",
            ticker="AAA",
            company_name="Test Company",
            report_year=int(period),
        )
        location = replace(
            location,
            matched_metric="LNST",
            matched_period=period,
            match_basis=MatchBasis.EXACT_ROW_PATH,
            ticker="AAA",
            company_name="Test Company",
            report_year=int(period),
            statement_scope=item.statement_scope,
            table_class=TableClass.INCOME_STATEMENT,
        )
        pairs.append((item, location))
    return pairs


class FakeNLU:
    def __init__(self, output):
        self.output = output
        self.calls = 0

    def understand(self, raw_question):
        self.calls += 1
        assert raw_question == self.output.query_understanding.raw_question
        return self.output


class FakeRetrieval:
    def __init__(self, pairs, *, fail=False):
        self.pairs = pairs
        self.fail = fail
        self.calls = []

    def retrieve(self, request):
        self.calls.append(request)
        if self.fail:
            raise RetrievalContractError("FIXTURE_RETRIEVAL_FAILED", "fixture")
        hints = {
            location.candidate_id: (
                hint(
                    f"scale-{location.matched_period}",
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


class FakeCellLocator:
    def __init__(self, pairs):
        self.pairs = pairs
        self.calls = 0

    def locate(self, query, candidates):
        self.calls += 1
        return [location for _, location in self.pairs]


class FakeEvidenceBuilder:
    def __init__(self, pairs, *, omit_last=False):
        self.pairs = pairs
        self.omit_last = omit_last
        self.calls = 0

    def build(self, locations, candidates):
        self.calls += 1
        items = [item for item, _ in self.pairs]
        return items[:-1] if self.omit_last else items


class FakeProgrammer:
    def __init__(self, *, reject=False, invalid_program=False):
        self.reject = reject
        self.invalid_program = invalid_program
        self.calls = []

    def generate(self, programmer_input, model_tier):
        self.calls.append((programmer_input, model_tier))
        if self.reject:
            return ProgrammerResult(
                ProgrammerStatus.REJECTED,
                None,
                "FIXTURE_PROGRAMMER_REJECTED",
                "fixture rejection",
            )
        result = generate_program(programmer_input)
        if self.invalid_program:
            payload = result.program.to_dict()
            payload["program_id"] = "0" * 64
            result = ProgrammerResult(
                ProgrammerStatus.GENERATED,
                Program.from_dict(payload),
                None,
                None,
            )
        return result


class FakeSandbox:
    def __init__(self, *, fail=False, wrong_output=False):
        self.fail = fail
        self.wrong_output = wrong_output
        self.calls = []

    def execute(self, request):
        self.calls.append(request)
        if self.fail:
            return ExecutionResult(
                schema_version=EXECUTION_RESULT_SCHEMA_VERSION,
                program_id=request.program.program_id,
                success=False,
                output=None,
                failure=ExecutionFailure(
                    ExecutionFailureStage.ARITHMETIC,
                    "FIXTURE_SANDBOX_FAILED",
                    "fixture execution failure",
                ),
                execution_ms=0,
            )
        result = replace(interpret_execution_request(request), execution_ms=0)
        if self.wrong_output:
            result = replace(
                result,
                output=ExecutionOutput(
                    kind=result.output.kind,
                    values=[ExecutionDatum(CanonicalDecimal("999"), Scale.BILLION, None)],
                ),
            )
        return result


class MemoryBindingStore:
    def __init__(self):
        self.values = {}
        self.resolve_calls = 0

    def put(self, binding_map):
        encoded = json.dumps(binding_map.to_dict(), sort_keys=True).encode()
        reference = f"binding://fixture/{sha256(encoded).hexdigest()}"
        self.values[reference] = BindingMap.from_dict(binding_map.to_dict())
        return reference

    def resolve(self, binding_ref):
        self.resolve_calls += 1
        return BindingMap.from_dict(self.values[binding_ref].to_dict())

    def contains(self, binding_ref):
        return binding_ref in self.values

    def discard(self, binding_ref):
        self.values.pop(binding_ref, None)


def make_graph(
    current_understanding,
    pairs,
    *,
    gate=None,
    retrieval_fail=False,
    evidence_incomplete=False,
    programmer_reject=False,
    invalid_program=False,
    sandbox_fail=False,
    verification_fail=False,
    checkpointer=None,
):
    gate = PlanningGate(True, [], []) if gate is None else gate
    nlu = FakeNLU(NLUStageOutput(current_understanding, gate))
    retrieval = FakeRetrieval(pairs, fail=retrieval_fail)
    locator = FakeCellLocator(pairs)
    builder = FakeEvidenceBuilder(pairs, omit_last=evidence_incomplete)
    programmer = FakeProgrammer(
        reject=programmer_reject, invalid_program=invalid_program
    )
    sandbox = FakeSandbox(fail=sandbox_fail, wrong_output=verification_fail)
    bindings = MemoryBindingStore()
    deps = OrchestrationDependencies(
        nlu=nlu,
        retrieval=retrieval,
        cell_locator=locator,
        evidence_builder=builder,
        programmer=programmer,
        sandbox=sandbox,
        binding_store=bindings,
        top_k=10,
        artifact_versions=ARTIFACT_VERSIONS,
    )
    return StraightThroughOrchestrationGraph(deps, checkpointer=checkpointer), deps


def run(graph, current_understanding, *, request_id="request-straight-through", config=None):
    return graph.run(
        request_id=request_id,
        raw_question=current_understanding.raw_question,
        config=config,
    )


def test_successful_lookup_straight_through_path_and_exact_stage_sequence():
    current = query_understanding()
    original_understanding = current.to_dict()
    graph, deps = make_graph(current, fixture_pairs(("2015",), ("12.5",)))

    result = run(graph, current)

    assert STRAIGHT_THROUGH_NODE_SEQUENCE == (
        "nlu", "supervisor", "retrieval", "evidence",
        "programmer", "sandbox", "verification",
    )
    assert result.successful
    assert isinstance(result.verification_report, VerificationReport)
    assert result.state.attempts[0].attempt_index == 0
    assert result.state.attempts[0].model_tier is ModelTier.CHEAP
    assert len(deps.retrieval.calls) == 1
    assert len(deps.programmer.calls) == 1
    assert len(deps.sandbox.calls) == 1
    request = deps.retrieval.calls[0]
    plan = result.state.supervisor_result.plan
    assert current.to_dict() == original_understanding
    assert request.plan.to_dict() == plan.to_dict()
    assert request.query.company.ticker == plan.company.ticker
    assert request.query.company.name == plan.company.name
    assert request.query.periods == plan.periods
    assert request.query.statement_scope is plan.statement_scope
    assert request.query.eligible_source_types == plan.evidence_sources
    assert [item.to_dict() for item in request.plan.retrieval_requirements] == [
        item.to_dict() for item in plan.retrieval_requirements
    ]


@pytest.mark.parametrize(
    ("periods", "operation", "values", "expected", "tier"),
    [
        (("2014", "2015"), Operation.GROWTH, ("100", "125"), "25", ModelTier.STRONG),
        (("2013", "2014", "2015"), Operation.AGGREGATE, ("10", "20", "30"), "20000000", ModelTier.STRONG),
    ],
)
def test_successful_growth_and_average_fixture_paths(
    periods, operation, values, expected, tier
):
    current = query_understanding(periods, operation)
    graph, _ = make_graph(current, fixture_pairs(periods, values))

    result = run(graph, current)

    assert result.successful
    attempt = result.state.attempts[0]
    assert attempt.model_tier is tier
    assert attempt.execution_result.output.values[0].value == expected


def test_planning_gate_stop_prevents_supervisor_and_retrieval():
    current = query_understanding()
    blocked = PlanningGate(False, [FindingName.COMPANY], [])
    graph, deps = make_graph(current, fixture_pairs(("2015",), ("12.5",)), gate=blocked)

    result = run(graph, current)

    assert isinstance(result.failure, PlanningGate)
    assert result.state.supervisor_result is None
    assert result.state.attempts == ()
    assert deps.retrieval.calls == []


def test_supervisor_abstain_stops_without_retrieval_or_attempt():
    current = query_understanding(scope_missing=True)
    graph, deps = make_graph(current, fixture_pairs(("2015",), ("12.5",)))

    result = run(graph, current)

    assert result.failure.abstain
    assert result.failure.plan is None
    assert result.state.plan_fingerprint is None
    assert result.state.attempts == ()
    assert deps.retrieval.calls == []


def test_retrieval_failure_preserves_native_typed_failure():
    current = query_understanding()
    graph, _ = make_graph(
        current, fixture_pairs(("2015",), ("12.5",)), retrieval_fail=True
    )

    result = run(graph, current)

    assert isinstance(result.failure, RetrievalContractError)
    assert result.failure.code == "FIXTURE_RETRIEVAL_FAILED"
    assert result.state.attempts[0].evidence_items == ()


def test_evidence_incomplete_stops_before_scale_linking_and_programmer():
    periods = ("2014", "2015")
    current = query_understanding(periods, Operation.GROWTH)
    graph, deps = make_graph(
        current,
        fixture_pairs(periods, ("100", "125")),
        evidence_incomplete=True,
    )

    result = run(graph, current)

    assert not result.failure.complete
    attempt = result.state.attempts[0]
    assert attempt.evidence_completeness == result.failure
    assert attempt.scale_unit_resolutions == ()
    assert deps.programmer.calls == []


def test_unavailable_table_class_is_typed_and_never_inferred_from_plan():
    current = query_understanding()
    pairs = fixture_pairs(("2015",), ("12.5",))
    item, location = pairs[0]
    pairs = [(item, replace(location, table_class=None))]
    graph, deps = make_graph(current, pairs)

    result = run(graph, current)

    assert isinstance(result.failure, OrchestrationFailure)
    assert result.failure.code == OrchestrationFailureCode.TABLE_CLASS_UNAVAILABLE.value
    assert result.state.attempts[0].cell_locations[0].table_class is None
    assert deps.programmer.calls == []


def test_programmer_rejection_stops_before_sandbox():
    current = query_understanding()
    graph, deps = make_graph(
        current,
        fixture_pairs(("2015",), ("12.5",)),
        programmer_reject=True,
    )

    result = run(graph, current)

    assert isinstance(result.failure, ProgrammerResult)
    assert result.failure.status is ProgrammerStatus.REJECTED
    assert deps.sandbox.calls == []


def test_generated_program_is_revalidated_before_sandbox():
    current = query_understanding()
    graph, deps = make_graph(
        current,
        fixture_pairs(("2015",), ("12.5",)),
        invalid_program=True,
    )

    result = run(graph, current)

    assert isinstance(result.failure, ProgrammerResult)
    assert result.failure.failure_code == "PROGRAM_ID_MISMATCH"
    assert deps.sandbox.calls == []


def test_sandbox_failure_preserves_execution_failure_and_skips_verification():
    current = query_understanding()
    graph, _ = make_graph(
        current,
        fixture_pairs(("2015",), ("12.5",)),
        sandbox_fail=True,
    )

    result = run(graph, current)

    assert isinstance(result.failure, ExecutionResult)
    assert not result.failure.success
    assert result.failure.failure.code == "FIXTURE_SANDBOX_FAILED"
    assert result.state.attempts[0].verification_report is None


def test_verification_failure_preserves_report_without_retry_directive():
    current = query_understanding()
    graph, _ = make_graph(
        current,
        fixture_pairs(("2015",), ("12.5",)),
        verification_fail=True,
    )

    result = run(graph, current)

    assert isinstance(result.failure, VerificationReport)
    assert not result.failure.passed
    assert result.state.attempts[0].verification_report == result.failure
    assert result.state.attempts[0].retry_directive is None
    assert result.state.attempts[0].execution_directive is None


def test_binding_map_absent_from_state_and_langgraph_checkpoint():
    current = query_understanding()
    saver = MemorySaver()
    graph, _ = make_graph(
        current,
        fixture_pairs(("2015",), ("12.5",)),
        checkpointer=saver,
    )
    config = {"configurable": {"thread_id": "batch2-checkpoint"}}

    result = run(graph, current, config=config)
    checkpoint = graph.graph.get_state(config).values
    encoded = json.dumps(checkpoint, sort_keys=True)

    assert result.successful
    assert "binding_map" not in encoded.casefold()
    assert '"bindings"' not in encoded.casefold()
    assert result.state.attempts[0].binding_ref.startswith("binding://fixture/")
    programmer_input = graph.dependencies.programmer.calls[0][0]
    assert "raw_value" not in json.dumps(programmer_input.to_dict()).casefold()
    assert "normalized_value" not in json.dumps(programmer_input.to_dict()).casefold()


def test_plan_and_retrieval_fingerprints_are_unchanged_and_runs_repeat_exactly():
    current = query_understanding()
    pairs = fixture_pairs(("2015",), ("12.5",))
    graph, deps = make_graph(current, pairs)

    first = run(graph, current)
    plan_fingerprint = first.state.plan_fingerprint
    policy_fingerprint = first.state.retrieval_policy_fingerprint
    request = deps.retrieval.calls[0]
    second = run(graph, current)

    assert first.state.plan_fingerprint == plan_fingerprint
    assert first.state.retrieval_policy_fingerprint == policy_fingerprint
    assert request.plan_fingerprint == plan_fingerprint
    assert request.retrieval_policy_fingerprint == policy_fingerprint
    assert first.state.to_dict() == second.state.to_dict()
    assert first.verification_report.to_dict() == second.verification_report.to_dict()
