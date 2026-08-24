import copy
import unittest

from src.indexing.cell_normalizer import (
    normalize_cell_text,
    normalize_source_cell_text,
)
from src.indexing.schemas import SourceCell, SourceSpan


class CellTextNormalizationTests(unittest.TestCase):
    def test_normalizes_derived_text_without_changing_source_cell(self):
        cell = SourceCell(
            cell_id="cell_1",
            table_id="table_1",
            source_row_index=0,
            source_cell_index=0,
            source_span=SourceSpan(10, 40),
            raw_html="<td>Lợi&nbsp; nhuận\r\n sau thuế</td>",
            raw_inner_html="Lợi&nbsp; nhuận\r\n sau thuế",
            extracted_text="  Lợi&nbsp;\t nhuận\r\n sau\u00a0thuế  ",
            rowspan=1,
            colspan=1,
        )
        original = copy.deepcopy(cell.to_dict())

        normalized = normalize_source_cell_text(cell)

        self.assertEqual(normalized, "Lợi nhuận sau thuế")
        self.assertEqual(cell.to_dict(), original)

    def test_nfkc_preserves_vietnamese_diacritics_and_stored_case(self):
        self.assertEqual(
            normalize_cell_text("Ｌợi\u00a0NHUẬN"),
            "Lợi NHUẬN",
        )

    def test_decodes_nested_standard_entities_and_is_idempotent(self):
        once = normalize_cell_text("A&amp;nbsp;&amp;amp; B")
        twice = normalize_cell_text(once)
        self.assertEqual(once, "A & B")
        self.assertEqual(twice, once)

    def test_converts_unicode_whitespace_and_empty_cells_remain_empty(self):
        self.assertEqual(normalize_cell_text(" \t\n\u2003\u2028 "), "")
        self.assertEqual(normalize_cell_text("A\u2003B\u2028C"), "A B C")


if __name__ == "__main__":
    unittest.main()
