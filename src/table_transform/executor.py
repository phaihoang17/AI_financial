"""Deterministic pure-function executor for M6B table operations."""

from __future__ import annotations

from hashlib import sha256
from typing import Callable, Dict, List, Sequence

from src.supervisor.formula_registry import get_formula
from src.table_transform.schemas import (
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
    TableTransformFailureCode,
    canonical_json,
)
from src.understanding.schemas import SchemaValidationError


class TableTransformExecutionError(SchemaValidationError):
    """Raised when an operation cannot be validly executed on a TableState."""

    def __init__(self, code: TableTransformFailureCode, message: str) -> None:
        self.code = code
        super().__init__(f"{code.value}: {message}")


def make_derived_cell_placeholder(
    formula_id: str,
    input_placeholders: Sequence[str],
) -> str:
    """Generate deterministic symbolic placeholder for derived table cell."""
    payload = {
        "formula_id": formula_id,
        "inputs": sorted(input_placeholders),
    }
    digest = sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return f"val_derived_{digest}"


def make_aggregate_cell_placeholder(
    agg_func: str,
    input_placeholders: Sequence[str],
) -> str:
    """Generate deterministic symbolic placeholder for aggregated table cell."""
    payload = {
        "agg_func": agg_func,
        "inputs": sorted(input_placeholders),
    }
    digest = sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return f"val_agg_{digest}"


def _execute_select_rows(state: TableState, op: SelectRowsOp) -> TableState:
    if not state.rows:
        raise TableTransformExecutionError(
            TableTransformFailureCode.EMPTY_TABLE,
            "cannot execute SelectRowsOp on empty table",
        )

    if op.filter_column is not None and op.filter_column not in state.columns:
        raise TableTransformExecutionError(
            TableTransformFailureCode.COLUMN_NOT_FOUND,
            f"filter_column '{op.filter_column}' not found in table columns {state.columns}",
        )

    selected_rows: List[TableRow] = []
    labels_set = set(op.row_labels) if op.row_labels is not None else None
    indices_set = set(op.row_indices) if op.row_indices is not None else None
    values_set = set(op.filter_values) if op.filter_values is not None else None

    for index, row in enumerate(state.rows):
        if labels_set is not None and row.row_label not in labels_set:
            continue
        if indices_set is not None and index not in indices_set:
            continue
        if values_set is not None:
            cell = row.cells.get(op.filter_column)  # type: ignore[arg-type]
            if cell is None or cell.value not in values_set:
                continue
        selected_rows.append(row)

    if not selected_rows:
        raise TableTransformExecutionError(
            TableTransformFailureCode.ALL_ROWS_FILTERED,
            "SelectRowsOp filtered out all rows in table",
        )

    return TableState.create(
        table_id=state.table_id,
        columns=list(state.columns),
        rows=selected_rows,
        step_index=state.step_index + 1,
        history=list(state.history),
    )


def _execute_select_columns(state: TableState, op: SelectColumnsOp) -> TableState:
    existing_cols = set(state.columns)
    for col in op.columns:
        if col not in existing_cols:
            raise TableTransformExecutionError(
                TableTransformFailureCode.COLUMN_NOT_FOUND,
                f"column '{col}' not found in table columns {state.columns}",
            )

    projected_rows: List[TableRow] = []
    for row in state.rows:
        projected_cells = {col: row.cells[col] for col in op.columns}
        projected_rows.append(
            TableRow(
                row_id=row.row_id,
                row_label=row.row_label,
                cells=projected_cells,
            )
        )

    return TableState.create(
        table_id=state.table_id,
        columns=list(op.columns),
        rows=projected_rows,
        step_index=state.step_index + 1,
        history=list(state.history),
    )


def _execute_add_derived_column(state: TableState, op: AddDerivedColumnOp) -> TableState:
    if op.new_column in state.columns:
        raise TableTransformExecutionError(
            TableTransformFailureCode.DUPLICATE_COLUMN,
            f"new_column '{op.new_column}' already exists in table",
        )

    existing_cols = set(state.columns)
    for col in op.input_columns:
        if col not in existing_cols:
            raise TableTransformExecutionError(
                TableTransformFailureCode.COLUMN_NOT_FOUND,
                f"input column '{col}' not found in table columns {state.columns}",
            )

    # Check formula validity in FormulaRegistry
    if get_formula(op.formula_id) is None:
        raise TableTransformExecutionError(
            TableTransformFailureCode.UNSUPPORTED_FORMULA,
            f"formula_id '{op.formula_id}' is not registered in FormulaRegistry",
        )

    new_rows: List[TableRow] = []
    for row in state.rows:
        input_cells = [row.cells[c] for c in op.input_columns]
        input_placeholders = [c.value for c in input_cells if c.is_placeholder]
        derived_val = make_derived_cell_placeholder(op.formula_id, input_placeholders)

        derived_cell = TableCell(
            cell_id=f"derived_{row.row_id}_{op.new_column}",
            value=derived_val,
            is_placeholder=True,
            derived_formula_id=op.formula_id,
            input_cell_ids=[c.cell_id for c in input_cells],
            row_path=list(input_cells[0].row_path) if input_cells else [],
            column_path=[op.new_column],
        )

        updated_cells = dict(row.cells)
        updated_cells[op.new_column] = derived_cell
        new_rows.append(
            TableRow(
                row_id=row.row_id,
                row_label=row.row_label,
                cells=updated_cells,
            )
        )

    return TableState.create(
        table_id=state.table_id,
        columns=state.columns + [op.new_column],
        rows=new_rows,
        step_index=state.step_index + 1,
        history=list(state.history),
    )


