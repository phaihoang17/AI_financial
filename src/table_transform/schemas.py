"""Canonical M6B table transformation contracts and schemas."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
import json
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Type, TypeVar, Union

from src.understanding.schemas import SchemaValidationError

TABLE_TRANSFORM_SCHEMA_VERSION = "m6b-table-transform-v1"

EnumT = TypeVar("EnumT", bound=Enum)


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SchemaValidationError(f"{path} must be an object")
    return value


def _exact_keys(data: Mapping[str, Any], expected: Set[str], path: str) -> None:
    missing = expected - set(data)
    unknown = set(data) - expected
    if missing:
        raise SchemaValidationError(
            f"{path} is missing fields: {', '.join(sorted(missing))}"
        )
    if unknown:
        raise SchemaValidationError(
            f"{path} has unknown fields: {', '.join(sorted(map(str, unknown)))}"
        )


def _string(value: Any, path: str, *, non_empty: bool = True) -> str:
    if not isinstance(value, str):
        raise SchemaValidationError(f"{path} must be a string")
    if non_empty and not value:
        raise SchemaValidationError(f"{path} must be non-empty")
    return value


def _optional_string(value: Any, path: str) -> Optional[str]:
    if value is not None and not isinstance(value, str):
        raise SchemaValidationError(f"{path} must be a string or null")
    if value == "":
        raise SchemaValidationError(f"{path} must be non-empty when present")
    return value


def _string_list(value: Any, path: str, *, non_empty: bool = False, unique: bool = False) -> List[str]:
    if not isinstance(value, list):
        raise SchemaValidationError(f"{path} must be a list")
    result = [_string(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if non_empty and not result:
        raise SchemaValidationError(f"{path} must be non-empty")
    if unique and len(result) != len(set(result)):
        raise SchemaValidationError(f"{path} must contain unique items")
    return result


def _optional_string_list(value: Any, path: str, *, non_empty: bool = False, unique: bool = False) -> Optional[List[str]]:
    if value is None:
        return None
    return _string_list(value, path, non_empty=non_empty, unique=unique)


def _int_list(value: Any, path: str, *, non_empty: bool = False, non_negative: bool = True) -> List[int]:
    if not isinstance(value, list):
        raise SchemaValidationError(f"{path} must be a list")
    result = []
    for index, item in enumerate(value):
        if not isinstance(item, int) or isinstance(item, bool):
            raise SchemaValidationError(f"{path}[{index}] must be an integer")
        if non_negative and item < 0:
            raise SchemaValidationError(f"{path}[{index}] must be non-negative")
        result.append(item)
    if non_empty and not result:
        raise SchemaValidationError(f"{path} must be non-empty")
    return result


def _optional_int_list(value: Any, path: str, *, non_empty: bool = False, non_negative: bool = True) -> Optional[List[int]]:
    if value is None:
        return None
    return _int_list(value, path, non_empty=non_empty, non_negative=non_negative)


def _enum(value: Any, enum_type: Type[EnumT], path: str) -> EnumT:
    if not isinstance(value, enum_type):
        raise SchemaValidationError(f"{path} must be a {enum_type.__name__}")
    return value


def _parse_enum(value: Any, enum_type: Type[EnumT], path: str) -> EnumT:
    if not isinstance(value, str):
        raise SchemaValidationError(f"{path} must be a string enum value")
    try:
        return enum_type(value)
    except ValueError as error:
        allowed = ", ".join(member.value for member in enum_type)
        raise SchemaValidationError(f"{path} must be one of: {allowed}") from error


def canonical_json(value: Any) -> str:
    """Serialize contract payload with canonical JSON rules."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


class TableOperationType(str, Enum):
    SELECT_ROWS = "SELECT_ROWS"
    SELECT_COLUMNS = "SELECT_COLUMNS"
    ADD_DERIVED_COLUMN = "ADD_DERIVED_COLUMN"
    GROUP = "GROUP"
    SORT = "SORT"


class AggregateFunction(str, Enum):
    SUM = "SUM"
    AVERAGE = "AVERAGE"
    MIN = "MIN"
    MAX = "MAX"
    COUNT = "COUNT"


