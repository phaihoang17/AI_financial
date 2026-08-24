import copy
import unittest

from src.indexing.ids import content_sha256
from src.indexing.schemas import (
    HeaderHierarchy,
    LogicalTableGrid,
    M2A_SCHEMA_VERSION,
    NORMALIZATION_VERSION,
    NumericParseResult,
    NormalizedTable,
    Paragraph,
    ParsedDocument,
    ReportSource,
    REPRESENTATION_VERSION,
    RetrievalRepresentation,
    ScaleHintSource,
    ScaleUnitHint,
    TableTextLink,
)
from src.understanding.schemas import SchemaValidationError


RAW_REPORT = """===== PAGE 1 =====
BÁO CÁO TÀI CHÍNH HỢP NHẤT

Đơn vị: triệu VND

<table><tr><td rowspan=\"2\">CHỈ TIÊU</td><td colspan=\"2\">Tại ngày</td></tr><tr><td>2024</td><td>2023</td></tr></table>
"""


def report_payload():
    return {
        "schema_version": M2A_SCHEMA_VERSION,
        "corpus_id": "ViFinQA",
        "report_id": "report_1",
        "source_ref": "AAA/2024/AAA_financial_statements_2024_consolidated/report.txt",
        "ticker": "AAA",
        "company_name": "CTCP Nhựa An Phát Xanh",
        "report_year": 2024,
        "document_name": "AAA_financial_statements_2024_consolidated",
        "statement_scope": "HOP_NHAT",
        "raw_text": RAW_REPORT,
        "content_sha256": content_sha256(RAW_REPORT),
    }


def source_cell_payload(cell_id="cell_1", row=0, column=0, rowspan=2, colspan=1):
    return {
        "cell_id": cell_id,
        "table_id": "table_1",
        "source_row_index": row,
        "source_cell_index": column,
        "source_span": {"start": 80, "end": 120},
        "raw_html": "<td>CHỈ TIÊU</td>",
        "raw_inner_html": "CHỈ TIÊU",
        "extracted_text": "CHỈ TIÊU",
        "rowspan": rowspan,
        "colspan": colspan,
    }


def source_table_payload():
    return {
        "table_id": "table_1",
        "report_id": "report_1",
        "page_id": "page_1",
        "table_index": 0,
        "source_span": {"start": 70, "end": len(RAW_REPORT)},
        "raw_html": RAW_REPORT[70:],
        "parse_status": "VALID",
        "inline_caption_span": None,
        "inline_caption_text": None,
        "cells": [source_cell_payload()],
        "issues": [],
    }


def parsed_document_payload():
    return {
        "report_id": "report_1",
        "content_sha256": content_sha256(RAW_REPORT),
        "pages": [
            {
                "page_id": "page_1",
                "report_id": "report_1",
                "page_index": 0,
                "page_number": 1,
                "source_span": {"start": 0, "end": len(RAW_REPORT)},
                "content_span": {"start": 21, "end": len(RAW_REPORT)},
                "raw_text": RAW_REPORT,
                "tables": [source_table_payload()],
                "issues": [],
            }
        ],
        "issues": [],
    }


def grid_payload():
    return {
        "row_count": 2,
        "column_count": 2,
        "anchors": [
            {
                "source_cell_id": "cell_1",
                "anchor_row": 0,
                "anchor_column": 0,
                "rowspan": 2,
                "colspan": 1,
            },
            {
                "source_cell_id": "cell_2",
                "anchor_row": 0,
                "anchor_column": 1,
                "rowspan": 1,
                "colspan": 1,
            },
            {
                "source_cell_id": "cell_3",
                "anchor_row": 1,
                "anchor_column": 1,
                "rowspan": 1,
                "colspan": 1,
            },
        ],
        "slots": [["cell_1", "cell_2"], ["cell_1", "cell_3"]],
    }


def header_hierarchy_payload():
    return {
        "nodes": [
            {
                "header_id": "header_period",
                "axis": "COLUMN",
                "label": "Tại ngày",
                "source_cell_ids": ["cell_2"],
                "parent_header_id": None,
                "depth": 0,
                "resolution": "EXPLICIT",
            },
            {
                "header_id": "header_2024",
                "axis": "COLUMN",
                "label": "2024",
                "source_cell_ids": ["cell_3"],
                "parent_header_id": "header_period",
                "depth": 1,
                "resolution": "EXPLICIT",
            },
        ]
    }


def normalized_cell_payload():
    return {
        "normalized_cell_id": "ncell_3",
        "source_cell_id": "cell_3",
        "table_id": "table_1",
        "anchor_row": 1,
        "anchor_column": 1,
        "rowspan": 1,
        "colspan": 1,
        "normalized_text": "1.250,75",
        "role": "DATA",
        "numeric": {
            "status": "PARSED",
            "raw_text": "1.250,75",
            "normalized_lexeme": "1250.75",
            "decimal_value": "1250.75",
            "percent_literal": False,
        },
        "row_path": [],
        "column_path": [
            {"header_id": "header_period", "label": "Tại ngày"},
            {"header_id": "header_2024", "label": "2024"},
        ],
        "issues": [],
    }


