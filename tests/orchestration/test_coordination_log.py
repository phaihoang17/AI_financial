from __future__ import annotations

import json

from src.evaluation.e2e_fixtures import e2e_fixture_cases
from src.orchestration.coordination_log import (
    COORDINATION_LOG_SCHEMA_VERSION,
    CoordinationEventType,
    build_coordination_log,
    coordination_log_to_dict,
)


def _run(case_id: str):
    case = {item.case_id: item for item in e2e_fixture_cases()}[case_id]
    return case.graph_factory().run(
        request_id=f"coord-{case_id}", raw_question=case.raw_question
    ).state


def _types(state):
    return [event.event_type for event in build_coordination_log(state)]


def test_single_attempt_pass_logs_one_attempt_only():
    assert _types(_run("growth-rate")) == [CoordinationEventType.ATTEMPT_RECORDED]


def test_retry_logs_budget_consumption_without_exhaustion():
    events = build_coordination_log(_run("programmer-retry"))
    assert [e.event_type for e in events] == [
        CoordinationEventType.ATTEMPT_RECORDED,
        CoordinationEventType.ATTEMPT_RECORDED,
        CoordinationEventType.RETRY_BUDGET_CONSUMED,
    ]
    consumed = events[-1]
    assert consumed.detail["retries_used"] == 1
    assert consumed.detail["max_retries"] == 2


def test_escalation_is_logged_on_cheap_to_strong():
    events = build_coordination_log(_run("cheap-strong"))
    escalation = [
        e for e in events if e.event_type is CoordinationEventType.MODEL_TIER_ESCALATION
    ]
    assert len(escalation) == 1
    assert escalation[0].detail == {"from_tier": "CHEAP", "to_tier": "STRONG"}


def test_exhausted_abstain_logs_terminal_failure_and_exhaustion():
    events = build_coordination_log(_run("retry-exhausted"))
    types = [e.event_type for e in events]
    assert CoordinationEventType.TERMINAL_FAILURE in types
    assert CoordinationEventType.RETRY_BUDGET_EXHAUSTED in types
    terminal = next(
        e for e in events if e.event_type is CoordinationEventType.TERMINAL_FAILURE
    )
    # Only stage/code are recorded — no free-form numeric payload.
    assert set(terminal.detail) == {"stage", "code"}


def test_clarification_logs_terminal_failure_without_attempts():
    assert _types(_run("clarification")) == [CoordinationEventType.TERMINAL_FAILURE]


def test_log_is_deterministic_and_pure():
    state = _run("retry-exhausted")
    before = state.to_dict()
    first = coordination_log_to_dict(state)
    second = coordination_log_to_dict(state)

    assert first == second
    assert first["schema_version"] == COORDINATION_LOG_SCHEMA_VERSION
    # The observer must not mutate the state it reads.
    assert state.to_dict() == before


def test_log_never_exposes_binding_refs_or_numeric_values():
    state = _run("growth-rate")
    serialized = json.dumps(coordination_log_to_dict(state), sort_keys=True)

    secrets: list[str] = []
    for attempt in state.attempts:
        if attempt.binding_ref is not None:
            secrets.append(attempt.binding_ref)
        if attempt.execution_result is not None and attempt.execution_result.output:
            for datum in attempt.execution_result.output.values:
                secrets.append(str(datum.value))
    assert secrets, "fixture must carry a binding ref and numeric output to redact"
    for secret in secrets:
        assert secret not in serialized


def test_every_fixture_state_produces_a_valid_serializable_log():
    for case in e2e_fixture_cases():
        state = case.graph_factory().run(
            request_id=f"coord-all-{case.case_id}", raw_question=case.raw_question
        ).state
        payload = coordination_log_to_dict(state)
        assert json.loads(json.dumps(payload, sort_keys=True)) == payload
