import copy
import unittest

from src.evidence.m5_schemas import (
    ScaleUnitResolution,
    ScaleUnitResolutionStatus,
)
from src.evidence.scale_unit_resolver import resolve_scale_unit, resolve_scale_units
from src.evidence.schemas import Scale
from src.indexing.schemas import ScaleHintSource
from src.retrieval.schemas import HintAssociation
from src.understanding.requested_scale_unit_parser import RequestedScale
from tests.evidence.m5_helpers import grounded_cell, hint, understanding


class ScaleUnitResolverTests(unittest.TestCase):
    def test_contract_round_trip(self):
        payload = {
            "evidence_id": "evidence-one",
            "status": "RESOLVED",
            "source_scale": "MILLION",
            "source_unit": None,
            "requested_output_scale": "BILLION",
            "requested_output_unit": None,
            "winning_hint_ids": ["hint-cell"],
            "considered_hint_ids": ["hint-cell", "hint-header"],
        }
        self.assertEqual(ScaleUnitResolution.from_dict(payload).to_dict(), payload)

    def test_cell_header_caption_text_and_linked_precedence(self):
        item, location = grounded_cell()
        clues = [
            hint("cell", location, ScaleHintSource.CELL, Scale.PERCENT, start=1),
            hint("header", location, ScaleHintSource.HEADER, Scale.THOUSAND, start=2),
            hint("caption", location, ScaleHintSource.CAPTION, Scale.MILLION, start=3),
            hint("text", location, ScaleHintSource.TEXT, Scale.BILLION, start=4),
            hint("linked", location, ScaleHintSource.TEXT, Scale.RAW, association=HintAssociation.LINKED, start=5),
        ]
        expected = [
            Scale.PERCENT,
            Scale.THOUSAND,
            Scale.MILLION,
            Scale.BILLION,
            Scale.RAW,
        ]
        for offset, scale in enumerate(expected):
            with self.subTest(offset=offset):
                result = resolve_scale_unit(
                    understanding(), item, location, clues[offset:]
                )
                self.assertIs(result.status, ScaleUnitResolutionStatus.RESOLVED)
                self.assertIs(result.source_scale, scale)
        result = resolve_scale_unit(understanding(), item, location, clues)
        self.assertEqual(result.winning_hint_ids, ["hint-cell"])
        self.assertEqual(set(result.considered_hint_ids), {clue.hint_id for clue in clues})

    def test_same_level_conflict_is_ambiguous(self):
        item, location = grounded_cell()
        result = resolve_scale_unit(
            understanding(),
            item,
            location,
            [
                hint("cell-a", location, ScaleHintSource.CELL, Scale.MILLION),
                hint("cell-b", location, ScaleHintSource.CELL, Scale.BILLION, start=2),
                hint("header", location, ScaleHintSource.HEADER, Scale.THOUSAND, start=3),
            ],
        )
        self.assertIs(result.status, ScaleUnitResolutionStatus.AMBIGUOUS)
        self.assertIsNone(result.source_scale)
        self.assertEqual(result.winning_hint_ids, [])
        self.assertEqual(len(result.considered_hint_ids), 3)

    def test_no_hint_is_unresolved_and_never_defaults_raw(self):
        item, location = grounded_cell()
        result = resolve_scale_unit(understanding(), item, location, [])
        self.assertIs(result.status, ScaleUnitResolutionStatus.UNRESOLVED)
        self.assertIsNone(result.source_scale)

    def test_percent_literal_is_exact_cell_scale(self):
        item, location = grounded_cell(percent_literal=True)
        result = resolve_scale_unit(
            understanding(),
            item,
            location,
            [hint("header", location, ScaleHintSource.HEADER, Scale.MILLION)],
        )
        self.assertIs(result.source_scale, Scale.PERCENT)
        self.assertEqual(result.winning_hint_ids, [])
        self.assertEqual(result.considered_hint_ids, ["hint-header"])

    def test_requested_output_stays_separate_and_numeric_is_unchanged(self):
        item, location = grounded_cell(decimal_value="12500000")
        item_before = copy.deepcopy(item.to_dict())
        numeric_before = copy.deepcopy(location.numeric.to_dict())
        result = resolve_scale_unit(
            understanding("BILLION", "presentation-unit"),
            item,
            location,
            [hint("header", location, ScaleHintSource.HEADER, Scale.MILLION)],
        )
        self.assertIs(result.source_scale, Scale.MILLION)
        self.assertIs(result.requested_output_scale, RequestedScale.BILLION)
        self.assertEqual(result.requested_output_unit, "presentation-unit")
        self.assertEqual(item.to_dict(), item_before)
        self.assertEqual(location.numeric.to_dict(), numeric_before)
        self.assertEqual(item.normalized_value, "12500000")

    def test_batch_resolution_is_deterministic_and_candidate_scoped(self):
        item_b, location_b = grounded_cell("b")
        item_a, location_a = grounded_cell("a")
        result = resolve_scale_units(
            understanding(),
            [item_b, item_a],
            {item_b.evidence_id: location_b, item_a.evidence_id: location_a},
            {
                location_b.candidate_id: [hint("b", location_b, ScaleHintSource.HEADER, Scale.BILLION)],
                location_a.candidate_id: [hint("a", location_a, ScaleHintSource.HEADER, Scale.MILLION)],
            },
        )
        self.assertEqual([item.evidence_id for item in result], ["evidence-a", "evidence-b"])
        self.assertEqual([item.source_scale for item in result], [Scale.MILLION, Scale.BILLION])


if __name__ == "__main__":
    unittest.main()
