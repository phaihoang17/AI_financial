import unittest

from src.indexing.document_parser import parse_document
from src.indexing.paragraph_extractor import extract_paragraphs

from test_document_parser import fixture_source, make_source


class ParagraphExtractionTests(unittest.TestCase):
    def test_extracts_page_local_paragraphs_outside_tables(self):
        source = fixture_source("basic_inline_table_ocr.txt")
        document = parse_document(source)
        paragraphs = extract_paragraphs(source, document)

        self.assertEqual(len(paragraphs), 6)
        self.assertEqual(
            [paragraph.paragraph_index for paragraph in paragraphs],
            [0, 1, 2, 3, 0, 1],
        )
        self.assertEqual(
            [paragraph.page_id for paragraph in paragraphs[:4]],
            [document.pages[0].page_id] * 4,
        )
        self.assertEqual(
            [paragraph.page_id for paragraph in paragraphs[4:]],
            [document.pages[1].page_id] * 2,
        )
        self.assertFalse(any("<table" in paragraph.raw_text for paragraph in paragraphs))
        self.assertFalse(any("40.548.813.597" in paragraph.raw_text for paragraph in paragraphs))

    def test_every_paragraph_is_an_exact_raw_source_slice(self):
        source = fixture_source("basic_inline_table_ocr.txt")
        paragraphs = extract_paragraphs(source, parse_document(source))

        for paragraph in paragraphs:
            self.assertEqual(
                source.raw_text[
                    paragraph.source_span.start : paragraph.source_span.end
                ],
                paragraph.raw_text,
            )
            self.assertEqual(paragraph.normalized_text, paragraph.raw_text)
            self.assertIsNone(paragraph.section_ref)

    def test_table_boundary_splits_adjacent_text_without_proximity_linking(self):
        raw = (
            "===== PAGE 1 =====\n"
            "Before<table><tr><td>A</td></tr></table>After"
        )
        source = make_source(raw)
        paragraphs = extract_paragraphs(source, parse_document(source))

        self.assertEqual([paragraph.raw_text for paragraph in paragraphs], ["Before", "After"])

    def test_malformed_table_does_not_consume_following_narrative(self):
        source = fixture_source("malformed_inline_table_ocr.txt")
        paragraphs = extract_paragraphs(source, parse_document(source))

        self.assertTrue(
            any(
                paragraph.raw_text.startswith("Đoạn văn sau bảng")
                for paragraph in paragraphs
            )
        )
        self.assertFalse(any("12.34.567" in paragraph.raw_text for paragraph in paragraphs))

    def test_inline_caption_does_not_create_synthetic_paragraph(self):
        raw = (
            "===== PAGE 1 =====\n"
            "<table><caption>Đơn vị: triệu đồng</caption>"
            "<tr><td>A</td></tr></table>\n"
            "Narrative paragraph.\n"
        )
        source = make_source(raw)
        paragraphs = extract_paragraphs(source, parse_document(source))

        self.assertEqual([paragraph.raw_text for paragraph in paragraphs], ["Narrative paragraph."])

    def test_extraction_does_not_normalize_internal_ocr_whitespace(self):
        raw = "===== PAGE 1 =====\nDòng\tOCR\u00a0giữ nguyên\n"
        source = make_source(raw)
        paragraph = extract_paragraphs(source, parse_document(source))[0]

        self.assertEqual(paragraph.raw_text, "Dòng\tOCR\u00a0giữ nguyên")
        self.assertEqual(paragraph.normalized_text, paragraph.raw_text)


if __name__ == "__main__":
    unittest.main()
