"""Pure deterministic routing policy for M4 v1 plans."""

from __future__ import annotations

from typing import Mapping, Optional, Sequence

from src.supervisor.schemas import (
    EvidenceSource,
    FormulaDefinition,
    ModelTier,
    QuestionType,
    ReasoningMode,
    RetrievalRequirement,
    VerifyProfile,
)
from src.understanding.schemas import SchemaValidationError


NUMERIC_FINANCIAL_METRIC_POLICY = "NUMERIC_FINANCIAL_METRIC"

APPROVED_EVIDENCE_POLICY_REGISTRY: Mapping[str, tuple[EvidenceSource, ...]] = {
    NUMERIC_FINANCIAL_METRIC_POLICY: (EvidenceSource.TABLE,),
}


def _ordered_requirement_sources(
    requirements: Sequence[RetrievalRequirement],
) -> list[EvidenceSource]:
    sources: list[EvidenceSource] = []
    for requirement in requirements:
        if not isinstance(requirement, RetrievalRequirement):
            raise SchemaValidationError(
                "requirements must contain RetrievalRequirement values"
            )
        if requirement.source_type not in sources:
            sources.append(requirement.source_type)
    return sources


def plan_requirement_source_types(
    *,
    formula: Optional[FormulaDefinition],
    explicit_requirements: Sequence[RetrievalRequirement] = (),
    policy_name: str = NUMERIC_FINANCIAL_METRIC_POLICY,
    policy_registry: Mapping[
        str, Sequence[EvidenceSource]
    ] = APPROVED_EVIDENCE_POLICY_REGISTRY,
) -> list[EvidenceSource]:
    """Apply formula, explicit-requirement, then approved-policy precedence."""
    if formula is not None:
        return list(formula.evidence_sources)

    explicit_sources = _ordered_requirement_sources(explicit_requirements)
    if explicit_sources:
        return explicit_sources

    try:
        policy_sources = list(policy_registry[policy_name])
    except KeyError as error:
        raise SchemaValidationError(
            f"no approved evidence policy named {policy_name!r}"
        ) from error
    if not policy_sources or any(
        not isinstance(source, EvidenceSource) for source in policy_sources
    ):
        raise SchemaValidationError(
            "approved evidence policy must contain EvidenceSource values"
        )
    if len(policy_sources) != len(set(policy_sources)):
        raise SchemaValidationError(
            "approved evidence policy sources must be deduplicated"
        )
    return policy_sources


def derive_evidence_sources(
    requirements: Sequence[RetrievalRequirement],
) -> list[EvidenceSource]:
    """Derive the exact ordered source set supported by plan requirements."""
    sources = _ordered_requirement_sources(requirements)
    if not sources:
        raise SchemaValidationError(
            "evidence sources require at least one retrieval requirement"
        )
    return sources


def route_reasoning_mode(
    question_type: QuestionType,
    *,
    formula: Optional[FormulaDefinition],
) -> ReasoningMode:
    """Route only from canonical question/formula structure; v1 disables transforms."""
    if not isinstance(question_type, QuestionType):
        raise SchemaValidationError("question_type must be a QuestionType")
    if formula is not None:
        if formula.question_type is not question_type:
            raise SchemaValidationError(
                "formula question_type must match the planned question_type"
            )
        if formula.reasoning_mode is ReasoningMode.TABLE_TRANSFORM:
            raise SchemaValidationError("TABLE_TRANSFORM is disabled in M4 v1")
        return formula.reasoning_mode
    if question_type in {QuestionType.LOOKUP, QuestionType.MULTI_PERIOD}:
        return ReasoningMode.DIRECT
    raise SchemaValidationError(
        "derived ratio and aggregate questions require a registered formula"
    )


def route_model_tier(
    reasoning_mode: ReasoningMode,
    *,
    registry_required_tier: Optional[ModelTier] = None,
) -> ModelTier:
    """Route DIRECT to CHEAP and PROGRAM to STRONG, with upgrade-only override."""
    if reasoning_mode is ReasoningMode.TABLE_TRANSFORM:
        raise SchemaValidationError("TABLE_TRANSFORM is disabled in M4 v1")
    if reasoning_mode is ReasoningMode.DIRECT:
        tier = ModelTier.CHEAP
    elif reasoning_mode is ReasoningMode.PROGRAM:
        tier = ModelTier.STRONG
    else:
        raise SchemaValidationError("reasoning_mode must be a ReasoningMode")

    if registry_required_tier is None:
        return tier
    if not isinstance(registry_required_tier, ModelTier):
        raise SchemaValidationError(
            "registry_required_tier must be a ModelTier or null"
        )
    if registry_required_tier is ModelTier.STRONG:
        return ModelTier.STRONG
    return tier


def route_verification_profile(
    question_type: QuestionType,
    reasoning_mode: ReasoningMode,
    formula_id: Optional[str],
    requirements: Sequence[RetrievalRequirement],
) -> VerifyProfile:
    """Select LIGHT only for one-requirement direct formula-free lookups."""
    required_count = sum(requirement.required for requirement in requirements)
    if (
        question_type is QuestionType.LOOKUP
        and reasoning_mode is ReasoningMode.DIRECT
        and formula_id is None
        and required_count == 1
    ):
        return VerifyProfile.LIGHT
    return VerifyProfile.STRICT


def requires_scale_resolution(
    evidence_sources: Sequence[EvidenceSource],
    *,
    requested_scale: Optional[str],
    requested_unit: Optional[str],
) -> bool:
    """Preserve the approved Batch 1 scale-resolution routing flag."""
    return (
        EvidenceSource.TABLE in evidence_sources
        or requested_scale is not None
        or requested_unit is not None
    )
