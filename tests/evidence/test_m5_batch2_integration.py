import copy
import json
import unittest

from src.evidence.numeric_masking import mask_numeric_evidence
from src.evidence.scale_unit_resolver import resolve_scale_units
from src.evidence.schema_linker import link_schema
from src.evidence.schemas import CanonicalDecimal, Scale
from src.evidence.value_binder import build_binding_map
from src.indexing.schemas import ScaleHintSource
from tests.evidence.m5_helpers import grounded_cell, hint, plan, understanding


class M5Batch2IntegrationTests(unittest.TestCase):
    def test_full_plan_to_execution_binding_chain_keeps_programmer_values_masked(self):
        current_plan = plan()
        item, location = grounded_cell(
            raw_value="12.500.000", decimal_value="12500000"
        )
        evidence_before = copy.deepcopy(item.to_dict())
        locations = {item.evidence_id: location}
        scales = resolve_scale_units(
            understanding("BILLION"),
            [item],
            locations,
            {
                location.candidate_id: [
                    hint(
                        "header",
                        location,
                        ScaleHintSource.HEADER,
                        Scale.MILLION,
                    )
                ]
            },
        )
        links = link_schema(current_plan, [item], locations)
        masked = mask_numeric_evidence(
            current_plan, [item], locations, links, scales
        )
        bindings = build_binding_map(masked, [item], locations, scales)

        programmer_payload = json.dumps(masked.to_dict(), ensure_ascii=False)
        self.assertNotIn("12.500.000", programmer_payload)
        self.assertNotIn("12500000", programmer_payload)
        self.assertEqual(bindings.bindings[0].value, CanonicalDecimal("12500000"))
        self.assertIs(bindings.bindings[0].source_scale, Scale.MILLION)
        self.assertEqual(
            bindings.bindings[0].requested_output_scale.value, "BILLION"
        )
        self.assertEqual(
            masked.items[0].evidence_id,
            bindings.bindings[0].evidence_id,
        )
        self.assertEqual(
            links[0].matched_source_cell_id, location.source_cell_id
        )
        self.assertEqual(item.to_dict(), evidence_before)


if __name__ == "__main__":
    unittest.main()
