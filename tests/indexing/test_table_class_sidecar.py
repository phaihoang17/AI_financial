import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from src.indexing.schemas import ReportSource
from src.indexing.table_class_sidecar import (
    TABLE_CLASS_REGISTRY,
    TableClassSidecar,
    TableClassSidecarError,
    build_table_class_sidecar,
    classify_report,
    validate_table_class_sidecar,
)
from src.supervisor.schemas import TableClass
from tests.indexing.test_provenance_sidecar import ProvenanceSidecarTests


def _report(text: str, report_id: str = "r") -> ReportSource:
    return ReportSource(
        schema_version="m2a.v1",
        corpus_id="c",
        report_id=report_id,
        source_ref="x",
        ticker="AAA",
        company_name="T",
        report_year=2015,
        document_name="x",
        statement_scope=None,
        raw_text=text,
        content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


_TABLE = "<table><tr><td>Chỉ tiêu</td><td>2015</td></tr><tr><td>LNST</td><td>1.000</td></tr></table>"


class ClassifierTests(unittest.TestCase):
    def _one(self, text: str):
        hints = classify_report(_report(text))
        return hints

    def test_code_only_match(self):
        hints = self._one(f"===== PAGE 1 =====\nMẫu số B03-DN\n{_TABLE}\n")
        self.assertEqual([h.table_class for h in hints], [TableClass.CASH_FLOW_STATEMENT])
        self.assertEqual(hints[0].registry_entry_id, "CASH_FLOW_STATEMENT::B03-DN")

    def test_title_only_match(self):
        hints = self._one(
            f"===== PAGE 1 =====\nBÁO CÁO LƯU CHUYỂN TIỀN TỆ\n{_TABLE}\n"
        )
        self.assertEqual([h.table_class for h in hints], [TableClass.CASH_FLOW_STATEMENT])

    def test_code_and_title_agreement_is_one_deterministic_hint(self):
        text = (
            "===== PAGE 1 =====\n"
            "BẢNG CÂN ĐỐI KẾ TOÁN\n"
            "Mẫu số B01-DN/HN\n"
            f"{_TABLE}\n"
        )
        hints = self._one(text)
        self.assertEqual(len(hints), 1)
        self.assertIs(hints[0].table_class, TableClass.BALANCE_SHEET)
        # Earliest span wins: the title precedes the code here.
        self.assertEqual(hints[0].registry_entry_id, "BALANCE_SHEET::BANG-CAN-DOI-KE-TOAN")

    def test_conflicting_forms_produce_no_hint(self):
        text = (
            "===== PAGE 1 =====\n"
            "BẢNG CÂN ĐỐI KẾ TOÁN\n"
            "Mẫu số B02-DN\n"
            f"{_TABLE}\n"
        )
        self.assertEqual(self._one(text), [])

    def test_multiple_agreeing_matches_still_one_hint(self):
        text = (
            "===== PAGE 1 =====\n"
            "BÁO CÁO KẾT QUẢ HOẠT ĐỘNG KINH DOANH\n"
            "Mẫu số B02-DN (BÁO CÁO KẾT QUẢ HOẠT ĐỘNG KINH DOANH)\n"
            f"{_TABLE}\n"
        )
        hints = self._one(text)
        self.assertEqual(len(hints), 1)
        self.assertIs(hints[0].table_class, TableClass.INCOME_STATEMENT)

    def test_no_match_leaves_table_unclassified(self):
        self.assertEqual(
            self._one(f"===== PAGE 1 =====\nMột đoạn văn không liên quan\n{_TABLE}\n"), []
        )

    def test_preceding_context_is_same_page_only(self):
        text = (
            "===== PAGE 1 =====\n"
            "BẢNG CÂN ĐỐI KẾ TOÁN\n"
            f"{_TABLE}\n"
            "===== PAGE 2 =====\n"
            f"{_TABLE}\n"
        )
        hints = classify_report(_report(text))
        # Page 1 table classified; page 2 table has no same-page title.
        self.assertEqual(len(hints), 1)
        self.assertIs(hints[0].table_class, TableClass.BALANCE_SHEET)

    def test_previous_table_bounds_the_window(self):
        text = (
            "===== PAGE 1 =====\n"
            "THUYẾT MINH BÁO CÁO TÀI CHÍNH\n"
            f"{_TABLE}\n"
            f"{_TABLE}\n"
        )
        hints = classify_report(_report(text))
        # Only the first table sits after the NOTES title; the second is clamped.
        self.assertEqual(len(hints), 1)
        self.assertIs(hints[0].table_class, TableClass.NOTES)

    def test_nfkc_case_and_bounded_formatting_slack(self):
        # Fullwidth digits/letters (NFKC), lowercase, and " - " / "/" slack.
        text = "===== PAGE 1 =====\nmẫu số Ｂ０１ - ＤＮ/HN\n" + _TABLE + "\n"
        hints = classify_report(_report(text))
        self.assertEqual([h.table_class for h in hints], [TableClass.BALANCE_SHEET])

    def test_matched_span_round_trips_to_source_text(self):
        text = (
            "===== PAGE 1 =====\n"
            "BÁO CÁO KẾT QUẢ HOẠT ĐỘNG KINH DOANH\n"
            f"{_TABLE}\n"
        )
        source = _report(text)
        hint = classify_report(source)[0]
        span = hint.matched_source_span
        self.assertEqual(source.raw_text[span.start : span.end], hint.matched_text)
        self.assertEqual(hint.matched_text, "BÁO CÁO KẾT QUẢ HOẠT ĐỘNG KINH DOANH")

    def test_registry_never_consults_metric_mappings(self):
        # Every recogniser is a literal statement code or title, not a metric.
        for entry in TABLE_CLASS_REGISTRY:
            self.assertTrue(entry.literal)
            self.assertIsInstance(entry.table_class, TableClass)


class SidecarArtifactTests(unittest.TestCase):
    def _build(self, root: Path, *, text: str, sub: str = "tc"):
        raw_root, corpus, _ = ProvenanceSidecarTests()._artifacts(root, text=text)
        sidecar_root = root / sub
        build_table_class_sidecar(corpus, raw_root, sidecar_root)
        return raw_root, corpus, sidecar_root

    def test_build_is_deterministic_and_lookup_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_root, corpus, sidecar_root = self._build(
                root, text="BẢNG CÂN ĐỐI KẾ TOÁN\nMẫu số B01-DN"
            )
            first = json.loads((sidecar_root / "manifest.json").read_text())
            second = build_table_class_sidecar(corpus, raw_root, sidecar_root)
            self.assertEqual(first, second)
            self.assertEqual(first["table_class_hint_count"], 1)

            sidecar = TableClassSidecar(sidecar_root, corpus)
            artifact = sidecar_root / first["artifact_directory"]
            with sqlite3.connect(artifact / "table_class.sqlite") as connection:
                report_id, page_id, table_id = connection.execute(
                    "SELECT report_id, page_id, table_id FROM table_class_hints"
                ).fetchone()
            self.assertIs(
                sidecar.table_class_for(report_id, page_id, table_id),
                TableClass.BALANCE_SHEET,
            )
            hint = sidecar.get_hint(report_id, page_id, table_id)
            self.assertEqual(
                hint.matched_text.upper().replace(" ", ""),
                "BẢNGCÂNĐỐIKẾTOÁN".upper().replace(" ", ""),
            )

    def test_exact_table_id_lookup_and_missing_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, corpus, sidecar_root = self._build(
                root, text="BÁO CÁO LƯU CHUYỂN TIỀN TỆ"
            )
            sidecar = TableClassSidecar(sidecar_root, corpus)
            with sqlite3.connect(
                sidecar_root
                / json.loads((sidecar_root / "manifest.json").read_text())["artifact_directory"]
                / "table_class.sqlite"
            ) as connection:
                report_id, page_id, table_id = connection.execute(
                    "SELECT report_id, page_id, table_id FROM table_class_hints"
                ).fetchone()
            self.assertIs(
                sidecar.table_class_for(report_id, page_id, table_id),
                TableClass.CASH_FLOW_STATEMENT,
            )
            # Any non-exact key -> no hint, not a guess.
            self.assertIsNone(sidecar.table_class_for(report_id, page_id, "other-table"))
            self.assertIsNone(sidecar.table_class_for(report_id, "other-page", table_id))
            self.assertIsNone(sidecar.get_hint("other-report", page_id, table_id))

    def test_no_recognisable_context_yields_empty_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, corpus, sidecar_root = self._build(root, text="Đơn vị tính: đồng")
            manifest = json.loads((sidecar_root / "manifest.json").read_text())
            self.assertEqual(manifest["table_class_hint_count"], 0)
            sidecar = TableClassSidecar(sidecar_root, corpus)
            self.assertIsNone(sidecar.table_class_for("r", "p", "t"))

    def test_validate_rejects_corpus_and_registry_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, corpus, sidecar_root = self._build(
                root, text="BẢNG CÂN ĐỐI KẾ TOÁN"
            )
            validate_table_class_sidecar(sidecar_root, corpus)

            _, other_corpus, _ = self._build(
                root / "other", text="BÁO CÁO LƯU CHUYỂN TIỀN TỆ", sub="tc2"
            )
            with self.assertRaisesRegex(TableClassSidecarError, "SIDECAR_CORPUS_MISMATCH"):
                validate_table_class_sidecar(sidecar_root, other_corpus)

            manifest_path = sidecar_root / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["registry_version"] = "tampered"
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(TableClassSidecarError, "SIDECAR_REGISTRY_MISMATCH"):
                validate_table_class_sidecar(sidecar_root, corpus)

    def test_corrupt_sidecar_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, corpus, sidecar_root = self._build(
                root, text="BẢNG CÂN ĐỐI KẾ TOÁN"
            )
            manifest = json.loads((sidecar_root / "manifest.json").read_text())
            database = (
                sidecar_root / manifest["artifact_directory"] / manifest["database"]["file"]
            )
            database.write_bytes(b"not a sqlite database")
            with self.assertRaisesRegex(TableClassSidecarError, "SIDECAR_DATABASE_CORRUPT"):
                validate_table_class_sidecar(sidecar_root, corpus)
            with self.assertRaises(TableClassSidecarError):
                TableClassSidecar(sidecar_root, corpus)


class LocateCellsIntegrationTests(unittest.TestCase):
    def test_locate_cells_uses_source_backed_table_class(self):
        from src.retrieval.corpus_candidates import iter_corpus_candidates
        from src.retrieval.evidence import EvidenceProvenanceRepository, locate_cells
        from src.retrieval.schemas import RetrievalCompany, RetrievalQuery
        from src.supervisor.schemas import EvidenceSource
        from src.understanding.schemas import StatementScope

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_root, corpus, _ = ProvenanceSidecarTests()._artifacts(
                root, text="BÁO CÁO KẾT QUẢ HOẠT ĐỘNG KINH DOANH\nMẫu số B02-DN"
            )
            sidecar_root = root / "tc"
            build_table_class_sidecar(corpus, raw_root, sidecar_root)
            table = next(
                c
                for c in iter_corpus_candidates(corpus)
                if c.source_type is EvidenceSource.TABLE
            )
            query = RetrievalQuery(
                "Doanh thu",
                RetrievalCompany("Test Company", "AAA"),
                ["2024"],
                None,
                StatementScope.HOP_NHAT,
                ["Doanh thu"],
                None,
                [EvidenceSource.TABLE],
                None,
                None,
                ["Doanh thu"],
            )

            plain = EvidenceProvenanceRepository(corpus, raw_root)
            self.assertTrue(
                all(loc.table_class is None for loc in locate_cells(query, [table], plain))
            )

            wired = EvidenceProvenanceRepository(
                corpus,
                raw_root,
                table_class_sidecar=TableClassSidecar(sidecar_root, corpus),
            )
            located = locate_cells(query, [table], wired)
            self.assertTrue(located)
            self.assertTrue(
                all(loc.table_class is TableClass.INCOME_STATEMENT for loc in located)
            )


if __name__ == "__main__":
    unittest.main()
