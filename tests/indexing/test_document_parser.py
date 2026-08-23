from pathlib import Path
import unittest

from src.indexing.document_parser import parse_document
from src.indexing.ids import content_sha256, make_report_id
from src.indexing.schemas import (
    IssueSeverity,
    M2A_SCHEMA_VERSION,
    ReportSource,
    TableParseStatus,
)


FIXTURE_ROOT = Path(__file__).with_name("fixtures")


def make_source(raw_text, name="report.txt"):
    return ReportSource(
        schema_version=M2A_SCHEMA_VERSION,
        corpus_id="test-corpus",
        report_id=make_report_id("test-corpus", name),
        source_ref=name,
        ticker="AAA",
        company_name=None,
        report_year=2024,
        document_name=name.removesuffix(".txt"),
        statement_scope=None,
        raw_text=raw_text,
        content_sha256=content_sha256(raw_text),
    )


def fixture_source(name):
    raw = (FIXTURE_ROOT / name).read_bytes().decode("utf-8", errors="strict")
    return make_source(raw, name)


class PageExtractionTests(unittest.TestCase):
    def test_pages_preserve_exact_report_offsets_and_raw_slices(self):
        source = fixture_source("basic_inline_table_ocr.txt")
        document = parse_document(source)

        self.assertEqual([page.page_number for page in document.pages], [1, 2])
        self.assertEqual([page.page_index for page in document.pages], [0, 1])
        for page in document.pages:
            self.assertEqual(
                source.raw_text[page.source_span.start : page.source_span.end],
                page.raw_text,
            )
            self.assertTrue(page.raw_text.startswith("===== PAGE "))

    def test_missing_page_markers_returns_fatal_issue_without_fabricated_page(self):
        source = make_source("BÁO CÁO TÀI CHÍNH\n<table></table>")
        document = parse_document(source)

        self.assertEqual(document.pages, [])
        self.assertEqual(document.issues[0].code, "FATAL_NO_PAGE_MARKERS")
        self.assertIs(document.issues[0].severity, IssueSeverity.FATAL)

    def test_duplicate_and_non_monotonic_numbers_preserve_source_order(self):
        source = make_source(
            "===== PAGE 2 =====\nA\n===== PAGE 2 =====\nB\n"
        )
        document = parse_document(source)

        self.assertEqual([page.page_number for page in document.pages], [2, 2])
        self.assertEqual(
            [issue.code for issue in document.pages[1].issues],
            ["DUPLICATE_PAGE_NUMBER", "NON_MONOTONIC_PAGE_NUMBER"],
        )


class TableAndCellExtractionTests(unittest.TestCase):
    def test_table_and_cells_preserve_exact_html_spans(self):
        source = fixture_source("basic_inline_table_ocr.txt")
        document = parse_document(source)
        table = document.pages[0].tables[0]

        self.assertIs(table.parse_status, TableParseStatus.VALID)
        self.assertEqual(
            source.raw_text[table.source_span.start : table.source_span.end],
            table.raw_html,
        )
        self.assertEqual(len(table.cells), 6)
        for cell in table.cells:
            self.assertEqual(
                source.raw_text[cell.source_span.start : cell.source_span.end],
                cell.raw_html,
            )
        self.assertEqual(table.cells[-2].extracted_text, "40.548.813.597")

    def test_merged_attributes_remain_on_source_anchor_cells(self):
        source = fixture_source("merged_header_ocr.txt")
        table = parse_document(source).pages[0].tables[0]

        first = table.cells[0]
        parent_header = table.cells[2]
        self.assertEqual((first.rowspan, first.colspan), (2, 2))
        self.assertEqual((parent_header.rowspan, parent_header.colspan), (1, 2))
        self.assertEqual(parent_header.extracted_text, "Tại ngày")

    def test_inline_caption_remains_on_source_table(self):
        raw = (
            "===== PAGE 1 =====\n"
            "<table><caption>Đơn vị: triệu đồng</caption>"
            "<tr><td>Doanh thu</td><td>1.250</td></tr></table>\n"
        )
        source = make_source(raw)
        table = parse_document(source).pages[0].tables[0]

        self.assertEqual(table.inline_caption_text, "Đơn vị: triệu đồng")
        self.assertEqual(
            source.raw_text[
                table.inline_caption_span.start : table.inline_caption_span.end
            ],
            "<caption>Đơn vị: triệu đồng</caption>",
        )

    def test_unclosed_table_is_retained_without_partial_cells(self):
        source = fixture_source("malformed_inline_table_ocr.txt")
        page = parse_document(source).pages[0]
        table = page.tables[0]

        self.assertIs(table.parse_status, TableParseStatus.UNPARSEABLE)
        self.assertEqual(table.cells, [])
        self.assertIn("rowspan=\"two\"", table.raw_html)
        self.assertIn("UNCLOSED_TABLE", [issue.code for issue in table.issues])

    def test_invalid_span_does_not_default_or_guess(self):
        raw = (
            "===== PAGE 1 =====\n"
            "<table><tr><td rowspan=\"two\">Doanh thu</td><td>1.250</td>"
            "</tr></table>\n"
        )
        table = parse_document(make_source(raw)).pages[0].tables[0]

        self.assertIs(table.parse_status, TableParseStatus.UNPARSEABLE)
        self.assertEqual(table.cells, [])
        self.assertIn("INVALID_CELL_SPAN", [issue.code for issue in table.issues])

    def test_table_is_never_merged_across_page_boundary(self):
        raw = (
            "===== PAGE 1 =====\n"
            "<table><tr><td>A</td></tr>\n\n"
            "===== PAGE 2 =====\n"
            "</table>\n"
        )
        document = parse_document(make_source(raw))

        self.assertEqual(len(document.pages[0].tables), 1)
        self.assertIs(
            document.pages[0].tables[0].parse_status,
            TableParseStatus.UNPARSEABLE,
        )
        self.assertEqual(document.pages[1].tables, [])
        self.assertIn(
            "UNMATCHED_TABLE_END",
            [issue.code for issue in document.pages[1].issues],
        )


if __name__ == "__main__":
    unittest.main()
