"""Unit tests for TASK-06C bounded table transform planner."""

import pytest

from src.evidence.m5_schemas import make_value_placeholder
from src.orchestration.schemas import EvaluationFailureStage
from src.programmer.schemas import ProgramOperation
from src.supervisor.schemas import (
    EvidenceSource,
    Plan,
    QuestionType,
    ReasoningMode,
    RetrievalRequirement,
    StatementScope,
    TableClass,
)
from src.table_transform.planner import (
    is_terminal_state,
    plan_and_transform,
    TableTransformStatus,
)
from src.table_transform.schemas import (
    TableCell,
    TableRow,
    TableState,
    TableTransformFailureCode,
)
from src.understanding.schemas import (
    CompanyUnderstanding,
    MetricUnderstanding,
    Operation,
    PeriodKind,
    PeriodUnderstanding,
    QueryUnderstanding,
    StatementScopeUnderstanding,
)


def _cell(cell_id: str, metric: str, period: str, req_id: str) -> TableCell:
    ev_id = f"ev_{cell_id}"
    return TableCell(
        cell_id=cell_id,
        value=make_value_placeholder(ev_id),
        is_placeholder=True,
        evidence_id=ev_id,
        requirement_id=req_id,
        metric=metric,
        period=period,
    )


def _make_lookup_plan(metric="LNST", period="2022") -> Plan:
    req = RetrievalRequirement.create(
        source_type=EvidenceSource.TABLE,
        table_class=TableClass.INCOME_STATEMENT,
        metric=metric,
        period=period,
    )
    return Plan.from_dict({
        "question_type": "LOOKUP",
        "company": {"name": "Test Company", "ticker": "AAA"},
        "periods": [period],
        "period_kind": "NAM",
        "statement_scope": "HOP_NHAT",
        "target_metrics": [metric],
        "derived_target": None,
        "formula_id": None,
        "tables_needed": ["INCOME_STATEMENT"],
        "retrieval_requirements": [req.to_dict()],
        "evidence_sources": ["TABLE"],
        "reasoning_mode": "DIRECT",
        "requires_scale_resolution": True,
        "model_tier": "CHEAP",
        "verify_profile": "LIGHT",
        "max_retries": 1,
        "confidence": 1.0,
        "abstain": False,
        "abstain_reason": None,
    })


def _make_query_understanding(metric="LNST", period="2022") -> QueryUnderstanding:
    return QueryUnderstanding(
        raw_question=f"{metric} năm {period}",
        company=CompanyUnderstanding(raw="AAA", ticker="AAA", name="Test Company", confidence=1.0),
        periods=[PeriodUnderstanding(raw=period, value=period, kind=PeriodKind.NAM)],
        statement_scope=StatementScopeUnderstanding(value=StatementScope.HOP_NHAT, confidence=1.0, inferred=False),
        metrics=[MetricUnderstanding(raw=metric, canonical=metric, confidence=1.0)],
        operation=Operation.NONE,
        requested_scale=None,
        requested_unit=None,
        missing_information=[],
        ambiguities=[],
        confidence=1.0,
    )


def _make_complex_table(req_id: str) -> TableState:
    # Table with 3 rows (Doanh thu, Chi phí, LNST) and 3 columns (2020, 2021, 2022)
    r1 = TableRow(
        row_id="r1",
        row_label="LNST",
        cells={
            "2020": _cell("c_dt_20", "LNST", "2020", req_id),
            "2021": _cell("c_dt_21", "LNST", "2021", req_id),
            "2022": _cell("c_dt_22", "LNST", "2022", req_id),
        },
    )
    r2 = TableRow(
        row_id="r2",
        row_label="Chi phí",
        cells={
            "2020": _cell("c_cp_20", "Chi phí", "2020", "other_req"),
            "2021": _cell("c_cp_21", "Chi phí", "2021", "other_req"),
            "2022": _cell("c_cp_22", "Chi phí", "2022", "other_req"),
        },
    )
    r3 = TableRow(
        row_id="r3",
        row_label="Doanh thu",
        cells={
            "2020": _cell("c_ln_20", "Doanh thu", "2020", "other_req2"),
            "2021": _cell("c_ln_21", "Doanh thu", "2021", "other_req2"),
            "2022": _cell("c_ln_22", "Doanh thu", "2022", "other_req2"),
        },
    )
    return TableState.create(
        table_id="tab_complex",
        columns=["2020", "2021", "2022"],
        rows=[r1, r2, r3],
    )


