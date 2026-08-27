import contextlib
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from src.indexing.corpus_artifact import build_corpus_artifact
from src.indexing.table_class_provenance_audit import (
    TableClassProvenanceAuditError,
    audit_table_class_provenance,
    main,
)
from src.indexing.table_class_sidecar import build_table_class_sidecar
from tests.indexing.test_corpus_artifact import CharTokenizer


_TABLE = (
    "<table><tr><td>Chỉ tiêu</td><td>2024</td></tr>"
    "<tr><td>Doanh thu</td><td>1.250</td></tr></table>"
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class TableClassProvenanceAuditTests(unittest.TestCase):
    def _build(self, root: Path, body: str):
        raw_root = root / "financial_statements"
        report = raw_root / "AAA" / "2024" / "AAA_financial_statements_2024_consolidated"
        report.mkdir(parents=True)
        (report / "AAA_financial_statements_2024_consolidated_extracted.txt").write_text(
            f"===== PAGE 1 =====\n{body}\n", encoding="utf-8"
        )
        metadata = root / "code_stock.csv"
        metadata.write_text("Mã CK,Tên công ty\nAAA,Test Company\n", encoding="utf-8")
        with patch(
            "src.indexing.corpus_artifact.load_bge_m3_tokenizer",
            return_value=CharTokenizer(),
        ):
            corpus = build_corpus_artifact(
                raw_root,
                root / "m2-corpus",
                company_metadata=metadata,
                max_records_per_shard=2,
                max_shard_bytes=100_000,
            )
        sidecar = root / "m2-table-class"
        build_table_class_sidecar(corpus, raw_root, sidecar)
        return raw_root, corpus, sidecar

    def _refresh_database_manifest(self, sidecar: Path, *, hint_count: int | None = None):
        manifest_path = sidecar / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        database = sidecar / manifest["artifact_directory"] / manifest["database"]["file"]
        manifest["database"]["sha256"] = _sha256_file(database)
        if hint_count is not None:
            manifest["table_class_hint_count"] = hint_count
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )

    def test_pass_reports_coverage_and_preserves_unmatched_none(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw_root, corpus, sidecar = self._build(
                root,
                "BẢNG CÂN ĐỐI KẾ TOÁN\n"
                f"{_TABLE}\n"
                "Không có mã hoặc tiêu đề báo cáo\n"
                f"{_TABLE}",
            )

            report = audit_table_class_provenance(corpus, sidecar, raw_root)

        self.assertEqual(report["status"], "PASS")
        self.assertEqual(
            report["production_flag_clearance"],
            "ELIGIBLE_IF_INPUTS_ARE_APPROVED_CANONICAL_FULL_M2",
        )
        self.assertEqual(report["total_reports"], 1)
        self.assertEqual(report["total_tables"], 2)
        self.assertEqual(report["total_classified_tables"], 1)
        self.assertEqual(report["classification_coverage"], 0.5)
        self.assertEqual(report["counts_per_table_class"]["BALANCE_SHEET"], 1)
        self.assertEqual(report["unmatched_table_count"], 1)
        self.assertEqual(report["ambiguous_conflict_count"], 0)
        self.assertEqual(report["exact_source_span_round_trip_failure_count"], 0)
        self.assertEqual(report["production_evidence_path"]["failure_count"], 0)
        self.assertTrue(
            report["production_evidence_path"]["unclassified_none_check"]["passed"]
        )

    def test_source_conflict_is_counted_but_remains_unclassified(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw_root, corpus, sidecar = self._build(
                root,
                "BÁO CÁO KẾT QUẢ HOẠT ĐỘNG KINH DOANH\n"
                f"{_TABLE}\n"
                "BẢNG CÂN ĐỐI KẾ TOÁN\nMẫu số B02-DN\n"
                f"{_TABLE}",
            )
            report = audit_table_class_provenance(corpus, sidecar, raw_root)

        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["ambiguous_conflict_count"], 1)
        self.assertEqual(report["unmatched_table_count"], 1)
        self.assertNotIn("SOURCE_SUPPORTED_HINT_MISMATCH", report["blockers"])

    def test_source_span_round_trip_corruption_blocks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw_root, corpus, sidecar = self._build(
                root, f"BẢNG CÂN ĐỐI KẾ TOÁN\n{_TABLE}"
            )
            manifest = json.loads((sidecar / "manifest.json").read_text(encoding="utf-8"))
            database = sidecar / manifest["artifact_directory"] / manifest["database"]["file"]
            with sqlite3.connect(database) as connection:
                canonical = connection.execute(
                    "SELECT canonical_json FROM table_class_hints"
                ).fetchone()[0]
                payload = json.loads(canonical)
                payload["matched_text"] = "X" * len(payload["matched_text"])
                changed = json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                connection.execute(
                    "UPDATE table_class_hints SET matched_text=?, canonical_json=?",
                    (payload["matched_text"], changed),
                )
                connection.commit()
            self._refresh_database_manifest(sidecar)

            report = audit_table_class_provenance(corpus, sidecar, raw_root)

        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["exact_source_span_round_trip_failure_count"], 1)
        self.assertIn("SOURCE_SPAN_ROUND_TRIP_FAILED", report["blockers"])

    def test_invalid_source_span_blocks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw_root, corpus, sidecar = self._build(
                root, f"BẢNG CÂN ĐỐI KẾ TOÁN\n{_TABLE}"
            )
            manifest = json.loads((sidecar / "manifest.json").read_text(encoding="utf-8"))
            database = sidecar / manifest["artifact_directory"] / manifest["database"]["file"]
            with sqlite3.connect(database) as connection:
                canonical = connection.execute(
                    "SELECT canonical_json FROM table_class_hints"
                ).fetchone()[0]
                payload = json.loads(canonical)
                payload["matched_source_span"]["end"] = 1_000_000
                changed = json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                connection.execute(
                    "UPDATE table_class_hints SET matched_span_end=?, canonical_json=?",
                    (1_000_000, changed),
                )
                connection.commit()
            self._refresh_database_manifest(sidecar)

            report = audit_table_class_provenance(corpus, sidecar, raw_root)

        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["invalid_source_span_count"], 1)
        self.assertIn("INVALID_SOURCE_SPAN", report["blockers"])

    def test_duplicate_and_conflicting_table_id_mappings_block(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw_root, corpus, sidecar = self._build(
                root, f"BẢNG CÂN ĐỐI KẾ TOÁN\n{_TABLE}"
            )
            manifest = json.loads((sidecar / "manifest.json").read_text(encoding="utf-8"))
            database = sidecar / manifest["artifact_directory"] / manifest["database"]["file"]
            with sqlite3.connect(database) as connection:
                connection.executescript(
                    """
                    ALTER TABLE table_class_hints RENAME TO old_table_class_hints;
                    CREATE TABLE table_class_hints (
                        hint_id TEXT, report_id TEXT, page_id TEXT, table_id TEXT,
                        table_class TEXT, registry_entry_id TEXT,
                        matched_span_start INTEGER, matched_span_end INTEGER,
                        matched_text TEXT, canonical_json TEXT
                    );
                    INSERT INTO table_class_hints SELECT * FROM old_table_class_hints;
                    INSERT INTO table_class_hints
                    SELECT hint_id || '-conflict', report_id, page_id, table_id,
                           'INCOME_STATEMENT', registry_entry_id,
                           matched_span_start, matched_span_end, matched_text, canonical_json
                    FROM old_table_class_hints;
                    DROP TABLE old_table_class_hints;
                    """
                )
                connection.commit()
            self._refresh_database_manifest(sidecar, hint_count=2)

            report = audit_table_class_provenance(corpus, sidecar, raw_root)

        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["duplicate_table_id_mapping_count"], 1)
        self.assertEqual(report["conflicting_table_id_mapping_count"], 1)
        self.assertIn("DUPLICATE_SIDECAR_TABLE_ID_MAPPING", report["blockers"])
        self.assertIn("CONFLICTING_SIDECAR_TABLE_ID_MAPPING", report["blockers"])

    def test_exact_corpus_mismatch_fails_closed_and_cli_exits_one(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, _, sidecar = self._build(root / "first", f"BẢNG CÂN ĐỐI KẾ TOÁN\n{_TABLE}")
            raw_root, corpus, _ = self._build(
                root / "second", f"BÁO CÁO LƯU CHUYỂN TIỀN TỆ\n{_TABLE}"
            )
            with self.assertRaises(TableClassProvenanceAuditError) as raised:
                audit_table_class_provenance(corpus, sidecar, raw_root)
            self.assertEqual(raised.exception.code, "SIDECAR_CORPUS_MISMATCH")

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "--corpus-artifact",
                        str(corpus),
                        "--table-class-sidecar",
                        str(sidecar),
                        "--raw-corpus-root",
                        str(raw_root),
                    ]
                )
            payload = json.loads(output.getvalue())

        self.assertEqual(exit_code, 1)
        self.assertEqual(payload["status"], "BLOCKED")
        self.assertEqual(payload["production_flag_clearance"], "BLOCKED")
        self.assertEqual(payload["error"]["code"], "SIDECAR_CORPUS_MISMATCH")


if __name__ == "__main__":
    unittest.main()
