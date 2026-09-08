"""Unit tests for TASK-06B deterministic table operation executor."""

import pytest

from src.table_transform.executor import (
    TableTransformExecutionError,
    execute_operation,
)
from src.table_transform.schemas import (
    AddDerivedColumnOp,
    AggregateFunction,
    GroupOp,
    SelectColumnsOp,
    SelectRowsOp,
    SortOp,
    TableCell,
    TableOperation,
    TableOperationType,
    TableRow,
    TableState,
    TableTransformFailureCode,
)


def _cell(cell_id: str, val: str, is_placeholder: bool = True) -> TableCell:
    return TableCell(
        cell_id=cell_id,
        value=val,
        is_placeholder=is_placeholder,
        evidence_id=f"ev_{cell_id}",
        requirement_id=f"req_{cell_id}",
        metric="LNST",
        period="2023",
    )


def _build_test_table() -> TableState:
    # 3 rows, 3 columns: segment, 2022, 2023
    r1 = TableRow(
        row_id="r1",
        row_label="Segment A",
        cells={
            "segment": TableCell(cell_id="c_s1", value="Segment A", is_placeholder=False),
            "2022": _cell("c_1_22", "val_100"),
            "2023": _cell("c_1_23", "val_150"),
        },
    )
    r2 = TableRow(
        row_id="r2",
        row_label="Segment B",
        cells={
            "segment": TableCell(cell_id="c_s2", value="Segment B", is_placeholder=False),
            "2022": _cell("c_2_22", "val_200"),
            "2023": _cell("c_2_23", "val_180"),
        },
    )
    r3 = TableRow(
        row_id="r3",
        row_label="Segment A",
        cells={
            "segment": TableCell(cell_id="c_s3", value="Segment A", is_placeholder=False),
            "2022": _cell("c_3_22", "val_50"),
            "2023": _cell("c_3_23", "val_70"),
        },
    )
    return TableState.create(
        table_id="tab_test",
        columns=["segment", "2022", "2023"],
        rows=[r1, r2, r3],
    )


