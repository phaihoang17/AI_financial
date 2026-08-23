import json
from pathlib import Path
import unittest


FIXTURE_ROOT = Path(__file__).with_name("fixtures")


class RawOcrFixtureTests(unittest.TestCase):
    def test_manifest_references_utf8_raw_ocr_fixtures(self):
        manifest = json.loads((FIXTURE_ROOT / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["fixtures"]), 3)
        for item in manifest["fixtures"]:
            with self.subTest(file=item["file"]):
                raw = (FIXTURE_ROOT / item["file"]).read_bytes()
                text = raw.decode("utf-8", errors="strict")
                self.assertIn("===== PAGE ", text)
                self.assertIn("<table", text)

    def test_merged_fixture_contains_both_span_types(self):
        text = (FIXTURE_ROOT / "merged_header_ocr.txt").read_text(encoding="utf-8")
        self.assertIn('rowspan="2"', text)
        self.assertIn('colspan="2"', text)

    def test_malformed_fixture_remains_intentionally_unmodified(self):
        text = (FIXTURE_ROOT / "malformed_inline_table_ocr.txt").read_text(encoding="utf-8")
        self.assertIn('rowspan="two"', text)
        self.assertNotIn("</table>", text)
        self.assertIn("12.34.567", text)


if __name__ == "__main__":
    unittest.main()