class TableTransformFailureCode(str, Enum):
    INVALID_OPERATION_INPUT = "INVALID_OPERATION_INPUT"
    COLUMN_NOT_FOUND = "COLUMN_NOT_FOUND"
    DUPLICATE_COLUMN = "DUPLICATE_COLUMN"
    EMPTY_TABLE = "EMPTY_TABLE"
    ALL_ROWS_FILTERED = "ALL_ROWS_FILTERED"
    UNSUPPORTED_FORMULA = "UNSUPPORTED_FORMULA"
    INVALID_AGGREGATION = "INVALID_AGGREGATION"
    MAX_STEPS_EXCEEDED = "MAX_STEPS_EXCEEDED"
    TERMINAL_STATE_UNREACHABLE = "TERMINAL_STATE_UNREACHABLE"
    CANNOT_CONSTRUCT_PROGRAM = "CANNOT_CONSTRUCT_PROGRAM"


@dataclass(frozen=True)
class TableCell:
    cell_id: str
    value: str
    is_placeholder: bool
    evidence_id: Optional[str] = None
    requirement_id: Optional[str] = None
    metric: Optional[str] = None
    period: Optional[str] = None
    row_path: List[str] = field(default_factory=list)
    column_path: List[str] = field(default_factory=list)
    derived_formula_id: Optional[str] = None
    input_cell_ids: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        object.__setattr__(self, "cell_id", _string(self.cell_id, "cell_id"))
        object.__setattr__(self, "value", _string(self.value, "value", non_empty=False))
        if not isinstance(self.is_placeholder, bool):
            raise SchemaValidationError("is_placeholder must be a boolean")
        object.__setattr__(self, "evidence_id", _optional_string(self.evidence_id, "evidence_id"))
        object.__setattr__(self, "requirement_id", _optional_string(self.requirement_id, "requirement_id"))
        object.__setattr__(self, "metric", _optional_string(self.metric, "metric"))
        object.__setattr__(self, "period", _optional_string(self.period, "period"))
        object.__setattr__(self, "row_path", _string_list(list(self.row_path), "row_path"))
        object.__setattr__(self, "column_path", _string_list(list(self.column_path), "column_path"))
        object.__setattr__(self, "derived_formula_id", _optional_string(self.derived_formula_id, "derived_formula_id"))
        object.__setattr__(self, "input_cell_ids", _string_list(list(self.input_cell_ids), "input_cell_ids"))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cell_id": self.cell_id,
            "value": self.value,
            "is_placeholder": self.is_placeholder,
            "evidence_id": self.evidence_id,
            "requirement_id": self.requirement_id,
            "metric": self.metric,
            "period": self.period,
            "row_path": list(self.row_path),
            "column_path": list(self.column_path),
            "derived_formula_id": self.derived_formula_id,
            "input_cell_ids": list(self.input_cell_ids),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "TableCell":
        data = _mapping(value, "TableCell")
        _exact_keys(
            data,
            {
                "cell_id",
                "value",
                "is_placeholder",
                "evidence_id",
                "requirement_id",
                "metric",
                "period",
                "row_path",
                "column_path",
                "derived_formula_id",
                "input_cell_ids",
            },
            "TableCell",
        )
        return cls(
            cell_id=data["cell_id"],
            value=data["value"],
            is_placeholder=data["is_placeholder"],
            evidence_id=data["evidence_id"],
            requirement_id=data["requirement_id"],
            metric=data["metric"],
            period=data["period"],
            row_path=data["row_path"],
            column_path=data["column_path"],
            derived_formula_id=data["derived_formula_id"],
            input_cell_ids=data["input_cell_ids"],
        )


