"""Canonical M8 verification contracts and deterministic report aggregation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src.evidence.m5_schemas import BindingMap, ScaleUnitResolution, SchemaLinkResult
from src.evidence.schemas import EvidenceItem
from src.programmer.schemas import Program
from src.retrieval.evidence import CellLocation, EvidenceCompletenessResult
from src.sandbox.schemas import ExecutionResult
from src.supervisor.schemas import ModelTier, Plan, VerifyProfile
from src.understanding.schemas import SchemaValidationError


class VerificationFailureCategory(str, Enum):
    GROUNDING = "GROUNDING"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NUMERIC = "NUMERIC"
    SCALE_UNIT = "SCALE_UNIT"
    FINANCIAL_LOGIC = "FINANCIAL_LOGIC"


class RetryAction(str, Enum):
    PASS = "PASS"
    RETRY_RETRIEVAL = "RETRY_RETRIEVAL"
    RETRY_PROGRAMMER = "RETRY_PROGRAMMER"
    ESCALATE_STRONG = "ESCALATE_STRONG"
    ABSTAIN = "ABSTAIN"


FAILURE_PRECEDENCE = (
    VerificationFailureCategory.GROUNDING,
    VerificationFailureCategory.INSUFFICIENT_EVIDENCE,
    VerificationFailureCategory.NUMERIC,
    VerificationFailureCategory.SCALE_UNIT,
    VerificationFailureCategory.FINANCIAL_LOGIC,
)


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


def _enum(value: Any, enum_type: type[Enum], path: str) -> Enum:
    if not isinstance(value, enum_type):
        raise SchemaValidationError(f"{path} must be a {enum_type.__name__}")
    return value


def _parse_enum(value: Any, enum_type: type[Enum], path: str) -> Enum:
    if not isinstance(value, str):
        raise SchemaValidationError(f"{path} must be a string enum value")
    try:
        return enum_type(value)
    except ValueError as error:
        allowed = ", ".join(item.value for item in enum_type)
        raise SchemaValidationError(f"{path} must be one of: {allowed}") from error


def _typed_list(value: Any, item_type: type, path: str) -> list:
    if not isinstance(value, list) or not all(isinstance(item, item_type) for item in value):
        raise SchemaValidationError(f"{path} must contain {item_type.__name__} values")
    return list(value)


def _string_list(value: Any, path: str) -> List[str]:
    if not isinstance(value, list):
        raise SchemaValidationError(f"{path} must be a list")
    result = [_string(item, f"{path}[{index}]", non_empty=True) for index, item in enumerate(value)]
    if len(result) != len(set(result)):
        raise SchemaValidationError(f"{path} must not contain duplicates")
    return result


@dataclass(frozen=True)
class VerificationRequest:
    plan: Plan
    program: Program
    evidence_items: List[EvidenceItem]
    cell_locations: List[CellLocation]
    schema_links: List[SchemaLinkResult]
    scale_unit_resolutions: List[ScaleUnitResolution]
    binding_map: BindingMap
    evidence_completeness: EvidenceCompletenessResult
    execution_result: ExecutionResult
    verify_profile: VerifyProfile

    def __post_init__(self) -> None:
        if not isinstance(self.plan, Plan):
            raise SchemaValidationError("plan must be an executable Plan")
        if not isinstance(self.program, Program):
            raise SchemaValidationError("program must be a Program")
        object.__setattr__(self, "evidence_items", _typed_list(self.evidence_items, EvidenceItem, "evidence_items"))
        object.__setattr__(self, "cell_locations", _typed_list(self.cell_locations, CellLocation, "cell_locations"))
        object.__setattr__(self, "schema_links", _typed_list(self.schema_links, SchemaLinkResult, "schema_links"))
        object.__setattr__(
            self,
            "scale_unit_resolutions",
            _typed_list(
                self.scale_unit_resolutions,
                ScaleUnitResolution,
                "scale_unit_resolutions",
            ),
        )
        if not isinstance(self.binding_map, BindingMap):
            raise SchemaValidationError("binding_map must be a BindingMap")
        if not isinstance(self.evidence_completeness, EvidenceCompletenessResult):
            raise SchemaValidationError(
                "evidence_completeness must be an EvidenceCompletenessResult"
            )
        if not isinstance(self.execution_result, ExecutionResult):
            raise SchemaValidationError("execution_result must be an ExecutionResult")
        profile = _enum(self.verify_profile, VerifyProfile, "verify_profile")
        object.__setattr__(self, "verify_profile", profile)
        evidence_ids = [item.evidence_id for item in self.evidence_items]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise SchemaValidationError("evidence_items must have unique evidence_id values")
        location_ids = [item.location_id for item in self.cell_locations]
        if len(location_ids) != len(set(location_ids)):
            raise SchemaValidationError("cell_locations must have unique location_id values")
        resolution_ids = [
            item.evidence_id for item in self.scale_unit_resolutions
        ]
        if len(resolution_ids) != len(set(resolution_ids)):
            raise SchemaValidationError(
                "scale_unit_resolutions must have unique evidence_id values"
            )


@dataclass(frozen=True)
class VerificationCheckResult:
    check_id: str
    category: VerificationFailureCategory
    passed: bool
    reason_code: Optional[str]
    subject_ids: List[str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "check_id", _string(self.check_id, "check_id", non_empty=True))
        object.__setattr__(self, "category", _enum(self.category, VerificationFailureCategory, "category"))
        if not isinstance(self.passed, bool):
            raise SchemaValidationError("passed must be a boolean")
        object.__setattr__(self, "reason_code", _optional_string(self.reason_code, "reason_code"))
        object.__setattr__(self, "subject_ids", _string_list(self.subject_ids, "subject_ids"))
        if self.passed and self.reason_code is not None:
            raise SchemaValidationError("passed check requires null reason_code")
        if not self.passed and (
            self.reason_code is None or not self.reason_code.strip()
        ):
            raise SchemaValidationError("failed check requires a non-empty reason_code")

    @classmethod
    def from_dict(cls, value: Any) -> "VerificationCheckResult":
        data = _mapping(value, "VerificationCheckResult")
        _exact_keys(
            data,
            {"check_id", "category", "passed", "reason_code", "subject_ids"},
            "VerificationCheckResult",
        )
        return cls(
            check_id=data["check_id"],
            category=_parse_enum(
                data["category"], VerificationFailureCategory, "category"
            ),
            passed=data["passed"],
            reason_code=data["reason_code"],
            subject_ids=data["subject_ids"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "check_id": self.check_id,
            "category": self.category.value,
            "passed": self.passed,
            "reason_code": self.reason_code,
            "subject_ids": list(self.subject_ids),
        }


def aggregate_failure(
    checks: Sequence[VerificationCheckResult],
) -> tuple[Optional[VerificationFailureCategory], Optional[str]]:
    for category in FAILURE_PRECEDENCE:
        for check in checks:
            if not check.passed and check.category is category:
                return category, check.reason_code
    return None, None


@dataclass(frozen=True)
class VerificationReport:
    passed: bool
    checks: List[VerificationCheckResult]
    failure_category: Optional[VerificationFailureCategory]
    failure_reason: Optional[str]

    def __post_init__(self) -> None:
        if not isinstance(self.passed, bool):
            raise SchemaValidationError("passed must be a boolean")
        checks = _typed_list(self.checks, VerificationCheckResult, "checks")
        object.__setattr__(self, "checks", checks)
        check_ids = [check.check_id for check in checks]
        if len(check_ids) != len(set(check_ids)):
            raise SchemaValidationError("checks must have unique check_id values")
        category = self.failure_category
        if category is not None:
            category = _enum(category, VerificationFailureCategory, "failure_category")
        object.__setattr__(self, "failure_category", category)
        object.__setattr__(self, "failure_reason", _optional_string(self.failure_reason, "failure_reason"))
        expected_category, expected_reason = aggregate_failure(checks)
        if self.passed != (expected_category is None):
            raise SchemaValidationError("passed must equal the aggregate check outcome")
        if self.failure_category is not expected_category or self.failure_reason != expected_reason:
            raise SchemaValidationError(
                "failure fields must use the first failed category by canonical precedence"
            )

    @classmethod
    def create(cls, checks: Sequence[VerificationCheckResult]) -> "VerificationReport":
        category, reason = aggregate_failure(checks)
        return cls(category is None, list(checks), category, reason)

    @classmethod
    def from_dict(cls, value: Any) -> "VerificationReport":
        data = _mapping(value, "VerificationReport")
        _exact_keys(
            data,
            {"passed", "checks", "failure_category", "failure_reason"},
            "VerificationReport",
        )
        if not isinstance(data["checks"], list):
            raise SchemaValidationError("checks must be a list")
        category = data["failure_category"]
        return cls(
            passed=data["passed"],
            checks=[VerificationCheckResult.from_dict(item) for item in data["checks"]],
            failure_category=(
                None
                if category is None
                else _parse_enum(
                    category, VerificationFailureCategory, "failure_category"
                )
            ),
            failure_reason=data["failure_reason"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "checks": [check.to_dict() for check in self.checks],
            "failure_category": (
                None if self.failure_category is None else self.failure_category.value
            ),
            "failure_reason": self.failure_reason,
        }


def _non_negative_integer(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SchemaValidationError(f"{path} must be an integer")
    if value < 0:
        raise SchemaValidationError(f"{path} must be non-negative")
    return value


@dataclass(frozen=True)
class RetryState:
    retries_used: int
    max_retries: int
    current_model_tier: ModelTier
    strong_escalated: bool
    terminal: bool

    def __post_init__(self) -> None:
        retries_used = _non_negative_integer(self.retries_used, "retries_used")
        max_retries = _non_negative_integer(self.max_retries, "max_retries")
        if retries_used > max_retries:
            raise SchemaValidationError("retries_used cannot exceed max_retries")
        object.__setattr__(self, "retries_used", retries_used)
        object.__setattr__(self, "max_retries", max_retries)
        object.__setattr__(
            self,
            "current_model_tier",
            _enum(self.current_model_tier, ModelTier, "current_model_tier"),
        )
        if not isinstance(self.strong_escalated, bool):
            raise SchemaValidationError("strong_escalated must be a boolean")
        if not isinstance(self.terminal, bool):
            raise SchemaValidationError("terminal must be a boolean")
        if self.strong_escalated and self.current_model_tier is not ModelTier.STRONG:
            raise SchemaValidationError(
                "strong_escalated requires current_model_tier STRONG"
            )

    @classmethod
    def initial(cls, plan: Plan) -> "RetryState":
        if not isinstance(plan, Plan):
            raise SchemaValidationError("plan must be an executable Plan")
        return cls(
            retries_used=0,
            max_retries=plan.max_retries,
            current_model_tier=plan.model_tier,
            strong_escalated=False,
            terminal=False,
        )

    @classmethod
    def from_dict(cls, value: Any) -> "RetryState":
        data = _mapping(value, "RetryState")
        _exact_keys(
            data,
            {
                "retries_used",
                "max_retries",
                "current_model_tier",
                "strong_escalated",
                "terminal",
            },
            "RetryState",
        )
        return cls(
            retries_used=data["retries_used"],
            max_retries=data["max_retries"],
            current_model_tier=_parse_enum(
                data["current_model_tier"], ModelTier, "current_model_tier"
            ),
            strong_escalated=data["strong_escalated"],
            terminal=data["terminal"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "retries_used": self.retries_used,
            "max_retries": self.max_retries,
            "current_model_tier": self.current_model_tier.value,
            "strong_escalated": self.strong_escalated,
            "terminal": self.terminal,
        }


@dataclass(frozen=True)
class RetryDirective:
    action: RetryAction
    failure_category: Optional[VerificationFailureCategory]
    reason_code: Optional[str]
    next_state: RetryState
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "action", _enum(self.action, RetryAction, "action"))
        category = self.failure_category
        if category is not None:
            category = _enum(
                category, VerificationFailureCategory, "failure_category"
            )
        object.__setattr__(self, "failure_category", category)
        object.__setattr__(
            self, "reason_code", _optional_string(self.reason_code, "reason_code")
        )
        if not isinstance(self.next_state, RetryState):
            raise SchemaValidationError("next_state must be a RetryState")
        object.__setattr__(self, "reason", _string(self.reason, "reason", non_empty=True))
        if self.action is RetryAction.PASS:
            if self.failure_category is not None or self.reason_code is not None:
                raise SchemaValidationError(
                    "PASS requires null failure_category and reason_code"
                )
        elif self.failure_category is None or not self.reason_code:
            raise SchemaValidationError(
                "non-PASS directive requires failure_category and reason_code"
            )
        if self.action in {
            RetryAction.RETRY_RETRIEVAL,
            RetryAction.RETRY_PROGRAMMER,
            RetryAction.ESCALATE_STRONG,
        } and self.next_state.terminal:
            raise SchemaValidationError("retry directive cannot have terminal next_state")
        if self.action in {RetryAction.PASS, RetryAction.ABSTAIN} and not self.next_state.terminal:
            raise SchemaValidationError("PASS and ABSTAIN require terminal next_state")
        if self.action is RetryAction.ESCALATE_STRONG and (
            self.next_state.current_model_tier is not ModelTier.STRONG
            or not self.next_state.strong_escalated
        ):
            raise SchemaValidationError(
                "ESCALATE_STRONG requires an escalated STRONG next_state"
            )

    @classmethod
    def from_dict(cls, value: Any) -> "RetryDirective":
        data = _mapping(value, "RetryDirective")
        _exact_keys(
            data,
            {
                "action",
                "failure_category",
                "reason_code",
                "next_state",
                "reason",
            },
            "RetryDirective",
        )
        category = data["failure_category"]
        return cls(
            action=_parse_enum(data["action"], RetryAction, "action"),
            failure_category=(
                None
                if category is None
                else _parse_enum(
                    category, VerificationFailureCategory, "failure_category"
                )
            ),
            reason_code=data["reason_code"],
            next_state=RetryState.from_dict(data["next_state"]),
            reason=data["reason"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action.value,
            "failure_category": (
                None if self.failure_category is None else self.failure_category.value
            ),
            "reason_code": self.reason_code,
            "next_state": self.next_state.to_dict(),
            "reason": self.reason,
        }
