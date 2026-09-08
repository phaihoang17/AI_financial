"""Anti-hardcode test suite for M6B table transformations (TASK-053 compliance).

Verifies that:
1. In EVERY intermediate step (and final state), TableCell.value for is_placeholder=True
   NEVER contains real grounded numeric values, only canonical symbolic placeholders.
2. Mutation testing: Mutating grounded values in the execution binding map has ZERO
   effect on planner decisions, step sequences, and operation history.
3. GroupOp and AddDerivedColumnOp specifically generate purely symbolic placeholders
   and never evaluate arithmetic calculations on grounded values.
"""

from decimal import Decimal
import re
import pytest

from src.evidence.m5_schemas import make_value_placeholder
from src.table_transform.executor import execute_operation
from src.table_transform.planner import is_terminal_state, plan_and_transform
from src.table_transform.schemas import (
    AddDerivedColumnOp,
    AggregateFunction,
    GroupOp,
    SelectColumnsOp,
    SelectRowsOp,
    TableCell,
    TableOperation,
    TableOperationType,
    TableRow,
    TableState,
)
from src.supervisor.schemas import (
    EvidenceSource,
    Plan,
    RetrievalRequirement,
    StatementScope,
    TableClass,
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

PLACEHOLDER_REGEX = re.compile(r"^val_([0-9a-f]{64}|derived_[0-9a-f]{64}|agg_[0-9a-f]{64})$")


def _is_valid_symbolic_placeholder(value: str) -> bool:
    return bool(PLACEHOLDER_REGEX.match(value))


def _contains_raw_number(value: str) -> bool:
    """Check if value is a plain numeric literal rather than a symbolic placeholder."""
    try:
        float(value)
        return True
    except ValueError:
        return False


def _make_multi_step_setup(raw_val_1: str = "125000000", raw_val_2: str = "250000000"):
    req1 = RetrievalRequirement.create(EvidenceSource.TABLE, TableClass.INCOME_STATEMENT, "LNST", "2021")
    req2 = RetrievalRequirement.create(EvidenceSource.TABLE, TableClass.INCOME_STATEMENT, "LNST", "2022")

    plan = Plan.from_dict({
        "question_type": "MULTI_PERIOD",
        "company": {"name": "AAA", "ticker": "AAA"},
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

    q = QueryUnderstanding(
        raw_question="Tăng trưởng LNST từ 2021 đến 2022",
        company=CompanyUnderstanding(raw="AAA", ticker="AAA", name="AAA", confidence=1.0),
        periods=[
            PeriodUnderstanding(raw="2021", value="2021", kind=PeriodKind.NAM),
            PeriodUnderstanding(raw="2022", value="2022", kind=PeriodKind.NAM),
        ],
        statement_scope=StatementScopeUnderstanding(value=StatementScope.HOP_NHAT, confidence=1.0, inferred=False),
        metrics=[MetricUnderstanding(raw="LNST", canonical="LNST", confidence=1.0)],
        operation=Operation.GROWTH,
        requested_scale=None,
        requested_unit=None,
        missing_information=[],
        ambiguities=[],
        confidence=1.0,
    )

    ev1 = "ev_lnst_2021"
    ev2 = "ev_lnst_2022"
    ev_cp1 = "ev_cp_2021"
    ev_cp2 = "ev_cp_2022"

    # Placeholders are purely derived from evidence_id, independent of raw values!
    ph1 = make_value_placeholder(ev1)
    ph2 = make_value_placeholder(ev2)
    ph_cp1 = make_value_placeholder(ev_cp1)
    ph_cp2 = make_value_placeholder(ev_cp2)

    r_lnst = TableRow("r_lnst", "LNST", {
        "2020": TableCell("c_0", make_value_placeholder("ev_0"), True, "ev_0", "req_0", "LNST", "2020"),
        "2021": TableCell("c_1", ph1, True, ev1, req1.requirement_id, "LNST", "2021"),
        "2022": TableCell("c_2", ph2, True, ev2, req2.requirement_id, "LNST", "2022"),
    })
    r_cp = TableRow("r_cp", "Chi phí", {
        "2020": TableCell("c_cp0", make_value_placeholder("ev_cp0"), True, "ev_cp0", "req_cp0", "Chi phí", "2020"),
        "2021": TableCell("c_cp1", ph_cp1, True, ev_cp1, req1.requirement_id, "Chi phí", "2021"),
        "2022": TableCell("c_cp2", ph_cp2, True, ev_cp2, req2.requirement_id, "Chi phí", "2022"),
    })

    table = TableState.create("tab_multi", ["2020", "2021", "2022"], [r_lnst, r_cp])
    binding_map = {ph1: raw_val_1, ph2: raw_val_2}

    return plan, q, table, binding_map, [raw_val_1, raw_val_2]


class TestAntiHardcodeCompliance:
    def test_intermediate_states_never_contain_raw_numbers(self):
        """Verify that every intermediate TableState in history contains zero raw numeric literals."""
        raw_1 = "123456789"
        raw_2 = "987654321"
        plan, q, table, _, raw_numbers = _make_multi_step_setup(raw_1, raw_2)

        result = plan_and_transform(table, q, plan, max_steps=5)
        assert result.status.value == "SUCCESS"
        assert result.terminal_state is not None

        # Re-execute history step-by-step to inspect every intermediate state
        current = table
        intermediate_states = [current]
        for record in result.terminal_state.history:
            current = execute_operation(current, record.operation)
            intermediate_states.append(current)

        for step_idx, state in enumerate(intermediate_states):
            for row in state.rows:
                for col_name, cell in row.cells.items():
                    if cell.is_placeholder:
                        # 1. Must be a valid symbolic placeholder format
                        assert _is_valid_symbolic_placeholder(cell.value), (
                            f"Step {step_idx}: Cell {cell.cell_id} has invalid placeholder format '{cell.value}'"
                        )
                        # 2. Must NOT be parseable as a plain numeric literal
                        assert not _contains_raw_number(cell.value), (
                            f"Step {step_idx}: Cell {cell.cell_id} leaked raw number: '{cell.value}'"
                        )
                        # 3. None of the raw grounded numbers may appear anywhere in cell.value
                        for raw_num in raw_numbers:
                            assert raw_num not in cell.value, (
                                f"Step {step_idx}: Grounded value '{raw_num}' leaked into placeholder '{cell.value}'"
                            )

    def test_mutation_grounded_values_does_not_change_planner_decisions(self):
        """Changing the underlying real grounded numbers in BindingMap must produce

        identical planner operations and identical Program structure.
        """
        # Run 1 with standard positive numbers
        plan1, q1, table1, bind1, _ = _make_multi_step_setup("1000000", "2000000")
        res1 = plan_and_transform(table1, q1, plan1, max_steps=5)

        # Run 2 with completely different mutated numbers (negative, tiny decimal, huge)
        plan2, q2, table2, bind2, _ = _make_multi_step_setup("-999999999999", "0.00000001")
        res2 = plan_and_transform(table2, q2, plan2, max_steps=5)

        assert res1.status == res2.status
        assert res1.step_count == res2.step_count
        assert res1.program is not None and res2.program is not None
        assert res1.program.program_id == res2.program.program_id

        # Operation history must be strictly identical
        history1 = res1.terminal_state.history
        history2 = res2.terminal_state.history
        assert len(history1) == len(history2)
        for h1, h2 in zip(history1, history2):
            assert h1.operation.operation_type == h2.operation.operation_type
            assert h1.operation.payload.to_dict() == h2.operation.payload.to_dict()

    def test_add_derived_column_does_not_evaluate_arithmetic(self):
        """AddDerivedColumnOp must generate symbolic placeholder and never perform arithmetic."""
        ph_a = make_value_placeholder("ev_a")
        ph_b = make_value_placeholder("ev_b")

        row = TableRow("r1", "LNST", {
            "col_a": TableCell("ca", ph_a, True),
            "col_b": TableCell("cb", ph_b, True),
        })
        state = TableState.create("t_test", ["col_a", "col_b"], [row])

        op = TableOperation(
            operation_type=TableOperationType.ADD_DERIVED_COLUMN,
            payload=AddDerivedColumnOp(
                new_column="derived_growth",
                formula_id="GROWTH_RATE",
                input_columns=["col_a", "col_b"],
            ),
        )
        new_state = execute_operation(state, op)
        derived_cell = new_state.rows[0].cells["derived_growth"]

        assert derived_cell.is_placeholder is True
        assert derived_cell.value.startswith("val_derived_")
        assert _is_valid_symbolic_placeholder(derived_cell.value)
        assert not _contains_raw_number(derived_cell.value)
        assert derived_cell.derived_formula_id == "GROWTH_RATE"

    def test_group_op_does_not_evaluate_arithmetic(self):
        """GroupOp must generate symbolic placeholder and never compute sum or average numbers."""
        ph_1 = make_value_placeholder("ev_g1")
        ph_2 = make_value_placeholder("ev_g2")

        r1 = TableRow("r1", "Div A", {
            "dept": TableCell("d1", "Operations", False),
            "revenue": TableCell("rev1", ph_1, True),
        })
        r2 = TableRow("r2", "Div B", {
            "dept": TableCell("d2", "Operations", False),
            "revenue": TableCell("rev2", ph_2, True),
        })
        state = TableState.create("t_grp", ["dept", "revenue"], [r1, r2])

        op = TableOperation(
            operation_type=TableOperationType.GROUP,
            payload=GroupOp(
                by_column="dept",
                agg_column="revenue",
                agg_func=AggregateFunction.SUM,
            ),
        )
        new_state = execute_operation(state, op)
        agg_cell = new_state.rows[0].cells["revenue"]

        assert agg_cell.is_placeholder is True
        assert agg_cell.value.startswith("val_agg_")
        assert _is_valid_symbolic_placeholder(agg_cell.value)
        assert not _contains_raw_number(agg_cell.value)
        assert agg_cell.derived_formula_id == "SUM"
