"""Canonical normalized-table assembly for TASK-025."""

from __future__ import annotations

from src.indexing.header_inference import infer_header_structure
from src.indexing.ids import make_normalized_cell_id, make_normalized_table_id
from src.indexing.schemas import (
    LogicalTableGrid,
    NORMALIZATION_VERSION,
    NormalizedCell,
    NormalizedTable,
    SourceTable,
)


def assemble_normalized_table(
    table: SourceTable, grid: LogicalTableGrid
) -> NormalizedTable:
    """Assemble one normalized cell per source anchor without changing sources."""

    inferred = infer_header_structure(table, grid)
    source_cells = {cell.cell_id: cell for cell in table.cells}
    cells = []
    for anchor in grid.anchors:
        source_cell = source_cells[anchor.source_cell_id]
        cells.append(
            NormalizedCell(
                normalized_cell_id=make_normalized_cell_id(
                    source_cell.cell_id, NORMALIZATION_VERSION
                ),
                source_cell_id=source_cell.cell_id,
                table_id=table.table_id,
                anchor_row=anchor.anchor_row,
                anchor_column=anchor.anchor_column,
                rowspan=source_cell.rowspan,
                colspan=source_cell.colspan,
                normalized_text=inferred.normalized_texts[source_cell.cell_id],
                role=inferred.roles[source_cell.cell_id],
                numeric=inferred.numeric_results[source_cell.cell_id],
                row_path=list(inferred.row_paths[source_cell.cell_id]),
                column_path=list(inferred.column_paths[source_cell.cell_id]),
                issues=[],
            )
        )
    return NormalizedTable(
        normalized_table_id=make_normalized_table_id(
            table.table_id, NORMALIZATION_VERSION
        ),
        source_table_id=table.table_id,
        report_id=table.report_id,
        page_id=table.page_id,
        normalization_version=NORMALIZATION_VERSION,
        grid=grid,
        header_hierarchy=inferred.hierarchy,
        cells=cells,
        period_labels=list(inferred.period_labels),
        issues=list(table.issues),
    )
