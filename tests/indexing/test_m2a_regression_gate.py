import unittest

from src.indexing.document_parser import parse_document
from src.indexing.grid_builder import build_logical_table_grid
from src.indexing.normalized_table_assembler import assemble_normalized_table
from src.indexing.numeric_parser import parse_numeric_string
from src.indexing.paragraph_extractor import extract_paragraphs
from src.indexing.scale_unit_hint_extractor import extract_scale_unit_hints
from src.indexing.table_text_linker import link_tables_to_paragraphs
from src.indexing.schemas import NumericParseStatus, TableParseStatus

from tests.indexing.test_document_parser import fixture_source, make_source


class M2ARegressionGate(unittest.TestCase):
    def test_traceable_end_to_end_normalization(self):
        source = make_source(
            "===== PAGE 1 =====\nĐơn vị: triệu\n"
            "<table><caption>Quy mô nghìn</caption>"
            '<tr><td rowspan="2">CHỈ TIÊU</td><td colspan="2">Tại ngày</td></tr>'
            "<tr><td>2024</td><td>2023</td></tr>"
            "<tr><td>Doanh thu</td><td> 1.250 </td><td>1.000</td></tr></table>\n"
        )
        raw_before = source.raw_text
        document = parse_document(source)
        paragraphs = extract_paragraphs(source, document)
        table = document.pages[0].tables[0]
        grid = build_logical_table_grid(table)
        normalized = assemble_normalized_table(table, grid)
        links = link_tables_to_paragraphs(source, document, paragraphs)
        hints = extract_scale_unit_hints(source, document, [normalized], paragraphs, links)

        self.assertEqual(source.raw_text, raw_before)
        for cell in table.cells:
            self.assertEqual(source.raw_text[cell.source_span.start:cell.source_span.end], cell.raw_html)
        self.assertEqual(len(normalized.cells), len(table.cells))
        value = next(cell for cell in normalized.cells if cell.normalized_text == "1.250")
        self.assertEqual(value.numeric.raw_text, " 1.250 ")
        self.assertEqual(value.numeric.decimal_value, "1250")
        self.assertEqual([item.label for item in value.row_path], ["Doanh thu"])
        self.assertEqual([item.label for item in value.column_path], ["Tại ngày", "2024"])
        self.assertEqual(normalized.period_labels, ["2024", "2023"])
        self.assertEqual(len(links), 1)
        self.assertTrue(hints)
        self.assertTrue(all(hint.report_id == source.report_id for hint in hints))

    def test_structural_holes_and_merged_anchors_are_preserved(self):
        source = make_source(
            "===== PAGE 1 =====\n"
            "<table><tr><td>A</td></tr><tr><td>B</td><td>C</td></tr></table>\n"
        )
        table = parse_document(source).pages[0].tables[0]
        grid = build_logical_table_grid(table)
        self.assertIsNone(grid.slots[0][1])
        self.assertEqual(len(grid.anchors), len(table.cells))

    def test_malformed_and_ambiguous_inputs_never_fabricate_values(self):
        malformed_source = fixture_source("malformed_inline_table_ocr.txt")
        malformed = parse_document(malformed_source).pages[0].tables[0]
        self.assertIs(malformed.parse_status, TableParseStatus.UNPARSEABLE)
        self.assertEqual(malformed.cells, [])
        numeric = parse_numeric_string("12.50.000")
        self.assertIs(numeric.status, NumericParseStatus.MALFORMED)
        self.assertIsNone(numeric.decimal_value)

        source = make_source(
            "===== PAGE 1 =====\nBefore.\n"
            "<table><tr><td>A</td></tr></table>\nAfter.\n"
        )
        document = parse_document(source)
        paragraphs = extract_paragraphs(source, document)
        self.assertEqual(link_tables_to_paragraphs(source, document, paragraphs), [])


if __name__ == "__main__":
    unittest.main()
