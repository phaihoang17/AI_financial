"""Typed, inert stage ports for M9 orchestration.

The protocols define worker boundaries only.  They deliberately contain no
LangGraph nodes, routing decisions, retries, or business logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from types import MappingProxyType
from typing import Any, Dict, Mapping, Protocol, Sequence, runtime_checkable

from src.evidence.m5_schemas import BindingMap
from src.evidence.schemas import EvidenceItem
from src.orchestration.schemas import (
    make_plan_fingerprint,
    make_retrieval_policy_fingerprint,
)
from src.programmer.schemas import ProgrammerInput, ProgrammerResult
from src.retrieval.evidence import CellLocation, EvidenceCompletenessResult
from src.retrieval.schemas import (
    RetrievedScaleUnitHint,
    RetrievalCandidate,
    RetrievalQuery,
)
from src.sandbox.schemas import ExecutionResult, SandboxExecutionRequest
from src.supervisor.schemas import ModelTier, Plan
from src.understanding.planning_gate import PlanningGate
from src.understanding.schemas import QueryUnderstanding, SchemaValidationError


def _non_empty(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SchemaValidationError(f"{path} must be a non-empty string")
    return value


def _positive_int(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SchemaValidationError(f"{path} must be a positive integer")
    return value


@dataclass(frozen=True)
class NLUStageOutput:
    """Validated output of the NLU boundary."""

    query_understanding: QueryUnderstanding
    planning_gate: PlanningGate

    def __post_init__(self) -> None:
        if not isinstance(self.query_understanding, QueryUnderstanding):
            raise SchemaValidationError(
                "query_understanding must be a QueryUnderstanding"
            )
        if not isinstance(self.planning_gate, PlanningGate):
            raise SchemaValidationError("planning_gate must be a PlanningGate")
        object.__setattr__(
            self,
            "query_understanding",
            QueryUnderstanding.from_dict(self.query_understanding.to_dict()),
        )
        object.__setattr__(
            self,
            "planning_gate",
            PlanningGate.from_dict(self.planning_gate.to_dict()),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query_understanding": self.query_understanding.to_dict(),
            "planning_gate": self.planning_gate.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "NLUStageOutput":
        if not isinstance(value, Mapping):
            raise SchemaValidationError("NLUStageOutput must be an object")
        expected = {"query_understanding", "planning_gate"}
        if set(value) != expected:
            raise SchemaValidationError(
                "NLUStageOutput requires exactly query_understanding and planning_gate"
            )
        return cls(
            query_understanding=QueryUnderstanding.from_dict(
                value["query_understanding"]
            ),
            planning_gate=PlanningGate.from_dict(value["planning_gate"]),
        )


@dataclass(frozen=True)
class RetrievalStageRequest:
    """Immutable retrieval policy passed across the retrieval port."""

    plan: Plan
    query: RetrievalQuery
    top_k: int
    artifact_versions: Mapping[str, str]
    plan_fingerprint: str
    retrieval_policy_fingerprint: str
    _content_fingerprint: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.plan, Plan):
            raise SchemaValidationError("plan must be an executable Plan")
        if not isinstance(self.query, RetrievalQuery):
            raise SchemaValidationError("query must be a RetrievalQuery")
        plan = Plan.from_dict(self.plan.to_dict())
        query = RetrievalQuery.from_dict(self.query.to_dict())
        top_k = _positive_int(self.top_k, "top_k")
        if not isinstance(self.artifact_versions, Mapping) or not self.artifact_versions:
            raise SchemaValidationError(
                "artifact_versions must be a non-empty mapping"
            )
        versions = {
            _non_empty(key, "artifact_versions key"): _non_empty(
                item, f"artifact_versions[{key!r}]"
            )
            for key, item in sorted(self.artifact_versions.items())
        }
        if self.plan_fingerprint != make_plan_fingerprint(plan):
            raise SchemaValidationError(
                "plan_fingerprint does not match retrieval Plan"
            )
        expected_policy = make_retrieval_policy_fingerprint(
            plan,
            query,
            top_k=top_k,
            artifact_versions=versions,
        )
        if self.retrieval_policy_fingerprint != expected_policy:
            raise SchemaValidationError(
                "retrieval_policy_fingerprint does not match immutable policy"
            )
        object.__setattr__(self, "plan", plan)
        object.__setattr__(self, "query", query)
        object.__setattr__(self, "top_k", top_k)
        object.__setattr__(
            self, "artifact_versions", MappingProxyType(versions)
        )
        object.__setattr__(
            self,
            "_content_fingerprint",
            self._current_content_fingerprint(),
        )

    def _current_content_fingerprint(self) -> str:
        payload = {
            "plan": self.plan.to_dict(),
            "query": self.query.to_dict(),
            "top_k": self.top_k,
            "artifact_versions": dict(self.artifact_versions),
            "plan_fingerprint": self.plan_fingerprint,
            "retrieval_policy_fingerprint": self.retrieval_policy_fingerprint,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return sha256(encoded).hexdigest()

    def assert_immutable(self) -> None:
        if self._current_content_fingerprint() != self._content_fingerprint:
            raise SchemaValidationError(
                "RetrievalStageRequest was mutated after creation"
            )


@dataclass(frozen=True)
class RetrievalArtifacts:
    """Retrieval/evidence artifacts returned without executing later stages."""

    query: RetrievalQuery
    candidates: tuple[RetrievalCandidate, ...] = ()
    scale_hints_by_candidate: Mapping[
        str, tuple[RetrievedScaleUnitHint, ...]
    ] = field(default_factory=dict)
    cell_locations: tuple[CellLocation, ...] = ()
    evidence_items: tuple[EvidenceItem, ...] = ()
    evidence_completeness: EvidenceCompletenessResult | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.query, RetrievalQuery):
            raise SchemaValidationError("query must be a RetrievalQuery")
        object.__setattr__(
            self, "query", RetrievalQuery.from_dict(self.query.to_dict())
        )
        typed_sequences = (
            ("candidates", RetrievalCandidate),
            ("cell_locations", CellLocation),
            ("evidence_items", EvidenceItem),
        )
        for field_name, contract_type in typed_sequences:
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
            raise SchemaValidationError(
                "scale_hints_by_candidate must be a mapping"
            )
        hints = {}
        for candidate_id, values in sorted(
            self.scale_hints_by_candidate.items()
        ):
            candidate_id = _non_empty(
                candidate_id, "scale_hints_by_candidate key"
            )
            if not isinstance(values, (list, tuple)) or not all(
                isinstance(item, RetrievedScaleUnitHint) for item in values
            ):
                raise SchemaValidationError(
                    "scale_hints_by_candidate values must contain "
                    "RetrievedScaleUnitHint"
                )
            hints[candidate_id] = tuple(
                RetrievedScaleUnitHint.from_dict(item.to_dict()) for item in values
            )
        object.__setattr__(
            self, "scale_hints_by_candidate", MappingProxyType(hints)
        )
        if self.evidence_completeness is not None:
            if not isinstance(
                self.evidence_completeness, EvidenceCompletenessResult
            ):
                raise SchemaValidationError(
                    "evidence_completeness must be an "
                    "EvidenceCompletenessResult or null"
                )
            object.__setattr__(
                self,
                "evidence_completeness",
                EvidenceCompletenessResult.from_dict(
                    self.evidence_completeness.to_dict()
                ),
            )


@runtime_checkable
class NLUStagePort(Protocol):
    def understand(self, raw_question: str) -> NLUStageOutput:
        """Return validated understanding and its deterministic planning gate."""


@runtime_checkable
class RetrievalStagePort(Protocol):
    def retrieve(self, request: RetrievalStageRequest) -> RetrievalArtifacts:
        """Retrieve under the exact immutable policy carried by request."""


@runtime_checkable
class CellLocatorStagePort(Protocol):
    """Injected adapter over the existing deterministic M3 cell locator."""

    def locate(
        self,
        query: RetrievalQuery,
        candidates: Sequence[RetrievalCandidate],
    ) -> Sequence[CellLocation]:
        """Locate source-backed cells without adding Plan-derived provenance."""


@runtime_checkable
class EvidenceBuilderStagePort(Protocol):
    """Injected adapter over the existing deterministic M3 EvidenceItem builder."""

    def build(
        self,
        locations: Sequence[CellLocation],
        candidates: Sequence[RetrievalCandidate],
    ) -> Sequence[EvidenceItem]:
        """Build exact source-backed evidence without retrieval widening."""


@runtime_checkable
class ProgrammerStagePort(Protocol):
    def generate(
        self, programmer_input: ProgrammerInput, model_tier: ModelTier
    ) -> ProgrammerResult:
        """Generate from symbolic ProgrammerInput; no BindingMap is exposed."""


@runtime_checkable
class SandboxStagePort(Protocol):
    def execute(self, request: SandboxExecutionRequest) -> ExecutionResult:
        """Execute a validated request in the existing sandbox boundary."""


@runtime_checkable
class BindingStorePort(Protocol):
    """Process-local binding capability; implementations must not checkpoint values."""

    def put(self, binding_map: BindingMap) -> str:
        """Store a BindingMap and return a new opaque process-local reference."""

    def resolve(self, binding_ref: str) -> BindingMap:
        """Resolve only for Sandbox/Verification integration code."""

    def contains(self, binding_ref: str) -> bool:
        """Return whether the process-local reference can still be resolved."""

    def discard(self, binding_ref: str) -> None:
        """Discard the process-local reference."""
