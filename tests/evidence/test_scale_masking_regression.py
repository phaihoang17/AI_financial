import unittest

from src.evidence.m5_schemas import ScaleUnitResolutionStatus
from src.evidence.numeric_masking import (
    NumericMaskingError,
    NumericMaskingFailureCode,
    mask_numeric_evidence,
)
from src.evidence.scale_unit_resolver import resolve_scale_units
from src.evidence.schema_linker import link_schema
from src.evidence.schemas import Scale
from src.indexing.schemas import ScaleHintSource
from tests.evidence.m5_helpers import grounded_cell, hint, plan, understanding


class Task055ScaleMaskingRegressionTests(unittest.TestCase):
    def inputs(self, hints):
        current_plan = plan()
        item, location = grounded_cell()
        locations = {item.evidence_id: location}
        scale_results = resolve_scale_units(
            understanding(),
            [item],
            locations,
            {location.candidate_id: hints(location)},
        )
        schema_links = link_schema(current_plan, [item], locations)
        return current_plan, item, locations, scale_results, schema_links

    def test_resolved_retrieved_hint_crosses_numeric_masking_gate(self):
        current_plan, item, locations, scales, links = self.inputs(
            lambda location: [
                hint("header", location, ScaleHintSource.HEADER, Scale.MILLION)
            ]
        )
        self.assertIs(scales[0].status, ScaleUnitResolutionStatus.RESOLVED)
        bundle = mask_numeric_evidence(
            current_plan, [item], locations, links, scales
        )
        self.assertEqual(bundle.items[0].evidence_id, item.evidence_id)

    def test_unresolved_required_scale_blocks_numeric_masking(self):
        current_plan, item, locations, scales, links = self.inputs(
            lambda _location: []
        )
        self.assertIs(scales[0].status, ScaleUnitResolutionStatus.UNRESOLVED)
        with self.assertRaises(NumericMaskingError) as caught:
            mask_numeric_evidence(
                current_plan, [item], locations, links, scales
            )
        self.assertIs(
            caught.exception.code,
            NumericMaskingFailureCode.SCALE_RESOLUTION_NOT_RESOLVED,
        )

    def test_ambiguous_required_scale_blocks_numeric_masking(self):
        current_plan, item, locations, scales, links = self.inputs(
            lambda location: [
                hint("header-million", location, ScaleHintSource.HEADER, Scale.MILLION),
                hint("header-billion", location, ScaleHintSource.HEADER, Scale.BILLION, start=2),
            ]
        )
        self.assertIs(scales[0].status, ScaleUnitResolutionStatus.AMBIGUOUS)
        with self.assertRaises(NumericMaskingError) as caught:
            mask_numeric_evidence(
                current_plan, [item], locations, links, scales
            )
        self.assertIs(
            caught.exception.code,
            NumericMaskingFailureCode.SCALE_RESOLUTION_NOT_RESOLVED,
        )


if __name__ == "__main__":
    unittest.main()
