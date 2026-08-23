import unittest

from src.indexing.ids import (
    canonical_json_bytes,
    content_sha256,
    make_cell_id,
    make_page_id,
    make_report_id,
    make_table_id,
)
from src.indexing.schemas import SourceSpan
from src.understanding.schemas import SchemaValidationError


class StableIdTests(unittest.TestCase):
    def test_same_identity_inputs_produce_same_full_identifier(self):
        first = make_report_id("ViFinQA", "AAA/2024/document/report.txt")
        second = make_report_id("ViFinQA", "AAA/2024/document/report.txt")
        self.assertEqual(first, second)
        self.assertRegex(first, r"^report_[0-9a-f]{64}$")

    def test_report_identity_is_path_based_and_child_identity_is_content_versioned(self):
        report_id = make_report_id("ViFinQA", "AAA/2024/document/report.txt")
        page_span = SourceSpan(0, 100)
        first_page = make_page_id(report_id, content_sha256("first"), 0, page_span)
        second_page = make_page_id(report_id, content_sha256("second"), 0, page_span)
        self.assertNotEqual(first_page, second_page)

    def test_coordinates_produce_distinct_table_and_cell_ids(self):
        report_id = make_report_id("ViFinQA", "AAA/2024/document/report.txt")
        page_id = make_page_id(report_id, content_sha256("source"), 0, SourceSpan(0, 100))
        table_0 = make_table_id(page_id, 0, SourceSpan(10, 50))
        table_1 = make_table_id(page_id, 1, SourceSpan(60, 90))
        self.assertNotEqual(table_0, table_1)
        self.assertNotEqual(
            make_cell_id(table_0, 0, 0, SourceSpan(12, 20)),
            make_cell_id(table_0, 0, 1, SourceSpan(21, 30)),
        )

    def test_canonical_json_is_utf8_sorted_and_compact(self):
        encoded = canonical_json_bytes({"z": 1, "a": "triệu"})
        self.assertEqual(encoded, '{"a":"triệu","z":1}'.encode("utf-8"))

    def test_canonical_json_rejects_nan(self):
        with self.assertRaises(SchemaValidationError):
            canonical_json_bytes({"value": float("nan")})


if __name__ == "__main__":
    unittest.main()
