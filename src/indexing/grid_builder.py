"""Lossless merged-cell grid construction for TASK-022."""

from __future__ import annotations

from typing import Dict, List, Tuple

from src.indexing.schemas import (
    LogicalTableGrid,
    MergedCellAnchor,
    SourceCell,
    SourceTable,
    TableParseStatus,
)
from src.understanding.schemas import SchemaValidationError


def _ordered_cells(table: SourceTable) -> List[SourceCell]:
    coordinates = [
        (cell.source_row_index, cell.source_cell_index) for cell in table.cells
    ]
    if len(coordinates) != len(set(coordinates)):
        raise SchemaValidationError(
            "source cells must have unique row/cell coordinates"
        )
    return sorted(
        table.cells,
        key=lambda cell: (cell.source_row_index, cell.source_cell_index),
    )


def build_logical_table_grid(table: SourceTable) -> LogicalTableGrid:
    """Place each source cell once and point all covered slots to its anchor."""

    if not isinstance(table, SourceTable):
        raise SchemaValidationError("table must be a SourceTable")
    if table.parse_status is TableParseStatus.UNPARSEABLE:
        raise SchemaValidationError(
            "cannot build a logical grid for an unparseable source table"
        )

    occupied: Dict[Tuple[int, int], str] = {}
    anchors: List[MergedCellAnchor] = []
    row_cursors: Dict[int, int] = {}
    row_count = 0
    column_count = 0

    for cell in _ordered_cells(table):
        row = cell.source_row_index
        column = row_cursors.get(row, 0)
        while (row, column) in occupied:
            column += 1

        covered = [
            (covered_row, covered_column)
            for covered_row in range(row, row + cell.rowspan)
            for covered_column in range(column, column + cell.colspan)
        ]
        conflicts = [coordinate for coordinate in covered if coordinate in occupied]
        if conflicts:
            conflicting_ids = sorted({occupied[coordinate] for coordinate in conflicts})
            raise SchemaValidationError(
                "cell span overlaps existing anchors: "
                f"{cell.cell_id} conflicts with {', '.join(conflicting_ids)}"
            )

        for coordinate in covered:
            occupied[coordinate] = cell.cell_id
        anchors.append(
            MergedCellAnchor(
                source_cell_id=cell.cell_id,
                anchor_row=row,
                anchor_column=column,
                rowspan=cell.rowspan,
                colspan=cell.colspan,
            )
        )
        row_cursors[row] = column + cell.colspan
        row_count = max(row_count, row + cell.rowspan)
        column_count = max(column_count, column + cell.colspan)

    slots = [
        [occupied.get((row, column)) for column in range(column_count)]
        for row in range(row_count)
    ]
    return LogicalTableGrid(
        row_count=row_count,
        column_count=column_count,
        anchors=anchors,
        slots=slots,
    )
