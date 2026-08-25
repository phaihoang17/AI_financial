import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from src.indexing.corpus_artifact import build_corpus_artifact
from src.indexing.provenance_sidecar import (
    ProvenanceSidecar,
    ProvenanceSidecarError,
    _create_database,
    _insert_provenance,
    build_provenance_sidecar,
    validate_provenance_sidecar,
)
from src.indexing.scale_unit_hint_extractor import extract_scale_unit_hints
from src.indexing.document_parser import parse_document
from src.indexing.paragraph_extractor import extract_paragraphs
from src.indexing.grid_builder import build_logical_table_grid
from src.indexing.normalized_table_assembler import assemble_normalized_table
from src.indexing.table_text_linker import link_tables_to_paragraphs
from tests.indexing.test_corpus_artifact import CharTokenizer


class ProvenanceSidecarTests(unittest.TestCase):
    def _artifacts(self, root: Path, *, text: str = "Đơn vị: tỷ.") -> tuple[Path, Path, Path]:
        raw_root = root / "financial_statements"
        report = raw_root / "AAA" / "2024" / "AAA_financial_statements_2024_consolidated"
        report.mkdir(parents=True)
        (report / "AAA_financial_statements_2024_consolidated_extracted.txt").write_text(
            "===== PAGE 1 =====\n"
            f"{text}\n"
            "<table><tr><td>Chỉ tiêu</td><td>Giá trị (triệu)</td></tr>"
            "<tr><td>Doanh thu</td><td>1.250</td></tr></table>\n",
            encoding="utf-8",
        )
        metadata = root / "code_stock.csv"
        metadata.write_text("Mã CK,Tên công ty\nAAA,Test Company\n", encoding="utf-8")
        with patch("src.indexing.corpus_artifact.load_bge_m3_tokenizer", return_value=CharTokenizer()):
            corpus = build_corpus_artifact(
                raw_root,
                root / "m2-corpus",
                company_metadata=metadata,
                max_records_per_shard=2,
                max_shard_bytes=100_000,
            )
        sidecar_root = root / "m2-provenance"
        build_provenance_sidecar(corpus, raw_root, sidecar_root)
        return raw_root, corpus, sidecar_root

    def test_round_trip_lookups_spans_references_and_deterministic_build(self):
        with tempfile.TemporaryDirectory() as temporary:
            raw_root, corpus, sidecar_root = self._artifacts(Path(temporary))
            first = json.loads((sidecar_root / "manifest.json").read_text())
            second = build_provenance_sidecar(corpus, raw_root, sidecar_root)
            repository = ProvenanceSidecar(sidecar_root, corpus)
            artifact = sidecar_root / first["artifact_directory"]
            with sqlite3.connect(artifact / "provenance.sqlite") as connection:
                hint_json = connection.execute(
                    "SELECT canonical_json FROM scale_unit_hints WHERE table_id IS NOT NULL ORDER BY hint_id LIMIT 1"
                ).fetchone()[0]
                link_json = connection.execute(
                    "SELECT canonical_json FROM table_text_links ORDER BY link_id LIMIT 1"
                ).fetchone()[0]

            hint = repository.get_hint(json.loads(hint_json)["hint_id"])
            link = repository.get_links_by_table_id(json.loads(link_json)["table_id"])[0]
            hints_by_source = repository.get_hints_by_source_ref(hint.source_ref)
            hints_by_table = repository.get_hints_by_table_id(hint.table_id)
            links_by_paragraph = repository.get_links_by_paragraph_id(link.paragraph_id)

        self.assertEqual(first, second)
        self.assertEqual(first["referenced_hint_count"], first["scale_unit_hint_count"])
        self.assertEqual(first["referenced_link_count"], first["table_text_link_count"])
        self.assertEqual(hint.to_dict(), json.loads(hint_json))
        self.assertEqual(link.to_dict(), json.loads(link_json))
        self.assertEqual(hints_by_source, [hint])
        self.assertIn(hint, hints_by_table)
        self.assertEqual(links_by_paragraph, [link])
        self.assertEqual(hint.raw_hint_text, "tỷ")
        self.assertLess(hint.source_span.start, hint.source_span.end)

    def test_corruption_and_corpus_mismatch_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, corpus, sidecar_root = self._artifacts(root)
            manifest = json.loads((sidecar_root / "manifest.json").read_text())
            database = sidecar_root / manifest["artifact_directory"] / manifest["database"]["file"]
            database.write_bytes(b"not sqlite")
            with self.assertRaisesRegex(ProvenanceSidecarError, "SIDECAR_DATABASE_CORRUPT"):
                validate_provenance_sidecar(sidecar_root, corpus)

            _, other_corpus, _ = self._artifacts(root / "other", text="Đơn vị: nghìn.")
            with self.assertRaisesRegex(ProvenanceSidecarError, "SIDECAR_CORPUS_MISMATCH"):
                validate_provenance_sidecar(sidecar_root, other_corpus)

    def test_duplicate_hint_ids_are_rejected_by_sidecar_storage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw_root, _, _ = self._artifacts(root)
            raw = next(raw_root.glob("*/*/*/*_extracted.txt")).read_text(encoding="utf-8")
            from src.indexing.schemas import ReportSource

            source = ReportSource(
                schema_version="m2a.v1", corpus_id="test", report_id="report", source_ref="x",
                ticker="AAA", company_name="Test", report_year=2024, document_name="x",
                statement_scope=None, raw_text=raw,
                content_sha256=__import__("hashlib").sha256(raw.encode()).hexdigest(),
            )
            document = parse_document(source)
            paragraphs = extract_paragraphs(source, document)
            tables = [
                assemble_normalized_table(table, build_logical_table_grid(table))
                for page in document.pages for table in page.tables
            ]
            links = link_tables_to_paragraphs(source, document, paragraphs)
            hints = extract_scale_unit_hints(source, document, tables, paragraphs, links)
            connection = _create_database(root / "duplicate.sqlite", metadata={})
            try:
                with self.assertRaisesRegex(ProvenanceSidecarError, "SIDECAR_DUPLICATE_ID"):
                    _insert_provenance(connection, [hints[0], hints[0]], [])
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
