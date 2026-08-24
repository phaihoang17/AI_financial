import json
import unittest

from src.indexing.document_parser import parse_document
from src.indexing.grid_builder import build_logical_table_grid
from src.indexing.normalized_table_assembler import assemble_normalized_table
from src.indexing.paragraph_extractor import extract_paragraphs
from src.indexing.retrieval_representation_builder import build_retrieval_representations
from src.indexing.scale_unit_hint_extractor import extract_scale_unit_hints
from src.indexing.table_text_linker import link_tables_to_paragraphs
from src.supervisor.schemas import EvidenceSource

from tests.indexing.test_document_parser import make_source


class RetrievalRepresentationBuilderTests(unittest.TestCase):
    def build(self):
        source = make_source(
            "===== PAGE 1 =====\nĐơn vị: triệu\n"
            "<table>"
            '<tr><td rowspan="2">CHỈ TIÊU</td><td colspan="2">Tại ngày</td></tr>'
            "<tr><td>2024</td><td>2023</td></tr>"
            "<tr><td>Doanh thu</td><td>1.250</td><td>1.000</td></tr>"
            "<tr><td>Doanh thu</td><td>2.250</td><td>2.000</td></tr>"
            "</table>\n"
        )
        document = parse_document(source)
        paragraphs = extract_paragraphs(source, document)
        tables = [
            assemble_normalized_table(table, build_logical_table_grid(table))
            for page in document.pages
            for table in page.tables
        ]
        links = link_tables_to_paragraphs(source, document, paragraphs)
        hints = extract_scale_unit_hints(source, document, tables, paragraphs, links)
        representations = build_retrieval_representations(
            source, tables, paragraphs, links, hints
        )
        return source, tables, paragraphs, links, hints, representations

    def test_builds_separate_table_and_text_representations(self):
        _, tables, paragraphs, _, _, representations = self.build()
        self.assertEqual(len(representations), len(tables) + len(paragraphs))
        self.assertEqual(
            [item.source_type for item in representations],
            [EvidenceSource.TABLE, EvidenceSource.TEXT],
        )

    def test_table_content_is_fixed_order_compact_json_without_ids(self):
        source, tables, _, _, _, representations = self.build()
        representation = representations[0]
        payload = json.loads(representation.content)

        self.assertEqual(
            list(payload),
            ["ticker", "company_name", "report_year", "statement_scope", "period_labels", "cells"],
        )
        self.assertEqual(list(payload["cells"][0]), ["row_path", "column_path", "text"])
        self.assertEqual(len(payload["cells"]), len(tables[0].cells))
        self.assertNotIn("cell_", representation.content)
        self.assertNotIn("header_", representation.content)
        self.assertEqual(representation.content, json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        self.assertEqual(payload["ticker"], source.ticker)

    def test_structured_paths_are_deduplicated_in_row_major_first_order(self):
        _, _, _, _, _, representations = self.build()
        table = representations[0]
        self.assertTrue(table.row_paths)
        self.assertTrue(table.column_paths)
        self.assertEqual(
            len(table.row_paths),
            len({tuple((item.header_id, item.label) for item in path) for path in table.row_paths}),
        )
        self.assertTrue(all(path for path in table.row_paths + table.column_paths))

    def test_text_content_links_and_hint_ids_remain_traceable(self):
        _, tables, paragraphs, links, hints, representations = self.build()
        table_rep, text_rep = representations
        text_payload = json.loads(text_rep.content)

        self.assertEqual(
            list(text_payload),
            ["ticker", "company_name", "report_year", "statement_scope", "section_ref", "kind", "text"],
        )
        self.assertEqual(text_payload["text"], paragraphs[0].normalized_text)
        self.assertEqual(table_rep.linked_source_ids, [links[0].paragraph_id])
        self.assertEqual(text_rep.linked_source_ids, [links[0].table_id])
        self.assertEqual(table_rep.scale_unit_hint_ids, [hint.hint_id for hint in hints])
        self.assertEqual(text_rep.scale_unit_hint_ids, [hint.hint_id for hint in hints if hint.source_ref == paragraphs[0].paragraph_id])
        self.assertEqual(table_rep.source_ref, tables[0].normalized_table_id)
        self.assertEqual(text_rep.source_ref, paragraphs[0].paragraph_id)

    def test_representation_ids_and_content_are_deterministic(self):
        first = self.build()[-1]
        second = self.build()[-1]
        self.assertEqual([item.to_dict() for item in first], [item.to_dict() for item in second])


if __name__ == "__main__":
    unittest.main()
