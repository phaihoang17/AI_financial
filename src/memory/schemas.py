"""Answer-free, allowlisted experience contracts for optional M11 memory."""
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256

from src.programmer.schemas import canonical_json
from src.retrieval.query_embedding import _require_sha256
from src.supervisor.schemas import Plan, QuestionType
from src.understanding.schemas import (
    PeriodKind, SchemaValidationError, _require_exact_keys, _require_mapping,
)

MEMORY_SCHEMA_VERSION = "m11-experience-v1"


class MemoryKind(str, Enum):
    STRATEGY = "STRATEGY"
    GUARD = "GUARD"


class MemoryRule(str, Enum):
    REQUIRED_INPUTS = "REQUIRED_INPUTS"
    SCALAR_FORMULA_INPUTS = "SCALAR_FORMULA_INPUTS"
    PREVIOUS_THEN_CURRENT = "PREVIOUS_THEN_CURRENT"
    COMPLETE_JSON = "COMPLETE_JSON"
    PRESERVE_SOURCE_IDS = "PRESERVE_SOURCE_IDS"


RULE_TEXT = {
    MemoryRule.REQUIRED_INPUTS: "Declare every required masked input and use it in the output dependency closure.",
    MemoryRule.SCALAR_FORMULA_INPUTS: "Pass separate scalar references directly to the registered formula; do not wrap them in COLLECT.",
    MemoryRule.PREVIOUS_THEN_CURRENT: "GROWTH_RATE input order is previous period, then current period.",
    MemoryRule.COMPLETE_JSON: "Return one complete JSON object with only the prescribed Program proposal fields.",
    MemoryRule.PRESERVE_SOURCE_IDS: "Never change evidence, report, period or placeholder identities to repair grounding.",
}


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise SchemaValidationError(f"{name} must be a non-empty string")
    return value


@dataclass(frozen=True)
class MemoryContext:
    question_type: QuestionType
    formula_id: str | None
    metrics: tuple[str, ...]
    period_kind: PeriodKind
    required_inputs: int

    def __post_init__(self):
        if not isinstance(self.question_type, QuestionType) or not isinstance(self.period_kind, PeriodKind):
            raise SchemaValidationError("memory context enums are invalid")
        if self.formula_id is not None:
            _text(self.formula_id, "formula_id")
        if not isinstance(self.metrics, tuple) or not self.metrics or len(set(self.metrics)) != len(self.metrics):
            raise SchemaValidationError("memory metrics must be a non-empty unique tuple")
        for metric in self.metrics:
            _text(metric, "metric")
        if isinstance(self.required_inputs, bool) or not isinstance(self.required_inputs, int) or self.required_inputs < 1:
            raise SchemaValidationError("required_inputs must be positive")

    @classmethod
    def from_plan(cls, plan: Plan):
        return cls(plan.question_type, plan.formula_id, tuple(plan.target_metrics),
                   plan.period_kind, sum(r.required for r in plan.retrieval_requirements))

    def compatible_with(self, other):
        return isinstance(other, MemoryContext) and (
            self.question_type, self.formula_id, self.metrics, self.period_kind
        ) == (other.question_type, other.formula_id, other.metrics, other.period_kind)

    def to_dict(self):
        return {"question_type": self.question_type.value, "formula_id": self.formula_id,
                "metrics": list(self.metrics), "period_kind": self.period_kind.value,
                "required_inputs": self.required_inputs}

    @classmethod
    def from_dict(cls, data):
        data = _require_mapping(data, "MemoryContext")
        _require_exact_keys(data, {"question_type", "formula_id", "metrics", "period_kind", "required_inputs"}, "MemoryContext")
        if not isinstance(data["metrics"], list):
            raise SchemaValidationError("metrics must be a list")
        return cls(QuestionType(data["question_type"]), data["formula_id"], tuple(data["metrics"]),
                   PeriodKind(data["period_kind"]), data["required_inputs"])


@dataclass(frozen=True)
class MemoryEntry:
    context: MemoryContext
    question_signature: str
    kind: MemoryKind
    rules: tuple[MemoryRule, ...]
    source_id: str
    split: str
    dataset_id: str
    trajectory_digest: str

    def __post_init__(self):
        if not isinstance(self.context, MemoryContext) or not isinstance(self.kind, MemoryKind):
            raise SchemaValidationError("memory context/kind is invalid")
        if not isinstance(self.rules, tuple) or not self.rules or len(set(self.rules)) != len(self.rules):
            raise SchemaValidationError("memory rules must be non-empty and unique")
        if not all(isinstance(rule, MemoryRule) for rule in self.rules):
            raise SchemaValidationError("memory rules must use the allowlist")
        if MemoryRule.PREVIOUS_THEN_CURRENT in self.rules and self.context.formula_id != "GROWTH_RATE":
            raise SchemaValidationError("growth-order advice requires GROWTH_RATE context")
        if MemoryRule.SCALAR_FORMULA_INPUTS in self.rules and self.context.formula_id is None:
            raise SchemaValidationError("formula advice requires a formula context")
        if self.split not in {"TRAIN", "DEV"}:
            raise SchemaValidationError("memory may contain only TRAIN/DEV provenance")
        for name in ("source_id", "dataset_id"):
            _text(getattr(self, name), name)
        for name in ("question_signature", "trajectory_digest"):
            _require_sha256(getattr(self, name), name)

    def to_dict(self):
        return {"schema_version": MEMORY_SCHEMA_VERSION, "context": self.context.to_dict(),
                "question_signature": self.question_signature, "kind": self.kind.value,
                "rules": [rule.value for rule in self.rules], "source_id": self.source_id,
                "split": self.split, "dataset_id": self.dataset_id,
                "trajectory_digest": self.trajectory_digest}

    @property
    def entry_id(self):
        return sha256(canonical_json(self.to_dict()).encode()).hexdigest()

    @property
    def content_signature(self):
        context = self.context.to_dict()
        context.pop("required_inputs")
        return canonical_json({"context": context, "kind": self.kind.value,
                               "rules": [r.value for r in self.rules]})

    @classmethod
    def from_dict(cls, data):
        data = _require_mapping(data, "MemoryEntry")
        _require_exact_keys(data, {"schema_version", "context", "question_signature", "kind", "rules",
                                  "source_id", "split", "dataset_id", "trajectory_digest"}, "MemoryEntry")
        if data["schema_version"] != MEMORY_SCHEMA_VERSION or not isinstance(data["rules"], list):
            raise SchemaValidationError("memory version/rules are invalid")
        return cls(MemoryContext.from_dict(data["context"]), data["question_signature"],
                   MemoryKind(data["kind"]), tuple(MemoryRule(rule) for rule in data["rules"]),
                   data["source_id"], data["split"], data["dataset_id"], data["trajectory_digest"])