def normalized_table_payload():
    corner_cell = {
        "normalized_cell_id": "ncell_1",
        "source_cell_id": "cell_1",
        "table_id": "table_1",
        "anchor_row": 0,
        "anchor_column": 0,
        "rowspan": 2,
        "colspan": 1,
        "normalized_text": "CHỈ TIÊU",
        "role": "CORNER",
        "numeric": {
            "status": "NOT_NUMERIC",
            "raw_text": "CHỈ TIÊU",
            "normalized_lexeme": None,
            "decimal_value": None,
            "percent_literal": False,
        },
        "row_path": [],
        "column_path": [],
        "issues": [],
    }
    parent_header_cell = {
        "normalized_cell_id": "ncell_2",
        "source_cell_id": "cell_2",
        "table_id": "table_1",
        "anchor_row": 0,
        "anchor_column": 1,
        "rowspan": 1,
        "colspan": 1,
        "normalized_text": "Tại ngày",
        "role": "COLUMN_HEADER",
        "numeric": {
            "status": "NOT_NUMERIC",
            "raw_text": "Tại ngày",
            "normalized_lexeme": None,
            "decimal_value": None,
            "percent_literal": False,
        },
        "row_path": [],
        "column_path": [{"header_id": "header_period", "label": "Tại ngày"}],
        "issues": [],
    }
    return {
        "normalized_table_id": "ntable_1",
        "source_table_id": "table_1",
        "report_id": "report_1",
        "page_id": "page_1",
        "normalization_version": NORMALIZATION_VERSION,
        "grid": grid_payload(),
        "header_hierarchy": header_hierarchy_payload(),
        "cells": [normalized_cell_payload(), corner_cell, parent_header_cell],
        "period_labels": ["2024"],
        "issues": [],
    }


def table_representation_payload():
    return {
        "representation_id": "representation_1",
        "representation_version": REPRESENTATION_VERSION,
        "source_type": "TABLE",
        "granularity": "TABLE",
        "source_ref": "ntable_1",
        "report_id": "report_1",
        "page_ids": ["page_1"],
        "table_id": "table_1",
        "paragraph_id": None,
        "ticker": "AAA",
        "company_name": "CTCP Nhựa An Phát Xanh",
        "report_year": 2024,
        "statement_scope": "HOP_NHAT",
        "period_labels": ["2024"],
        "content": '{"ticker":"AAA","company_name":"CTCP Nhựa An Phát Xanh","report_year":2024,"statement_scope":"HOP_NHAT","period_labels":["2024"],"cells":[{"row_path":[],"column_path":["Tại ngày","2024"],"text":"1.250,75"}]}',
        "row_paths": [],
        "column_paths": [
            [
                {"header_id": "header_period", "label": "Tại ngày"},
                {"header_id": "header_2024", "label": "2024"},
            ]
        ],
        "linked_source_ids": ["paragraph_caption"],
        "scale_unit_hint_ids": ["hint_1"],
    }


class ReportAndSourceSchemaTests(unittest.TestCase):
    def test_report_source_round_trip_preserves_raw_ocr(self):
        payload = report_payload()
        source = ReportSource.from_dict(copy.deepcopy(payload))
        self.assertEqual(source.raw_text, RAW_REPORT)
        self.assertEqual(source.to_dict(), payload)

    def test_ticker_and_report_year_are_required_for_this_corpus_contract(self):
        for field_name in ("ticker", "report_year"):
            with self.subTest(field_name=field_name):
                payload = report_payload()
                payload[field_name] = None
                with self.assertRaises(SchemaValidationError):
                    ReportSource.from_dict(payload)

    def test_report_hash_must_match_raw_text(self):
        payload = report_payload()
        payload["raw_text"] += "changed"
        with self.assertRaises(SchemaValidationError):
            ReportSource.from_dict(payload)

    def test_parsed_document_round_trip(self):
        payload = parsed_document_payload()
        self.assertEqual(ParsedDocument.from_dict(payload).to_dict(), payload)

    def test_unparseable_table_cannot_expose_cells(self):
        payload = source_table_payload()
        payload["parse_status"] = "UNPARSEABLE"
        with self.assertRaises(SchemaValidationError):
            ParsedDocument.from_dict({
                **parsed_document_payload(),
                "pages": [{**parsed_document_payload()["pages"][0], "tables": [payload]}],
            })


