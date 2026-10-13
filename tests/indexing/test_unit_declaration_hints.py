"""ADR-062 query-time currency-unit hints and unit-declaration links."""

import unittest

from src.evidence.schemas import Scale
from src.indexing.provenance_sidecar import _extract_report_provenance
from src.indexing.schemas import (
    ScaleHintSource,
    ScaleHintStatus,
    ScaleUnitHint,
    SourceSpan,
    TableTextLinkBasis,
    TableTextRelation,
)
from src.understanding.schemas import SchemaValidationError

from tests.indexing.test_document_parser import make_source

STATEMENT = (
    "===== PAGE 1 =====\n"
    "BÁO CÁO KẾT QUẢ HOẠT ĐỘNG KINH DOANH\n\n"
    "{declaration}\n\n"
    "<table><tr><td>CHỈ TIÊU</td><td>{header}</td></tr>"
    "<tr><td>Lợi nhuận sau thuế</td><td>309.193.823.134</td></tr></table>\n\n"
    "9\n"
)


def provenance(raw):
    source = make_source(raw)
    hints, links = _extract_report_provenance(source)
    return source, hints, links


def units(hints):
    return sorted(
        (hint.source_kind.value, hint.raw_hint_text, hint.scale_candidate.value)
        for hint in hints
        if hint.unit_candidate is not None
    )


class UnitDeclarationHintTests(unittest.TestCase):
    def test_declaration_above_footed_table_links_and_reads_raw_vnd(self):
        source, hints, links = provenance(
            STATEMENT.format(declaration="Đơn vị tính: VND", header="Năm nay"),
        )
        self.assertEqual(len(links), 1)
        self.assertIs(links[0].relation, TableTextRelation.CAPTION)
        self.assertIs(links[0].basis, TableTextLinkBasis.IMMEDIATE_BEFORE)
        (hint,) = [hint for hint in hints if hint.unit_candidate is not None]
        self.assertEqual(
            (hint.source_kind, hint.table_id, hint.scale_candidate, hint.unit_candidate),
            (ScaleHintSource.TEXT, links[0].table_id, Scale.RAW, "VND"),
        )
        self.assertEqual(
            source.raw_text[hint.source_span.start : hint.source_span.end], "VND"
        )

    def test_scale_word_units_and_table_headers(self):
        _, hints, _ = provenance(
            STATEMENT.format(declaration="Đơn vị: Triệu đồng Việt Nam", header="2023 VND"),
        )
        self.assertEqual(
            units(hints),
            [("HEADER", "VND", "RAW"), ("TEXT", "Triệu đồng", "MILLION")],
        )
        self.assertTrue(all(hint.status is ScaleHintStatus.AMBIGUOUS for hint in hints))

    def test_words_that_only_look_like_units_yield_nothing(self):
        for declaration, header in (
            ("Theo hợp đồng thanh toán bằng VND", "Năm nay"),
            ("Đơn vị tính: cổ phiếu", "Năm nay"),
            ("ĐVT: Ngân VND", "Ngân VND"),
        ):
            with self.subTest(declaration=declaration):
                _, hints, links = provenance(
                    STATEMENT.format(declaration=declaration, header=header),
                )
                self.assertEqual(units(hints), [])
                self.assertEqual(links, [])

    def test_raw_requires_an_explicit_unit(self):
        with self.assertRaises(SchemaValidationError):
            ScaleUnitHint(
                "hint", "report", "page", None, ScaleHintSource.CELL, "cell",
                SourceSpan(0, 3), "VND", "vnd", Scale.RAW, None, ScaleHintStatus.EXTRACTED,
            )


if __name__ == "__main__":
    unittest.main()
