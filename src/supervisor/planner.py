"""Deterministic M4 planning from validated NLU contracts."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from src.supervisor.formula_registry import (
    FORMULA_REGISTRY,
    get_formula,
)
from src.supervisor.router import (
    derive_evidence_sources,
    plan_requirement_source_types,
    requires_scale_resolution,
    route_model_tier,
    route_reasoning_mode,
    route_verification_profile,
)
from src.supervisor.schemas import (
    EvidenceSource,
    FormulaDefinition,
    FormulaPeriodRule,
    MetricTableMapping,
    Plan,
    PlanCompany,
    QuestionType,
    RetrievalRequirement,
    SupervisorAbstainReason,
    SupervisorResult,
    TableClass,
    make_supervisor_input_fingerprint,
)
from src.supervisor.table_registry import METRIC_TABLE_REGISTRY, table_for_metric
from src.understanding.planning_gate import PlanningGate
from src.understanding.schemas import (
    Operation,
    PeriodKind,
    PeriodUnderstanding,
    QueryUnderstanding,
    SchemaValidationError,
)


class SupervisorPlanningError(ValueError):
    def __init__(self, reason: SupervisorAbstainReason, message: str) -> None:
        self.reason = reason
        super().__init__(f"{reason.value}: {message}")


@dataclass(frozen=True)
class PeriodPlan:
    periods: list[str]
    period_kind: PeriodKind


def classify_question_type(
    operation: Operation,
    *,
    ratio_formula: Optional[FormulaDefinition] = None,
) -> QuestionType:
    """TASK-040 exact operation-to-question-type mapping."""
    mapping = {
        Operation.NONE: QuestionType.LOOKUP,
        Operation.GROWTH: QuestionType.MULTI_PERIOD,
        Operation.AGGREGATE: QuestionType.AGGREGATE,
        Operation.COMPARE: QuestionType.MULTI_PERIOD,
    }
    if operation in mapping:
        return mapping[operation]
    if operation is Operation.RATIO:
        if ratio_formula is None or ratio_formula.operation is not Operation.RATIO:
            raise SupervisorPlanningError(
                SupervisorAbstainReason.UNSUPPORTED_RATIO,
                "ratio requires an explicitly registered metric-specific formula",
            )
        return QuestionType.DERIVED_RATIO
    if operation is Operation.UNKNOWN:
        raise SupervisorPlanningError(
            SupervisorAbstainReason.UNKNOWN_OPERATION, "operation is unknown"
        )
    raise SupervisorPlanningError(
        SupervisorAbstainReason.UNSUPPORTED_QUESTION_TYPE,
        "operation does not map to an approved question type",
    )


def select_formula(
    understanding: QueryUnderstanding,
    registry: Sequence[FormulaDefinition] = FORMULA_REGISTRY,
) -> Optional[FormulaDefinition]:
    """TASK-043 selects registry metadata and never authors a formula."""
    operation = understanding.operation
    if operation in {Operation.NONE, Operation.COMPARE}:
        return None
    if operation is Operation.UNKNOWN:
        raise SupervisorPlanningError(
            SupervisorAbstainReason.UNKNOWN_OPERATION, "operation is unknown"
        )
    if operation is Operation.GROWTH:
        formula = get_formula("GROWTH_RATE", registry)
    elif operation is Operation.AGGREGATE:
        formula = get_formula("AVERAGE", registry)
    elif operation is Operation.RATIO:
        requested = {metric.canonical for metric in understanding.metrics}
        matches = [
            formula
            for formula in registry
            if formula.operation is Operation.RATIO
            and formula.derived_target in requested
        ]
        if len(matches) != 1:
            raise SupervisorPlanningError(
                SupervisorAbstainReason.UNSUPPORTED_RATIO,
                "no unique registered ratio matches the requested metric",
            )
        formula = matches[0]
    else:
        formula = None
    if formula is None or formula.operation is not operation:
        raise SupervisorPlanningError(
            SupervisorAbstainReason.MISSING_FORMULA,
            "the approved registry has no formula for this operation",
        )
    return formula


def validate_formula_id(
    formula_id: str,
    registry: Sequence[FormulaDefinition] = FORMULA_REGISTRY,
) -> FormulaDefinition:
    formula = get_formula(formula_id, registry)
    if formula is None:
        raise SupervisorPlanningError(
            SupervisorAbstainReason.MISSING_FORMULA,
            f"formula_id {formula_id!r} is not registered",
        )
    return formula


def plan_required_periods(
    requested: Sequence[PeriodUnderstanding],
    *,
    operation: Operation,
    formula: Optional[FormulaDefinition],
) -> PeriodPlan:
    """TASK-042 validates source periods without deriving a missing date."""
    if not requested:
        raise SupervisorPlanningError(
            SupervisorAbstainReason.INVALID_PERIOD_REQUIREMENT,
            "at least one requested period is required",
        )
    kinds = {period.kind for period in requested}
    if len(kinds) != 1:
        raise SupervisorPlanningError(
            SupervisorAbstainReason.MIXED_PERIOD_KIND_UNSUPPORTED,
            "M4 v1 requires homogeneous requested period kinds",
        )
    periods: list[str] = []
    for period in requested:
        if period.value not in periods:
            periods.append(period.value)
    rule = formula.period_rule if formula is not None else None
    valid = True
    if rule is FormulaPeriodRule.SINGLE:
        valid = len(periods) == 1
    elif rule is FormulaPeriodRule.EXACT_TWO:
        valid = len(periods) == 2
    elif rule is FormulaPeriodRule.AT_LEAST_TWO:
        valid = len(periods) >= 2
    elif operation is Operation.COMPARE:
        valid = len(periods) >= 2
    if not valid:
        raise SupervisorPlanningError(
            SupervisorAbstainReason.INVALID_PERIOD_REQUIREMENT,
            "requested periods do not satisfy the approved period rule",
        )
    return PeriodPlan(periods, next(iter(kinds)))


def generate_retrieval_requirements(
    understanding: QueryUnderstanding,
    period_plan: PeriodPlan,
    *,
    formula: Optional[FormulaDefinition],
    table_registry: Sequence[MetricTableMapping] = METRIC_TABLE_REGISTRY,
) -> list[RetrievalRequirement]:
    """Generate explicit ordered requirements from supported semantics."""
    requested_metrics = [metric.canonical for metric in understanding.metrics]
    if formula is not None and formula.operation is Operation.RATIO:
        metrics = list(formula.required_metrics)
    else:
        metrics = requested_metrics
    if formula is not None and formula.operation is Operation.GROWTH and len(metrics) != 1:
        raise SupervisorPlanningError(
            SupervisorAbstainReason.INVALID_PLAN,
            "GROWTH_RATE v1 requires exactly one requested target metric",
        )
    requirements: list[RetrievalRequirement] = []
    sources = plan_requirement_source_types(formula=formula)
    for source in sources:
        if source is EvidenceSource.TABLE:
            for metric in metrics:
                table_class = table_for_metric(metric, table_registry)
                if table_class is None:
                    raise SupervisorPlanningError(
                        SupervisorAbstainReason.MISSING_TABLE_MAPPING,
                        f"no approved table mapping for metric {metric!r}",
                    )
                for period in period_plan.periods:
                    requirements.append(
                        RetrievalRequirement.create(
                            EvidenceSource.TABLE, table_class, metric, period
                        )
                    )
        else:
            requirements.append(
                RetrievalRequirement.create(source, None, None, None)
            )
    if not requirements:
        raise SupervisorPlanningError(
            SupervisorAbstainReason.INVALID_PLAN,
            "supported planning semantics produced no retrieval requirements",
        )
    if len({item.requirement_id for item in requirements}) != len(requirements):
        raise SupervisorPlanningError(
            SupervisorAbstainReason.INVALID_PLAN,
            "planning semantics produced duplicate retrieval requirements",
        )
    return requirements


def derive_tables_needed(
    requirements: Sequence[RetrievalRequirement],
) -> list[TableClass]:
    """TASK-041 derives table classes in first-occurrence order."""
    result: list[TableClass] = []
    for requirement in requirements:
        if requirement.table_class is not None and requirement.table_class not in result:
            result.append(requirement.table_class)
    if any(
        requirement.source_type is EvidenceSource.TABLE
        and requirement.table_class is None
        for requirement in requirements
    ):
        raise SupervisorPlanningError(
            SupervisorAbstainReason.MISSING_TABLE_MAPPING,
            "TABLE requirement is missing a table class",
        )
    return result


def _first_occurrence(values):
    result = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _abstain(
    gate: PlanningGate,
    fingerprint: str,
    reason: SupervisorAbstainReason,
) -> SupervisorResult:
    return SupervisorResult(gate, None, True, reason, fingerprint)


def supervise_batch1(
    understanding: QueryUnderstanding,
    planning_gate: PlanningGate,
    *,
    formula_registry: Sequence[FormulaDefinition] = FORMULA_REGISTRY,
    table_registry: Sequence[MetricTableMapping] = METRIC_TABLE_REGISTRY,
) -> SupervisorResult:
    """TASK-044 produces either one validated Plan or a typed abstention."""
    if not isinstance(understanding, QueryUnderstanding):
        raise TypeError("understanding must be a QueryUnderstanding")
    if not isinstance(planning_gate, PlanningGate):
        raise TypeError("planning_gate must be a PlanningGate")
    fingerprint = make_supervisor_input_fingerprint(
        understanding.to_dict(), planning_gate
    )
    if not planning_gate.allowed:
        reason = (
            SupervisorAbstainReason.UNKNOWN_OPERATION
            if understanding.operation is Operation.UNKNOWN
            else SupervisorAbstainReason.PLANNING_GATE_BLOCKED
        )
        return _abstain(planning_gate, fingerprint, reason)
    if understanding.statement_scope.value is None:
        return _abstain(
            planning_gate,
            fingerprint,
            SupervisorAbstainReason.MISSING_STATEMENT_SCOPE,
        )
    try:
        formula = select_formula(understanding, formula_registry)
        question_type = classify_question_type(
            understanding.operation,
            ratio_formula=formula if understanding.operation is Operation.RATIO else None,
        )
        period_plan = plan_required_periods(
            understanding.periods,
            operation=understanding.operation,
            formula=formula,
        )
        requirements = generate_retrieval_requirements(
            understanding,
            period_plan,
            formula=formula,
            table_registry=table_registry,
        )
        tables_needed = derive_tables_needed(requirements)
        evidence_sources = derive_evidence_sources(requirements)
        target_metrics = _first_occurrence(
            requirement.metric
            for requirement in requirements
            if requirement.metric is not None
        )
        periods = _first_occurrence(
            requirement.period
            for requirement in requirements
            if requirement.period is not None
        )
        reasoning_mode = route_reasoning_mode(question_type, formula=formula)
        model_tier = route_model_tier(reasoning_mode)
        verify_profile = route_verification_profile(
            question_type,
            reasoning_mode,
            None if formula is None else formula.formula_id,
            requirements,
        )
        is_lookup = question_type is QuestionType.LOOKUP
        plan = Plan(
            question_type=question_type,
            company=PlanCompany(
                name=understanding.company.name,
                ticker=understanding.company.ticker,
            ),
            periods=periods,
            period_kind=period_plan.period_kind,
            statement_scope=understanding.statement_scope.value,
            target_metrics=target_metrics,
            derived_target=None if formula is None else formula.derived_target,
            formula_id=None if formula is None else formula.formula_id,
            tables_needed=tables_needed,
            retrieval_requirements=requirements,
            evidence_sources=evidence_sources,
            reasoning_mode=reasoning_mode,
            requires_scale_resolution=requires_scale_resolution(
                evidence_sources,
                requested_scale=understanding.requested_scale,
                requested_unit=understanding.requested_unit,
            ),
            model_tier=model_tier,
            verify_profile=verify_profile,
            max_retries=1 if is_lookup else 2,
            confidence=understanding.confidence,
            abstain=False,
            abstain_reason=None,
        )
        return SupervisorResult(planning_gate, plan, False, None, fingerprint)
    except SupervisorPlanningError as error:
        return _abstain(planning_gate, fingerprint, error.reason)
    except (SchemaValidationError, TypeError, ValueError):
        return _abstain(
            planning_gate, fingerprint, SupervisorAbstainReason.INVALID_PLAN
        )
