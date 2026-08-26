"""Checkpoint-safe M9 state contracts and transition guards.

This module stores only an opaque ``binding_ref``.  ``BindingMap`` is never a
field of either M9State or AttemptRecord.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from hashlib import sha256
import json
from types import MappingProxyType
from typing import Any, Dict, Mapping, Optional

from src.evidence.m5_schemas import (
    MaskedEvidenceBundle,
    ScaleUnitResolution,
    SchemaLinkResult,
)
from src.evidence.schemas import EvidenceItem
from src.orchestration.schemas import (
    ExecutionDirective,
    FailureAttribution,
    FinalResponse,
    FinalResponseStatus,
    M9Phase,
    M9_STATE_SCHEMA_VERSION,
    M9_STATE_SCHEMA_VERSION_V1,
    make_plan_fingerprint,
    validate_retrieval_query_against_plan,
)
from src.programmer.schemas import (
    ProgrammerInput,
    ProgrammerResult,
    ProgrammerStatus,
)
from src.retrieval.evidence import CellLocation, EvidenceCompletenessResult
from src.retrieval.query_builder import build_retrieval_query
from src.retrieval.schemas import (
    RetrievedScaleUnitHint,
    RetrievalCandidate,
    RetrievalQuery,
)
from src.sandbox.schemas import ExecutionResult
from src.supervisor.schemas import (
    ModelTier,
    SupervisorResult,
    make_supervisor_input_fingerprint,
)
from src.understanding.planning_gate import PlanningGate
from src.understanding.schemas import QueryUnderstanding, SchemaValidationError
from src.verification.schemas import RetryAction, RetryDirective, RetryState, VerificationReport


class AttemptEntryStage(str, Enum):
    RETRIEVAL = "RETRIEVAL"
    PROGRAMMER = "PROGRAMMER"
    SANDBOX = "SANDBOX"


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SchemaValidationError(f"{path} must be an object")
    return value


def _exact_keys(data: Mapping[str, Any], expected: set[str], path: str) -> None:
    missing = expected - set(data)
    unknown = set(data) - expected
    if missing:
        raise SchemaValidationError(
            f"{path} is missing fields: {', '.join(sorted(missing))}"
        )
    if unknown:
        raise SchemaValidationError(
            f"{path} has unknown fields: {', '.join(sorted(map(str, unknown)))}"
        )


def _string(value: Any, path: str, *, non_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise SchemaValidationError(f"{path} must be a string")
    if non_empty and not value.strip():
        raise SchemaValidationError(f"{path} must be non-empty")
    return value


def _optional_string(value: Any, path: str) -> Optional[str]:
    if value is not None and not isinstance(value, str):
        raise SchemaValidationError(f"{path} must be a string or null")
    return value


def _non_negative_int(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SchemaValidationError(f"{path} must be a non-negative integer")
    return value


def _sha256_string(value: Any, path: str) -> str:
    value = _string(value, path, non_empty=True)
    if len(value) != 64 or value != value.lower():
        raise SchemaValidationError(f"{path} must be a lowercase SHA-256")
    try:
        int(value, 16)
    except ValueError as error:
        raise SchemaValidationError(f"{path} must be a lowercase SHA-256") from error
    return value


def _parse_enum(value: Any, enum_type: type[Enum], path: str) -> Enum:
    if not isinstance(value, str):
        raise SchemaValidationError(f"{path} must be a string enum value")
    try:
        return enum_type(value)
    except ValueError as error:
        allowed = ", ".join(item.value for item in enum_type)
        raise SchemaValidationError(f"{path} must be one of: {allowed}") from error


def _enum(value: Any, enum_type: type[Enum], path: str) -> Enum:
    if not isinstance(value, enum_type):
        raise SchemaValidationError(f"{path} must be a {enum_type.__name__}")
    return value


def _payload_fingerprint(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _clone_optional(value: Any, contract_type: type) -> Any:
    if value is None:
        return None
    if not isinstance(value, contract_type):
        raise SchemaValidationError(
            f"value must be a {contract_type.__name__} or null"
        )
    return contract_type.from_dict(value.to_dict())


@dataclass(frozen=True)
class AttemptRecord:
    attempt_index: int
    entry_stage: AttemptEntryStage
    model_tier: ModelTier

    retrieval_query: Optional[RetrievalQuery] = None
    retrieval_candidates: tuple[RetrievalCandidate, ...] = ()
    scale_hints_by_candidate: Mapping[str, tuple[RetrievedScaleUnitHint, ...]] = field(
        default_factory=dict
    )
    cell_locations: tuple[CellLocation, ...] = ()
    evidence_items: tuple[EvidenceItem, ...] = ()
    evidence_completeness: Optional[EvidenceCompletenessResult] = None

    scale_unit_resolutions: tuple[ScaleUnitResolution, ...] = ()
    schema_links: tuple[SchemaLinkResult, ...] = ()
    masked_evidence: Optional[MaskedEvidenceBundle] = None
    binding_ref: Optional[str] = None

    programmer_input: Optional[ProgrammerInput] = None
    programmer_result: Optional[ProgrammerResult] = None
    execution_result: Optional[ExecutionResult] = None
    verification_report: Optional[VerificationReport] = None
    retry_directive: Optional[RetryDirective] = None
    execution_directive: Optional[ExecutionDirective] = None

    _content_fingerprint: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "attempt_index", _non_negative_int(self.attempt_index, "attempt_index")
        )
        object.__setattr__(
            self, "entry_stage", _enum(self.entry_stage, AttemptEntryStage, "entry_stage")
        )
        object.__setattr__(
            self, "model_tier", _enum(self.model_tier, ModelTier, "model_tier")
        )
        object.__setattr__(
            self,
            "retrieval_query",
            _clone_optional(self.retrieval_query, RetrievalQuery),
        )

        clone_specs = (
            ("retrieval_candidates", RetrievalCandidate),
            ("cell_locations", CellLocation),
            ("evidence_items", EvidenceItem),
            ("scale_unit_resolutions", ScaleUnitResolution),
            ("schema_links", SchemaLinkResult),
        )
        for field_name, contract_type in clone_specs:
            values = getattr(self, field_name)
            if not isinstance(values, (list, tuple)) or not all(
                isinstance(item, contract_type) for item in values
            ):
                raise SchemaValidationError(
                    f"{field_name} must contain {contract_type.__name__} values"
                )
            object.__setattr__(
                self,
                field_name,
                tuple(contract_type.from_dict(item.to_dict()) for item in values),
            )

        if not isinstance(self.scale_hints_by_candidate, Mapping):
            raise SchemaValidationError("scale_hints_by_candidate must be a mapping")
        cloned_hints: Dict[str, tuple[RetrievedScaleUnitHint, ...]] = {}
        for candidate_id, hints in sorted(self.scale_hints_by_candidate.items()):
            candidate_id = _string(
                candidate_id, "scale_hints_by_candidate key", non_empty=True
            )
            if not isinstance(hints, (list, tuple)) or not all(
                isinstance(item, RetrievedScaleUnitHint) for item in hints
            ):
                raise SchemaValidationError(
                    "scale_hints_by_candidate values must contain RetrievedScaleUnitHint"
                )
            cloned_hints[candidate_id] = tuple(
                RetrievedScaleUnitHint.from_dict(item.to_dict()) for item in hints
            )
        object.__setattr__(
            self, "scale_hints_by_candidate", MappingProxyType(cloned_hints)
        )

        optional_specs = (
            ("evidence_completeness", EvidenceCompletenessResult),
            ("masked_evidence", MaskedEvidenceBundle),
            ("programmer_input", ProgrammerInput),
            ("programmer_result", ProgrammerResult),
            ("execution_result", ExecutionResult),
            ("verification_report", VerificationReport),
            ("retry_directive", RetryDirective),
            ("execution_directive", ExecutionDirective),
        )
        for field_name, contract_type in optional_specs:
            object.__setattr__(
                self,
                field_name,
                _clone_optional(getattr(self, field_name), contract_type),
            )

        object.__setattr__(
            self, "binding_ref", _optional_string(self.binding_ref, "binding_ref")
        )
        if self.binding_ref is not None and not self.binding_ref.strip():
            raise SchemaValidationError("binding_ref must be non-empty when present")

        if self.programmer_result is not None and self.programmer_input is None:
            raise SchemaValidationError("programmer_result requires programmer_input")
        if (
            self.masked_evidence is not None
            and self.programmer_input is not None
            and self.masked_evidence.to_dict()
            != self.programmer_input.masked_evidence.to_dict()
        ):
            raise SchemaValidationError(
                "ProgrammerInput must use the current masked_evidence"
            )
        if self.execution_result is not None:
            if (
                self.programmer_result is None
                or self.programmer_result.status is not ProgrammerStatus.GENERATED
                or self.programmer_result.program is None
            ):
                raise SchemaValidationError(
                    "execution_result requires a generated ProgrammerResult"
                )
            if self.binding_ref is None:
                raise SchemaValidationError("execution_result requires binding_ref")
            if self.execution_result.program_id != self.programmer_result.program.program_id:
                raise SchemaValidationError(
                    "execution_result must match the generated Program"
                )
        if self.verification_report is not None:
            if self.execution_result is None or not self.execution_result.success:
                raise SchemaValidationError(
                    "verification_report requires successful execution"
                )
        if self.retry_directive is not None:
            if self.verification_report is None:
                raise SchemaValidationError(
                    "retry_directive requires a VerificationReport"
                )
            if self.verification_report.passed != (
                self.retry_directive.action is RetryAction.PASS
            ):
                raise SchemaValidationError(
                    "retry_directive PASS must exactly match verification outcome"
                )
        if self.execution_directive is not None:
            if (
                self.execution_result is None
                or self.execution_result.success
                or self.execution_result.failure is None
            ):
                raise SchemaValidationError(
                    "execution_directive requires a failed ExecutionResult"
                )
            failure = self.execution_result.failure
            if (
                self.execution_directive.execution_failure_stage is not failure.stage
                or self.execution_directive.execution_failure_code != failure.code
            ):
                raise SchemaValidationError(
                    "execution_directive must preserve ExecutionFailure stage/code"
                )
            if self.verification_report is not None or self.retry_directive is not None:
                raise SchemaValidationError(
                    "execution failure cannot create verification artifacts"
                )
        object.__setattr__(self, "_content_fingerprint", _payload_fingerprint(self._payload()))

    def _payload(self) -> Dict[str, Any]:
        return {
            "attempt_index": self.attempt_index,
            "entry_stage": self.entry_stage.value,
            "model_tier": self.model_tier.value,
            "retrieval_query": None if self.retrieval_query is None else self.retrieval_query.to_dict(),
            "retrieval_candidates": [item.to_dict() for item in self.retrieval_candidates],
            "scale_hints_by_candidate": {
                key: [item.to_dict() for item in values]
                for key, values in self.scale_hints_by_candidate.items()
            },
            "cell_locations": [item.to_dict() for item in self.cell_locations],
            "evidence_items": [item.to_dict() for item in self.evidence_items],
            "evidence_completeness": (
                None
                if self.evidence_completeness is None
                else self.evidence_completeness.to_dict()
            ),
            "scale_unit_resolutions": [item.to_dict() for item in self.scale_unit_resolutions],
            "schema_links": [item.to_dict() for item in self.schema_links],
            "masked_evidence": None if self.masked_evidence is None else self.masked_evidence.to_dict(),
            "binding_ref": self.binding_ref,
            "programmer_input": None if self.programmer_input is None else self.programmer_input.to_dict(),
            "programmer_result": None if self.programmer_result is None else self.programmer_result.to_dict(),
            "execution_result": None if self.execution_result is None else self.execution_result.to_dict(),
            "verification_report": None if self.verification_report is None else self.verification_report.to_dict(),
            "retry_directive": None if self.retry_directive is None else self.retry_directive.to_dict(),
            "execution_directive": None if self.execution_directive is None else self.execution_directive.to_dict(),
        }

    def assert_immutable(self) -> None:
        if _payload_fingerprint(self._payload()) != self._content_fingerprint:
            raise SchemaValidationError("AttemptRecord was mutated after creation")

    def to_dict(self) -> Dict[str, Any]:
        self.assert_immutable()
        return self._payload()

    @classmethod
    def from_dict(cls, value: Any) -> "AttemptRecord":
        data = _mapping(value, "AttemptRecord")
        expected = {
            "attempt_index", "entry_stage", "model_tier", "retrieval_query",
            "retrieval_candidates", "scale_hints_by_candidate", "cell_locations",
            "evidence_items", "evidence_completeness", "scale_unit_resolutions",
            "schema_links", "masked_evidence", "binding_ref", "programmer_input",
            "programmer_result", "execution_result", "verification_report",
            "retry_directive", "execution_directive",
        }
        _exact_keys(data, expected, "AttemptRecord")
        list_fields = (
            "retrieval_candidates", "cell_locations", "evidence_items",
            "scale_unit_resolutions", "schema_links",
        )
        if any(not isinstance(data[name], list) for name in list_fields):
            raise SchemaValidationError("AttemptRecord artifact fields must be lists")
        if not isinstance(data["scale_hints_by_candidate"], Mapping):
            raise SchemaValidationError("scale_hints_by_candidate must be an object")
        return cls(
            attempt_index=data["attempt_index"],
            entry_stage=_parse_enum(data["entry_stage"], AttemptEntryStage, "entry_stage"),
            model_tier=_parse_enum(data["model_tier"], ModelTier, "model_tier"),
            retrieval_query=None if data["retrieval_query"] is None else RetrievalQuery.from_dict(data["retrieval_query"]),
            retrieval_candidates=tuple(RetrievalCandidate.from_dict(item) for item in data["retrieval_candidates"]),
            scale_hints_by_candidate={
                key: tuple(RetrievedScaleUnitHint.from_dict(item) for item in values)
                for key, values in data["scale_hints_by_candidate"].items()
            },
            cell_locations=tuple(CellLocation.from_dict(item) for item in data["cell_locations"]),
            evidence_items=tuple(EvidenceItem.from_dict(item) for item in data["evidence_items"]),
            evidence_completeness=(
                None
                if data["evidence_completeness"] is None
                else EvidenceCompletenessResult.from_dict(data["evidence_completeness"])
            ),
            scale_unit_resolutions=tuple(ScaleUnitResolution.from_dict(item) for item in data["scale_unit_resolutions"]),
            schema_links=tuple(SchemaLinkResult.from_dict(item) for item in data["schema_links"]),
            masked_evidence=None if data["masked_evidence"] is None else MaskedEvidenceBundle.from_dict(data["masked_evidence"]),
            binding_ref=data["binding_ref"],
            programmer_input=None if data["programmer_input"] is None else ProgrammerInput.from_dict(data["programmer_input"]),
            programmer_result=None if data["programmer_result"] is None else ProgrammerResult.from_dict(data["programmer_result"]),
            execution_result=None if data["execution_result"] is None else ExecutionResult.from_dict(data["execution_result"]),
            verification_report=None if data["verification_report"] is None else VerificationReport.from_dict(data["verification_report"]),
            retry_directive=None if data["retry_directive"] is None else RetryDirective.from_dict(data["retry_directive"]),
            execution_directive=None if data["execution_directive"] is None else ExecutionDirective.from_dict(data["execution_directive"]),
        )


@dataclass(frozen=True)
class M9State:
    schema_version: str
    request_id: str
    raw_question: str
    phase: M9Phase
    outcome: Optional[FinalResponseStatus]

    query_understanding: Optional[QueryUnderstanding]
    planning_gate: Optional[PlanningGate]
    supervisor_result: Optional[SupervisorResult]
    plan_fingerprint: Optional[str]

    retrieval_policy_fingerprint: Optional[str]
    retry_state: Optional[RetryState]
    attempts: tuple[AttemptRecord, ...]

    final_response: Optional[FinalResponse]
    failure_attribution: Optional[FailureAttribution]

    def __post_init__(self) -> None:
        if self.schema_version != M9_STATE_SCHEMA_VERSION:
            raise SchemaValidationError(
                f"schema_version must be {M9_STATE_SCHEMA_VERSION}"
            )
        object.__setattr__(self, "request_id", _string(self.request_id, "request_id", non_empty=True))
        object.__setattr__(self, "raw_question", _string(self.raw_question, "raw_question"))
        object.__setattr__(self, "phase", _enum(self.phase, M9Phase, "phase"))
        if self.outcome is not None:
            object.__setattr__(self, "outcome", _enum(self.outcome, FinalResponseStatus, "outcome"))

        object.__setattr__(self, "query_understanding", _clone_optional(self.query_understanding, QueryUnderstanding))
        object.__setattr__(self, "planning_gate", _clone_optional(self.planning_gate, PlanningGate))
        object.__setattr__(self, "supervisor_result", _clone_optional(self.supervisor_result, SupervisorResult))
        if (
            self.query_understanding is not None
            and self.raw_question != self.query_understanding.raw_question
        ):
            raise SchemaValidationError(
                "raw_question must equal QueryUnderstanding.raw_question"
            )
        if self.supervisor_result is not None:
            if self.query_understanding is None or self.planning_gate is None:
                raise SchemaValidationError(
                    "supervisor_result requires QueryUnderstanding and PlanningGate"
                )
            if self.supervisor_result.planning_gate.to_dict() != self.planning_gate.to_dict():
                raise SchemaValidationError(
                    "SupervisorResult PlanningGate must equal state PlanningGate"
                )
            expected_input = make_supervisor_input_fingerprint(
                self.query_understanding.to_dict(), self.planning_gate
            )
            if self.supervisor_result.input_fingerprint != expected_input:
                raise SchemaValidationError(
                    "SupervisorResult input fingerprint does not match state"
                )

        plan = None if self.supervisor_result is None else self.supervisor_result.plan
        if plan is None:
            if self.plan_fingerprint is not None:
                raise SchemaValidationError("plan_fingerprint requires executable Plan")
            if self.retry_state is not None or self.attempts:
                raise SchemaValidationError("retry state and attempts require executable Plan")
        else:
            expected_plan_fingerprint = make_plan_fingerprint(plan)
            if self.plan_fingerprint != expected_plan_fingerprint:
                raise SchemaValidationError("plan_fingerprint does not match SupervisorResult.plan")
            object.__setattr__(self, "plan_fingerprint", _sha256_string(self.plan_fingerprint, "plan_fingerprint"))

        if self.retrieval_policy_fingerprint is not None:
            object.__setattr__(
                self,
                "retrieval_policy_fingerprint",
                _sha256_string(
                    self.retrieval_policy_fingerprint,
                    "retrieval_policy_fingerprint",
                ),
            )
            if plan is None:
                raise SchemaValidationError(
                    "retrieval_policy_fingerprint requires executable Plan"
                )

        object.__setattr__(self, "retry_state", _clone_optional(self.retry_state, RetryState))
        if self.retry_state is not None and plan is not None:
            if self.retry_state.max_retries != plan.max_retries:
                raise SchemaValidationError(
                    "RetryState.max_retries must equal Plan.max_retries"
                )

        if not isinstance(self.attempts, (list, tuple)) or not all(
            isinstance(item, AttemptRecord) for item in self.attempts
        ):
            raise SchemaValidationError("attempts must contain AttemptRecord values")
        attempts = tuple(AttemptRecord.from_dict(item.to_dict()) for item in self.attempts)
        object.__setattr__(self, "attempts", attempts)
        if attempts:
            if self.retry_state is None or self.retrieval_policy_fingerprint is None:
                raise SchemaValidationError(
                    "attempts require retry state and retrieval policy fingerprint"
                )
            expected_indexes = list(range(len(attempts)))
            actual_indexes = [item.attempt_index for item in attempts]
            if actual_indexes != expected_indexes:
                raise SchemaValidationError(
                    "attempt indexes must be contiguous from zero"
                )
            if attempts[-1].attempt_index != self.retry_state.retries_used:
                raise SchemaValidationError(
                    "last attempt index must equal RetryState.retries_used"
                )
            if attempts[-1].model_tier is not self.retry_state.current_model_tier:
                raise SchemaValidationError(
                    "current attempt model tier must equal RetryState"
                )
            retrieval_queries = [
                item.retrieval_query
                for item in attempts
                if item.retrieval_query is not None
            ]
            for retrieval_query in retrieval_queries:
                validate_retrieval_query_against_plan(plan, retrieval_query)
                expected_query = build_retrieval_query(
                    plan, self.query_understanding
                )
                if retrieval_query.to_dict() != expected_query.to_dict():
                    raise SchemaValidationError(
                        "retrieval query must equal the deterministic Plan query"
                    )
            if retrieval_queries:
                canonical_query = retrieval_queries[0].to_dict()
                if any(
                    query.to_dict() != canonical_query
                    for query in retrieval_queries[1:]
                ):
                    raise SchemaValidationError(
                        "retrieval query cannot change across attempts"
                    )
            canonical_plan = plan.to_dict()
            for attempt in attempts:
                if (
                    attempt.programmer_input is not None
                    and attempt.programmer_input.plan.to_dict()
                    != canonical_plan
                ):
                    raise SchemaValidationError(
                        "ProgrammerInput Plan must equal SupervisorResult.plan"
                    )
            tiers = [item.model_tier for item in attempts]
            if any(
                previous is ModelTier.STRONG and current is ModelTier.CHEAP
                for previous, current in zip(tiers, tiers[1:])
            ):
                raise SchemaValidationError("attempt model tier cannot downgrade")

        object.__setattr__(self, "final_response", _clone_optional(self.final_response, FinalResponse))
        object.__setattr__(self, "failure_attribution", _clone_optional(self.failure_attribution, FailureAttribution))
        is_terminal = self.phase is M9Phase.TERMINAL
        if is_terminal:
            if self.outcome is None or self.final_response is None:
                raise SchemaValidationError(
                    "terminal state requires outcome and final_response"
                )
            if self.outcome is not self.final_response.status:
                raise SchemaValidationError(
                    "state outcome must equal FinalResponse.status"
                )
            left = None if self.failure_attribution is None else self.failure_attribution.to_dict()
            right = (
                None
                if self.final_response.failure_attribution is None
                else self.final_response.failure_attribution.to_dict()
            )
            if left != right:
                raise SchemaValidationError(
                    "state and FinalResponse failure attribution must match"
                )
            if self.retry_state is not None:
                if not self.retry_state.terminal:
                    raise SchemaValidationError("terminal state requires terminal RetryState")
                if (
                    self.final_response.retry_state is None
                    or self.final_response.retry_state.to_dict() != self.retry_state.to_dict()
                ):
                    raise SchemaValidationError(
                        "FinalResponse RetryState must equal state RetryState"
                    )
            self._validate_pass(plan)
        elif self.outcome is not None or self.final_response is not None or self.failure_attribution is not None:
            raise SchemaValidationError(
                "non-terminal state cannot contain terminal outcome fields"
            )

    def _validate_pass(self, plan: Any) -> None:
        if self.outcome is not FinalResponseStatus.PASS:
            return
        if plan is None or not self.attempts or self.final_response is None:
            raise SchemaValidationError("PASS requires Plan and completed attempt")
        attempt = self.attempts[-1]
        if attempt.execution_result is None or not attempt.execution_result.success:
            raise SchemaValidationError("PASS requires successful ExecutionResult")
        if attempt.verification_report is None or not attempt.verification_report.passed:
            raise SchemaValidationError("PASS requires passed VerificationReport")
        if attempt.retry_directive is None or attempt.retry_directive.action is not RetryAction.PASS:
            raise SchemaValidationError("PASS requires M8 PASS directive")
        answer = self.final_response.answer
        if answer is None or answer.output.to_dict() != attempt.execution_result.output.to_dict():
            raise SchemaValidationError(
                "FinalAnswer must copy the verified ExecutionOutput exactly"
            )
        if (
            answer.question_type is not plan.question_type
            or list(answer.target_metrics) != plan.target_metrics
            or answer.derived_target != plan.derived_target
            or answer.formula_id != plan.formula_id
        ):
            raise SchemaValidationError("FinalAnswer metadata must equal Plan")

    @property
    def terminal(self) -> bool:
        return self.phase is M9Phase.TERMINAL

    def append_attempt(
        self, attempt: AttemptRecord, *, next_retry_state: RetryState
    ) -> "M9State":
        """Append one immutable current attempt; execute no stage work."""
        if self.terminal:
            raise SchemaValidationError("terminal state cannot transition")
        for previous in self.attempts:
            previous.assert_immutable()
        if not isinstance(attempt, AttemptRecord):
            raise SchemaValidationError("attempt must be an AttemptRecord")
        if not isinstance(next_retry_state, RetryState):
            raise SchemaValidationError("next_retry_state must be a RetryState")
        if attempt.attempt_index != len(self.attempts):
            raise SchemaValidationError("new attempt index must follow previous attempts")
        if attempt.attempt_index != next_retry_state.retries_used:
            raise SchemaValidationError(
                "attempt_index must equal next_retry_state.retries_used"
            )
        phase = {
            AttemptEntryStage.RETRIEVAL: M9Phase.RETRIEVAL,
            AttemptEntryStage.PROGRAMMER: M9Phase.PROGRAMMER,
            AttemptEntryStage.SANDBOX: M9Phase.SANDBOX,
        }[attempt.entry_stage]
        return replace(
            self,
            phase=phase,
            retry_state=next_retry_state,
            attempts=(*self.attempts, attempt),
        )

    def terminate(
        self,
        response: FinalResponse,
    ) -> "M9State":
        """Attach a terminal response; reject repeated terminal transitions."""
        if self.terminal:
            raise SchemaValidationError("terminal state cannot transition")
        if not isinstance(response, FinalResponse):
            raise SchemaValidationError("response must be a FinalResponse")
        return replace(
            self,
            phase=M9Phase.TERMINAL,
            outcome=response.status,
            retry_state=response.retry_state,
            final_response=response,
            failure_attribution=response.failure_attribution,
        )

    def to_dict(self) -> Dict[str, Any]:
        for attempt in self.attempts:
            attempt.assert_immutable()
        if self.supervisor_result is not None and self.supervisor_result.plan is not None:
            if make_plan_fingerprint(self.supervisor_result.plan) != self.plan_fingerprint:
                raise SchemaValidationError("Plan was mutated after state creation")
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "raw_question": self.raw_question,
            "phase": self.phase.value,
            "outcome": None if self.outcome is None else self.outcome.value,
            "query_understanding": None if self.query_understanding is None else self.query_understanding.to_dict(),
            "planning_gate": None if self.planning_gate is None else self.planning_gate.to_dict(),
            "supervisor_result": None if self.supervisor_result is None else self.supervisor_result.to_dict(),
            "plan_fingerprint": self.plan_fingerprint,
            "retrieval_policy_fingerprint": self.retrieval_policy_fingerprint,
            "retry_state": None if self.retry_state is None else self.retry_state.to_dict(),
            "attempts": [item.to_dict() for item in self.attempts],
            "final_response": None if self.final_response is None else self.final_response.to_dict(),
            "failure_attribution": None if self.failure_attribution is None else self.failure_attribution.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "M9State":
        data = _mapping(value, "M9State")
        _exact_keys(
            data,
            {
                "schema_version", "request_id", "raw_question", "phase", "outcome",
                "query_understanding", "planning_gate", "supervisor_result",
                "plan_fingerprint", "retrieval_policy_fingerprint", "retry_state",
                "attempts", "final_response", "failure_attribution",
            },
            "M9State",
        )
        if not isinstance(data["attempts"], list):
            raise SchemaValidationError("attempts must be a list")
        schema_version = data["schema_version"]
        if schema_version == M9_STATE_SCHEMA_VERSION_V1:
            schema_version = M9_STATE_SCHEMA_VERSION
        return cls(
            schema_version=schema_version,
            request_id=data["request_id"],
            raw_question=data["raw_question"],
            phase=_parse_enum(data["phase"], M9Phase, "phase"),
            outcome=None if data["outcome"] is None else _parse_enum(data["outcome"], FinalResponseStatus, "outcome"),
            query_understanding=None if data["query_understanding"] is None else QueryUnderstanding.from_dict(data["query_understanding"]),
            planning_gate=None if data["planning_gate"] is None else PlanningGate.from_dict(data["planning_gate"]),
            supervisor_result=None if data["supervisor_result"] is None else SupervisorResult.from_dict(data["supervisor_result"]),
            plan_fingerprint=data["plan_fingerprint"],
            retrieval_policy_fingerprint=data["retrieval_policy_fingerprint"],
            retry_state=None if data["retry_state"] is None else RetryState.from_dict(data["retry_state"]),
            attempts=tuple(AttemptRecord.from_dict(item) for item in data["attempts"]),
            final_response=None if data["final_response"] is None else FinalResponse.from_dict(data["final_response"]),
            failure_attribution=None if data["failure_attribution"] is None else FailureAttribution.from_dict(data["failure_attribution"]),
        )
