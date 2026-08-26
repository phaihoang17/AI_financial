from __future__ import annotations

from dataclasses import replace

from src.evaluation.e2e import evaluate_e2e_case
from src.evaluation.e2e_fixtures import (
    _graph_factory,
    e2e_fixture_cases,
    fixture_pairs,
    fixture_understanding,
)
from src.evaluation.harness import EvaluationFailureStage
from src.orchestration.graph import OrchestrationGraph
from src.programmer.schemas import ProgrammerResult, ProgrammerStatus
from src.retrieval.schemas import RetrievalContractError
from src.sandbox.schemas import ExecutionFailureStage
from src.sandbox.security import SecurityFailureCode
from src.understanding.planning_gate import PlanningGate
from src.understanding.schemas import StatementScopeUnderstanding


def _cases_by_id():
    return {item.case_id: item for item in e2e_fixture_cases()}


def _run_with_dependency(case_id, dependency_name, dependency):
    understanding = fixture_understanding(case_id)
    factory = _graph_factory(
        understanding,
        PlanningGate(True, [], []),
        [fixture_pairs(("2015",), ("12.5",))],
        ("success",),
    )
    base = factory()
    dependencies = replace(base.dependencies, **{dependency_name: dependency})
    return OrchestrationGraph(dependencies).run(
        request_id=f"attribution-{case_id}",
        raw_question=understanding.raw_question,
    ).state


class _FailedRetrieval:
    def retrieve(self, request):
        raise RetrievalContractError("FIXTURE_RETRIEVAL_FAILED", "fixture")


class _RejectedProgrammer:
    def generate(self, programmer_input, model_tier):
        return ProgrammerResult(
            ProgrammerStatus.REJECTED,
            None,
            "FIXTURE_PROGRAMMER_REJECTED",
            "fixture rejection",
        )


class _FailedNLU:
    def understand(self, raw_question):
        raise ValueError("fixture NLU failure")


def test_repaired_failures_are_not_terminally_attributed():
    for case_id in (
        "retrieval-retry",
        "programmer-retry",
        "cheap-strong",
        "sandbox-retry",
    ):
        result = evaluate_e2e_case(_cases_by_id()[case_id])
        assert result.passed
        assert result.terminal_failure is None


def test_nlu_and_supervisor_terminal_stages_remain_typed():
    nlu_state = _run_with_dependency("nlu-failure", "nlu", _FailedNLU())
    understanding = replace(
        fixture_understanding("supervisor-failure"),
        statement_scope=StatementScopeUnderstanding(None, False, 0.0),
    )
    supervisor_graph = _graph_factory(
        understanding,
        PlanningGate(True, [], []),
        [fixture_pairs(("2015",), ("12.5",))],
        ("success",),
    )()
    supervisor_state = supervisor_graph.run(
        request_id="attribution-supervisor-failure",
        raw_question=understanding.raw_question,
    ).state

    assert nlu_state.failure_attribution.stage is EvaluationFailureStage.NLU
    assert nlu_state.failure_attribution.code == "NLU_STAGE_FAILED"
    assert supervisor_state.failure_attribution.stage is EvaluationFailureStage.SUPERVISOR
    assert supervisor_state.failure_attribution.code == "MISSING_STATEMENT_SCOPE"


def test_earliest_unrecovered_failure_after_retrieval_repair_is_terminal():
    understanding = fixture_understanding("repaired-then-security")
    good = fixture_pairs(("2015",), ("12.5",))
    bad = tuple(
        (replace(item, report_year=2014), replace(location, report_year=2014))
        for item, location in good
    )
    graph = _graph_factory(
        understanding,
        PlanningGate(True, [], []),
        [bad, good],
        (
            "success",
            (
                ExecutionFailureStage.SECURITY,
                SecurityFailureCode.NETWORK_ACCESS_DENIED.value,
            ),
        ),
    )()

    state = graph.run(
        request_id="attribution-repaired-then-security",
        raw_question=understanding.raw_question,
    ).state

    assert state.attempts[0].retry_directive is not None
    assert state.failure_attribution.stage is EvaluationFailureStage.SANDBOX
    assert state.failure_attribution.code == SecurityFailureCode.NETWORK_ACCESS_DENIED.value
    assert state.failure_attribution.attempt_index == 1


def test_retrieval_failure_preserves_native_attribution():
    state = _run_with_dependency("retrieval-failure", "retrieval", _FailedRetrieval())

    assert state.failure_attribution.stage is EvaluationFailureStage.RETRIEVAL
    assert state.failure_attribution.code == "FIXTURE_RETRIEVAL_FAILED"
    assert state.failure_attribution.attempt_index == 0


def test_evidence_failure_preserves_requirement_and_evidence_ids():
    understanding = fixture_understanding("evidence-failure")
    graph = _graph_factory(
        understanding,
        PlanningGate(True, [], []),
        [fixture_pairs(("2015",), ("12.5",), table_class=False)],
        ("success",),
    )()

    state = graph.run(
        request_id="attribution-evidence-failure",
        raw_question=understanding.raw_question,
    ).state

    attribution = state.failure_attribution
    assert attribution.stage is EvaluationFailureStage.EVIDENCE
    assert attribution.code == "TABLE_CLASS_UNAVAILABLE"
    assert attribution.requirement_ids
    assert attribution.evidence_ids
    assert attribution.program_ids == ()


def test_programmer_failure_preserves_grounded_ids():
    state = _run_with_dependency(
        "programmer-failure", "programmer", _RejectedProgrammer()
    )

    attribution = state.failure_attribution
    assert attribution.stage is EvaluationFailureStage.PROGRAMMER
    assert attribution.code == "FIXTURE_PROGRAMMER_REJECTED"
    assert attribution.requirement_ids
    assert attribution.evidence_ids
    assert attribution.program_ids == ()


def test_sandbox_verification_and_exhaustion_attribution():
    security = evaluate_e2e_case(_cases_by_id()["security-abstain"])
    verification = evaluate_e2e_case(_cases_by_id()["verification-abstain"])
    exhaustion = evaluate_e2e_case(_cases_by_id()["retry-exhausted"])

    assert security.terminal_failure.stage is EvaluationFailureStage.SANDBOX
    assert security.terminal_failure.program_ids
    assert verification.terminal_failure.stage is EvaluationFailureStage.VERIFICATION
    assert verification.terminal_failure.code == "OUTPUT_SCALE_UNIT_MISMATCH"
    assert exhaustion.terminal_failure.stage is EvaluationFailureStage.VERIFICATION
    assert exhaustion.terminal_failure.code == "OUTPUT_KIND_MISMATCH"
    assert exhaustion.terminal_failure.attempt_index == 1


def test_pass_has_no_failure_attribution():
    result = evaluate_e2e_case(_cases_by_id()["lookup"])

    assert result.final_status.value == "PASS"
    assert result.terminal_failure is None
