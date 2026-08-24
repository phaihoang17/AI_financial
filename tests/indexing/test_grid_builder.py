import unittest

from src.indexing.grid_builder import build_logical_table_grid
from src.indexing.schemas import (
    LogicalTableGrid,
    MergedCellAnchor,
    SourceCell,
    SourceSpan,
    SourceTable,
    TableParseStatus,
)
from src.understanding.schemas import SchemaValidationError


def make_cell(cell_id, row, index, rowspan=1, colspan=1):
    return SourceCell(
        cell_id=cell_id,
        table_id="table_1",
        source_row_index=row,
        source_cell_index=index,
        source_span=SourceSpan(index, index + 1),
        raw_html=f"<td>{cell_id}</td>",
        raw_inner_html=cell_id,
        extracted_text=cell_id,
        rowspan=rowspan,
        colspan=colspan,
    )


def make_table(cells, status=TableParseStatus.VALID):
    return SourceTable(
        table_id="table_1",
        report_id="report_1",
        page_id="page_1",
        table_index=0,
        source_span=SourceSpan(0, 100),
        raw_html="<table></table>",
        parse_status=status,
        inline_caption_span=None,
        inline_caption_text=None,
        cells=cells,
        issues=[],
    )


class LogicalGridBuilderTests(unittest.TestCase):
    def test_merged_slots_reference_one_source_anchor_without_copying_values(self):
        table = make_table(
            [
                make_cell("cell_a", 0, 0, rowspan=2, colspan=2),
                make_cell("cell_b", 0, 1),
                make_cell("cell_c", 1, 0),
            ]
        )

        grid = build_logical_table_grid(table)

        self.assertEqual(grid.row_count, 2)
        self.assertEqual(grid.column_count, 3)
        self.assertEqual(
            grid.slots,
            [["cell_a", "cell_a", "cell_b"], ["cell_a", "cell_a", "cell_c"]],
        )
        self.assertEqual([anchor.source_cell_id for anchor in grid.anchors], ["cell_a", "cell_b", "cell_c"])
        self.assertEqual((grid.anchors[0].rowspan, grid.anchors[0].colspan), (2, 2))

    def test_row_major_placement_is_deterministic_and_holes_remain_null(self):
        table = make_table(
            [
                make_cell("cell_c", 1, 1),
                make_cell("cell_a", 0, 0),
                make_cell("cell_b", 1, 0),
            ]
        )

        grid = build_logical_table_grid(table)

        self.assertEqual([anchor.source_cell_id for anchor in grid.anchors], ["cell_a", "cell_b", "cell_c"])
        self.assertEqual(grid.slots, [["cell_a", None], ["cell_b", "cell_c"]])

    def test_overlapping_spans_fail_instead_of_shifting_or_repairing(self):
        table = make_table(
            [
                make_cell("left", 0, 0, rowspan=2),
                make_cell("middle", 0, 1),
                make_cell("right", 0, 2, rowspan=2),
                make_cell("overlap", 1, 0, colspan=2),
            ]
        )

        with self.assertRaisesRegex(SchemaValidationError, "overlaps"):
            build_logical_table_grid(table)

    def test_unparseable_table_has_no_guessed_grid(self):
        with self.assertRaisesRegex(SchemaValidationError, "unparseable"):
            build_logical_table_grid(make_table([], TableParseStatus.UNPARSEABLE))

    def test_schema_rejects_anchor_reference_outside_declared_span(self):
        with self.assertRaisesRegex(SchemaValidationError, "only in the slots"):
            LogicalTableGrid(
                row_count=1,
                column_count=2,
                anchors=[MergedCellAnchor("cell_a", 0, 0, 1, 1)],
                slots=[["cell_a", "cell_a"]],
            )


if __name__ == "__main__":
    unittest.main()
