import copy
import math
import unittest

from src.understanding.schemas import QueryUnderstanding, SchemaValidationError


def valid_payload():
    return {
        "raw_question": "ROE của AAA năm 2015 là bao nhiêu?",
        "company": {
            "raw": "AAA",
            "name": "Công ty Cổ phần Nhựa An Phát Xanh",
            "ticker": "AAA",
            "confidence": 0.99,
        },
        "periods": [{"value": "2015", "kind": "NAM", "raw": "năm 2015"}],
        "statement_scope": {
            "value": "HOP_NHAT",
            "inferred": True,
            "confidence": 0.8,
        },
        "metrics": [
            {"raw": "ROE", "canonical": "ROE", "confidence": 0.98}
        ],
        "operation": "ratio",
        "requested_scale": "percent",
        "requested_unit": "%",
        "missing_information": [],
        "ambiguities": [],
        "confidence": 0.9,
    }


class QueryUnderstandingSchemaTests(unittest.TestCase):
    def test_fully_populated_valid_schema(self):
        payload = valid_payload()

        schema = QueryUnderstanding.from_dict(payload)

        self.assertEqual(schema.to_dict(), payload)

    def test_unresolved_company_remains_null(self):
        payload = valid_payload()
        payload["company"] = {
            "raw": None,
            "name": None,
            "ticker": None,
            "confidence": 0.0,
        }
        payload["missing_information"] = ["company"]

        schema = QueryUnderstanding.from_dict(payload)

        self.assertIsNone(schema.company.raw)
        self.assertIsNone(schema.company.name)
        self.assertIsNone(schema.company.ticker)

    def test_multiple_periods(self):
        payload = valid_payload()
        payload["periods"] = [
            {"value": "2014", "kind": "NAM", "raw": "2014"},
            {"value": "2015-Q3", "kind": "QUY", "raw": "quý 3/2015"},
            {"value": "2015-9M", "kind": "LUY_KE", "raw": "lũy kế 9 tháng"},
        ]

        schema = QueryUnderstanding.from_dict(payload)

        self.assertEqual([period.kind.value for period in schema.periods], ["NAM", "QUY", "LUY_KE"])

    def test_multiple_metrics(self):
        payload = valid_payload()
        payload["metrics"] = [
            {"raw": "lãi ròng", "canonical": "LNST", "confidence": 0.95},
            {
                "raw": "vốn chủ sở hữu",
                "canonical": "VON_CHU_SO_HUU",
                "confidence": 0.9,
            },
        ]

        schema = QueryUnderstanding.from_dict(payload)

        self.assertEqual([metric.canonical for metric in schema.metrics], ["LNST", "VON_CHU_SO_HUU"])

    def test_invalid_operation(self):
        payload = valid_payload()
        payload["operation"] = "calculate"

        with self.assertRaises(SchemaValidationError):
            QueryUnderstanding.from_dict(payload)

    def test_invalid_period_kind(self):
        payload = valid_payload()
        payload["periods"][0]["kind"] = "THANG"

        with self.assertRaises(SchemaValidationError):
            QueryUnderstanding.from_dict(payload)

    def test_confidence_below_zero(self):
        payload = valid_payload()
        payload["confidence"] = -0.01

        with self.assertRaises(SchemaValidationError):
            QueryUnderstanding.from_dict(payload)

    def test_confidence_above_one(self):
        payload = valid_payload()
        payload["confidence"] = 1.01

        with self.assertRaises(SchemaValidationError):
            QueryUnderstanding.from_dict(payload)

    def test_all_nested_confidences_are_bounded(self):
        invalid_paths = [
            ("company", "confidence"),
            ("statement_scope", "confidence"),
            ("metrics", 0, "confidence"),
        ]
        for path in invalid_paths:
            with self.subTest(path=path):
                payload = valid_payload()
                target = payload
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = math.inf
                with self.assertRaises(SchemaValidationError):
                    QueryUnderstanding.from_dict(payload)

    def test_null_statement_scope(self):
        payload = valid_payload()
        payload["statement_scope"] = {
            "value": None,
            "inferred": False,
            "confidence": 0.0,
        }

        schema = QueryUnderstanding.from_dict(payload)

        self.assertIsNone(schema.statement_scope.value)

    def test_explicit_and_inferred_scope_are_distinguishable(self):
        explicit_payload = valid_payload()
        explicit_payload["statement_scope"]["inferred"] = False
        inferred_payload = valid_payload()
        inferred_payload["statement_scope"]["inferred"] = True

        explicit = QueryUnderstanding.from_dict(explicit_payload)
        inferred = QueryUnderstanding.from_dict(inferred_payload)

        self.assertFalse(explicit.statement_scope.inferred)
        self.assertTrue(inferred.statement_scope.inferred)

    def test_requested_scale_and_unit_may_be_null(self):
        payload = valid_payload()
        payload["requested_scale"] = None
        payload["requested_unit"] = None

        schema = QueryUnderstanding.from_dict(payload)

        self.assertIsNone(schema.requested_scale)
        self.assertIsNone(schema.requested_unit)

    def test_missing_information_and_ambiguities(self):
        payload = valid_payload()
        payload["missing_information"] = ["period"]
        payload["ambiguities"] = ["statement_scope"]

        schema = QueryUnderstanding.from_dict(payload)

        self.assertEqual(schema.missing_information, ["period"])
        self.assertEqual(schema.ambiguities, ["statement_scope"])

    def test_empty_periods_and_metrics_are_allowed(self):
        payload = valid_payload()
        payload["periods"] = []
        payload["metrics"] = []
        payload["missing_information"] = ["period", "metric"]

        schema = QueryUnderstanding.from_dict(payload)

        self.assertEqual(schema.periods, [])
        self.assertEqual(schema.metrics, [])

    def test_missing_field_is_rejected(self):
        payload = valid_payload()
        del payload["operation"]

        with self.assertRaises(SchemaValidationError):
            QueryUnderstanding.from_dict(payload)

    def test_unknown_field_is_rejected(self):
        payload = valid_payload()
        payload["extra"] = "not allowed"

        with self.assertRaises(SchemaValidationError):
            QueryUnderstanding.from_dict(payload)

    def test_serialization_round_trip(self):
        payload = valid_payload()

        first = QueryUnderstanding.from_dict(copy.deepcopy(payload))
        second = QueryUnderstanding.from_dict(first.to_dict())

        self.assertEqual(second.to_dict(), payload)


if __name__ == "__main__":
    unittest.main()
