"""TASK-112 coordination-failure logging.

A pure, read-only observer over a completed or in-progress :class:`M9State`. It
derives typed coordination records — attempt progression, model-tier
escalation, retry-budget consumption/exhaustion, terminal stage failures, and
the three M10 coordination-failure categories (conflicting worker state, stale
worker/state observation, message/schema corruption).

Guarantees:

- It never mutates state and never raises on a validated state, so it cannot
  change orchestration control flow. It performs no routing, retry, repair, or
  budget/answer change.
- It reads only already-public coordination fields (phase, outcome, retry
  counters, tiers, attempt entry stages, terminal stage/code, the M8 retry
  action, and the failure-attribution attempt pointer). It never reads or emits
  ``binding_ref``, masked/bound evidence, execution outputs, prompts, secrets,
  or any numeric value, so no ``BindingMap`` or numeric secret can leak into a
  log. Every event ``detail`` is a small map of stable diagnostic scalars.

Detection rules only use invariants M9's own contracts do **not** already
enforce, so a genuinely valid state emits none of the coordination-failure
events. If a category cannot be deterministically proven from the available
state it is not emitted (fail closed).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Mapping, Optional

from src.orchestration.schemas import FinalResponseStatus
from src.orchestration.state import M9State
from src.supervisor.schemas import ModelTier
from src.understanding.schemas import SchemaValidationError
from src.verification.schemas import RetryAction


COORDINATION_LOG_SCHEMA_VERSION = "m10-coordination-log-v1"


class CoordinationEventType(str, Enum):
    ATTEMPT_RECORDED = "ATTEMPT_RECORDED"
    MODEL_TIER_ESCALATION = "MODEL_TIER_ESCALATION"
    RETRY_BUDGET_CONSUMED = "RETRY_BUDGET_CONSUMED"
    RETRY_BUDGET_EXHAUSTED = "RETRY_BUDGET_EXHAUSTED"
    TERMINAL_FAILURE = "TERMINAL_FAILURE"
    CONFLICTING_WORKER_STATE = "CONFLICTING_WORKER_STATE"
    STALE_WORKER_STATE = "STALE_WORKER_STATE"
    MESSAGE_SCHEMA_CORRUPTION = "MESSAGE_SCHEMA_CORRUPTION"


# Only redaction-safe scalar values are ever placed in an event detail.
_SafeDetailValue = Optional[object]


@dataclass(frozen=True)
class CoordinationEvent:
    event_type: CoordinationEventType
    attempt_index: Optional[int]
    detail: Dict[str, _SafeDetailValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.event_type, CoordinationEventType):
            raise SchemaValidationError("event_type must be a CoordinationEventType")
        if self.attempt_index is not None and (
            not isinstance(self.attempt_index, int)
            or isinstance(self.attempt_index, bool)
            or self.attempt_index < 0
        ):
            raise SchemaValidationError("attempt_index must be a non-negative int")
        if not isinstance(self.detail, dict) or not all(
            isinstance(key, str)
            and isinstance(value, (str, int, bool, type(None)))
            for key, value in self.detail.items()
        ):
            raise SchemaValidationError(
                "detail must map strings to redaction-safe scalars"
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type.value,
            "attempt_index": self.attempt_index,
            "detail": dict(self.detail),
        }


def _conflicting_worker_state_event(
    state: M9State,
) -> Optional[CoordinationEvent]:
    """Detect a terminal outcome that contradicts its own worker output.

    Two proven contradictions, neither rejected by ``M9State`` validation:

    - a non-``PASS`` terminal outcome whose final :class:`AttemptRecord` carries
      an M8 ``RetryDirective`` with ``action == PASS`` (``_validate_pass`` only
      constrains the ``PASS`` direction, so a passed verification worker paired
      with a non-``PASS`` terminal outcome is representable);
    - a ``CLARIFICATION`` terminal outcome with a non-empty attempt history
      (clarification responses are only built before any executable attempt
      exists).
    """
    if not state.terminal or state.outcome is None:
        return None
    attempt_count = len(state.attempts)
    last_index = state.attempts[-1].attempt_index if state.attempts else None

    if state.outcome is not FinalResponseStatus.PASS and state.attempts:
        directive = state.attempts[-1].retry_directive
        if directive is not None and directive.action is RetryAction.PASS:
            return CoordinationEvent(
                CoordinationEventType.CONFLICTING_WORKER_STATE,
                last_index,
                {
                    "signal": "M8_PASS_DIRECTIVE_WITH_NON_PASS_TERMINAL",
                    "outcome": state.outcome.value,
                    "attempt_count": attempt_count,
                },
            )

    if state.outcome is FinalResponseStatus.CLARIFICATION and state.attempts:
        return CoordinationEvent(
            CoordinationEventType.CONFLICTING_WORKER_STATE,
            last_index,
            {
                "signal": "CLARIFICATION_TERMINAL_WITH_ATTEMPTS",
                "outcome": state.outcome.value,
                "attempt_count": attempt_count,
            },
        )
    return None


def _stale_worker_state_event(state: M9State) -> Optional[CoordinationEvent]:
    """Detect a failure attribution that does not track the current attempt.

    ``build_failure_response`` always stamps
    ``FailureAttribution.attempt_index`` with ``attempts[-1].attempt_index`` (or
    ``None`` when there is no attempt). ``M9State`` never re-checks that field
    against the attempt tuple, so an attribution pointing at a superseded,
    out-of-range, or absent attempt is a stale observation that never advanced
    with the run.
    """
    if not state.terminal or state.failure_attribution is None:
        return None
    expected = state.attempts[-1].attempt_index if state.attempts else None
    attributed = state.failure_attribution.attempt_index
    if attributed == expected:
        return None
    return CoordinationEvent(
        CoordinationEventType.STALE_WORKER_STATE,
        expected,
        {
            "signal": "FAILURE_ATTRIBUTION_ATTEMPT_STALE",
            "attributed_attempt_index": attributed,
            "current_attempt_index": expected,
            "attempt_count": len(state.attempts),
        },
    )


def build_coordination_log(state: M9State) -> tuple[CoordinationEvent, ...]:
    """Derive the ordered coordination log for one orchestration state."""
    if not isinstance(state, M9State):
        raise TypeError("state must be an M9State")

    events: list[CoordinationEvent] = []
    previous_tier: Optional[ModelTier] = None
    for attempt in state.attempts:
        events.append(
            CoordinationEvent(
                CoordinationEventType.ATTEMPT_RECORDED,
                attempt.attempt_index,
                {
                    "entry_stage": attempt.entry_stage.value,
                    "model_tier": attempt.model_tier.value,
                },
            )
        )
        if (
            previous_tier is ModelTier.CHEAP
            and attempt.model_tier is ModelTier.STRONG
        ):
            events.append(
                CoordinationEvent(
                    CoordinationEventType.MODEL_TIER_ESCALATION,
                    attempt.attempt_index,
                    {
                        "from_tier": ModelTier.CHEAP.value,
                        "to_tier": ModelTier.STRONG.value,
                    },
                )
            )
        previous_tier = attempt.model_tier

    retry_state = state.retry_state
    if retry_state is not None and retry_state.retries_used > 0:
        events.append(
            CoordinationEvent(
                CoordinationEventType.RETRY_BUDGET_CONSUMED,
                None,
                {
                    "retries_used": retry_state.retries_used,
                    "max_retries": retry_state.max_retries,
                    "strong_escalated": retry_state.strong_escalated,
                },
            )
        )

    failure = state.failure_attribution
    if failure is not None:
        events.append(
            CoordinationEvent(
                CoordinationEventType.TERMINAL_FAILURE,
                failure.attempt_index,
                {"stage": failure.stage.value, "code": failure.code},
            )
        )
        if (
            retry_state is not None
            and retry_state.retries_used == retry_state.max_retries
            and retry_state.max_retries > 0
        ):
            events.append(
                CoordinationEvent(
                    CoordinationEventType.RETRY_BUDGET_EXHAUSTED,
                    None,
                    {
                        "retries_used": retry_state.retries_used,
                        "max_retries": retry_state.max_retries,
                    },
                )
            )

    conflicting = _conflicting_worker_state_event(state)
    if conflicting is not None:
        events.append(conflicting)
    stale = _stale_worker_state_event(state)
    if stale is not None:
        events.append(stale)

    return tuple(events)


def scan_coordination_payload(
    payload: Mapping[str, Any],
) -> tuple[CoordinationEvent, ...]:
    """Observe one raw orchestration checkpoint payload at its fail-closed edge.

    The dict handed between orchestration nodes (``_OrchestrationGraphState``'s
    ``checkpoint``) is the coordination message. ``M9State.from_dict`` is the
    existing boundary every node already crosses; if it rejects the payload as
    contract-invalid the run cannot proceed, and this records a single
    :attr:`CoordinationEventType.MESSAGE_SCHEMA_CORRUPTION` event carrying only
    the boundary name and the exception class name — never the payload body or
    the exception message. A payload that parses is delegated to
    :func:`build_coordination_log` so callers get the full log for valid input.
    """
    try:
        state = M9State.from_dict(payload)
    except SchemaValidationError as error:
        return (
            CoordinationEvent(
                CoordinationEventType.MESSAGE_SCHEMA_CORRUPTION,
                None,
                {
                    "signal": "CHECKPOINT_CONTRACT_INVALID",
                    "boundary": "M9State.from_dict",
                    "error_type": type(error).__name__,
                },
            ),
        )
    return build_coordination_log(state)


def coordination_log_to_dict(state: M9State) -> Dict[str, Any]:
    """Serialize the coordination log with its schema version."""
    return {
        "schema_version": COORDINATION_LOG_SCHEMA_VERSION,
        "events": [event.to_dict() for event in build_coordination_log(state)],
    }


def coordination_payload_scan_to_dict(
    payload: Mapping[str, Any],
) -> Dict[str, Any]:
    """Serialize a raw-payload scan with its schema version."""
    return {
        "schema_version": COORDINATION_LOG_SCHEMA_VERSION,
        "events": [event.to_dict() for event in scan_coordination_payload(payload)],
    }
