import unittest

from src.evidence.masking_schemas import (
    SchemaLinkMatchBasis,
    SchemaLinkResult,
    SchemaLinkStatus,
)
from src.evidence.schema_linker import link_schema
from src.indexing.schemas import HeaderPathEntry
from tests.evidence.helpers import grounded_cell, plan


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
        item_b, location_b = grounded_cell("b", raw_value="13.00", decimal_value="13.00")
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


def test_schema_linking_uses_target_report_not_next_reports_comparative_column():
    from dataclasses import replace
    current, current_location = grounded_cell(
        "current", row_labels=("Lợi nhuận sau thuế TNDN (60 = 50 - 51 - 52) Phân bổ cho:",))
    comparative, comparative_location = grounded_cell("comparative", row_labels=("Lợi nhuận sau thuế TNDN",))
    current = replace(current, report_year=2015)
    comparative = replace(comparative, report_year=2016)
    current_location = replace(current_location, report_year=2015)
    comparative_location = replace(comparative_location, report_year=2016)
    result = link_schema(plan(), [comparative, current], {
        current.evidence_id: current_location,
        comparative.evidence_id: comparative_location,
    })
    assert len(result) == 1
    assert result[0].status is SchemaLinkStatus.RESOLVED
    assert result[0].evidence_id == current.evidence_id
    assert result[0].row_path == current_location.row_path


def test_schema_linking_preserves_wrong_year_for_verifier_retry_when_no_target_report():
    from dataclasses import replace
    item, location = grounded_cell()
    item = replace(item, report_year=2016)
    location = replace(location, report_year=2016)
    result = link_schema(plan(), [item], {item.evidence_id: location})
    assert len(result) == 1
    assert result[0].status is SchemaLinkStatus.RESOLVED
    assert result[0].evidence_id == item.evidence_id
    assert location.report_year == 2016


def test_same_year_duplicates_with_different_values_remain_ambiguous():
    from dataclasses import replace
    pairs = [grounded_cell("first"), grounded_cell("second", raw_value="13.00", decimal_value="13.00")]
    evidence = [replace(item, report_year=2015) for item, _ in pairs]
    locations = {item.evidence_id: replace(location, report_year=2015)
                 for item, location in pairs}
    result = link_schema(plan(), evidence, locations)
    assert len(result) == 2
    assert all(link.status is SchemaLinkStatus.AMBIGUOUS for link in result)


def test_one_figure_repeated_in_one_report_resolves_to_the_income_statement_cell():
    from dataclasses import replace

    from src.supervisor.schemas import TableClass
    pairs = [grounded_cell(name) for name in ("carried", "statement")]
    evidence = [replace(item, report_year=2015) for item, _ in pairs]
    locations = {item.evidence_id: replace(location, report_year=2015) for item, location in pairs}
    locations["evidence-statement"] = replace(locations["evidence-statement"], table_class=TableClass.INCOME_STATEMENT)
    result = link_schema(plan(), evidence, locations)
    assert [(link.status, link.evidence_id) for link in result] == [
        (SchemaLinkStatus.RESOLVED, "evidence-statement")]


def test_equal_values_from_different_reports_remain_ambiguous():
    from dataclasses import replace
    pairs = [grounded_cell(name) for name in ("first", "second")]
    evidence = [replace(pairs[0][0], report_year=2015),
                replace(pairs[1][0], report_year=2015, report_ref="other-report")]
    locations = {pairs[0][0].evidence_id: replace(pairs[0][1], report_year=2015),
                 pairs[1][0].evidence_id: replace(pairs[1][1], report_year=2015, report_id="other-report")}
    result = link_schema(plan(), evidence, locations)
    assert all(link.status is SchemaLinkStatus.AMBIGUOUS for link in result)


def test_one_repeated_figure_prefers_the_fullest_table():
    from dataclasses import replace
    note, note_location = grounded_cell("a-note")
    statement, statement_location = grounded_cell("b-statement")
    other, other_location = grounded_cell("c-other-line", row_labels=("Doanh thu",))
    statement_location = replace(statement_location, table_id="statement-table")
    other_location = replace(other_location, table_id="statement-table")
    statement = replace(statement, table_ref="statement-table")
    other = replace(other, table_ref="statement-table")
    result = link_schema(plan(), [note, statement, other], {
        note.evidence_id: note_location, statement.evidence_id: statement_location,
        other.evidence_id: other_location,
    })
    assert [(link.status, link.evidence_id) for link in result] == [
        (SchemaLinkStatus.RESOLVED, "evidence-b-statement")]
