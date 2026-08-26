"""Canonical LangGraph orchestration control flow.

Financial and retrieval policy stays in deterministic nodes. This module owns
only sequencing, routing, checkpoint projection, and compatibility wrappers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from src.orchestration.nodes import (
    OrchestrationDependencies,
    OrchestrationFailure,
    StageTransition,
    initial_state,
    run_evidence,
    run_nlu,
    run_programmer,
    run_retrieval,
    run_sandbox,
    run_supervisor,
    run_verification,
)
from src.orchestration.routing import (
    RoutingTransition,
    route_answer,
    route_evidence,
    route_nlu,
    route_programmer,
    route_retrieval,
    route_sandbox,
    route_supervisor,
    route_verification,
)
from src.orchestration.schemas import FinalResponseStatus
from src.orchestration.state import M9State
from src.programmer.schemas import ProgrammerResult
from src.retrieval.evidence import EvidenceCompletenessResult
from src.retrieval.schemas import RetrievalContractError
from src.sandbox.schemas import ExecutionResult
from src.supervisor.schemas import SupervisorResult
from src.understanding.planning_gate import PlanningGate
from src.verification.schemas import VerificationReport


ORCHESTRATION_NODE_SEQUENCE = (
    "nlu",
    "supervisor",
    "retrieval",
    "evidence",
    "programmer",
    "sandbox",
    "verification",
    "answer",
)

# Retained only for the deprecated straight-through graph compatibility API.
STRAIGHT_THROUGH_NODE_SEQUENCE = ORCHESTRATION_NODE_SEQUENCE[:-1]


class _OrchestrationGraphState(TypedDict):
    checkpoint: dict[str, Any]
    next_node: str


@dataclass(frozen=True)
class OrchestrationResult:
    state: M9State

    @property
    def successful(self) -> bool:
        return self.state.outcome is FinalResponseStatus.PASS


def _project_route(transition: RoutingTransition) -> _OrchestrationGraphState:
    return {
        "checkpoint": transition.state.to_dict(),
        "next_node": transition.next_node,
    }


def _route_next(value: _OrchestrationGraphState) -> str:
    return value["next_node"]


class OrchestrationGraph:
    """Run bounded directive-driven attempts through one terminal response."""

    def __init__(
        self,
        dependencies: OrchestrationDependencies,
        *,
        checkpointer: Any = None,
    ) -> None:
        self.dependencies = dependencies
        builder = StateGraph(_OrchestrationGraphState)
        stage_functions = {
            "nlu": route_nlu,
            "supervisor": route_supervisor,
            "retrieval": route_retrieval,
            "evidence": route_evidence,
            "programmer": route_programmer,
            "sandbox": route_sandbox,
            "verification": route_verification,
            "answer": route_answer,
        }
        for name, function in stage_functions.items():
            builder.add_node(name, self._node(function))
        builder.add_edge(START, "nlu")
        destinations = {name: name for name in ORCHESTRATION_NODE_SEQUENCE}
        destinations["end"] = END
        for name in ORCHESTRATION_NODE_SEQUENCE:
            builder.add_conditional_edges(name, _route_next, destinations)
        self.graph = builder.compile(checkpointer=checkpointer)

    def _node(self, function):
        def node(value: _OrchestrationGraphState) -> _OrchestrationGraphState:
            state = M9State.from_dict(value["checkpoint"])
            return _project_route(function(state, self.dependencies))

        return node

    def run(
        self,
        *,
        request_id: str,
        raw_question: str,
        config: Optional[Mapping[str, Any]] = None,
    ) -> OrchestrationResult:
        state = initial_state(request_id=request_id, raw_question=raw_question)
        output = self.graph.invoke(
            {"checkpoint": state.to_dict(), "next_node": "nlu"},
            config=None if config is None else dict(config),
        )
        restored = M9State.from_dict(output["checkpoint"])
        if not restored.terminal:
            raise RuntimeError("orchestration graph stopped without a terminal state")
        return OrchestrationResult(restored)


class _StraightThroughGraphState(TypedDict):
    checkpoint: dict[str, Any]
    stop: bool
    failure: Optional[dict[str, Any]]


@dataclass(frozen=True)
class StraightThroughOrchestrationResult:
    """Result retained for the deprecated no-retry compatibility graph."""

    state: M9State
    failure_stage: Optional[str]
    failure: Optional[object]

    @property
    def verification_report(self) -> Optional[VerificationReport]:
        if not self.state.attempts:
            return None
        return self.state.attempts[-1].verification_report

    @property
    def successful(self) -> bool:
        report = self.verification_report
        return self.failure is None and report is not None and report.passed


def _failure_payload(transition: StageTransition) -> Optional[dict[str, Any]]:
    if transition.failure_kind is None:
        return None
    return {
        "kind": transition.failure_kind,
        "code": transition.failure_code,
        "message": transition.failure_message,
        "payload": transition.failure_payload,
        "stage": transition.state.phase.value,
    }


def _project_straight_through(
    transition: StageTransition,
) -> _StraightThroughGraphState:
    return {
        "checkpoint": transition.state.to_dict(),
        "stop": transition.stop,
        "failure": _failure_payload(transition),
    }


def _route_straight_through(value: _StraightThroughGraphState) -> str:
    return "stop" if value["stop"] else "continue"


def _decode_failure(
    value: Optional[Mapping[str, Any]],
) -> tuple[Optional[str], Optional[object]]:
    if value is None:
        return None, None
    kind = value["kind"]
    payload = value.get("payload")
    stage = value["stage"]
    if kind == "PLANNING_GATE":
        failure = PlanningGate.from_dict(payload)
    elif kind == "SUPERVISOR_RESULT":
        failure = SupervisorResult.from_dict(payload)
    elif kind == "EVIDENCE_COMPLETENESS":
        failure = EvidenceCompletenessResult.from_dict(payload)
    elif kind == "PROGRAMMER_RESULT":
        failure = ProgrammerResult.from_dict(payload)
    elif kind == "EXECUTION_RESULT":
        failure = ExecutionResult.from_dict(payload)
    elif kind == "VERIFICATION_REPORT":
        failure = VerificationReport.from_dict(payload)
    elif kind == "RETRIEVAL_ERROR":
        failure = RetrievalContractError(value["code"], value["message"])
    else:
        from src.orchestration.schemas import M9Phase

        failure = OrchestrationFailure(
            M9Phase(stage), value["code"], value["message"]
        )
    return stage, failure


class StraightThroughOrchestrationGraph:
    """Deprecated no-retry graph retained for import/behavior compatibility."""

    def __init__(
        self,
        dependencies: OrchestrationDependencies,
        *,
        checkpointer: Any = None,
    ) -> None:
        self.dependencies = dependencies
        builder = StateGraph(_StraightThroughGraphState)
        stage_functions = {
            "nlu": run_nlu,
            "supervisor": run_supervisor,
            "retrieval": run_retrieval,
            "evidence": run_evidence,
            "programmer": run_programmer,
            "sandbox": run_sandbox,
            "verification": run_verification,
        }
        for name, function in stage_functions.items():
            builder.add_node(name, self._node(function))
        builder.add_edge(START, "nlu")
        for current, following in zip(
            STRAIGHT_THROUGH_NODE_SEQUENCE,
            STRAIGHT_THROUGH_NODE_SEQUENCE[1:],
        ):
            builder.add_conditional_edges(
                current,
                _route_straight_through,
                {"continue": following, "stop": END},
            )
        builder.add_edge("verification", END)
        self.graph = builder.compile(checkpointer=checkpointer)

    def _node(self, function):
        def node(value: _StraightThroughGraphState) -> _StraightThroughGraphState:
            state = M9State.from_dict(value["checkpoint"])
            return _project_straight_through(function(state, self.dependencies))

        return node

    def run(
        self,
        *,
        request_id: str,
        raw_question: str,
        config: Optional[Mapping[str, Any]] = None,
    ) -> StraightThroughOrchestrationResult:
        state = initial_state(request_id=request_id, raw_question=raw_question)
        output = self.graph.invoke(
            {"checkpoint": state.to_dict(), "stop": False, "failure": None},
            config=None if config is None else dict(config),
        )
        restored = M9State.from_dict(output["checkpoint"])
        failure_stage, failure = _decode_failure(output.get("failure"))
        return StraightThroughOrchestrationResult(restored, failure_stage, failure)


# Deprecated compatibility aliases. Keep these as identity aliases so there is
# no second final-graph implementation path.
M9_BATCH2_NODE_SEQUENCE = STRAIGHT_THROUGH_NODE_SEQUENCE
M9Batch2Graph = StraightThroughOrchestrationGraph
M9Batch2Result = StraightThroughOrchestrationResult
M9_BATCH3_NODES = ORCHESTRATION_NODE_SEQUENCE
M9Batch3Graph = OrchestrationGraph
M9Batch3Result = OrchestrationResult


__all__ = [
    "ORCHESTRATION_NODE_SEQUENCE",
    "OrchestrationGraph",
    "OrchestrationResult",
    "M9_BATCH2_NODE_SEQUENCE",
    "M9Batch2Graph",
    "M9Batch2Result",
    "M9_BATCH3_NODES",
    "M9Batch3Graph",
    "M9Batch3Result",
]
