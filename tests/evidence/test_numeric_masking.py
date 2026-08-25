import copy
from dataclasses import replace
from hashlib import sha256
import json
import unittest

from src.evidence.m5_schemas import (
    M5_MASKING_CONTRACT_VERSION,
    MaskedEvidenceBundle,
    ScaleUnitResolution,
    ScaleUnitResolutionStatus,
    make_value_placeholder,
)
from src.evidence.numeric_masking import (
    NumericMaskingError,
    NumericMaskingFailureCode,
    mask_numeric_evidence,
)
from src.evidence.schema_linker import link_schema
from tests.evidence.m5_helpers import (
    grounded_cell,
    multi_period_plan,
    plan,
    resolved_scale,
)


class NumericMaskingTests(unittest.TestCase):
    def mask_one(self, *, current_plan=None, item=None, location=None, scales=None):
        current_plan = plan() if current_plan is None else current_plan
        if item is None or location is None:
            item, location = grounded_cell()
        links = link_schema(
            current_plan, [item], {item.evidence_id: location}
        )
        return mask_numeric_evidence(
            current_plan,
            [item],
            {item.evidence_id: location},
            links,
            [resolved_scale(item)] if scales is None else scales,
        )

    def test_deterministic_full_sha_placeholder_and_contract_round_trip(self):
        item, location = grounded_cell()
        bundle = self.mask_one(item=item, location=location)
        payload = json.dumps(
            {
                "contract_version": M5_MASKING_CONTRACT_VERSION,
                "evidence_id": item.evidence_id,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        expected = f"val_{sha256(payload).hexdigest()}"
        self.assertEqual(bundle.items[0].placeholder, expected)
        self.assertEqual(len(expected), 68)
        self.assertEqual(
            MaskedEvidenceBundle.from_dict(bundle.to_dict()).to_dict(),
            bundle.to_dict(),
        )

    def test_same_evidence_reuses_placeholder_and_different_evidence_does_not(self):
        same_a = make_value_placeholder("evidence-one")
        same_b = make_value_placeholder("evidence-one")
        different = make_value_placeholder("evidence-two")
        self.assertEqual(same_a, same_b)
        self.assertNotEqual(same_a, different)

    def test_programmer_bundle_has_no_raw_or_normalized_numeric_leakage(self):
        literal = "987654321.123"
        item, location = grounded_cell(raw_value=literal, decimal_value=literal)
        bundle = self.mask_one(item=item, location=location)
        serialized = json.dumps(bundle.to_dict(), ensure_ascii=False)
        self.assertNotIn(literal, serialized)
        self.assertNotIn("raw_value", serialized)
        self.assertNotIn("normalized_value", serialized)
        self.assertEqual(bundle.items[0].metric, "LNST")
        self.assertEqual(bundle.items[0].period, "2015")
        self.assertEqual(bundle.items[0].row_path, location.row_path)
        self.assertEqual(bundle.items[0].column_path, location.column_path)

    def test_ambiguous_schema_is_rejected(self):
        current_plan = plan()
        item_a, location_a = grounded_cell("a")
        item_b, location_b = grounded_cell("b")
        evidence = [item_a, item_b]
        locations = {
            item_a.evidence_id: location_a,
            item_b.evidence_id: location_b,
        }
        links = link_schema(current_plan, evidence, locations)
        with self.assertRaises(NumericMaskingError) as caught:
            mask_numeric_evidence(
                current_plan,
                evidence,
                locations,
                links,
                [resolved_scale(item_a), resolved_scale(item_b)],
            )
        self.assertIs(
            caught.exception.code,
            NumericMaskingFailureCode.SCHEMA_LINK_AMBIGUOUS,
        )

    def test_unresolved_schema_is_rejected(self):
        current_plan = plan()
        item, location = grounded_cell(
            row_labels=("Unknown",), column_labels=("2014",)
        )
        links = link_schema(
            current_plan, [item], {item.evidence_id: location}
        )
        with self.assertRaises(NumericMaskingError) as caught:
            mask_numeric_evidence(
                current_plan,
                [item],
                {item.evidence_id: location},
                links,
                [resolved_scale(item)],
            )
        self.assertIs(
            caught.exception.code,
            NumericMaskingFailureCode.SCHEMA_LINK_UNRESOLVED,
        )

    def test_missing_canonical_decimal_is_rejected(self):
        current_plan = plan()
        item, location = grounded_cell()
        non_numeric = replace(item, normalized_value=None)
        links = link_schema(
            current_plan, [non_numeric], {non_numeric.evidence_id: location}
        )
        with self.assertRaises(NumericMaskingError) as caught:
            mask_numeric_evidence(
                current_plan,
                [non_numeric],
                {non_numeric.evidence_id: location},
                links,
                [resolved_scale(non_numeric)],
            )
        self.assertIs(
            caught.exception.code,
            NumericMaskingFailureCode.EVIDENCE_NOT_NUMERIC,
        )

    def test_required_unresolved_scale_is_rejected(self):
        current_plan = plan()
        item, location = grounded_cell()
        links = link_schema(
            current_plan, [item], {item.evidence_id: location}
        )
        unresolved = ScaleUnitResolution(
            evidence_id=item.evidence_id,
            status=ScaleUnitResolutionStatus.UNRESOLVED,
            source_scale=None,
            source_unit=None,
            requested_output_scale=None,
            requested_output_unit=None,
            winning_hint_ids=[],
            considered_hint_ids=[],
        )
        with self.assertRaises(NumericMaskingError) as caught:
            mask_numeric_evidence(
                current_plan,
                [item],
                {item.evidence_id: location},
                links,
                [unresolved],
            )
        self.assertIs(
            caught.exception.code,
            NumericMaskingFailureCode.SCALE_RESOLUTION_NOT_RESOLVED,
        )

    def test_source_order_follows_plan_not_evidence_input(self):
        current_plan = multi_period_plan()
        item_2015, location_2015 = grounded_cell("2015", column_labels=("2015",))
        item_2014, location_2014 = grounded_cell("2014", column_labels=("2014",))
        evidence = [item_2015, item_2014]
        locations = {
            item_2015.evidence_id: location_2015,
            item_2014.evidence_id: location_2014,
        }
        evidence_before = copy.deepcopy([item.to_dict() for item in evidence])
        links = link_schema(current_plan, evidence, locations)
        bundle = mask_numeric_evidence(
            current_plan,
            evidence,
            locations,
            links,
            [resolved_scale(item_2015), resolved_scale(item_2014)],
        )
        self.assertEqual([item.period for item in bundle.items], ["2014", "2015"])
        self.assertEqual([item.to_dict() for item in evidence], evidence_before)


if __name__ == "__main__":
    unittest.main()