@dataclass(frozen=True)
class TableRow:
    row_id: str
    row_label: str
    cells: Dict[str, TableCell]

    def __post_init__(self) -> None:
        object.__setattr__(self, "row_id", _string(self.row_id, "row_id"))
        object.__setattr__(self, "row_label", _string(self.row_label, "row_label", non_empty=False))
        if not isinstance(self.cells, Mapping):
            raise SchemaValidationError("cells must be a mapping of column name to TableCell")
        checked_cells = {}
        for col_name, cell in self.cells.items():
            if not isinstance(col_name, str) or not col_name:
                raise SchemaValidationError("cells key must be non-empty string column name")
            if not isinstance(cell, TableCell):
                raise SchemaValidationError(f"cells['{col_name}'] must be a TableCell")
            checked_cells[col_name] = cell
        object.__setattr__(self, "cells", checked_cells)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "row_id": self.row_id,
            "row_label": self.row_label,
            "cells": {k: v.to_dict() for k, v in self.cells.items()},
        }

    @classmethod
    def from_dict(cls, value: Any) -> "TableRow":
        data = _mapping(value, "TableRow")
        _exact_keys(data, {"row_id", "row_label", "cells"}, "TableRow")
        cells_raw = _mapping(data["cells"], "TableRow.cells")
        return cls(
            row_id=data["row_id"],
            row_label=data["row_label"],
            cells={k: TableCell.from_dict(v) for k, v in cells_raw.items()},
        )


