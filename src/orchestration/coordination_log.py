"""TASK-112 coordination-failure logging.

A pure, read-only observer over a completed or in-progress :class:`M9State`. It
derives typed coordination records — attempt progression, model-tier
escalation, retry-budget consumption/exhaustion, and terminal stage failures —
that support the M10 monitoring surface (conflicting/stale worker state, retry
loops, schema corruption).

Guarantees:

- It never mutates state and never raises on a validated state, so it cannot
  change orchestration control flow.
- It reads only already-public coordination fields (phase, outcome, retry
  counters, tiers, attempt entry stages, terminal stage/code). It never reads
  or emits ``binding_ref``, masked/bound evidence, execution outputs, or any
  numeric value, so no ``BindingMap`` or numeric secret can leak into a log.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

from src.orchestration.state import M9State
from src.supervisor.schemas import ModelTier
from src.understanding.schemas import SchemaValidationError


COORDINATION_LOG_SCHEMA_VERSION = "m10-coordination-log-v1"


class CoordinationEventType(str, Enum):
    ATTEMPT_RECORDED = "ATTEMPT_RECORDED"
    MODEL_TIER_ESCALATION = "MODEL_TIER_ESCALATION"
    RETRY_BUDGET_CONSUMED = "RETRY_BUDGET_CONSUMED"
    RETRY_BUDGET_EXHAUSTED = "RETRY_BUDGET_EXHAUSTED"
    TERMINAL_FAILURE = "TERMINAL_FAILURE"


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

    return tuple(events)


def coordination_log_to_dict(state: M9State) -> Dict[str, Any]:
    """Serialize the coordination log with its schema version."""
    return {
        "schema_version": COORDINATION_LOG_SCHEMA_VERSION,
        "events": [event.to_dict() for event in build_coordination_log(state)],
    }
