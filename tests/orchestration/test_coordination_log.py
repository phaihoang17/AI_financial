from __future__ import annotations

import json
from dataclasses import replace

from src.evaluation.e2e_fixtures import e2e_fixture_cases
from src.evaluation.harness import EvaluationFailureStage
from src.orchestration.coordination_log import (
    COORDINATION_LOG_SCHEMA_VERSION,
    CoordinationEventType,
    build_coordination_log,
    coordination_log_to_dict,
    coordination_payload_scan_to_dict,
    scan_coordination_payload,
)
from src.orchestration.schemas import (
    FailureAttribution,
    FinalResponse,
    FinalResponseStatus,
    M9Phase,
    terminal_retry_state,
)
from src.verification.schemas import RetryAction


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


# --- TASK-112 coordination-failure categories -------------------------------

_COORDINATION_FAILURE_TYPES = frozenset(
    {
        CoordinationEventType.CONFLICTING_WORKER_STATE,
        CoordinationEventType.STALE_WORKER_STATE,
        CoordinationEventType.MESSAGE_SCHEMA_CORRUPTION,
    }
)


def _conflicting_state():
    """Terminal ABSTAIN whose final attempt still carries an M8 PASS directive."""
    passed = _run("growth-rate")
    last = passed.attempts[-1]
    assert last.retry_directive is not None
    assert last.retry_directive.action is RetryAction.PASS

    terminal_retry = terminal_retry_state(passed.retry_state)
    attribution = FailureAttribution(
        EvaluationFailureStage.VERIFICATION,
        "COORDINATION_TEST_ABSTAIN",
        "fixture-built conflicting terminal",
        last.attempt_index,
    )
    response = FinalResponse(
        status=FinalResponseStatus.ABSTAIN,
        answer=None,
        reason_code="COORDINATION_TEST_ABSTAIN",
        message="fixture-built conflicting terminal",
        retry_state=terminal_retry,
        failure_attribution=attribution,
    )
    return replace(
        passed,
        phase=M9Phase.TERMINAL,
        outcome=FinalResponseStatus.ABSTAIN,
        retry_state=terminal_retry,
        final_response=response,
        failure_attribution=attribution,
    )


def _stale_state():
    """Terminal ABSTAIN whose attribution points at a superseded attempt."""
    base = _run("retry-exhausted")
    assert len(base.attempts) == 2
    assert base.failure_attribution.attempt_index == 1

    stale_attribution = replace(base.failure_attribution, attempt_index=0)
    stale_response = replace(
        base.final_response, failure_attribution=stale_attribution
    )
    return replace(
        base,
        failure_attribution=stale_attribution,
        final_response=stale_response,
    )


def test_conflicting_worker_state_detected():
    events = build_coordination_log(_conflicting_state())
    conflicting = [
        e
        for e in events
        if e.event_type is CoordinationEventType.CONFLICTING_WORKER_STATE
    ]
    assert len(conflicting) == 1
    assert conflicting[0].detail == {
        "signal": "M8_PASS_DIRECTIVE_WITH_NON_PASS_TERMINAL",
        "outcome": "ABSTAIN",
        "attempt_count": 1,
    }
    assert conflicting[0].attempt_index == 0


def test_conflicting_state_preserves_existing_events():
    assert [e.event_type for e in build_coordination_log(_conflicting_state())] == [
        CoordinationEventType.ATTEMPT_RECORDED,
        CoordinationEventType.TERMINAL_FAILURE,
        CoordinationEventType.CONFLICTING_WORKER_STATE,
    ]


def test_stale_worker_state_detected():
    events = build_coordination_log(_stale_state())
    stale = [
        e for e in events if e.event_type is CoordinationEventType.STALE_WORKER_STATE
    ]
    assert len(stale) == 1
    assert stale[0].detail == {
        "signal": "FAILURE_ATTRIBUTION_ATTEMPT_STALE",
        "attributed_attempt_index": 0,
        "current_attempt_index": 1,
        "attempt_count": 2,
    }
    assert stale[0].attempt_index == 1