@dataclass(frozen=True)
class SelectRowsOp:
    row_labels: Optional[List[str]] = None
    row_indices: Optional[List[int]] = None
    filter_column: Optional[str] = None
    filter_values: Optional[List[str]] = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "row_labels",
            _optional_string_list(self.row_labels, "row_labels", non_empty=True),
        )
        object.__setattr__(
            self,
            "row_indices",
            _optional_int_list(self.row_indices, "row_indices", non_empty=True, non_negative=True),
        )
        object.__setattr__(
            self,
            "filter_column",
            _optional_string(self.filter_column, "filter_column"),
        )
        object.__setattr__(
            self,
            "filter_values",
            _optional_string_list(self.filter_values, "filter_values", non_empty=True),
        )
        if (
            self.row_labels is None
            and self.row_indices is None
            and self.filter_column is None
        ):
            raise SchemaValidationError(
                "SelectRowsOp requires at least one of row_labels, row_indices, or filter_column"
            )
        if self.filter_column is not None and self.filter_values is None:
            raise SchemaValidationError(
                "SelectRowsOp filter_column requires non-null filter_values"
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "row_labels": None if self.row_labels is None else list(self.row_labels),
            "row_indices": None if self.row_indices is None else list(self.row_indices),
            "filter_column": self.filter_column,
            "filter_values": None if self.filter_values is None else list(self.filter_values),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "SelectRowsOp":
        data = _mapping(value, "SelectRowsOp")
        _exact_keys(data, {"row_labels", "row_indices", "filter_column", "filter_values"}, "SelectRowsOp")
        return cls(
            row_labels=data["row_labels"],
            row_indices=data["row_indices"],
            filter_column=data["filter_column"],
            filter_values=data["filter_values"],
        )


@dataclass(frozen=True)
class SelectColumnsOp:
    columns: List[str]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "columns",
            _string_list(self.columns, "columns", non_empty=True, unique=True),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {"columns": list(self.columns)}

    @classmethod
    def from_dict(cls, value: Any) -> "SelectColumnsOp":
        data = _mapping(value, "SelectColumnsOp")
        _exact_keys(data, {"columns"}, "SelectColumnsOp")
        return cls(columns=data["columns"])


@dataclass(frozen=True)
class AddDerivedColumnOp:
    new_column: str
    formula_id: str
    input_columns: List[str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "new_column", _string(self.new_column, "new_column"))
        object.__setattr__(self, "formula_id", _string(self.formula_id, "formula_id"))
        object.__setattr__(
            self,
            "input_columns",
            _string_list(self.input_columns, "input_columns", non_empty=True, unique=True),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "new_column": self.new_column,
            "formula_id": self.formula_id,
            "input_columns": list(self.input_columns),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "AddDerivedColumnOp":
        data = _mapping(value, "AddDerivedColumnOp")
        _exact_keys(data, {"new_column", "formula_id", "input_columns"}, "AddDerivedColumnOp")
        return cls(
            new_column=data["new_column"],
            formula_id=data["formula_id"],
            input_columns=data["input_columns"],
        )


@dataclass(frozen=True)
class GroupOp:
    by_column: str
    agg_column: str
    agg_func: AggregateFunction

    def __post_init__(self) -> None:
        object.__setattr__(self, "by_column", _string(self.by_column, "by_column"))
        object.__setattr__(self, "agg_column", _string(self.agg_column, "agg_column"))
        object.__setattr__(self, "agg_func", _enum(self.agg_func, AggregateFunction, "agg_func"))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "by_column": self.by_column,
            "agg_column": self.agg_column,
            "agg_func": self.agg_func.value,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "GroupOp":
        data = _mapping(value, "GroupOp")
        _exact_keys(data, {"by_column", "agg_column", "agg_func"}, "GroupOp")
        return cls(
            by_column=data["by_column"],
            agg_column=data["agg_column"],
            agg_func=_parse_enum(data["agg_func"], AggregateFunction, "agg_func"),
        )


@dataclass(frozen=True)
class SortOp:
    by_column: str
    descending: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "by_column", _string(self.by_column, "by_column"))
        if not isinstance(self.descending, bool):
            raise SchemaValidationError("descending must be a boolean")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "by_column": self.by_column,
            "descending": self.descending,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "SortOp":
        data = _mapping(value, "SortOp")
        _exact_keys(data, {"by_column", "descending"}, "SortOp")
        return cls(
            by_column=data["by_column"],
            descending=data["descending"],
        )


TableOpPayload = Union[
    SelectRowsOp,
    SelectColumnsOp,
    AddDerivedColumnOp,
    GroupOp,
    SortOp,
]


@dataclass(frozen=True)
class TableOperation:
    operation_type: TableOperationType
    payload: TableOpPayload

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "operation_type",
            _enum(self.operation_type, TableOperationType, "operation_type"),
        )
        type_mapping = {
            TableOperationType.SELECT_ROWS: SelectRowsOp,
            TableOperationType.SELECT_COLUMNS: SelectColumnsOp,
            TableOperationType.ADD_DERIVED_COLUMN: AddDerivedColumnOp,
            TableOperationType.GROUP: GroupOp,
            TableOperationType.SORT: SortOp,
        }
        expected_type = type_mapping[self.operation_type]
        if not isinstance(self.payload, expected_type):
            raise SchemaValidationError(
                f"payload for {self.operation_type.value} must be {expected_type.__name__}"
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "operation_type": self.operation_type.value,
            "payload": self.payload.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "TableOperation":
        data = _mapping(value, "TableOperation")
        _exact_keys(data, {"operation_type", "payload"}, "TableOperation")
        op_type = _parse_enum(data["operation_type"], TableOperationType, "operation_type")
        payload_data = data["payload"]
        if op_type is TableOperationType.SELECT_ROWS:
            payload = SelectRowsOp.from_dict(payload_data)
        elif op_type is TableOperationType.SELECT_COLUMNS:
            payload = SelectColumnsOp.from_dict(payload_data)
        elif op_type is TableOperationType.ADD_DERIVED_COLUMN:
            payload = AddDerivedColumnOp.from_dict(payload_data)
        elif op_type is TableOperationType.GROUP:
            payload = GroupOp.from_dict(payload_data)
        elif op_type is TableOperationType.SORT:
            payload = SortOp.from_dict(payload_data)
        else:
            raise SchemaValidationError(f"unknown operation_type {op_type}")
        return cls(operation_type=op_type, payload=payload)


@dataclass(frozen=True)
class OperationRecord:
    step_index: int
    operation: TableOperation
    input_state_fingerprint: str
    output_state_fingerprint: str

    def __post_init__(self) -> None:
        if not isinstance(self.step_index, int) or self.step_index < 0:
            raise SchemaValidationError("step_index must be a non-negative integer")
        if not isinstance(self.operation, TableOperation):
            raise SchemaValidationError("operation must be a TableOperation")
        object.__setattr__(
            self,
            "input_state_fingerprint",
            _string(self.input_state_fingerprint, "input_state_fingerprint"),
        )
        object.__setattr__(
            self,
            "output_state_fingerprint",
            _string(self.output_state_fingerprint, "output_state_fingerprint"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_index": self.step_index,
            "operation": self.operation.to_dict(),
            "input_state_fingerprint": self.input_state_fingerprint,
            "output_state_fingerprint": self.output_state_fingerprint,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "OperationRecord":
        data = _mapping(value, "OperationRecord")
        _exact_keys(
            data,
            {"step_index", "operation", "input_state_fingerprint", "output_state_fingerprint"},
            "OperationRecord",
        )
        return cls(
            step_index=data["step_index"],
            operation=TableOperation.from_dict(data["operation"]),
            input_state_fingerprint=data["input_state_fingerprint"],
            output_state_fingerprint=data["output_state_fingerprint"],
        )


def compute_table_fingerprint(
    table_id: str,
    columns: Sequence[str],
    rows: Sequence[TableRow],
) -> str:
    """Compute canonical SHA-256 fingerprint over table structure and values."""
    payload = {
        "schema_version": TABLE_TRANSFORM_SCHEMA_VERSION,
        "table_id": table_id,
        "columns": list(columns),
        "rows": [row.to_dict() for row in rows],
    }
    canonical = canonical_json(payload).encode("utf-8")
    return sha256(canonical).hexdigest()


@dataclass(frozen=True)
class TableState:
    table_id: str
    columns: List[str]
    rows: List[TableRow]
    step_index: int = 0
    history: List[OperationRecord] = field(default_factory=list)
    fingerprint: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "table_id", _string(self.table_id, "table_id"))
        object.__setattr__(
            self,
            "columns",
            _string_list(self.columns, "columns", non_empty=False, unique=True),
        )
        if not isinstance(self.rows, list) or not all(isinstance(r, TableRow) for r in self.rows):
            raise SchemaValidationError("rows must be a list of TableRow")
        row_ids = [r.row_id for r in self.rows]
        if len(row_ids) != len(set(row_ids)):
            raise SchemaValidationError("rows must have unique row_ids")
        # Every row must contain exactly the columns defined in self.columns
        cols_set = set(self.columns)
        for row in self.rows:
            if set(row.cells) != cols_set:
                raise SchemaValidationError(
                    f"row '{row.row_id}' cells keys do not match table columns"
                )
        if not isinstance(self.step_index, int) or self.step_index < 0:
            raise SchemaValidationError("step_index must be non-negative integer")
        if not isinstance(self.history, list) or not all(isinstance(h, OperationRecord) for h in self.history):
            raise SchemaValidationError("history must be a list of OperationRecord")

        expected_fp = compute_table_fingerprint(self.table_id, self.columns, self.rows)
        if self.fingerprint and self.fingerprint != expected_fp:
            raise SchemaValidationError(
                f"fingerprint mismatch: got {self.fingerprint}, expected {expected_fp}"
            )
        object.__setattr__(self, "fingerprint", expected_fp)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": TABLE_TRANSFORM_SCHEMA_VERSION,
            "table_id": self.table_id,
            "columns": list(self.columns),
            "rows": [r.to_dict() for r in self.rows],
            "step_index": self.step_index,
            "history": [h.to_dict() for h in self.history],
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "TableState":
        data = _mapping(value, "TableState")
        _exact_keys(
            data,
            {
                "schema_version",
                "table_id",
                "columns",
                "rows",
                "step_index",
                "history",
                "fingerprint",
            },
            "TableState",
        )
        if data["schema_version"] != TABLE_TRANSFORM_SCHEMA_VERSION:
            raise SchemaValidationError(
                f"schema_version must be {TABLE_TRANSFORM_SCHEMA_VERSION}"
            )
        return cls(
            table_id=data["table_id"],
            columns=data["columns"],
            rows=[TableRow.from_dict(r) for r in data["rows"]],
            step_index=data["step_index"],
            history=[OperationRecord.from_dict(h) for h in data["history"]],
            fingerprint=data["fingerprint"],
        )

    @classmethod
    def create(
        cls,
        table_id: str,
        columns: List[str],
        rows: List[TableRow],
        step_index: int = 0,
        history: Optional[List[OperationRecord]] = None,
    ) -> "TableState":
        return cls(
            table_id=table_id,
            columns=columns,
            rows=rows,
            step_index=step_index,
            history=[] if history is None else history,
            fingerprint="",  # will be computed in __post_init__
        )