class NormalizedSchemaTests(unittest.TestCase):
    def test_merged_grid_references_single_anchor(self):
        grid = LogicalTableGrid.from_dict(grid_payload())
        self.assertEqual(grid.slots[0][0], "cell_1")
        self.assertEqual(grid.slots[1][0], "cell_1")

    def test_grid_rejects_a_slot_that_does_not_reference_its_anchor(self):
        payload = grid_payload()
        payload["slots"][1][0] = None
        with self.assertRaises(SchemaValidationError):
            LogicalTableGrid.from_dict(payload)

    def test_header_hierarchy_round_trip(self):
        payload = header_hierarchy_payload()
        self.assertEqual(HeaderHierarchy.from_dict(payload).to_dict(), payload)

    def test_normalized_table_preserves_structured_header_paths(self):
        payload = normalized_table_payload()
        table = NormalizedTable.from_dict(payload)
        self.assertEqual(
            [item.header_id for item in table.cells[0].column_path],
            ["header_period", "header_2024"],
        )
        self.assertEqual(table.to_dict(), payload)

    def test_numeric_result_uses_lossless_decimal_string(self):
        payload = normalized_cell_payload()["numeric"]
        result = NumericParseResult.from_dict(payload)
        self.assertEqual(result.decimal_value, "1250.75")
        self.assertFalse(result.percent_literal)

    def test_non_parsed_numeric_result_cannot_carry_a_value(self):
        payload = normalized_cell_payload()["numeric"]
        payload["status"] = "AMBIGUOUS"
        with self.assertRaises(SchemaValidationError):
            NumericParseResult.from_dict(payload)


class TextHintAndRepresentationSchemaTests(unittest.TestCase):
    def test_paragraph_and_caption_link_round_trip(self):
        paragraph_payload = {
            "paragraph_id": "paragraph_caption",
            "report_id": "report_1",
            "page_id": "page_1",
            "paragraph_index": 0,
            "source_span": {"start": 30, "end": 55},
            "raw_text": "Đơn vị tính: triệu đồng.",
            "normalized_text": "Đơn vị tính: triệu đồng.",
            "section_ref": None,
            "kind": "BODY",
        }
        link_payload = {
            "link_id": "link_caption",
            "table_id": "table_1",
            "paragraph_id": "paragraph_caption",
            "relation": "CAPTION",
            "basis": "IMMEDIATE_BEFORE",
            "evidence_span": {"start": 30, "end": 55},
            "issues": [],
        }

        self.assertEqual(Paragraph.from_dict(paragraph_payload).to_dict(), paragraph_payload)
        self.assertEqual(TableTextLink.from_dict(link_payload).to_dict(), link_payload)

    def test_inline_caption_is_not_a_table_text_link_basis(self):
        payload = {
            "link_id": "link_caption",
            "table_id": "table_1",
            "paragraph_id": "paragraph_caption",
            "relation": "CAPTION",
            "basis": "INLINE_CAPTION",
            "evidence_span": {"start": 30, "end": 55},
            "issues": [],
        }
        with self.assertRaises(SchemaValidationError):
            TableTextLink.from_dict(payload)

    def test_caption_hint_is_distinct_from_text(self):
        payload = {
            "hint_id": "hint_1",
            "report_id": "report_1",
            "page_id": "page_1",
            "table_id": "table_1",
            "source_kind": "CAPTION",
            "source_ref": "paragraph_caption",
            "source_span": {"start": 30, "end": 48},
            "raw_hint_text": "triệu VND",
            "normalized_hint_text": "triệu VND",
            "scale_candidate": "MILLION",
            "unit_candidate": "VND",
            "status": "EXTRACTED",
        }
        hint = ScaleUnitHint.from_dict(payload)
        self.assertIs(hint.source_kind, ScaleHintSource.CAPTION)
        self.assertEqual(hint.to_dict(), payload)

    def test_offline_hint_contract_rejects_question_source(self):
        payload = {
            "hint_id": "hint_1", "report_id": "report_1", "page_id": "page_1",
            "table_id": "table_1", "source_kind": "QUESTION", "source_ref": "question",
            "source_span": {"start": 0, "end": 1}, "raw_hint_text": "%",
            "normalized_hint_text": "%", "scale_candidate": "PERCENT",
            "unit_candidate": None, "status": "EXTRACTED",
        }
        with self.assertRaises(SchemaValidationError):
            ScaleUnitHint.from_dict(payload)

    def test_table_representation_uses_structured_header_paths(self):
        payload = table_representation_payload()
        representation = RetrievalRepresentation.from_dict(payload)
        path = representation.column_paths[0]
        self.assertEqual([(entry.header_id, entry.label) for entry in path], [
            ("header_period", "Tại ngày"), ("header_2024", "2024")
        ])
        self.assertEqual(representation.to_dict(), payload)

    def test_flat_string_header_paths_are_rejected(self):
        payload = table_representation_payload()
        payload["column_paths"] = [["Tại ngày", "2024"]]
        with self.assertRaises(SchemaValidationError):
            RetrievalRepresentation.from_dict(payload)


if __name__ == "__main__":
    unittest.main()