def _execute_group(state: TableState, op: GroupOp) -> TableState:
    if not state.rows:
        raise TableTransformExecutionError(
            TableTransformFailureCode.EMPTY_TABLE,
            "cannot execute GroupOp on empty table",
        )

    if op.by_column not in state.columns:
        raise TableTransformExecutionError(
            TableTransformFailureCode.COLUMN_NOT_FOUND,
            f"by_column '{op.by_column}' not found in table columns {state.columns}",
        )
    if op.agg_column not in state.columns:
        raise TableTransformExecutionError(
            TableTransformFailureCode.COLUMN_NOT_FOUND,
            f"agg_column '{op.agg_column}' not found in table columns {state.columns}",
        )

    groups: Dict[str, List[TableRow]] = {}
    for row in state.rows:
        key = row.cells[op.by_column].value
        groups.setdefault(key, []).append(row)

    # Sort keys for determinism
    sorted_keys = sorted(groups.keys())
    grouped_rows: List[TableRow] = []

    for key in sorted_keys:
        rows_in_group = groups[key]
        target_cells = [r.cells[op.agg_column] for r in rows_in_group]
        placeholders = [c.value for c in target_cells if c.is_placeholder]
        agg_val = make_aggregate_cell_placeholder(op.agg_func.value, placeholders)

        key_cell = TableCell(
            cell_id=f"grp_key_{key}_{op.by_column}",
            value=key,
            is_placeholder=False,
            row_path=[key],
            column_path=[op.by_column],
        )
        agg_cell = TableCell(
            cell_id=f"agg_{key}_{op.agg_column}",
            value=agg_val,
            is_placeholder=True,
            derived_formula_id=op.agg_func.value,
            input_cell_ids=[c.cell_id for c in target_cells],
            row_path=[key],
            column_path=[op.agg_column],
        )

        grouped_rows.append(
            TableRow(
                row_id=f"grp_row_{key}",
                row_label=key,
                cells={
                    op.by_column: key_cell,
                    op.agg_column: agg_cell,
                },
            )
        )

    return TableState.create(
        table_id=state.table_id,
        columns=[op.by_column, op.agg_column],
        rows=grouped_rows,
        step_index=state.step_index + 1,
        history=list(state.history),
    )


def _execute_sort(state: TableState, op: SortOp) -> TableState:
    if op.by_column not in state.columns:
        raise TableTransformExecutionError(
            TableTransformFailureCode.COLUMN_NOT_FOUND,
            f"by_column '{op.by_column}' not found in table columns {state.columns}",
        )

    # Python sort is stable. Secondary tie-breaker is row_id to ensure absolute determinism.
    sorted_rows = sorted(
        state.rows,
        key=lambda r: (r.cells[op.by_column].value, r.row_id),
        reverse=op.descending,
    )

    return TableState.create(
        table_id=state.table_id,
        columns=list(state.columns),
        rows=sorted_rows,
        step_index=state.step_index + 1,
        history=list(state.history),
    )


_HANDLERS: Dict[
    TableOperationType,
    Callable[[TableState, Any], TableState],
] = {
    TableOperationType.SELECT_ROWS: _execute_select_rows,
    TableOperationType.SELECT_COLUMNS: _execute_select_columns,
    TableOperationType.ADD_DERIVED_COLUMN: _execute_add_derived_column,
    TableOperationType.GROUP: _execute_group,
    TableOperationType.SORT: _execute_sort,
}


def execute_operation(state: TableState, operation: TableOperation) -> TableState:
    """Execute one atomic table operation deterministically on state, returning a new TableState."""
    if not isinstance(state, TableState):
        raise SchemaValidationError("state must be a TableState")
    if not isinstance(operation, TableOperation):
        raise SchemaValidationError("operation must be a TableOperation")

    handler = _HANDLERS.get(operation.operation_type)
    if handler is None:
        raise TableTransformExecutionError(
            TableTransformFailureCode.INVALID_OPERATION_INPUT,
            f"unsupported operation type {operation.operation_type}",
        )

    new_state = handler(state, operation.payload)

    # Append OperationRecord linking input and output state fingerprints
    record = OperationRecord(
        step_index=new_state.step_index,
        operation=operation,
        input_state_fingerprint=state.fingerprint,
        output_state_fingerprint=new_state.fingerprint,
    )

    updated_history = list(state.history) + [record]

    return TableState.create(
        table_id=new_state.table_id,
        columns=new_state.columns,
        rows=new_state.rows,
        step_index=new_state.step_index,
        history=updated_history,
    )