class TestTableExecutor:
    def test_select_rows_happy_path(self):
        state = _build_test_table()
        op = TableOperation(
            operation_type=TableOperationType.SELECT_ROWS,
            payload=SelectRowsOp(row_labels=["Segment B"]),
        )
        new_state = execute_operation(state, op)
        assert len(new_state.rows) == 1
        assert new_state.rows[0].row_id == "r2"
        assert new_state.step_index == 1
        assert len(new_state.history) == 1
        assert new_state.history[0].input_state_fingerprint == state.fingerprint
        assert new_state.history[0].output_state_fingerprint == new_state.fingerprint

    def test_select_rows_edge_cases(self):
        state = _build_test_table()
        # Non-existent filter column
        op_bad_col = TableOperation(
            operation_type=TableOperationType.SELECT_ROWS,
            payload=SelectRowsOp(filter_column="non_existent", filter_values=["A"]),
        )
        with pytest.raises(TableTransformExecutionError) as exc_info:
            execute_operation(state, op_bad_col)
        assert exc_info.value.code == TableTransformFailureCode.COLUMN_NOT_FOUND

        # All rows filtered out
        op_none = TableOperation(
            operation_type=TableOperationType.SELECT_ROWS,
            payload=SelectRowsOp(row_labels=["Segment NonExistent"]),
        )
        with pytest.raises(TableTransformExecutionError) as exc_info:
            execute_operation(state, op_none)
        assert exc_info.value.code == TableTransformFailureCode.ALL_ROWS_FILTERED

    def test_select_columns_happy_path(self):
        state = _build_test_table()
        op = TableOperation(
            operation_type=TableOperationType.SELECT_COLUMNS,
            payload=SelectColumnsOp(columns=["segment", "2023"]),
        )
        new_state = execute_operation(state, op)
        assert new_state.columns == ["segment", "2023"]
        assert len(new_state.rows) == 3
        for row in new_state.rows:
            assert set(row.cells.keys()) == {"segment", "2023"}

    def test_select_columns_edge_case_missing_col(self):
        state = _build_test_table()
        op = TableOperation(
            operation_type=TableOperationType.SELECT_COLUMNS,
            payload=SelectColumnsOp(columns=["2025"]),
        )
        with pytest.raises(TableTransformExecutionError) as exc_info:
            execute_operation(state, op)
        assert exc_info.value.code == TableTransformFailureCode.COLUMN_NOT_FOUND

    def test_add_derived_column_happy_path(self):
        state = _build_test_table()
        op = TableOperation(
            operation_type=TableOperationType.ADD_DERIVED_COLUMN,
            payload=AddDerivedColumnOp(
                new_column="growth",
                formula_id="GROWTH_RATE",
                input_columns=["2022", "2023"],
            ),
        )
        new_state = execute_operation(state, op)
        assert "growth" in new_state.columns
        for row in new_state.rows:
            derived_cell = row.cells["growth"]
            assert derived_cell.is_placeholder is True
            assert derived_cell.derived_formula_id == "GROWTH_RATE"
            assert len(derived_cell.input_cell_ids) == 2

    def test_add_derived_column_edge_cases(self):
        state = _build_test_table()
        # Duplicate column
        op_dup = TableOperation(
            operation_type=TableOperationType.ADD_DERIVED_COLUMN,
            payload=AddDerivedColumnOp(
                new_column="2023",
                formula_id="GROWTH_RATE",
                input_columns=["2022", "2023"],
            ),
        )
        with pytest.raises(TableTransformExecutionError) as exc:
            execute_operation(state, op_dup)
        assert exc.value.code == TableTransformFailureCode.DUPLICATE_COLUMN

        # Unsupported formula
        op_bad_form = TableOperation(
            operation_type=TableOperationType.ADD_DERIVED_COLUMN,
            payload=AddDerivedColumnOp(
                new_column="invalid_calc",
                formula_id="UNREGISTERED_FORMULA_XYZ",
                input_columns=["2022", "2023"],
            ),
        )
        with pytest.raises(TableTransformExecutionError) as exc:
            execute_operation(state, op_bad_form)
        assert exc.value.code == TableTransformFailureCode.UNSUPPORTED_FORMULA

    def test_group_happy_path(self):
        state = _build_test_table()
        op = TableOperation(
            operation_type=TableOperationType.GROUP,
            payload=GroupOp(
                by_column="segment",
                agg_column="2023",
                agg_func=AggregateFunction.SUM,
            ),
        )
        new_state = execute_operation(state, op)
        assert new_state.columns == ["segment", "2023"]
        # There are 2 distinct segments: Segment A (2 rows) and Segment B (1 row)
        assert len(new_state.rows) == 2
        # Keys sorted deterministically
        assert new_state.rows[0].row_label == "Segment A"
        assert new_state.rows[1].row_label == "Segment B"
        # Aggregated cell for Segment A should reference 2 input cells
        seg_a_agg = new_state.rows[0].cells["2023"]
        assert seg_a_agg.is_placeholder is True
        assert len(seg_a_agg.input_cell_ids) == 2

    def test_group_edge_cases(self):
        state = _build_test_table()
        op_bad_col = TableOperation(
            operation_type=TableOperationType.GROUP,
            payload=GroupOp(
                by_column="missing",
                agg_column="2023",
                agg_func=AggregateFunction.SUM,
            ),
        )
        with pytest.raises(TableTransformExecutionError) as exc:
            execute_operation(state, op_bad_col)
        assert exc.value.code == TableTransformFailureCode.COLUMN_NOT_FOUND

    def test_sort_happy_path_and_edge_case(self):
        state = _build_test_table()
        op_desc = TableOperation(
            operation_type=TableOperationType.SORT,
            payload=SortOp(by_column="segment", descending=True),
        )
        new_state = execute_operation(state, op_desc)
        # Segment B should be first when sorted descending
        assert new_state.rows[0].row_label == "Segment B"

        # Edge case: non-existent sort column
        op_missing = TableOperation(
            operation_type=TableOperationType.SORT,
            payload=SortOp(by_column="unknown_col"),
        )
        with pytest.raises(TableTransformExecutionError) as exc:
            execute_operation(state, op_missing)
        assert exc.value.code == TableTransformFailureCode.COLUMN_NOT_FOUND

    def test_determinism_and_immutability(self):
        """Executing the same operation multiple times produces identical state and hash."""
        state = _build_test_table()
        original_fp = state.fingerprint
        op = TableOperation(
            operation_type=TableOperationType.SELECT_COLUMNS,
            payload=SelectColumnsOp(columns=["2022", "2023"]),
        )

        out1 = execute_operation(state, op)
        out2 = execute_operation(state, op)
        out3 = execute_operation(state, op)

        assert out1.fingerprint == out2.fingerprint == out3.fingerprint
        assert out1.to_dict() == out2.to_dict() == out3.to_dict()
        # Original state is completely unmutated
        assert state.fingerprint == original_fp
        assert len(state.columns) == 3
