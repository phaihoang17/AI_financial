import unittest

from src.evaluation.supervisor import evaluate_supervisor_case
from src.evaluation.supervisor_fixtures import supervisor_fixture_cases
from src.retrieval.query_builder import build_retrieval_query
from src.supervisor.schemas import EvidenceSource


def case_by_id(case_id):
    return next(case for case in supervisor_fixture_cases() if case.case_id == case_id)


class SupervisorRetrievalBoundaryTests(unittest.TestCase):
    def test_valid_supervisor_result_builds_query_without_backend_access(self):
        case = case_by_id("simple-lnst-lookup")
        result = evaluate_supervisor_case(case).actual
        query = build_retrieval_query(result.plan, case.query_understanding)
        self.assertEqual(query.company.ticker, "AAA")
        self.assertEqual(query.periods, ["2024"])
        self.assertEqual(query.target_metrics, ["LNST"])
        self.assertEqual(query.evidence_sources, [EvidenceSource.TABLE])

    def test_abstaining_supervisor_result_cannot_cross_query_boundary(self):
        case = case_by_id("blocked-planning-gate")
        result = evaluate_supervisor_case(case).actual
        self.assertTrue(result.abstain)
        self.assertIsNone(result.plan)
        with self.assertRaises(TypeError):
            build_retrieval_query(result.plan, case.query_understanding)


if __name__ == "__main__":
    unittest.main()