def test_stale_state_preserves_existing_events():
    assert [e.event_type for e in build_coordination_log(_stale_state())] == [
        CoordinationEventType.ATTEMPT_RECORDED,
        CoordinationEventType.ATTEMPT_RECORDED,
        CoordinationEventType.MODEL_TIER_ESCALATION,
        CoordinationEventType.RETRY_BUDGET_CONSUMED,
        CoordinationEventType.TERMINAL_FAILURE,
        CoordinationEventType.RETRY_BUDGET_EXHAUSTED,
        CoordinationEventType.STALE_WORKER_STATE,
    ]


def test_message_schema_corruption_detected_for_invalid_enum():
    payload = dict(_run("lookup").to_dict())
    payload["phase"] = "NOT_A_PHASE"
    events = scan_coordination_payload(payload)
    assert [e.event_type for e in events] == [
        CoordinationEventType.MESSAGE_SCHEMA_CORRUPTION
    ]
    assert events[0].attempt_index is None
    assert events[0].detail == {
        "signal": "CHECKPOINT_CONTRACT_INVALID",
        "boundary": "M9State.from_dict",
        "error_type": "SchemaValidationError",
    }


def test_message_schema_corruption_detected_for_missing_field_and_non_mapping():
    payload = dict(_run("lookup").to_dict())
    del payload["attempts"]
    for corrupt in (payload, [], "not-a-checkpoint", None):
        events = scan_coordination_payload(corrupt)
        assert [e.event_type for e in events] == [
            CoordinationEventType.MESSAGE_SCHEMA_CORRUPTION
        ]


def test_valid_payload_scan_delegates_to_build_log():
    state = _run("retry-exhausted")
    assert scan_coordination_payload(state.to_dict()) == build_coordination_log(state)
    assert coordination_payload_scan_to_dict(state.to_dict()) == coordination_log_to_dict(
        state
    )


def test_valid_states_emit_no_coordination_failure_events():
    for case in e2e_fixture_cases():
        state = case.graph_factory().run(
            request_id=f"coord-clean-{case.case_id}",
            raw_question=case.raw_question,
        ).state
        assert _COORDINATION_FAILURE_TYPES.isdisjoint(_types(state))
        # A round-tripped valid checkpoint is never flagged as corrupt.
        assert _COORDINATION_FAILURE_TYPES.isdisjoint(
            e.event_type for e in scan_coordination_payload(state.to_dict())
        )


def test_coordination_failure_events_are_deterministic_and_single():
    for state in (_conflicting_state(), _stale_state()):
        first = [e.to_dict() for e in build_coordination_log(state)]
        second = [e.to_dict() for e in build_coordination_log(state)]
        assert first == second
        failure_events = [
            e["event_type"]
            for e in first
            if e["event_type"]
            in {t.value for t in _COORDINATION_FAILURE_TYPES}
        ]
        assert len(failure_events) == 1


def test_coordination_failure_events_redact_state_secrets():
    passing = _run("growth-rate")
    secrets = []
    for attempt in passing.attempts:
        if attempt.binding_ref is not None:
            secrets.append(attempt.binding_ref)
        if attempt.execution_result is not None and attempt.execution_result.output:
            for datum in attempt.execution_result.output.values:
                secrets.append(str(datum.value))
    assert secrets

    for state in (_conflicting_state(), _stale_state()):
        serialized = json.dumps(coordination_log_to_dict(state), sort_keys=True)
        for secret in secrets:
            assert secret not in serialized
        for event in build_coordination_log(state):
            if event.event_type not in _COORDINATION_FAILURE_TYPES:
                continue
            for key, value in event.detail.items():
                assert isinstance(key, str)
                assert isinstance(value, (str, int, bool, type(None)))
