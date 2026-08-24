import unittest

from src.indexing.document_parser import parse_document
from src.indexing.paragraph_extractor import extract_paragraphs
from src.indexing.table_text_linker import link_tables_to_paragraphs
from src.indexing.schemas import TableTextLinkBasis, TableTextRelation

from tests.indexing.test_document_parser import make_source


def link(raw):
    source = make_source(raw)
    document = parse_document(source)
    paragraphs = extract_paragraphs(source, document)
    return source, document, paragraphs, link_tables_to_paragraphs(
        source, document, paragraphs
    )


class TableTextLinkerTests(unittest.TestCase):
    def test_links_unique_immediate_before_paragraph(self):
        source, document, paragraphs, links = link(
            "===== PAGE 1 =====\nContext paragraph.\n"
            "<table><tr><td>A</td></tr></table>\n"
        )
        table = document.pages[0].tables[0]

        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].table_id, table.table_id)
        self.assertEqual(links[0].paragraph_id, paragraphs[0].paragraph_id)
        self.assertIs(links[0].relation, TableTextRelation.ADJACENT_CONTEXT)
        self.assertIs(links[0].basis, TableTextLinkBasis.IMMEDIATE_BEFORE)
        self.assertEqual(links[0].evidence_span, paragraphs[0].source_span)
        self.assertTrue(
            source.raw_text[paragraphs[0].source_span.end : table.source_span.start].isspace()
        )

    def test_links_unique_immediate_after_paragraph(self):
        _, _, paragraphs, links = link(
            "===== PAGE 1 =====\n"
            "<table><tr><td>A</td></tr></table>\nAfter table.\n"
        )
        self.assertEqual(len(links), 1)
        self.assertIs(links[0].basis, TableTextLinkBasis.IMMEDIATE_AFTER)
        self.assertEqual(links[0].paragraph_id, paragraphs[0].paragraph_id)

    def test_table_with_before_and_after_candidates_is_unlinked(self):
        _, _, _, links = link(
            "===== PAGE 1 =====\nBefore.\n"
            "<table><tr><td>A</td></tr></table>\nAfter.\n"
        )
        self.assertEqual(links, [])

    def test_paragraph_adjacent_to_two_tables_is_unlinked(self):
        _, _, _, links = link(
            "===== PAGE 1 =====\n"
            "<table><tr><td>A</td></tr></table>\nMiddle.\n"
            "<table><tr><td>B</td></tr></table>\n"
        )
        self.assertEqual(links, [])

    def test_inline_caption_never_creates_paragraph_or_link(self):
        _, _, paragraphs, links = link(
            "===== PAGE 1 =====\n"
            "<table><caption>Đơn vị: triệu</caption><tr><td>A</td></tr></table>\n"
        )
        self.assertEqual(paragraphs, [])
        self.assertEqual(links, [])


if __name__ == "__main__":
    unittest.main()
