import unittest

from src.indexing.document_parser import parse_document
from src.indexing.grid_builder import build_logical_table_grid
from src.indexing.normalized_table_assembler import assemble_normalized_table
from src.indexing.paragraph_extractor import extract_paragraphs
from src.indexing.scale_unit_hint_extractor import extract_scale_unit_hints
from src.indexing.schemas import ScaleHintSource, ScaleHintStatus
from src.indexing.table_text_linker import link_tables_to_paragraphs

from tests.indexing.test_document_parser import make_source


def extract(raw):
    source = make_source(raw)
    document = parse_document(source)
    paragraphs = extract_paragraphs(source, document)
    links = link_tables_to_paragraphs(source, document, paragraphs)
    normalized = [
        assemble_normalized_table(table, build_logical_table_grid(table))
        for page in document.pages
        for table in page.tables
    ]
    hints = extract_scale_unit_hints(
        source, document, normalized, paragraphs, links
    )
    return source, document, normalized, paragraphs, links, hints


class ScaleUnitHintExtractorTests(unittest.TestCase):
    def test_extracts_exact_occurrences_with_canonical_source_refs(self):
        source, document, normalized, paragraphs, links, hints = extract(
            "===== PAGE 1 =====\nĐơn vị: tỷ\n"
            "<table><caption>Đơn vị nghìn</caption>"
            "<tr><td>CHỈ TIÊU</td><td>Đơn vị triệu</td></tr>"
            "<tr><td>Biên lợi nhuận</td><td>10%</td></tr></table>\n"
        )
        table = document.pages[0].tables[0]
        header_ids = {
            source_id
            for node in normalized[0].header_hierarchy.nodes
            for source_id in node.source_cell_ids
        }

        self.assertEqual(len(links), 1)
        self.assertEqual(
            {hint.source_kind for hint in hints},
            {ScaleHintSource.TEXT, ScaleHintSource.CAPTION, ScaleHintSource.HEADER, ScaleHintSource.CELL},
        )
        for hint in hints:
            self.assertEqual(
                source.raw_text[hint.source_span.start : hint.source_span.end],
                hint.raw_hint_text,
            )
            self.assertIsNone(hint.unit_candidate)
            if hint.source_kind is ScaleHintSource.TEXT:
                self.assertEqual(hint.source_ref, paragraphs[0].paragraph_id)
                self.assertEqual(hint.table_id, table.table_id)
            elif hint.source_kind is ScaleHintSource.CAPTION:
                self.assertEqual(hint.source_ref, table.table_id)
            else:
                self.assertIn(hint.source_ref, {cell.cell_id for cell in table.cells})
                self.assertEqual(
                    hint.source_kind is ScaleHintSource.HEADER,
                    hint.source_ref in header_ids,
                )

    def test_conflicting_table_hints_remain_separate_and_ambiguous(self):
        _, _, _, _, _, hints = extract(
            "===== PAGE 1 =====\nĐơn vị: triệu\n"
            "<table><caption>Đơn vị tỷ</caption>"
            "<tr><td>Chỉ tiêu</td><td>2024</td></tr>"
            "<tr><td>Doanh thu</td><td>10</td></tr></table>\n"
        )
        self.assertEqual(len(hints), 2)
        self.assertEqual({hint.scale_candidate.value for hint in hints}, {"MILLION", "BILLION"})
        self.assertTrue(all(hint.status is ScaleHintStatus.AMBIGUOUS for hint in hints))

    def test_unlinked_text_hint_has_null_table_id(self):
        _, _, _, paragraphs, links, hints = extract(
            "===== PAGE 1 =====\nĐơn vị: ngàn.\n\nUnrelated narrative.\n"
        )
        self.assertEqual(links, [])
        self.assertEqual(len(paragraphs), 2)
        self.assertEqual(len(hints), 1)
        self.assertIsNone(hints[0].table_id)
        self.assertIs(hints[0].status, ScaleHintStatus.EXTRACTED)

    def test_complete_tokens_only_and_one_hint_per_occurrence(self):
        _, _, _, _, _, hints = extract(
            "===== PAGE 1 =====\ntriệu TRIỆU siêutriệu nghìnđồng phần   trăm ％ VND\n"
        )
        self.assertEqual(
            [hint.scale_candidate.value for hint in hints],
            ["MILLION", "MILLION", "PERCENT", "PERCENT"],
        )
        self.assertEqual([hint.normalized_hint_text for hint in hints], ["triệu", "triệu", "phần trăm", "%"])


if __name__ == "__main__":
    unittest.main()