class TestTableTransformPlanner:
    def test_minimal_steps_to_terminal_lookup(self):
        plan = _make_lookup_plan("LNST", "2022")
        req_id = plan.retrieval_requirements[0].requirement_id
        table = _make_complex_table(req_id)
        query = _make_query_understanding("LNST", "2022")

        # Initial state is not terminal
        assert not is_terminal_state(table, plan)

        result = plan_and_transform(table, query, plan, max_steps=5)
        assert result.status is TableTransformStatus.SUCCESS
        assert result.step_count == 2
        assert result.terminal_state is not None
        assert result.terminal_state.columns == ["2022"]
        assert len(result.terminal_state.rows) == 1
        assert result.terminal_state.rows[0].row_label == "LNST"

        # Check synthesized Program
        assert result.program is not None
        assert result.program.steps[0].operation is ProgramOperation.IDENTITY

    def test_hard_max_steps_cutoff(self):
        plan = _make_lookup_plan("LNST", "2022")
        req_id = plan.retrieval_requirements[0].requirement_id
        table = _make_complex_table(req_id)
        query = _make_query_understanding("LNST", "2022")

        # Table requires 2 steps (select rows, then select columns)
        # Limiting max_steps to 1 forces MAX_STEPS_EXCEEDED failure
        result = plan_and_transform(table, query, plan, max_steps=1)
        assert result.status is TableTransformStatus.FAILED
        assert result.failure_code == TableTransformFailureCode.MAX_STEPS_EXCEEDED.value
        assert result.failure_attribution is not None
        assert result.failure_attribution.stage is EvaluationFailureStage.PROGRAMMER
        assert result.failure_attribution.code == TableTransformFailureCode.MAX_STEPS_EXCEEDED.value

    def test_terminal_state_unreachable(self):
        # Querying a metric not present in table - using custom plan without mapping check failure
        req = RetrievalRequirement.create(EvidenceSource.TABLE, TableClass.INCOME_STATEMENT, "LNST", "2022")
        plan = Plan.from_dict({
            "question_type": "LOOKUP",
            "company": {"name": "Test Company", "ticker": "AAA"},
            "periods": ["2022"],
            "period_kind": "NAM",
            "statement_scope": "HOP_NHAT",
            "target_metrics": ["LNST"],
            "derived_target": None,
            "formula_id": None,
            "tables_needed": ["INCOME_STATEMENT"],
            "retrieval_requirements": [req.to_dict()],
            "evidence_sources": ["TABLE"],
            "reasoning_mode": "DIRECT",
            "requires_scale_resolution": True,
            "model_tier": "CHEAP",
            "verify_profile": "LIGHT",
            "max_retries": 1,
            "confidence": 1.0,
            "abstain": False,
            "abstain_reason": None,
        })
        # Create table with NO LNST row
        r1 = TableRow("r1", "Chi phí", {"2022": _cell("c1", "Chi phí", "2022", "req1")})
        table = TableState.create("tab_no_lnst", ["2022"], [r1])
        query = _make_query_understanding("LNST", "2022")

        result = plan_and_transform(table, query, plan, max_steps=5)
        assert result.status is TableTransformStatus.FAILED
        assert result.failure_code == TableTransformFailureCode.TERMINAL_STATE_UNREACHABLE.value
        assert result.failure_attribution is not None
        assert result.failure_attribution.stage is EvaluationFailureStage.PROGRAMMER

    def test_growth_rate_multi_period_plan(self):
        # Multi-period growth rate plan
        req1 = RetrievalRequirement.create(EvidenceSource.TABLE, TableClass.INCOME_STATEMENT, "LNST", "2021")
        req2 = RetrievalRequirement.create(EvidenceSource.TABLE, TableClass.INCOME_STATEMENT, "LNST", "2022")
        plan = Plan.from_dict({
            "question_type": "MULTI_PERIOD",
            "company": {"name": "Test Company", "ticker": "AAA"},
            "periods": ["2021", "2022"],
            "period_kind": "NAM",
            "statement_scope": "HOP_NHAT",
            "target_metrics": ["LNST"],
            "derived_target": "GROWTH",
            "formula_id": "GROWTH_RATE",
            "tables_needed": ["INCOME_STATEMENT"],
            "retrieval_requirements": [req1.to_dict(), req2.to_dict()],
            "evidence_sources": ["TABLE"],
            "reasoning_mode": "PROGRAM",
            "requires_scale_resolution": True,
            "model_tier": "STRONG",
            "verify_profile": "STRICT",
            "max_retries": 2,
            "confidence": 1.0,
            "abstain": False,
            "abstain_reason": None,
        })

        # Table with 2020, 2021, 2022 and extra row
        r1 = TableRow(
            row_id="r1",
            row_label="LNST",
            cells={
                "2020": _cell("c_ln_20", "LNST", "2020", "other"),
                "2021": _cell("c_ln_21", "LNST", "2021", req1.requirement_id),
                "2022": _cell("c_ln_22", "LNST", "2022", req2.requirement_id),
            },
        )
        r2 = TableRow(
            row_id="r2",
            row_label="Chi phí",
            cells={
                "2020": _cell("c_cp_20", "Chi phí", "2020", "other"),
                "2021": _cell("c_cp_21", "Chi phí", "2021", "other"),
                "2022": _cell("c_cp_22", "Chi phí", "2022", "other"),
            },
        )
        table = TableState.create("t_growth", ["2020", "2021", "2022"], [r1, r2])
        query = _make_query_understanding("LNST", "2022")

        result = plan_and_transform(table, query, plan, max_steps=5)
        assert result.status is TableTransformStatus.SUCCESS
        assert result.program is not None
        assert result.program.formula_id == "GROWTH_RATE"
        assert result.program.steps[0].operation is ProgramOperation.APPLY_REGISTERED_FORMULA

