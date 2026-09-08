"""Unit tests for TASK-06A table transform schemas and operation contracts."""

import pytest

from src.table_transform.schemas import (
    TABLE_TRANSFORM_SCHEMA_VERSION,
    AddDerivedColumnOp,
    AggregateFunction,
    GroupOp,
    OperationRecord,
    SelectColumnsOp,
    SelectRowsOp,
    SortOp,
    TableCell,
    TableOperation,
    TableOperationType,
    TableRow,
    TableState,
)
from src.understanding.schemas import SchemaValidationError


def _sample_cell(cell_id="c1", val="val_0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef", is_placeholder=True):
    return TableCell(
        cell_id=cell_id,
        value=val,
        is_placeholder=is_placeholder,
        evidence_id=f"ev_{cell_id}",
        requirement_id=f"req_{cell_id}",
        metric="LNST",
        period="2023",
        row_path=["Doanh thu", "LNST"],
        column_path=["2023"],
    )


def _sample_state():
    cell1 = _sample_cell("c1", "val_1111111111111111111111111111111111111111111111111111111111111111")
    cell2 = _sample_cell("c2", "val_2222222222222222222222222222222222222222222222222222222222222222")
    row1 = TableRow(row_id="r1", row_label="Doanh thu", cells={"2022": cell1, "2023": cell2})
    return TableState.create(
        table_id="tab_01",
        columns=["2022", "2023"],
        rows=[row1],
    )


class TestTableTransformSchemas:
    def test_table_cell_happy_path_and_serialization(self):
        cell = _sample_cell()
        cell_dict = cell.to_dict()
        assert cell_dict["cell_id"] == "c1"
        assert cell_dict["is_placeholder"] is True
        restored = TableCell.from_dict(cell_dict)
        assert restored == cell

    def test_table_cell_invalid(self):
        with pytest.raises(SchemaValidationError, match="cell_id must be non-empty"):
            TableCell(cell_id="", value="val", is_placeholder=True)
        with pytest.raises(SchemaValidationError, match="is_placeholder must be a boolean"):
            TableCell(cell_id="c1", value="val", is_placeholder="yes")  # type: ignore

    def test_table_row_and_state_validation(self):
        cell1 = _sample_cell("c1")
        cell2 = _sample_cell("c2")
        row1 = TableRow(row_id="r1", row_label="Doanh thu", cells={"2022": cell1, "2023": cell2})
        row_dict = row1.to_dict()
        restored_row = TableRow.from_dict(row_dict)
        assert restored_row == row1

        state = TableState.create(table_id="t1", columns=["2022", "2023"], rows=[row1])
        assert state.table_id == "t1"
        assert len(state.fingerprint) == 64
        state_dict = state.to_dict()
        restored_state = TableState.from_dict(state_dict)
        assert restored_state == state

    def test_table_state_mismatched_columns(self):
        cell1 = _sample_cell("c1")
        row1 = TableRow(row_id="r1", row_label="Doanh thu", cells={"2022": cell1})
        with pytest.raises(SchemaValidationError, match="cells keys do not match table columns"):
            TableState.create(table_id="t1", columns=["2022", "2023"], rows=[row1])

    def test_table_state_duplicate_row_ids(self):
        cell1 = _sample_cell("c1")
        row1 = TableRow(row_id="r1", row_label="Doanh thu", cells={"2022": cell1})
        row2 = TableRow(row_id="r1", row_label="Chi phí", cells={"2022": cell1})
        with pytest.raises(SchemaValidationError, match="rows must have unique row_ids"):
            TableState.create(table_id="t1", columns=["2022"], rows=[row1, row2])

    def test_select_rows_op_validation(self):
        op1 = SelectRowsOp(row_labels=["Doanh thu"])
        assert op1.row_labels == ["Doanh thu"]
        restored = SelectRowsOp.from_dict(op1.to_dict())
        assert restored == op1

        op2 = SelectRowsOp(row_indices=[0, 1])
        assert op2.row_indices == [0, 1]

        op3 = SelectRowsOp(filter_column="segment", filter_values=["A", "B"])
        assert op3.filter_column == "segment"

        with pytest.raises(SchemaValidationError, match="SelectRowsOp requires at least one of"):
            SelectRowsOp()

        with pytest.raises(SchemaValidationError, match="row_indices\\[0\\] must be non-negative"):
            SelectRowsOp(row_indices=[-1])

        with pytest.raises(SchemaValidationError, match="filter_column requires non-null filter_values"):
            SelectRowsOp(filter_column="seg", filter_values=None)

    def test_select_columns_op_validation(self):
        op = SelectColumnsOp(columns=["2022", "2023"])
        assert op.columns == ["2022", "2023"]
        restored = SelectColumnsOp.from_dict(op.to_dict())
        assert restored == op

        with pytest.raises(SchemaValidationError, match="columns must be non-empty"):
            SelectColumnsOp(columns=[])

        with pytest.raises(SchemaValidationError, match="columns must contain unique items"):
            SelectColumnsOp(columns=["2022", "2022"])

    def test_add_derived_column_op_validation(self):
        op = AddDerivedColumnOp(new_column="growth", formula_id="GROWTH_RATE", input_columns=["2022", "2023"])
        assert op.new_column == "growth"
        restored = AddDerivedColumnOp.from_dict(op.to_dict())
        assert restored == op

        with pytest.raises(SchemaValidationError, match="new_column must be non-empty"):
            AddDerivedColumnOp(new_column="", formula_id="GROWTH_RATE", input_columns=["2022"])

        with pytest.raises(SchemaValidationError, match="input_columns must contain unique items"):
            AddDerivedColumnOp(new_column="growth", formula_id="GROWTH_RATE", input_columns=["2022", "2022"])

    def test_group_op_validation(self):
        op = GroupOp(by_column="segment", agg_column="revenue", agg_func=AggregateFunction.SUM)
        assert op.agg_func is AggregateFunction.SUM
        restored = GroupOp.from_dict(op.to_dict())
        assert restored == op

        with pytest.raises(SchemaValidationError, match="agg_func must be a AggregateFunction"):
            GroupOp(by_column="segment", agg_column="revenue", agg_func="INVALID")  # type: ignore

    def test_sort_op_validation(self):
        op = SortOp(by_column="revenue", descending=True)
        assert op.descending is True
        restored = SortOp.from_dict(op.to_dict())
        assert restored == op

        with pytest.raises(SchemaValidationError, match="descending must be a boolean"):
            SortOp(by_column="rev", descending=1)  # type: ignore

    def test_table_operation_wrapper(self):
        sel_col = SelectColumnsOp(columns=["2023"])
        wrapper = TableOperation(operation_type=TableOperationType.SELECT_COLUMNS, payload=sel_col)
        assert wrapper.operation_type is TableOperationType.SELECT_COLUMNS
        data = wrapper.to_dict()
        restored = TableOperation.from_dict(data)
        assert restored == wrapper

        with pytest.raises(SchemaValidationError, match="payload for SELECT_ROWS must be SelectRowsOp"):
            TableOperation(operation_type=TableOperationType.SELECT_ROWS, payload=sel_col)  # type: ignore

    def test_operation_record_validation(self):
        sel_col = SelectColumnsOp(columns=["2023"])
        wrapper = TableOperation(operation_type=TableOperationType.SELECT_COLUMNS, payload=sel_col)
        rec = OperationRecord(
            step_index=1,
            operation=wrapper,
            input_state_fingerprint="a" * 64,
            output_state_fingerprint="b" * 64,
        )
        rec_dict = rec.to_dict()
        restored = OperationRecord.from_dict(rec_dict)
        assert restored == rec
