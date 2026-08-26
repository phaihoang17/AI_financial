"""Versioned M9 terminal, fingerprint, and execution-directive contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
from typing import Any, Dict, Mapping, Optional, Sequence

from src.evaluation.harness import EvaluationFailureStage
from src.numeric.scale_conversion import ScaleConversionFailureCode
from src.programmer.validator import ProgramValidationFailureCode
from src.retrieval.schemas import RetrievalQuery
from src.sandbox.executor import ExecutionInfrastructureFailureCode
from src.sandbox.interpreter import InterpreterFailureCode
from src.sandbox.limits import ResourceLimitFailureCode
from src.sandbox.policy import (
    ExecutionBindingFailureCode,
    ExecutionPolicyFailureCode,
)
from src.sandbox.schemas import (
    ExecutionFailureStage,
    ExecutionOutput,
)
from src.sandbox.security import SecurityFailureCode
from src.supervisor.schemas import EvidenceSource, Plan, QuestionType
from src.understanding.schemas import SchemaValidationError
from src.verification.schemas import RetryState


M9_STATE_SCHEMA_VERSION_V1 = "m9-orchestration-state-v1"
M9_STATE_SCHEMA_VERSION = "m9-orchestration-state-v2"
PLAN_FINGERPRINT_VERSION = "m9-plan-fingerprint-v1"
RETRIEVAL_POLICY_FINGERPRINT_VERSION = "m9-retrieval-policy-fingerprint-v1"


class M9Phase(str, Enum):
    INPUT = "INPUT"
    NLU = "NLU"
    SUPERVISOR = "SUPERVISOR"
    RETRIEVAL = "RETRIEVAL"
    EVIDENCE = "EVIDENCE"
    PROGRAMMER = "PROGRAMMER"
    SANDBOX = "SANDBOX"
    VERIFICATION = "VERIFICATION"
    ANSWER = "ANSWER"
    TERMINAL = "TERMINAL"


class FinalResponseStatus(str, Enum):
    PASS = "PASS"
    CLARIFICATION = "CLARIFICATION"
    ABSTAIN = "ABSTAIN"


class ExecutionAction(str, Enum):
    RETRY_RETRIEVAL = "RETRY_RETRIEVAL"
    RETRY_PROGRAMMER = "RETRY_PROGRAMMER"
    RETRY_SANDBOX = "RETRY_SANDBOX"
    ABSTAIN = "ABSTAIN"


class ExecutionClassificationError(SchemaValidationError):
    """Raised when an execution stage/code has no approved M9 contract entry."""


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


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _fingerprint(version: str, payload: Mapping[str, Any]) -> str:
    encoded = _canonical_json(
        {"schema_version": version, **dict(payload)}
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def make_plan_fingerprint(plan: Plan) -> str:
    """Fingerprint the only canonical Plan source without mutating it."""
    if not isinstance(plan, Plan):
        raise SchemaValidationError("plan must be an executable Plan")
    return _fingerprint(PLAN_FINGERPRINT_VERSION, {"plan": plan.to_dict()})


def validate_retrieval_query_against_plan(
    plan: Plan, query: RetrievalQuery
) -> RetrievalQuery:
    """Reject any retrieval filter or source eligibility outside the Plan."""
    if not isinstance(plan, Plan):
        raise SchemaValidationError("plan must be an executable Plan")
    if not isinstance(query, RetrievalQuery):
        raise SchemaValidationError("query must be a RetrievalQuery")
    if query.company.name != plan.company.name or query.company.ticker != plan.company.ticker:
        raise SchemaValidationError("retrieval company filters must equal Plan")
    if query.periods != plan.periods or query.period_kind is not plan.period_kind:
        raise SchemaValidationError("retrieval period filters must equal Plan")
    if query.statement_scope is not plan.statement_scope:
        raise SchemaValidationError("retrieval scope filter must equal Plan")
    if query.target_metrics != plan.target_metrics:
        raise SchemaValidationError("retrieval target metrics must equal Plan")
    if query.derived_target != plan.derived_target:
        raise SchemaValidationError("retrieval derived target must equal Plan")
    if query.evidence_sources != plan.evidence_sources:
        raise SchemaValidationError("retrieval evidence sources must equal Plan")
    if query.eligible_source_types != plan.evidence_sources:
        raise SchemaValidationError(
            "retrieval source eligibility must equal Plan evidence sources"
        )
    return query


def make_retrieval_policy_fingerprint(
    plan: Plan,
    query: RetrievalQuery,
    *,
    top_k: int,
    artifact_versions: Mapping[str, str],
) -> str:
    """Fingerprint exact requirements, filters, source types, top-k and artifacts."""
    if not isinstance(plan, Plan):
        raise SchemaValidationError("plan must be an executable Plan")
    if not isinstance(query, RetrievalQuery):
        raise SchemaValidationError("query must be a RetrievalQuery")
    validate_retrieval_query_against_plan(plan, query)
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
        raise SchemaValidationError("top_k must be a positive integer")
    if not isinstance(artifact_versions, Mapping) or not artifact_versions:
        raise SchemaValidationError("artifact_versions must be a non-empty mapping")
    normalized_versions: Dict[str, str] = {}
    for key, value in artifact_versions.items():
        normalized_versions[
            _string(key, "artifact_versions key", non_empty=True)
        ] = _string(value, f"artifact_versions[{key!r}]", non_empty=True)
    return _fingerprint(
        RETRIEVAL_POLICY_FINGERPRINT_VERSION,
        {
            "plan_fingerprint": make_plan_fingerprint(plan),
            "retrieval_requirements": [
                item.to_dict() for item in plan.retrieval_requirements
            ],
            "retrieval_query": query.to_dict(),
            "top_k": top_k,
            "artifact_versions": normalized_versions,
        },
    )


@dataclass(frozen=True)
class FailureAttribution:
    stage: EvaluationFailureStage
    code: str
    reason: str
    attempt_index: Optional[int]
    requirement_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    program_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "stage", _enum(self.stage, EvaluationFailureStage, "stage"))
        object.__setattr__(self, "code", _string(self.code, "code", non_empty=True))
        object.__setattr__(self, "reason", _string(self.reason, "reason", non_empty=True))
        if self.attempt_index is not None:
            object.__setattr__(
                self,
                "attempt_index",
                _non_negative_int(self.attempt_index, "attempt_index"),
            )
        for field_name in ("requirement_ids", "evidence_ids", "program_ids"):
            values = getattr(self, field_name)
            if not isinstance(values, (list, tuple)) or not all(
                isinstance(item, str) and item.strip() for item in values
            ):
                raise SchemaValidationError(
                    f"{field_name} must contain non-empty strings"
                )
            if len(values) != len(set(values)):
                raise SchemaValidationError(f"{field_name} must be unique")
            object.__setattr__(self, field_name, tuple(values))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stage": self.stage.value,
            "code": self.code,
            "reason": self.reason,
            "attempt_index": self.attempt_index,
            "requirement_ids": list(self.requirement_ids),
            "evidence_ids": list(self.evidence_ids),
            "program_ids": list(self.program_ids),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "FailureAttribution":
        data = _mapping(value, "FailureAttribution")
        legacy = {"stage", "code", "reason", "attempt_index"}
        current = legacy | {"requirement_ids", "evidence_ids", "program_ids"}
        if frozenset(data) not in {frozenset(legacy), frozenset(current)}:
            _exact_keys(data, current, "FailureAttribution")
        return cls(
            stage=_parse_enum(data["stage"], EvaluationFailureStage, "stage"),
            code=data["code"],
            reason=data["reason"],
            attempt_index=data["attempt_index"],
            requirement_ids=tuple(data.get("requirement_ids", ())),
            evidence_ids=tuple(data.get("evidence_ids", ())),
            program_ids=tuple(data.get("program_ids", ())),
        )


@dataclass(frozen=True)
class AnswerEvidenceReference:
    evidence_id: str
    requirement_id: str
    source_type: EvidenceSource
    report_ref: str
    page_ref: Optional[str]
    table_ref: Optional[str]
    paragraph_ref: Optional[str]
    metric: Optional[str]
    period: Optional[str]
    row_path: tuple[str, ...]
    column_path: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_id", _string(self.evidence_id, "evidence_id", non_empty=True))
        object.__setattr__(self, "requirement_id", _string(self.requirement_id, "requirement_id", non_empty=True))
        object.__setattr__(self, "source_type", _enum(self.source_type, EvidenceSource, "source_type"))
        object.__setattr__(self, "report_ref", _string(self.report_ref, "report_ref", non_empty=True))
        for field_name in ("page_ref", "table_ref", "paragraph_ref", "metric", "period"):
            object.__setattr__(self, field_name, _optional_string(getattr(self, field_name), field_name))
        for field_name in ("row_path", "column_path"):
            value = getattr(self, field_name)
            if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
                raise SchemaValidationError(f"{field_name} must contain strings")
            object.__setattr__(self, field_name, tuple(value))
        if self.source_type is EvidenceSource.TABLE and self.table_ref is None:
            raise SchemaValidationError("TABLE answer evidence requires table_ref")
        if self.source_type is EvidenceSource.TEXT and self.paragraph_ref is None:
            raise SchemaValidationError("TEXT answer evidence requires paragraph_ref")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "requirement_id": self.requirement_id,
            "source_type": self.source_type.value,
            "report_ref": self.report_ref,
            "page_ref": self.page_ref,
            "table_ref": self.table_ref,
            "paragraph_ref": self.paragraph_ref,
            "metric": self.metric,
            "period": self.period,
            "row_path": list(self.row_path),
            "column_path": list(self.column_path),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "AnswerEvidenceReference":
        data = _mapping(value, "AnswerEvidenceReference")
        _exact_keys(
            data,
            {
                "evidence_id", "requirement_id", "source_type", "report_ref",
                "page_ref", "table_ref", "paragraph_ref", "metric", "period",
                "row_path", "column_path",
            },
            "AnswerEvidenceReference",
        )
        return cls(
            evidence_id=data["evidence_id"],
            requirement_id=data["requirement_id"],
            source_type=_parse_enum(data["source_type"], EvidenceSource, "source_type"),
            report_ref=data["report_ref"],
            page_ref=data["page_ref"],
            table_ref=data["table_ref"],
            paragraph_ref=data["paragraph_ref"],
            metric=data["metric"],
            period=data["period"],
            row_path=tuple(data["row_path"]),
            column_path=tuple(data["column_path"]),
        )


@dataclass(frozen=True)
class FinalAnswer:
    question_type: QuestionType
    target_metrics: tuple[str, ...]
    derived_target: Optional[str]
    formula_id: Optional[str]
    output: ExecutionOutput
    evidence: tuple[AnswerEvidenceReference, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "question_type", _enum(self.question_type, QuestionType, "question_type"))
        if not isinstance(self.target_metrics, (list, tuple)) or not self.target_metrics:
            raise SchemaValidationError("target_metrics must be a non-empty list")
        if not all(isinstance(item, str) and item for item in self.target_metrics):
            raise SchemaValidationError("target_metrics must contain non-empty strings")
        object.__setattr__(self, "target_metrics", tuple(self.target_metrics))
        object.__setattr__(self, "derived_target", _optional_string(self.derived_target, "derived_target"))
        object.__setattr__(self, "formula_id", _optional_string(self.formula_id, "formula_id"))
        if not isinstance(self.output, ExecutionOutput):
            raise SchemaValidationError("output must be an ExecutionOutput")
        object.__setattr__(self, "output", ExecutionOutput.from_dict(self.output.to_dict()))
        if not isinstance(self.evidence, (list, tuple)) or not all(
            isinstance(item, AnswerEvidenceReference) for item in self.evidence
        ):
            raise SchemaValidationError("evidence must contain AnswerEvidenceReference values")
        object.__setattr__(self, "evidence", tuple(self.evidence))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question_type": self.question_type.value,
            "target_metrics": list(self.target_metrics),
            "derived_target": self.derived_target,
            "formula_id": self.formula_id,
            "output": self.output.to_dict(),
            "evidence": [item.to_dict() for item in self.evidence],
        }

    @classmethod
    def from_dict(cls, value: Any) -> "FinalAnswer":
        data = _mapping(value, "FinalAnswer")
        _exact_keys(
            data,
            {"question_type", "target_metrics", "derived_target", "formula_id", "output", "evidence"},
            "FinalAnswer",
        )
        if not isinstance(data["target_metrics"], list) or not isinstance(data["evidence"], list):
            raise SchemaValidationError("FinalAnswer list fields must be lists")
        return cls(
            question_type=_parse_enum(data["question_type"], QuestionType, "question_type"),
            target_metrics=tuple(data["target_metrics"]),
            derived_target=data["derived_target"],
            formula_id=data["formula_id"],
            output=ExecutionOutput.from_dict(data["output"]),
            evidence=tuple(AnswerEvidenceReference.from_dict(item) for item in data["evidence"]),
        )


@dataclass(frozen=True)
class FinalResponse:
    status: FinalResponseStatus
    answer: Optional[FinalAnswer]
    reason_code: Optional[str]
    message: str
    retry_state: Optional[RetryState]
    failure_attribution: Optional[FailureAttribution]

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _enum(self.status, FinalResponseStatus, "status"))
        if self.answer is not None and not isinstance(self.answer, FinalAnswer):
            raise SchemaValidationError("answer must be a FinalAnswer or null")
        object.__setattr__(self, "reason_code", _optional_string(self.reason_code, "reason_code"))
        object.__setattr__(self, "message", _string(self.message, "message", non_empty=True))
        if self.retry_state is not None and not isinstance(self.retry_state, RetryState):
            raise SchemaValidationError("retry_state must be a RetryState or null")
        if self.failure_attribution is not None and not isinstance(
            self.failure_attribution, FailureAttribution
        ):
            raise SchemaValidationError(
                "failure_attribution must be a FailureAttribution or null"
            )
        if self.retry_state is not None and not self.retry_state.terminal:
            raise SchemaValidationError("terminal response RetryState must be terminal")
        if self.status is FinalResponseStatus.PASS:
            if self.answer is None or self.reason_code is not None or self.failure_attribution is not None:
                raise SchemaValidationError(
                    "PASS requires answer and null failure fields"
                )
            if self.retry_state is None:
                raise SchemaValidationError("PASS requires terminal RetryState")
        else:
            if self.answer is not None:
                raise SchemaValidationError("non-PASS response requires null answer")
            if self.reason_code is None or not self.reason_code.strip():
                raise SchemaValidationError("non-PASS response requires reason_code")
            if self.failure_attribution is None:
                raise SchemaValidationError(
                    "non-PASS response requires failure_attribution"
                )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "answer": None if self.answer is None else self.answer.to_dict(),
            "reason_code": self.reason_code,
            "message": self.message,
            "retry_state": None if self.retry_state is None else self.retry_state.to_dict(),
            "failure_attribution": (
                None
                if self.failure_attribution is None
                else self.failure_attribution.to_dict()
            ),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "FinalResponse":
        data = _mapping(value, "FinalResponse")
        _exact_keys(
            data,
            {"status", "answer", "reason_code", "message", "retry_state", "failure_attribution"},
            "FinalResponse",
        )
        return cls(
            status=_parse_enum(data["status"], FinalResponseStatus, "status"),
            answer=None if data["answer"] is None else FinalAnswer.from_dict(data["answer"]),
            reason_code=data["reason_code"],
            message=data["message"],
            retry_state=None if data["retry_state"] is None else RetryState.from_dict(data["retry_state"]),
            failure_attribution=(
                None
                if data["failure_attribution"] is None
                else FailureAttribution.from_dict(data["failure_attribution"])
            ),
        )


_KNOWN_EXECUTION_CODES: Mapping[ExecutionFailureStage, frozenset[str]] = {
    ExecutionFailureStage.POLICY: frozenset(
        [item.value for item in ExecutionPolicyFailureCode]
        + [ProgramValidationFailureCode.FORBIDDEN_OPERATION.value]
    ),
    ExecutionFailureStage.VALIDATION: frozenset(
        item.value for item in ProgramValidationFailureCode
    ),
    ExecutionFailureStage.BINDING: frozenset(
        item.value for item in ExecutionBindingFailureCode
    ),
    ExecutionFailureStage.CONVERSION: frozenset(
        [item.value for item in ScaleConversionFailureCode]
        + [InterpreterFailureCode.INCOMPATIBLE_SCALES.value]
    ),
    ExecutionFailureStage.ARITHMETIC: frozenset(
        {
            InterpreterFailureCode.INVALID_FORMULA.value,
            InterpreterFailureCode.DIVISION_BY_ZERO.value,
            InterpreterFailureCode.DECIMAL_ARITHMETIC_ERROR.value,
            InterpreterFailureCode.OUTPUT_KIND_MISMATCH.value,
        }
    ),
    ExecutionFailureStage.RESOURCE: frozenset(
        item.value for item in ResourceLimitFailureCode
    ),
    ExecutionFailureStage.SECURITY: frozenset(
        item.value for item in SecurityFailureCode
    ),
    ExecutionFailureStage.INFRASTRUCTURE: frozenset(
        item.value for item in ExecutionInfrastructureFailureCode
    ),
}


@dataclass(frozen=True)
class ExecutionDirective:
    action: ExecutionAction
    execution_failure_stage: ExecutionFailureStage
    execution_failure_code: str
    next_retry_state: RetryState
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "action", _enum(self.action, ExecutionAction, "action"))
        stage = _enum(
            self.execution_failure_stage,
            ExecutionFailureStage,
            "execution_failure_stage",
        )
        object.__setattr__(self, "execution_failure_stage", stage)
        code = _string(
            self.execution_failure_code,
            "execution_failure_code",
            non_empty=True,
        )
        object.__setattr__(self, "execution_failure_code", code)
        if code not in _KNOWN_EXECUTION_CODES[stage]:
            raise ExecutionClassificationError(
                f"unregistered execution failure: {stage.value}/{code}"
            )
        if not isinstance(self.next_retry_state, RetryState):
            raise SchemaValidationError("next_retry_state must be a RetryState")
        object.__setattr__(self, "reason", _string(self.reason, "reason", non_empty=True))
        if self.action is ExecutionAction.ABSTAIN:
            if not self.next_retry_state.terminal:
                raise SchemaValidationError("ABSTAIN requires terminal next_retry_state")
        elif self.next_retry_state.terminal:
            raise SchemaValidationError("execution retry requires non-terminal next_retry_state")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action.value,
            "execution_failure_stage": self.execution_failure_stage.value,
            "execution_failure_code": self.execution_failure_code,
            "next_retry_state": self.next_retry_state.to_dict(),
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "ExecutionDirective":
        data = _mapping(value, "ExecutionDirective")
        _exact_keys(
            data,
            {"action", "execution_failure_stage", "execution_failure_code", "next_retry_state", "reason"},
            "ExecutionDirective",
        )
        try:
            failure_stage = _parse_enum(
                data["execution_failure_stage"],
                ExecutionFailureStage,
                "execution_failure_stage",
            )
        except SchemaValidationError as error:
            raise ExecutionClassificationError(
                "unregistered execution failure stage"
            ) from error
        return cls(
            action=_parse_enum(data["action"], ExecutionAction, "action"),
            execution_failure_stage=failure_stage,
            execution_failure_code=data["execution_failure_code"],
            next_retry_state=RetryState.from_dict(data["next_retry_state"]),
            reason=data["reason"],
        )


def validate_execution_directive_transition(
    plan: Plan,
    current_state: RetryState,
    directive: ExecutionDirective,
) -> ExecutionDirective:
    """Validate shared budget consumption without classifying or executing work."""
    if not isinstance(plan, Plan):
        raise SchemaValidationError("plan must be an executable Plan")
    if not isinstance(current_state, RetryState):
        raise SchemaValidationError("current_state must be a RetryState")
    if not isinstance(directive, ExecutionDirective):
        raise SchemaValidationError("directive must be an ExecutionDirective")
    if current_state.terminal:
        raise SchemaValidationError("terminal retry state cannot transition")
    next_state = directive.next_retry_state
    if current_state.max_retries != plan.max_retries or next_state.max_retries != plan.max_retries:
        raise SchemaValidationError("RetryState.max_retries must equal Plan.max_retries")
    if (
        next_state.current_model_tier is not current_state.current_model_tier
        or next_state.strong_escalated != current_state.strong_escalated
    ):
        raise SchemaValidationError(
            "execution directives cannot trigger model escalation or downgrade"
        )
    if directive.action is ExecutionAction.ABSTAIN:
        if next_state.retries_used != current_state.retries_used:
            raise SchemaValidationError("ABSTAIN must not consume retry budget")
    else:
        if current_state.retries_used >= current_state.max_retries:
            raise SchemaValidationError("retry budget is exhausted")
        if next_state.retries_used != current_state.retries_used + 1:
            raise SchemaValidationError("every execution retry must consume one retry")
    return directive


def terminal_retry_state(state: RetryState) -> RetryState:
    """Return the schema-valid terminal copy used by terminal contracts."""
    if not isinstance(state, RetryState):
        raise SchemaValidationError("state must be a RetryState")
    return RetryState(
        retries_used=state.retries_used,
        max_retries=state.max_retries,
        current_model_tier=state.current_model_tier,
        strong_escalated=state.strong_escalated,
        terminal=True,
    )


def next_execution_retry_state(state: RetryState) -> RetryState:
    """Consume one shared retry without changing model tier."""
    if not isinstance(state, RetryState):
        raise SchemaValidationError("state must be a RetryState")
    if state.terminal:
        raise SchemaValidationError("terminal retry state cannot transition")
    if state.retries_used >= state.max_retries:
        raise SchemaValidationError("retry budget is exhausted")
    return RetryState(
        retries_used=state.retries_used + 1,
        max_retries=state.max_retries,
        current_model_tier=state.current_model_tier,
        strong_escalated=state.strong_escalated,
        terminal=False,
    )
