import unittest

from src.understanding.company_resolver import (
    CompanyAlias,
    resolve_company,
)
from src.understanding.metric_normalizer import (
    MetricSynonym,
    initial_metric_registry,
    resolve_metric,
)
from src.understanding.planning_gate import (
    FindingName,
    PlanningGate,
    evaluate_planning_gate,
)
from src.understanding.schemas import Operation, SchemaValidationError
from src.understanding.temporal_parser import TemporalParserInput, parse_periods


ALIASES = [CompanyAlias(alias="AAA", name="An Phát", ticker="AAA")]


def company(raw="AAA", aliases=None):
    return resolve_company(raw, ALIASES if aliases is None else aliases)


def periods(text="năm 2015"):
    return parse_periods(TemporalParserInput(text=text))


def metrics(raw="LNST", registry=None):
    return [
        resolve_metric(
            raw,
            initial_metric_registry() if registry is None else registry,
        )
    ]


class PlanningGateTests(unittest.TestCase):
    def test_contract_is_exact_and_serializable(self):
        payload = {
            "allowed": False,
            "missing_information": ["COMPANY", "PERIOD"],
            "ambiguities": ["METRIC"],
        }
        gate = PlanningGate.from_dict(payload)
        self.assertEqual(gate.to_dict(), payload)

        with self.assertRaises(SchemaValidationError):
            PlanningGate.from_dict({**payload, "statement_scope": "RIENG"})
        with self.assertRaises(SchemaValidationError):
            PlanningGate(
                allowed=True,
                missing_information=[FindingName.COMPANY],
                ambiguities=[],
            )

    def test_resolved_hard_requirements_allow_none_operation(self):
        gate = evaluate_planning_gate(
            company(), periods(), metrics(), Operation.NONE
        )
        self.assertEqual(
            gate,
            PlanningGate(allowed=True, missing_information=[], ambiguities=[]),
        )

    def test_unknown_operation_blocks_without_inventing_a_finding(self):
        gate = evaluate_planning_gate(
            company(), periods(), metrics(), Operation.UNKNOWN
        )
        self.assertFalse(gate.allowed)
        self.assertEqual(gate.missing_information, [])
        self.assertEqual(gate.ambiguities, [])

    def test_unresolved_and_ambiguous_company_are_separate(self):
        unresolved = evaluate_planning_gate(
            company("ZZZ"), periods(), metrics(), Operation.NONE
        )
        self.assertEqual(unresolved.missing_information, [FindingName.COMPANY])

        ambiguous_aliases = [
            CompanyAlias(alias="An Phát", name="An Phát A", ticker="AAA"),
            CompanyAlias(alias="An Phát", name="An Phát B", ticker="AAB"),
        ]
        ambiguous = evaluate_planning_gate(
            company("An Phát", ambiguous_aliases),
            periods(),
            metrics(),
            Operation.NONE,
        )
        self.assertEqual(ambiguous.ambiguities, [FindingName.COMPANY])

    def test_empty_or_unresolved_requested_period_is_missing(self):
        for period_result in (periods("không có kỳ"), periods("quý 5/2015")):
            with self.subTest(period_result=period_result.to_dict()):
                gate = evaluate_planning_gate(
                    company(), period_result, metrics(), Operation.NONE
                )
                self.assertEqual(gate.missing_information, [FindingName.PERIOD])

    def test_empty_unresolved_and_ambiguous_metrics_are_surfaced(self):
        empty = evaluate_planning_gate(company(), periods(), [], Operation.NONE)
        self.assertEqual(empty.missing_information, [FindingName.METRIC])

        unresolved = evaluate_planning_gate(
            company(), periods(), metrics("doanh thu"), Operation.NONE
        )
        self.assertEqual(unresolved.missing_information, [FindingName.METRIC])

        ambiguous_registry = [
            MetricSynonym(synonym="kết quả", canonical="LNST"),
            MetricSynonym(synonym="kết quả", canonical="DOANH_THU"),
        ]
        ambiguous = evaluate_planning_gate(
            company(),
            periods(),
            metrics("kết quả", ambiguous_registry),
            Operation.NONE,
        )
        self.assertEqual(ambiguous.ambiguities, [FindingName.METRIC])

    def test_findings_are_deduplicated_in_canonical_order(self):
        unresolved_metrics = metrics("unknown") + metrics("also unknown")
        gate = evaluate_planning_gate(
            company("ZZZ"), periods("không có kỳ"), unresolved_metrics, Operation.NONE
        )
        self.assertEqual(
            gate.missing_information,
            [FindingName.COMPANY, FindingName.PERIOD, FindingName.METRIC],
        )

        with self.assertRaises(SchemaValidationError):
            PlanningGate(
                allowed=False,
                missing_information=[FindingName.METRIC, FindingName.COMPANY],
                ambiguities=[],
            )
        with self.assertRaises(SchemaValidationError):
            PlanningGate(
                allowed=False,
                missing_information=[FindingName.COMPANY, FindingName.COMPANY],
                ambiguities=[],
            )

    def test_resolver_outputs_are_not_modified(self):
        company_resolution = company()
        period_result = periods()
        metric_resolutions = metrics()
        before = (
            company_resolution.to_dict(),
            period_result.to_dict(),
            [resolution.to_dict() for resolution in metric_resolutions],
        )

        evaluate_planning_gate(
            company_resolution, period_result, metric_resolutions, Operation.NONE
        )

        after = (
            company_resolution.to_dict(),
            period_result.to_dict(),
            [resolution.to_dict() for resolution in metric_resolutions],
        )
        self.assertEqual(after, before)

    def test_statement_scope_is_not_a_gate_input(self):
        gate = evaluate_planning_gate(
            company(), periods(), metrics(), Operation.NONE
        )
        self.assertTrue(gate.allowed)

    def test_invalid_input_types_are_rejected(self):
        with self.assertRaises(SchemaValidationError):
            evaluate_planning_gate("AAA", periods(), metrics(), Operation.NONE)
        with self.assertRaises(SchemaValidationError):
            evaluate_planning_gate(company(), periods(), metrics(), "none")


if __name__ == "__main__":
    unittest.main()
