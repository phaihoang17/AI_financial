from src.evaluation.harness import EvaluationFailureStage
from src.orchestration.schemas import (
    FailureAttribution,
    FinalResponse,
    FinalResponseStatus,
    M9Phase,
    M9_STATE_SCHEMA_VERSION,
    make_plan_fingerprint,
    make_retrieval_policy_fingerprint,
    terminal_retry_state,
)
from src.orchestration.state import AttemptEntryStage, AttemptRecord, M9State
from src.retrieval.query_builder import build_retrieval_query
from src.supervisor.planner import supervise_batch1
from src.understanding.planning_gate import PlanningGate
from src.verification.schemas import RetryState
from tests.evidence.m5_helpers import understanding


ARTIFACT_VERSIONS = {
    "bm25": "fixture-bm25-v1",
    "embedding": "fixture-bge-m3-v1",
    "reranker": "fixture-bge-reranker-v1",
}


def approved_contracts():
    query_understanding = understanding()
    gate = PlanningGate(True, [], [])
    supervisor_result = supervise_batch1(query_understanding, gate)
    assert supervisor_result.plan is not None
    plan = supervisor_result.plan
    query = build_retrieval_query(plan, query_understanding)
    plan_fingerprint = make_plan_fingerprint(plan)
    policy_fingerprint = make_retrieval_policy_fingerprint(
        plan,
        query,
        top_k=10,
        artifact_versions=ARTIFACT_VERSIONS,
    )
    return (
        query_understanding,
        gate,
        supervisor_result,
        plan,
        query,
        plan_fingerprint,
        policy_fingerprint,
    )


def active_state(*, binding_ref="binding://process-local/opaque-1") -> M9State:
    (
        query_understanding,
        gate,
        supervisor_result,
        plan,
        query,
        plan_fingerprint,
        policy_fingerprint,
    ) = approved_contracts()
    retry_state = RetryState.initial(plan)
    attempt = AttemptRecord(
        attempt_index=0,
        entry_stage=AttemptEntryStage.RETRIEVAL,
        model_tier=plan.model_tier,
        retrieval_query=query,
        binding_ref=binding_ref,
    )
    return M9State(
        schema_version=M9_STATE_SCHEMA_VERSION,
        request_id="request-001",
        raw_question=query_understanding.raw_question,
        phase=M9Phase.RETRIEVAL,
        outcome=None,
        query_understanding=query_understanding,
        planning_gate=gate,
        supervisor_result=supervisor_result,
        plan_fingerprint=plan_fingerprint,
        retrieval_policy_fingerprint=policy_fingerprint,
        retry_state=retry_state,
        attempts=(attempt,),
        final_response=None,
        failure_attribution=None,
    )


def terminal_abstain_response(state: M9State) -> FinalResponse:
    assert state.retry_state is not None
    attribution = FailureAttribution(
        stage=EvaluationFailureStage.EVIDENCE,
        code="RETRY_BUDGET_EXHAUSTED",
        reason="No complete evidence remained within the shared retry budget.",
        attempt_index=state.attempts[-1].attempt_index,
    )
    return FinalResponse(
        status=FinalResponseStatus.ABSTAIN,
        answer=None,
        reason_code="RETRY_BUDGET_EXHAUSTED",
        message="Unable to produce a verified answer.",
        retry_state=terminal_retry_state(state.retry_state),
        failure_attribution=attribution,
    )
