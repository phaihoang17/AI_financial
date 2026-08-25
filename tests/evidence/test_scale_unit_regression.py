import copy
import unittest

from src.evidence.m5_schemas import ScaleUnitResolutionStatus
from src.evidence.scale_unit_resolver import resolve_scale_unit
from src.evidence.schemas import CanonicalDecimal, Scale
from src.indexing.schemas import ScaleHintSource
from src.retrieval.schemas import HintAssociation
from src.understanding.requested_scale_unit_parser import RequestedScale
from tests.evidence.m5_helpers import grounded_cell, hint, understanding


class Task055ScaleUnitRegressionTests(unittest.TestCase):
    def test_all_canonical_source_scales(self):
        item, location = grounded_cell()
        for scale in (
            Scale.RAW,
            Scale.THOUSAND,
            Scale.MILLION,
            Scale.BILLION,
            Scale.PERCENT,
        ):
            with self.subTest(scale=scale.value):
                result = resolve_scale_unit(
                    understanding(),
                    item,
                    location,
                    [
                        hint(
                            f"scale-{scale.value}",
                            location,
                            ScaleHintSource.CELL,
                            scale,
                        )
                    ],
                )
                self.assertIs(result.status, ScaleUnitResolutionStatus.RESOLVED)
                self.assertIs(result.source_scale, scale)

    def test_direct_cell_header_caption_text_and_linked_hint_sources(self):
        item, location = grounded_cell()
        cases = (
            (ScaleHintSource.CELL, HintAssociation.DIRECT, Scale.RAW),
            (ScaleHintSource.HEADER, HintAssociation.DIRECT, Scale.THOUSAND),
            (ScaleHintSource.CAPTION, HintAssociation.DIRECT, Scale.MILLION),
            (ScaleHintSource.TEXT, HintAssociation.DIRECT, Scale.BILLION),
            (ScaleHintSource.TEXT, HintAssociation.LINKED, Scale.PERCENT),
        )
        for index, (source_kind, association, scale) in enumerate(cases):
            with self.subTest(
                source_kind=source_kind.value,
                association=association.value,
            ):
                clue = hint(
                    f"source-{index}",
                    location,
                    source_kind,
                    scale,
                    association=association,
                )
                result = resolve_scale_unit(
                    understanding(), item, location, [clue]
                )
                self.assertIs(result.source_scale, scale)
                self.assertEqual(result.winning_hint_ids, [clue.hint_id])
                self.assertEqual(result.considered_hint_ids, [clue.hint_id])

    def test_complete_precedence_chain(self):
        item, location = grounded_cell()
        ordered = [
            hint("cell", location, ScaleHintSource.CELL, Scale.RAW, start=50),
            hint("header", location, ScaleHintSource.HEADER, Scale.THOUSAND, start=40),
            hint("caption", location, ScaleHintSource.CAPTION, Scale.MILLION, start=30),
            hint("text", location, ScaleHintSource.TEXT, Scale.BILLION, start=20),
            hint(
                "linked",
                location,
                ScaleHintSource.TEXT,
                Scale.PERCENT,
                association=HintAssociation.LINKED,
                start=10,
            ),
        ]
        expected = [
            Scale.RAW,
            Scale.THOUSAND,
            Scale.MILLION,
            Scale.BILLION,
            Scale.PERCENT,
        ]
        for offset, scale in enumerate(expected):
            with self.subTest(winner=scale.value):
                result = resolve_scale_unit(
                    understanding(), item, location, list(reversed(ordered[offset:]))
                )
                self.assertIs(result.source_scale, scale)
                self.assertEqual(
                    result.winning_hint_ids, [ordered[offset].hint_id]
                )

    def test_same_level_conflict_is_ambiguous(self):
        item, location = grounded_cell()
        clues = [
            hint("header-million", location, ScaleHintSource.HEADER, Scale.MILLION),
            hint("header-billion", location, ScaleHintSource.HEADER, Scale.BILLION, start=2),
            hint("caption", location, ScaleHintSource.CAPTION, Scale.THOUSAND, start=3),
        ]
        result = resolve_scale_unit(understanding(), item, location, clues)
        self.assertIs(result.status, ScaleUnitResolutionStatus.AMBIGUOUS)
        self.assertIsNone(result.source_scale)
        self.assertEqual(result.winning_hint_ids, [])
        self.assertEqual(
            result.considered_hint_ids,
            ["hint-header-million", "hint-header-billion", "hint-caption"],
        )

    def test_no_hint_is_unresolved_without_raw_default(self):
        item, location = grounded_cell()
        result = resolve_scale_unit(understanding(), item, location, [])
        self.assertIs(result.status, ScaleUnitResolutionStatus.UNRESOLVED)
        self.assertIsNone(result.source_scale)
        self.assertNotEqual(result.source_scale, Scale.RAW)
        self.assertEqual(result.winning_hint_ids, [])
        self.assertEqual(result.considered_hint_ids, [])

    def test_percent_literal_is_direct_cell_percent_without_conversion(self):
        item, location = grounded_cell(
            raw_value="12.50%", decimal_value="12.50", percent_literal=True
        )
        evidence_before = copy.deepcopy(item.to_dict())
        numeric_before = copy.deepcopy(location.numeric.to_dict())
        result = resolve_scale_unit(
            understanding(),
            item,
            location,
            [hint("header", location, ScaleHintSource.HEADER, Scale.MILLION)],
        )
        self.assertIs(result.status, ScaleUnitResolutionStatus.RESOLVED)
        self.assertIs(result.source_scale, Scale.PERCENT)
        self.assertEqual(item.normalized_value, CanonicalDecimal("12.50"))
        self.assertEqual(item.to_dict(), evidence_before)
        self.assertEqual(location.numeric.to_dict(), numeric_before)

    def test_requested_scale_and_unit_remain_separate_from_source(self):
        item, location = grounded_cell()
        result = resolve_scale_unit(
            understanding("BILLION", "requested-unit"),
            item,
            location,
            [
                hint(
                    "source",
                    location,
                    ScaleHintSource.HEADER,
                    Scale.MILLION,
                    unit="source-unit",
                )
            ],
        )
        self.assertIs(result.source_scale, Scale.MILLION)
        self.assertEqual(result.source_unit, "source-unit")
        self.assertIs(result.requested_output_scale, RequestedScale.BILLION)
        self.assertEqual(result.requested_output_unit, "requested-unit")

    def test_canonical_decimal_and_raw_value_are_never_converted(self):
        item, location = grounded_cell(
            raw_value="1.234.567,89", decimal_value="1234567.89"
        )
        before = copy.deepcopy(item.to_dict())
        result = resolve_scale_unit(
            understanding("THOUSAND"),
            item,
            location,
            [hint("billion", location, ScaleHintSource.CAPTION, Scale.BILLION)],
        )
        self.assertIs(result.source_scale, Scale.BILLION)
        self.assertIs(result.requested_output_scale, RequestedScale.THOUSAND)
        self.assertEqual(item.normalized_value, CanonicalDecimal("1234567.89"))
        self.assertEqual(item.raw_value, "1.234.567,89")
        self.assertEqual(item.to_dict(), before)

    def test_winning_and_considered_hint_provenance_is_complete_and_ordered(self):
        item, location = grounded_cell()
        clues = [
            hint(
                "linked",
                location,
                ScaleHintSource.TEXT,
                Scale.PERCENT,
                association=HintAssociation.LINKED,
                start=1,
            ),
            hint("caption", location, ScaleHintSource.CAPTION, Scale.BILLION, start=2),
            hint("header-a", location, ScaleHintSource.HEADER, Scale.MILLION, start=4),
            hint("header-b", location, ScaleHintSource.HEADER, Scale.MILLION, start=3),
        ]
        result = resolve_scale_unit(
            understanding(), item, location, list(reversed(clues))
        )
        self.assertEqual(
            result.winning_hint_ids, ["hint-header-b", "hint-header-a"]
        )
        self.assertEqual(
            result.considered_hint_ids,
            ["hint-header-b", "hint-header-a", "hint-caption", "hint-linked"],
        )

    def test_repeated_resolution_is_exactly_deterministic(self):
        item, location = grounded_cell()
        clues = [
            hint("header", location, ScaleHintSource.HEADER, Scale.THOUSAND, start=2),
            hint("caption", location, ScaleHintSource.CAPTION, Scale.MILLION, start=1),
        ]
        baseline = resolve_scale_unit(
            understanding("BILLION", "requested-unit"), item, location, clues
        ).to_dict()
        for _ in range(10):
            repeated = resolve_scale_unit(
                understanding("BILLION", "requested-unit"),
                item,
                location,
                list(reversed(clues)),
            )
            self.assertEqual(repeated.to_dict(), baseline)


if __name__ == "__main__":
    unittest.main()
