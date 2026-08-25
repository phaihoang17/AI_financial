"""Approved deterministic TASK-047 fixture cases."""

from __future__ import annotations

from typing import Iterable, Sequence

from src.evaluation.supervisor import (
    SUPERVISOR_EVALUATION_VERSION,
    ExpectedRetrievalRequirement,
    ExpectedSupervisorResult,
    SupervisorEvaluationCase,
)
from src.supervisor.schemas import (
    EvidenceSource,
    ModelTier,
    QuestionType,
    ReasoningMode,
    SupervisorAbstainReason,
    TableClass,
    VerifyProfile,
)
from src.understanding.planning_gate import FindingName, PlanningGate
from src.understanding.schemas import (
    CompanyUnderstanding,
    MetricUnderstanding,
    Operation,
    PeriodKind,
    PeriodUnderstanding,
    QueryUnderstanding,
    StatementScope,
    StatementScopeUnderstanding,
)


ALLOWED = PlanningGate(True, [], [])


def _understanding(
    case_id: str,
    *,
    operation: Operation = Operation.NONE,
    periods: Sequence[tuple[str, PeriodKind]] = (("2024", PeriodKind.NAM),),
    metric: str = "LNST",
    scope: StatementScope | None = StatementScope.HOP_NHAT,
    company_resolved: bool = True,
    requested_scale: str | None = None,
) -> QueryUnderstanding:
    company = (
        CompanyUnderstanding("AAA", "Fixture Company", "AAA", 1.0)
        if company_resolved
        else CompanyUnderstanding("không rõ", None, None, 0.0)
    )
    return QueryUnderstanding(
        raw_question=f"fixture:{case_id}",
        company=company,
        periods=[
            PeriodUnderstanding(value=value, kind=kind, raw=value)
            for value, kind in periods
        ],
        statement_scope=StatementScopeUnderstanding(
            value=scope,
            inferred=False,
            confidence=1.0 if scope is not None else 0.0,
        ),
        metrics=[MetricUnderstanding(metric, metric, 1.0)],
        operation=operation,
        requested_scale=requested_scale,
        requested_unit=None,
        missing_information=[] if company_resolved else ["company"],
        ambiguities=[],
        confidence=1.0,
    )


def _requirements(periods: Iterable[str]) -> list[ExpectedRetrievalRequirement]:
    return [
        ExpectedRetrievalRequirement(
            source_type=EvidenceSource.TABLE,
            table_class=TableClass.INCOME_STATEMENT,
            metric="LNST",
            period=period,
            required=True,
        )
        for period in periods
    ]


def _success(
    *,
    question_type: QuestionType,
    reasoning_mode: ReasoningMode,
    formula_id: str | None,
    periods: Sequence[str],
    model_tier: ModelTier,
    verify_profile: VerifyProfile,
) -> ExpectedSupervisorResult:
    return ExpectedSupervisorResult(
        abstain=False,
        abstain_reason=None,
        question_type=question_type,
        reasoning_mode=reasoning_mode,
        formula_id=formula_id,
        target_metrics=["LNST"],
        periods=list(periods),
        tables_needed=[TableClass.INCOME_STATEMENT],
        evidence_sources=[EvidenceSource.TABLE],
        retrieval_requirements=_requirements(periods),
        requires_scale_resolution=True,
        model_tier=model_tier,
        verify_profile=verify_profile,
    )


def _case(
    case_id: str,
    query_understanding: QueryUnderstanding,
    expected: ExpectedSupervisorResult,
    planning_gate: PlanningGate = ALLOWED,
) -> SupervisorEvaluationCase:
    return SupervisorEvaluationCase(
        case_id=case_id,
        version=SUPERVISOR_EVALUATION_VERSION,
        query_understanding=query_understanding,
        planning_gate=planning_gate,
        expected=expected,
    )


