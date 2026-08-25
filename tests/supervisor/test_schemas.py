import copy
import unittest

from src.supervisor.schemas import EvidenceSource, Plan, RetrievalRequirement, TableClass
from src.understanding.schemas import SchemaValidationError


def lookup_plan():
    requirement = RetrievalRequirement.create(
        EvidenceSource.TABLE, TableClass.INCOME_STATEMENT, "LNST", "2015"
    )
    return {
        "question_type": "LOOKUP",
        "company": {"name": "Công ty Cổ phần Nhựa An Phát Xanh", "ticker": "AAA"},
        "periods": ["2015"],
        "period_kind": "NAM",
        "statement_scope": "HOP_NHAT",
        "target_metrics": ["LNST"],
        "derived_target": None,
        "formula_id": None,
        "tables_needed": ["INCOME_STATEMENT"],
        "retrieval_requirements": [requirement.to_dict()],
        "evidence_sources": ["TABLE"],
        "reasoning_mode": "DIRECT",
        "requires_scale_resolution": True,
        "model_tier": "CHEAP",
        "verify_profile": "LIGHT",
        "max_retries": 1,
        "confidence": 0.95,
        "abstain": False,
        "abstain_reason": None,
    }


def growth_plan():
    payload = lookup_plan()
    requirements = [
        RetrievalRequirement.create(
            EvidenceSource.TABLE, TableClass.INCOME_STATEMENT, "LNST", period
        ).to_dict()
        for period in ("2014", "2015")
    ]
    payload.update(
        {
            "question_type": "MULTI_PERIOD",
            "periods": ["2014", "2015"],
            "target_metrics": ["LNST"],
            "derived_target": "GROWTH",
            "formula_id": "GROWTH_RATE",
            "tables_needed": ["INCOME_STATEMENT"],
            "retrieval_requirements": requirements,
            "reasoning_mode": "PROGRAM",
            "model_tier": "STRONG",
            "verify_profile": "STRICT",
            "max_retries": 2,
        }
    )
    return payload


class PlanSchemaTests(unittest.TestCase):
    def test_valid_lookup_plan(self):
        payload = lookup_plan()

        plan = Plan.from_dict(payload)

        self.assertEqual(plan.to_dict(), payload)

    def test_valid_growth_serialization(self):
        payload = growth_plan()

        plan = Plan.from_dict(payload)

        self.assertEqual(plan.to_dict(), payload)
        self.assertEqual(plan.periods, ["2014", "2015"])
        self.assertEqual(plan.tables_needed, [TableClass.INCOME_STATEMENT])

    def test_valid_multi_period_plan(self):
        payload = growth_plan()

        plan = Plan.from_dict(payload)

        self.assertEqual(plan.question_type.value, "MULTI_PERIOD")

    def test_valid_aggregate_plan(self):
        payload = lookup_plan()
        requirements = [
            RetrievalRequirement.create(
                EvidenceSource.TABLE, TableClass.INCOME_STATEMENT, "LNST", period
            ).to_dict()
            for period in ("2013", "2014", "2015")
        ]
        payload.update(
            {
                "question_type": "AGGREGATE",
                "periods": ["2013", "2014", "2015"],
                "derived_target": "AVERAGE",
                "formula_id": "AVERAGE",
                "reasoning_mode": "PROGRAM",
                "model_tier": "STRONG",
                "verify_profile": "STRICT",
                "max_retries": 2,
                "retrieval_requirements": requirements,
            }
        )

        plan = Plan.from_dict(payload)

        self.assertEqual(plan.question_type.value, "AGGREGATE")

    def test_invalid_enum_fields_are_rejected(self):
        cases = [
            ("question_type", "UNSUPPORTED"),
            ("period_kind", "THANG"),
            ("statement_scope", "AGGREGATED"),
            ("reasoning_mode", "REFLECTION"),
            ("model_tier", "MEDIUM"),
            ("verify_profile", "NONE"),
        ]
        for field, invalid_value in cases:
            with self.subTest(field=field):
                payload = lookup_plan()
                payload[field] = invalid_value
                with self.assertRaises(SchemaValidationError):
                    Plan.from_dict(payload)

        payload = lookup_plan()
        payload["evidence_sources"] = ["IMAGE"]
        with self.assertRaises(SchemaValidationError):
            Plan.from_dict(payload)

    def test_missing_field_is_rejected(self):
        payload = lookup_plan()
        del payload["period_kind"]

        with self.assertRaises(SchemaValidationError):
            Plan.from_dict(payload)

    def test_unknown_field_is_rejected(self):
        payload = lookup_plan()
        payload["extra"] = "not allowed"

        with self.assertRaises(SchemaValidationError):
            Plan.from_dict(payload)

    def test_confidence_bounds(self):
        for confidence in (-0.01, 1.01):
            with self.subTest(confidence=confidence):
                payload = lookup_plan()
                payload["confidence"] = confidence
                with self.assertRaises(SchemaValidationError):
                    Plan.from_dict(payload)

    def test_invalid_retry_budgets(self):
        for max_retries in (-1, 1.5, True):
            with self.subTest(max_retries=max_retries):
                payload = lookup_plan()
                payload["max_retries"] = max_retries
                with self.assertRaises(SchemaValidationError):
                    Plan.from_dict(payload)

    def test_nullable_fields(self):
        payload = lookup_plan()
        payload["derived_target"] = None
        payload["formula_id"] = None
        payload["abstain_reason"] = None

        plan = Plan.from_dict(payload)

        self.assertIsNone(plan.derived_target)
        self.assertIsNone(plan.formula_id)
        self.assertIsNone(plan.abstain_reason)

    def test_evidence_sources_must_be_derived_from_requirements(self):
        payload = lookup_plan()
        payload["evidence_sources"] = ["TEXT"]
        with self.assertRaises(SchemaValidationError):
            Plan.from_dict(payload)

    def test_serialization_round_trip(self):
        payload = growth_plan()

        first = Plan.from_dict(copy.deepcopy(payload))
        second = Plan.from_dict(first.to_dict())

        self.assertEqual(second.to_dict(), payload)


if __name__ == "__main__":
    unittest.main()
