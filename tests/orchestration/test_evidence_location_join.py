from dataclasses import replace
import unittest

from src.orchestration.nodes import (
    OrchestrationFailure,
    OrchestrationFailureCode,
    _locations_by_evidence,
)
from tests.evidence.m5_helpers import grounded_cell


class EvidenceLocationJoinTests(unittest.TestCase):
    """Distinct source cells may share report/table/header-path labels."""

    def test_shared_header_path_resolves_by_exact_location_identity(self):
        pairs = [grounded_cell(name) for name in ("a", "b")]
        items = [replace(item, evidence_id=loc.location_id) for item, loc in pairs]
        locations = [loc for _, loc in pairs]

        result = _locations_by_evidence(items, locations)

        self.assertEqual(
            result, {loc.location_id: loc for loc in locations}
        )

    def test_shared_header_path_without_identity_stays_ambiguous(self):
        pairs = [grounded_cell(name) for name in ("a", "b")]

        with self.assertRaises(OrchestrationFailure) as raised:
            _locations_by_evidence(
                [item for item, _ in pairs], [loc for _, loc in pairs]
            )

        self.assertEqual(
            raised.exception.code,
            OrchestrationFailureCode.EVIDENCE_LOCATION_AMBIGUOUS.value,
        )


if __name__ == "__main__":
    unittest.main()
