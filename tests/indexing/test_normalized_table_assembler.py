import unittest

from src.indexing.document_parser import parse_document
from src.indexing.grid_builder import build_logical_table_grid
from src.indexing.normalized_table_assembler import assemble_normalized_table
from src.indexing.schemas import NumericParseStatus
from src.indexing.schemas import IssueSeverity, ParseIssue, SourceSpan
from tests.indexing.test_document_parser import make_source


class NormalizedTableAssemblerTests(unittest.TestCase):
    def test_assembles_exactly_one_normalized_cell_per_source_anchor(self):
        raw = (
            "===== PAGE 1 =====\n"
            "<table>"
            '<tr><td rowspan="2">CHỈ TIÊU</td><td colspan="2">Tại ngày</td></tr>'
            "<tr><td>2024</td><td>2023</td></tr>"
            "<tr><td>Doanh thu</td><td>1.250</td><td>1.000</td></tr>"
            "</table>\n"
        )
        source = make_source(raw)
        page = parse_document(source).pages[0]
        table = page.tables[0]
        grid = build_logical_table_grid(table)

        normalized = assemble_normalized_table(table, grid)

        self.assertEqual(len(normalized.cells), len(table.cells))
        self.assertEqual(
            {cell.source_cell_id for cell in normalized.cells},
            {cell.cell_id for cell in table.cells},
        )
        self.assertEqual(normalized.source_table_id, table.table_id)
        self.assertEqual(normalized.report_id, page.report_id)
        self.assertEqual(normalized.page_id, page.page_id)
        self.assertIs(normalized.grid, grid)

    def test_preserves_spans_numeric_raw_value_paths_and_periods(self):
        raw = (
            "===== PAGE 1 =====\n"
            "<table>"
            "<tr><td>CHỈ TIÊU</td><td>Quý 1/2024</td></tr>"
            "<tr><td>Lợi nhuận</td><td> 1.250 </td></tr>"
            "</table>\n"
        )
        source = make_source(raw)
        table = parse_document(source).pages[0].tables[0]
        grid = build_logical_table_grid(table)

        normalized = assemble_normalized_table(table, grid)
        source_value = next(cell for cell in table.cells if "1.250" in cell.extracted_text)
        value = next(cell for cell in normalized.cells if cell.source_cell_id == source_value.cell_id)

        self.assertEqual((value.rowspan, value.colspan), (source_value.rowspan, source_value.colspan))
        self.assertEqual(value.normalized_text, "1.250")
        self.assertEqual(value.numeric.raw_text, source_value.extracted_text)
        self.assertIs(value.numeric.status, NumericParseStatus.PARSED)
        self.assertEqual(value.numeric.decimal_value, "1250")
        self.assertEqual([item.label for item in value.row_path], ["Lợi nhuận"])
        self.assertEqual([item.label for item in value.column_path], ["Quý 1/2024"])
        self.assertEqual(normalized.period_labels, ["2024-Q1"])

    def test_assembly_does_not_modify_raw_source_cells(self):
        source = make_source(
            "===== PAGE 1 =====\n"
            "<table><tr><td>Chỉ tiêu</td><td>2024</td></tr>"
            "<tr><td>Doanh thu</td><td>10</td></tr></table>\n"
        )
        table = parse_document(source).pages[0].tables[0]
        before = table.to_dict()

        assemble_normalized_table(table, build_logical_table_grid(table))

        self.assertEqual(table.to_dict(), before)
        self.assertIn(table.raw_html, source.raw_text)

    def test_retains_source_table_parse_issues(self):
        source = make_source(
            "===== PAGE 1 =====\n"
            "<table><tr><td>Chỉ tiêu</td><td>2024</td></tr>"
            "<tr><td>Doanh thu</td><td>10</td></tr></table>\n"
        )
        table = parse_document(source).pages[0].tables[0]
        table.issues.append(
            ParseIssue(
                code="SOURCE_WARNING",
                severity=IssueSeverity.WARNING,
                source_span=SourceSpan(table.source_span.start, table.source_span.end),
                message="retained source issue",
            )
        )

        normalized = assemble_normalized_table(table, build_logical_table_grid(table))

        self.assertEqual(normalized.issues, table.issues)

    def test_mismatched_grid_is_rejected_instead_of_breaking_traceability(self):
        first = parse_document(
            make_source("===== PAGE 1 =====\n<table><tr><td>A</td><td>1</td></tr></table>\n", "first.txt")
        ).pages[0].tables[0]
        second = parse_document(
            make_source("===== PAGE 1 =====\n<table><tr><td>B</td><td>2</td></tr></table>\n", "second.txt")
        ).pages[0].tables[0]

        with self.assertRaisesRegex(ValueError, "grid anchors"):
            assemble_normalized_table(first, build_logical_table_grid(second))


if __name__ == "__main__":
    unittest.main()