def supervisor_fixture_cases() -> list[SupervisorEvaluationCase]:
    """Return the 14 approved cases in stable source order."""
    cases: list[SupervisorEvaluationCase] = []

    case_id = "simple-lnst-lookup"
    cases.append(
        _case(
            case_id,
            _understanding(case_id),
            _success(
                question_type=QuestionType.LOOKUP,
                reasoning_mode=ReasoningMode.DIRECT,
                formula_id=None,
                periods=["2024"],
                model_tier=ModelTier.CHEAP,
                verify_profile=VerifyProfile.LIGHT,
            ),
        )
    )

    case_id = "single-period-quarter-lookup"
    cases.append(
        _case(
            case_id,
            _understanding(
                case_id,
                periods=(("2024-Q1", PeriodKind.QUY),),
                requested_scale="MILLION",
            ),
            _success(
                question_type=QuestionType.LOOKUP,
                reasoning_mode=ReasoningMode.DIRECT,
                formula_id=None,
                periods=["2024-Q1"],
                model_tier=ModelTier.CHEAP,
                verify_profile=VerifyProfile.LIGHT,
            ),
        )
    )

    case_id = "multi-period-compare"
    cases.append(
        _case(
            case_id,
            _understanding(
                case_id,
                operation=Operation.COMPARE,
                periods=(("2023", PeriodKind.NAM), ("2024", PeriodKind.NAM)),
            ),
            _success(
                question_type=QuestionType.MULTI_PERIOD,
                reasoning_mode=ReasoningMode.DIRECT,
                formula_id=None,
                periods=["2023", "2024"],
                model_tier=ModelTier.CHEAP,
                verify_profile=VerifyProfile.STRICT,
            ),
        )
    )

    case_id = "growth-rate"
    cases.append(
        _case(
            case_id,
            _understanding(
                case_id,
                operation=Operation.GROWTH,
                periods=(("2023", PeriodKind.NAM), ("2024", PeriodKind.NAM)),
            ),
            _success(
                question_type=QuestionType.MULTI_PERIOD,
                reasoning_mode=ReasoningMode.PROGRAM,
                formula_id="GROWTH_RATE",
                periods=["2023", "2024"],
                model_tier=ModelTier.STRONG,
                verify_profile=VerifyProfile.STRICT,
            ),
        )
    )

    case_id = "average"
    cases.append(
        _case(
            case_id,
            _understanding(
                case_id,
                operation=Operation.AGGREGATE,
                periods=(
                    ("2022", PeriodKind.NAM),
                    ("2023", PeriodKind.NAM),
                    ("2024", PeriodKind.NAM),
                ),
            ),
            _success(
                question_type=QuestionType.AGGREGATE,
                reasoning_mode=ReasoningMode.PROGRAM,
                formula_id="AVERAGE",
                periods=["2022", "2023", "2024"],
                model_tier=ModelTier.STRONG,
                verify_profile=VerifyProfile.STRICT,
            ),
        )
    )

    abstentions = (
        (
            "growth-wrong-period-count",
            _understanding("growth-wrong-period-count", operation=Operation.GROWTH),
            ALLOWED,
            SupervisorAbstainReason.INVALID_PERIOD_REQUIREMENT,
        ),
        (
            "aggregate-wrong-period-count",
            _understanding(
                "aggregate-wrong-period-count", operation=Operation.AGGREGATE
            ),
            ALLOWED,
            SupervisorAbstainReason.INVALID_PERIOD_REQUIREMENT,
        ),
        (
            "blocked-planning-gate",
            _understanding("blocked-planning-gate", company_resolved=False),
            PlanningGate(False, [FindingName.COMPANY], []),
            SupervisorAbstainReason.PLANNING_GATE_BLOCKED,
        ),
        (
            "unknown-operation",
            _understanding("unknown-operation", operation=Operation.UNKNOWN),
            PlanningGate(False, [], []),
            SupervisorAbstainReason.UNKNOWN_OPERATION,
        ),
        (
            "unsupported-ratio",
            _understanding(
                "unsupported-ratio", operation=Operation.RATIO, metric="ROE"
            ),
            ALLOWED,
            SupervisorAbstainReason.UNSUPPORTED_RATIO,
        ),
        (
            "missing-statement-scope",
            _understanding("missing-statement-scope", scope=None),
            ALLOWED,
            SupervisorAbstainReason.MISSING_STATEMENT_SCOPE,
        ),
        (
            "missing-table-mapping",
            _understanding("missing-table-mapping", metric="UNMAPPED"),
            ALLOWED,
            SupervisorAbstainReason.MISSING_TABLE_MAPPING,
        ),
        (
            "mixed-year-quarter-periods",
            _understanding(
                "mixed-year-quarter-periods",
                operation=Operation.COMPARE,
                periods=(
                    ("2024", PeriodKind.NAM),
                    ("2024-Q1", PeriodKind.QUY),
                ),
            ),
            ALLOWED,
            SupervisorAbstainReason.MIXED_PERIOD_KIND_UNSUPPORTED,
        ),
        (
            "mixed-quarter-cumulative-periods",
            _understanding(
                "mixed-quarter-cumulative-periods",
                operation=Operation.COMPARE,
                periods=(
                    ("2024-Q1", PeriodKind.QUY),
                    ("2024-6M", PeriodKind.LUY_KE),
                ),
            ),
            ALLOWED,
            SupervisorAbstainReason.MIXED_PERIOD_KIND_UNSUPPORTED,
        ),
    )
    cases.extend(
        _case(
            case_id,
            understanding,
            ExpectedSupervisorResult.abstention(reason),
            planning_gate,
        )
        for case_id, understanding, planning_gate, reason in abstentions
    )
    return cases
