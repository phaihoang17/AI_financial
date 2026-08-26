import json
from dataclasses import FrozenInstanceError, fields

import pytest

from src.evidence.m5_schemas import BindingMap
from src.programmer.generator import generate_program
from src.orchestration.schemas import (
    M9Phase,
    M9_STATE_SCHEMA_VERSION,
    M9_STATE_SCHEMA_VERSION_V1,
)
from src.orchestration.state import AttemptEntryStage, AttemptRecord, M9State
from src.sandbox.schemas import (
    EXECUTION_RESULT_SCHEMA_VERSION,
    ExecutionFailure,
    ExecutionFailureStage,
    ExecutionResult,
)
from src.understanding.schemas import SchemaValidationError
from src.verification.schemas import RetryState, VerificationReport
from tests.programmer.helpers import lookup_programmer_input
from tests.orchestration.helpers import active_state, terminal_abstain_response


def test_m9_state_serialization_round_trip_is_exact():
    state = active_state()

    payload = state.to_dict()
    restored = M9State.from_dict(json.loads(json.dumps(payload)))

    assert restored.to_dict() == payload
    assert payload["schema_version"] == M9_STATE_SCHEMA_VERSION
    assert restored.attempts[-1].attempt_index == restored.retry_state.retries_used


def test_v1_terminal_checkpoint_migrates_empty_attribution_identifiers():
    active = active_state()
    payload = active.terminate(terminal_abstain_response(active)).to_dict()
    payload["schema_version"] = M9_STATE_SCHEMA_VERSION_V1
    for attribution in (
        payload["failure_attribution"],
        payload["final_response"]["failure_attribution"],
    ):
        attribution.pop("requirement_ids")
        attribution.pop("evidence_ids")
        attribution.pop("program_ids")

    restored = M9State.from_dict(payload)

    assert restored.schema_version == M9_STATE_SCHEMA_VERSION
    assert restored.failure_attribution.requirement_ids == ()
    assert restored.failure_attribution.evidence_ids == ()
    assert restored.failure_attribution.program_ids == ()


def test_previous_attempts_are_frozen_and_nested_mutation_is_rejected():
    state = active_state()
    with pytest.raises(FrozenInstanceError):
        state.attempts[0].binding_ref = "changed"

    state.attempts[0].retrieval_query.periods.append("2016")
    next_state = RetryState(
        retries_used=1,
        max_retries=1,
        current_model_tier=state.retry_state.current_model_tier,
        strong_escalated=False,
        terminal=False,
    )
    next_attempt = AttemptRecord(
        attempt_index=1,
        entry_stage=AttemptEntryStage.RETRIEVAL,
        model_tier=next_state.current_model_tier,
    )
    with pytest.raises(SchemaValidationError, match="mutated"):
        state.append_attempt(next_attempt, next_retry_state=next_state)


def test_plan_fingerprint_detects_mutation_of_canonical_supervisor_plan():
    state = active_state()
    state.supervisor_result.plan.periods.append("2016")

    with pytest.raises(SchemaValidationError, match="Plan was mutated"):
        state.to_dict()


def test_embedded_programmer_plan_cannot_become_a_second_source_of_truth():
    state = active_state()
    payload = state.to_dict()
    programmer_input = lookup_programmer_input()
    programmer_payload = programmer_input.to_dict()
    programmer_payload["plan"]["max_retries"] = 0
    payload["attempts"][0]["programmer_input"] = programmer_payload

    with pytest.raises(
        SchemaValidationError,
        match="ProgrammerInput Plan must equal SupervisorResult.plan",
    ):
        M9State.from_dict(payload)


def test_retry_attempt_index_must_equal_retries_used():
    state = active_state()
    payload = state.to_dict()
    payload["attempts"][0]["attempt_index"] = 1

    with pytest.raises(SchemaValidationError, match="contiguous"):
        M9State.from_dict(payload)


def test_terminal_state_rejects_all_later_transitions():
    active = active_state()
    terminal = active.terminate(terminal_abstain_response(active))
    assert terminal.phase is M9Phase.TERMINAL

    with pytest.raises(SchemaValidationError, match="terminal state"):
        terminal.terminate(terminal_abstain_response(active))
    with pytest.raises(SchemaValidationError, match="terminal state"):
        terminal.append_attempt(
            AttemptRecord(
                attempt_index=1,
                entry_stage=AttemptEntryStage.SANDBOX,
                model_tier=terminal.retry_state.current_model_tier,
            ),
            next_retry_state=terminal.retry_state,
        )


def test_checkpoint_contains_only_opaque_binding_reference_without_values():
    state = active_state(binding_ref="binding://process-local/opaque-xyz")
    payload = state.to_dict()
    encoded = json.dumps(payload, sort_keys=True)

    assert payload["attempts"][0]["binding_ref"] == (
        "binding://process-local/opaque-xyz"
    )
    assert "binding_map" not in encoded.casefold()
    assert "bindings" not in encoded.casefold()
    assert "numeric binding" not in encoded.casefold()
    assert "BindingMap" not in {item.name for item in fields(AttemptRecord)}
    assert "binding_map" not in {item.name for item in fields(M9State)}
    assert BindingMap not in {item.type for item in fields(AttemptRecord)}


def test_lost_process_local_handle_can_resume_before_downstream_artifacts():
    state = active_state(binding_ref=None)
    restored = M9State.from_dict(state.to_dict())

    assert restored.attempts[-1].binding_ref is None
    assert restored.attempts[-1].programmer_result is None
    assert restored.attempts[-1].execution_result is None


def test_failed_execution_cannot_be_reclassified_as_verification():
    programmer_input = lookup_programmer_input()
    programmer_result = generate_program(programmer_input)
    failed = ExecutionResult(
        schema_version=EXECUTION_RESULT_SCHEMA_VERSION,
        program_id=programmer_result.program.program_id,
        success=False,
        output=None,
        failure=ExecutionFailure(
            stage=ExecutionFailureStage.ARITHMETIC,
            code="DIVISION_BY_ZERO",
            message="division by zero",
        ),
        execution_ms=1,
    )

    with pytest.raises(
        SchemaValidationError, match="requires successful execution"
    ):
        AttemptRecord(
            attempt_index=0,
            entry_stage=AttemptEntryStage.SANDBOX,
            model_tier=programmer_input.plan.model_tier,
            binding_ref="binding://process-local/failed",
            programmer_input=programmer_input,
            programmer_result=programmer_result,
            execution_result=failed,
            verification_report=VerificationReport.create([]),
        )
