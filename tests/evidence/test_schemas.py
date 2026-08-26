import copy
from dataclasses import fields
import unittest

from src.evidence.schemas import CanonicalDecimal, EvidenceItem
from src.indexing.numeric_parser import parse_numeric_string
from src.understanding.schemas import SchemaValidationError


CANONICAL_FIELDS = [
    "evidence_id",
    "source_type",
    "report_ref",
    "page_ref",
    "table_ref",
    "associated_table_ref",
    "statement_scope",
    "period",
    "metric",
    "row_label",
    "column_label",
    "row_path",
    "column_path",
    "text_span",
    "raw_value",
    "normalized_value",
    "unit",
    "scale",
    "scale_source",
    "retrieval_score",
    "rerank_score",
    "ticker",
    "company_name",
    "report_year",
    "paragraph_ref",
    "provenance_link_ids",
]


def table_evidence():
    return {
        "evidence_id": "evidence-table-1",
        "source_type": "TABLE",
        "report_ref": "AAA-2015-consolidated",
        "page_ref": "8",
        "table_ref": "KQKD-1",
        "associated_table_ref": None,
        "statement_scope": "HOP_NHAT",
        "period": "2015",
        "metric": "Lợi nhuận sau thuế",
        "row_label": "Lợi nhuận sau thuế thu nhập doanh nghiệp",
        "column_label": "Năm 2015",
        "row_path": ["Lợi nhuận", "Lợi nhuận sau thuế"],
        "column_path": ["Năm tài chính", "2015"],
        "text_span": None,
        "raw_value": "12.500.000",
        "normalized_value": "12500000",
        "unit": "VND",
        "scale": "RAW",
        "scale_source": "HEADER",
        "retrieval_score": 0.81,
        "rerank_score": 0.93,
        "ticker": "AAA",
        "company_name": "Công ty AAA",
        "report_year": 2015,
        "paragraph_ref": None,
        "provenance_link_ids": [],
    }


def text_evidence():
    payload = table_evidence()
    payload.update(
        {
            "evidence_id": "evidence-text-1",
            "source_type": "TEXT",
            "table_ref": None,
            "associated_table_ref": "KQKD-1",
            "row_label": None,
            "column_label": None,
            "row_path": [],
            "column_path": [],
            "text_span": "Đơn vị tính của bảng là triệu đồng.",
            "raw_value": None,
            "normalized_value": None,
            "unit": "VND",
            "scale": "MILLION",
            "scale_source": "TEXT",
            "paragraph_ref": "paragraph-1",
            "provenance_link_ids": ["link-1"],
        }
    )
    return payload


