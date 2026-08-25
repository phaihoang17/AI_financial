import copy
import unittest

from src.evidence.m5_schemas import ScaleUnitResolutionStatus, SchemaLinkStatus
from src.evidence.scale_unit_resolver import resolve_scale_units
from src.evidence.schema_linker import link_schema
from src.evidence.schemas import Scale
from src.indexing.schemas import ScaleHintSource
from tests.evidence.m5_helpers import grounded_cell, hint, plan, understanding


class M5Batch1IntegrationTests(unittest.TestCase):
    def test_plan_evidence_scale_then_schema_link_without_provenance_mutation(self):
        current_plan = plan()
        item, location = grounded_cell(row_labels=("Kết quả", "LNST"))
        evidence_before = copy.deepcopy(item.to_dict())
        location_before = copy.deepcopy(location)
        scales = resolve_scale_units(
            understanding("BILLION"),
            [item],
            {item.evidence_id: location},
            {
                location.candidate_id: [
                    hint("header", location, ScaleHintSource.HEADER, Scale.MILLION)
                ]
            },
        )
        links = link_schema(
            current_plan, [item], {item.evidence_id: location}
        )
        self.assertIs(scales[0].status, ScaleUnitResolutionStatus.RESOLVED)
        self.assertIs(scales[0].source_scale, Scale.MILLION)
        self.assertEqual(scales[0].requested_output_scale.value, "BILLION")
        self.assertIs(links[0].status, SchemaLinkStatus.RESOLVED)
        self.assertEqual(links[0].matched_source_cell_id, location.source_cell_id)
        self.assertEqual(item.to_dict(), evidence_before)
        self.assertEqual(location, location_before)


if __name__ == "__main__":
    unittest.main()
