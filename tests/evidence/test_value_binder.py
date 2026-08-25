import copy
from dataclasses import replace
import unittest

from src.evidence.m5_schemas import BindingMap
from src.evidence.numeric_masking import mask_numeric_evidence
from src.evidence.schema_linker import link_schema
from src.evidence.schemas import CanonicalDecimal, Scale
from src.evidence.value_binder import (
    ValueBindingError,
    ValueBindingFailureCode,
    build_binding_map,
)
from src.understanding.requested_scale_unit_parser import RequestedScale
from tests.evidence.m5_helpers import (
    grounded_cell,
    multi_period_plan,
    plan,
    resolved_scale,
)


class ValueBinderTests(unittest.TestCase):
    def one(self):
        current_plan = plan()
        item, location = grounded_cell(decimal_value="12500000")
        scale = resolved_scale(
            item,
            source_scale=Scale.MILLION,
            source_unit="document-unit",
            requested_output_scale=RequestedScale.BILLION,
            requested_output_unit="output-unit",
        )
        links = link_schema(
            current_plan, [item], {item.evidence_id: location}
        )
        masked = mask_numeric_evidence(
            current_plan,
            [item],
            {item.evidence_id: location},
            links,
            [scale],
        )
        return item, location, scale, masked

    def test_exact_binding_preserves_canonical_decimal_and_scale_metadata(self):
        item, location, scale, masked = self.one()
        evidence_before = copy.deepcopy(item.to_dict())
        result = build_binding_map(
            masked,
            [item],
            {item.evidence_id: location},
            [scale],
        )
        binding = result.bindings[0]
        self.assertEqual(binding.placeholder, masked.items[0].placeholder)
        self.assertEqual(binding.evidence_id, item.evidence_id)
        self.assertIsInstance(binding.value, CanonicalDecimal)
        self.assertIsInstance(binding.value, str)
        self.assertNotIsInstance(binding.value, float)
        self.assertEqual(binding.value, "12500000")
        self.assertIs(binding.source_scale, Scale.MILLION)
        self.assertIs(binding.requested_output_scale, RequestedScale.BILLION)
        self.assertEqual(item.to_dict(), evidence_before)

    def test_no_scale_or_unit_conversion(self):
        item, location, scale, masked = self.one()
        binding = build_binding_map(
            masked,
            [item],
            {item.evidence_id: location},
            [scale],
        ).bindings[0]
        self.assertEqual(binding.value, item.normalized_value)
        self.assertIs(binding.source_scale, Scale.MILLION)
        self.assertIs(binding.requested_output_scale, RequestedScale.BILLION)
        self.assertEqual(binding.source_unit, "document-unit")
        self.assertEqual(binding.requested_output_unit, "output-unit")

    def test_duplicate_placeholder_with_different_evidence_or_value_is_rejected(self):
        current_plan = multi_period_plan()
        item_a, location_a = grounded_cell("a", column_labels=("2014",))
        item_b, location_b = grounded_cell("b", column_labels=("2015",))
        evidence = [item_a, item_b]
        locations = {
            item_a.evidence_id: location_a,
            item_b.evidence_id: location_b,
        }
        scales = [resolved_scale(item_a), resolved_scale(item_b)]
        links = link_schema(current_plan, evidence, locations)
        masked = mask_numeric_evidence(
            current_plan, evidence, locations, links, scales
        )
        masked.items[1].placeholder = masked.items[0].placeholder
        with self.assertRaises(ValueBindingError) as caught:
            build_binding_map(masked, evidence, locations, scales)
        self.assertIs(
            caught.exception.code,
            ValueBindingFailureCode.DUPLICATE_PLACEHOLDER_CONFLICT,
        )

        _, _, _, clean_masked = self.one()
        item, location, scale, _ = self.one()
        conflicting = replace(
            item, normalized_value=CanonicalDecimal("999")
        )
        with self.assertRaises(ValueBindingError) as value_caught:
            build_binding_map(
                clean_masked,
                [item, conflicting],
                {item.evidence_id: location},
                [scale],
            )
        self.assertIs(
            value_caught.exception.code,
            ValueBindingFailureCode.DUPLICATE_PLACEHOLDER_CONFLICT,
        )

    def test_missing_placeholder_and_missing_evidence_are_typed_failures(self):
        item, location, scale, masked = self.one()
        masked.items[0].placeholder = ""
        with self.assertRaises(ValueBindingError) as placeholder_caught:
            build_binding_map(
                masked,
                [item],
                {item.evidence_id: location},
                [scale],
            )
        self.assertIs(
            placeholder_caught.exception.code,
            ValueBindingFailureCode.PLACEHOLDER_MISSING,
        )

        item, location, scale, masked = self.one()
        with self.assertRaises(ValueBindingError) as evidence_caught:
            build_binding_map(
                masked,
                [],
                {item.evidence_id: location},
                [scale],
            )
        self.assertIs(
            evidence_caught.exception.code,
            ValueBindingFailureCode.EVIDENCE_MISSING,
        )

    def test_binding_contract_round_trip_and_serialization_are_deterministic(self):
        current_plan = multi_period_plan()
        item_a, location_a = grounded_cell(
            "a", column_labels=("2014",), decimal_value="1.25"
        )
        item_b, location_b = grounded_cell(
            "b", column_labels=("2015",), decimal_value="2.50"
        )
        evidence = [item_b, item_a]
        locations = {
            item_a.evidence_id: location_a,
            item_b.evidence_id: location_b,
        }
        scales = [resolved_scale(item_b), resolved_scale(item_a)]
        masked = mask_numeric_evidence(
            current_plan,
            evidence,
            locations,
            link_schema(current_plan, evidence, locations),
            scales,
        )
        forward = build_binding_map(masked, evidence, locations, scales)
        masked.items.reverse()
        reverse = build_binding_map(masked, list(reversed(evidence)), locations, list(reversed(scales)))
        self.assertEqual(forward.to_dict(), reverse.to_dict())
        self.assertEqual(BindingMap.from_dict(forward.to_dict()).to_dict(), forward.to_dict())
        self.assertTrue(
            all(isinstance(binding.value, CanonicalDecimal) for binding in forward.bindings)
        )


if __name__ == "__main__":
    unittest.main()