class EvidenceItemSchemaTests(unittest.TestCase):
    def test_field_names_match_canonical_contract(self):
        self.assertEqual([field.name for field in fields(EvidenceItem)], CANONICAL_FIELDS)

    def test_fully_populated_table_evidence(self):
        payload = table_evidence()

        evidence = EvidenceItem.from_dict(payload)

        self.assertEqual(evidence.to_dict(), payload)

    def test_fully_populated_text_evidence(self):
        payload = text_evidence()

        evidence = EvidenceItem.from_dict(payload)

        self.assertEqual(evidence.to_dict(), payload)

    def test_hybrid_evidence_remains_separate_items(self):
        table_item = EvidenceItem.from_dict(table_evidence())
        text_item = EvidenceItem.from_dict(text_evidence())

        evidence_items = [table_item, text_item]

        self.assertEqual(
            [item.source_type.value for item in evidence_items], ["TABLE", "TEXT"]
        )
        self.assertEqual(text_item.associated_table_ref, table_item.table_ref)

    def test_hierarchical_paths_are_preserved(self):
        payload = table_evidence()

        evidence = EvidenceItem.from_dict(payload)

        self.assertEqual(evidence.row_path, payload["row_path"])
        self.assertEqual(evidence.column_path, payload["column_path"])

    def test_raw_and_normalized_values_remain_separate(self):
        payload = table_evidence()

        evidence = EvidenceItem.from_dict(payload)

        self.assertEqual(evidence.raw_value, "12.500.000")
        self.assertEqual(evidence.normalized_value, "12500000")
        self.assertIsInstance(evidence.normalized_value, CanonicalDecimal)

    def test_raw_value_accepts_string_number_and_null(self):
        for raw_value in ("1.250", 1250, 12.5, None):
            with self.subTest(raw_value=raw_value):
                payload = table_evidence()
                payload["raw_value"] = raw_value
                evidence = EvidenceItem.from_dict(payload)
                self.assertEqual(evidence.raw_value, raw_value)

    def test_optional_fields_may_be_null(self):
        payload = table_evidence()
        for field_name in (
            "page_ref",
            "table_ref",
            "associated_table_ref",
            "statement_scope",
            "period",
            "metric",
            "row_label",
            "column_label",
            "text_span",
            "raw_value",
            "normalized_value",
            "unit",
            "scale",
            "scale_source",
            "retrieval_score",
            "rerank_score",
        ):
            payload[field_name] = None
        payload["row_path"] = []
        payload["column_path"] = []

        evidence = EvidenceItem.from_dict(payload)

        self.assertEqual(evidence.to_dict(), payload)

    def test_scale_and_unit_provenance_are_preserved(self):
        payload = text_evidence()

        evidence = EvidenceItem.from_dict(payload)

        self.assertEqual(evidence.unit, "VND")
        self.assertEqual(evidence.scale.value, "MILLION")
        self.assertEqual(evidence.scale_source.value, "TEXT")

    def test_caption_scale_provenance_is_canonical(self):
        payload = text_evidence()
        payload["text_span"] = "Đơn vị tính: triệu đồng."
        payload["scale_source"] = "CAPTION"

        evidence = EvidenceItem.from_dict(payload)

        self.assertEqual(evidence.scale_source.value, "CAPTION")
        self.assertEqual(evidence.to_dict(), payload)

    def test_invalid_enum_fields_are_rejected(self):
        cases = [
            ("source_type", "HYBRID"),
            ("statement_scope", "AGGREGATED"),
            ("scale", "TRILLION"),
            ("scale_source", "FOOTNOTE"),
        ]
        for field_name, invalid_value in cases:
            with self.subTest(field_name=field_name):
                payload = table_evidence()
                payload[field_name] = invalid_value
                with self.assertRaises(SchemaValidationError):
                    EvidenceItem.from_dict(payload)

    def test_boolean_values_are_rejected_as_numbers(self):
        for field_name in (
            "raw_value",
            "normalized_value",
            "retrieval_score",
            "rerank_score",
        ):
            with self.subTest(field_name=field_name):
                payload = table_evidence()
                payload[field_name] = True
                with self.assertRaises(SchemaValidationError):
                    EvidenceItem.from_dict(payload)

    def test_missing_field_is_rejected(self):
        payload = table_evidence()
        del payload["report_ref"]

        with self.assertRaises(SchemaValidationError):
            EvidenceItem.from_dict(payload)

    def test_unknown_field_is_rejected(self):
        payload = table_evidence()
        payload["extra"] = "not allowed"

        with self.assertRaises(SchemaValidationError):
            EvidenceItem.from_dict(payload)

    def test_invalid_path_and_numeric_types_are_rejected(self):
        invalid_cases = [
            ("row_path", ["Assets", 1]),
            ("column_path", "2015"),
            ("normalized_value", 12500000),
            ("retrieval_score", "high"),
        ]
        for field_name, invalid_value in invalid_cases:
            with self.subTest(field_name=field_name):
                payload = table_evidence()
                payload[field_name] = invalid_value
                with self.assertRaises(SchemaValidationError):
                    EvidenceItem.from_dict(payload)

    def test_serialization_round_trip(self):
        payload = table_evidence()

        first = EvidenceItem.from_dict(copy.deepcopy(payload))
        second = EvidenceItem.from_dict(first.to_dict())

        self.assertEqual(second.to_dict(), payload)

    def test_canonical_decimal_lossless_boundary_and_rejections(self):
        for value in ("1250.75", "-1250.75", "1250", "0", "-0.5"):
            with self.subTest(value=value):
                payload = table_evidence()
                payload["normalized_value"] = value
                self.assertEqual(EvidenceItem.from_dict(payload).to_dict()["normalized_value"], value)
        parsed = parse_numeric_string("1.250,75")
        payload = table_evidence()
        payload["normalized_value"] = parsed.decimal_value
        self.assertEqual(EvidenceItem.from_dict(payload).normalized_value, parsed.decimal_value)
        for invalid in (1250, 1250.0, True, "01", "1.0.0", "1e3"):
            with self.subTest(invalid=invalid):
                payload = table_evidence()
                payload["normalized_value"] = invalid
                with self.assertRaises(SchemaValidationError):
                    EvidenceItem.from_dict(payload)


if __name__ == "__main__":
    unittest.main()
