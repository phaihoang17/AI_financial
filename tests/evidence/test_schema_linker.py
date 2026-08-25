import unittest

from src.evidence.m5_schemas import (
    SchemaLinkMatchBasis,
    SchemaLinkResult,
    SchemaLinkStatus,
)
from src.evidence.schema_linker import link_schema
from src.indexing.schemas import HeaderPathEntry
from tests.evidence.m5_helpers import grounded_cell, plan


class SchemaLinkerTests(unittest.TestCase):
    def test_contract_round_trip_preserves_structured_paths(self):
        payload = {
            "requirement_id": "requirement",
            "evidence_id": "evidence",
            "status": "RESOLVED",
            "metric": "LNST",
            "period": "2015",
            "row_path": [{"header_id": "row", "label": "LNST"}],
            "column_path": [{"header_id": "column", "label": "2015"}],
            "matched_source_cell_id": "cell",
            "match_basis": "EXACT_METRIC_AND_PERIOD",
        }
        result = SchemaLinkResult.from_dict(payload)
        self.assertIsInstance(result.row_path[0], HeaderPathEntry)
        self.assertEqual(result.to_dict(), payload)

    def test_exact_metric_path_only(self):
        item, location = grounded_cell(column_labels=("2014",))
        result = link_schema(plan(), [item], {item.evidence_id: location})[0]
        self.assertIs(result.status, SchemaLinkStatus.UNRESOLVED)
        self.assertEqual(result.metric, "LNST")
        self.assertIsNone(result.period)
        self.assertIs(result.match_basis, SchemaLinkMatchBasis.EXACT_METRIC_PATH)

    def test_exact_period_path_only(self):
        item, location = grounded_cell(row_labels=("Unmapped metric",))
        result = link_schema(plan(), [item], {item.evidence_id: location})[0]
        self.assertIs(result.status, SchemaLinkStatus.UNRESOLVED)
        self.assertIsNone(result.metric)
        self.assertEqual(result.period, "2015")
        self.assertIs(result.match_basis, SchemaLinkMatchBasis.EXACT_PERIOD_PATH)

    def test_exact_metric_and_period_resolves_using_existing_registry(self):
        item, location = grounded_cell(row_labels=("Báo cáo", "lãi ròng"))
        result = link_schema(plan(), [item], {item.evidence_id: location})[0]
        self.assertIs(result.status, SchemaLinkStatus.RESOLVED)
        self.assertEqual(result.metric, "LNST")
        self.assertEqual(result.period, "2015")
        self.assertEqual(result.matched_source_cell_id, "cell-one")
        self.assertIs(result.match_basis, SchemaLinkMatchBasis.EXACT_METRIC_AND_PERIOD)
        self.assertEqual(result.row_path, location.row_path)

    def test_multiple_valid_locations_are_ambiguous_and_ordered(self):
        item_b, location_b = grounded_cell("b")
        item_a, location_a = grounded_cell("a")
        results = link_schema(
            plan(),
            [item_b, item_a],
            {item_b.evidence_id: location_b, item_a.evidence_id: location_a},
        )
        self.assertEqual([item.status for item in results], [SchemaLinkStatus.AMBIGUOUS] * 2)
        self.assertEqual([item.evidence_id for item in results], ["evidence-a", "evidence-b"])

    def test_no_valid_match_is_unresolved(self):
        item, location = grounded_cell(
            row_labels=("Unknown",), column_labels=("2014",)
        )
        result = link_schema(plan(), [item], {item.evidence_id: location})[0]
        self.assertIs(result.status, SchemaLinkStatus.UNRESOLVED)
        self.assertIs(result.match_basis, SchemaLinkMatchBasis.STRUCTURAL)
        self.assertIsNone(result.matched_source_cell_id)

    def test_no_fuzzy_metric_match(self):
        item, location = grounded_cell(row_labels=("LNSTT",))
        result = link_schema(plan(), [item], {item.evidence_id: location})[0]
        self.assertIs(result.status, SchemaLinkStatus.UNRESOLVED)
        self.assertIsNone(result.metric)
        self.assertIs(result.match_basis, SchemaLinkMatchBasis.EXACT_PERIOD_PATH)

    def test_off_evidence_location_is_not_searched(self):
        item, location = grounded_cell(
            "in", row_labels=("Unknown",), column_labels=("2014",)
        )
        _, off_evidence = grounded_cell("off")
        result = link_schema(
            plan(),
            [item],
            {item.evidence_id: location, "not-an-evidence-id": off_evidence},
        )[0]
        self.assertIs(result.status, SchemaLinkStatus.UNRESOLVED)
        self.assertNotEqual(result.matched_source_cell_id, off_evidence.source_cell_id)


if __name__ == "__main__":
    unittest.main()
