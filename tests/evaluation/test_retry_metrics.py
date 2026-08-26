import unittest

from src.evaluation.e2e import RetrievedEvidenceE2ECaseResult, evaluate_e2e_cases
from src.evaluation.e2e_fixtures import e2e_fixture_cases
from src.evaluation.retry_metrics import retry_escalation_metrics
from src.orchestration.schemas import FinalResponseStatus


def result(case_id, *, retries_used, strong_escalated, status):
    return RetrievedEvidenceE2ECaseResult(
        case_id=case_id,
        nlu_correct=True,
        plan_correct=True,
        evidence_correct=True,
        trace_correct=True,
        execution_correct=True,
        verification_correct=True,
        status_correct=True,
        answer_correct=True,
        retry_correct=True,
        escalation_correct=True,
        failure_attribution_correct=True,
        final_status=status,
        retries_used=retries_used,
        strong_escalated=strong_escalated,
        terminal_failure=None,
    )


class RetryMetricsTests(unittest.TestCase):
    def test_rates_and_recovery(self):
        results = [
            result("a", retries_used=0, strong_escalated=False, status=FinalResponseStatus.PASS),
            result("b", retries_used=1, strong_escalated=False, status=FinalResponseStatus.PASS),
            result("c", retries_used=2, strong_escalated=True, status=FinalResponseStatus.PASS),
            result("d", retries_used=2, strong_escalated=True, status=FinalResponseStatus.ABSTAIN),
            result("e", retries_used=None, strong_escalated=False, status=FinalResponseStatus.CLARIFICATION),
        ]
        metrics = retry_escalation_metrics(results)
        self.assertEqual(metrics.total_cases, 5)
        self.assertEqual(metrics.executed_cases, 4)  # d/e excluded? e has None -> excluded
        self.assertEqual(metrics.retried_cases, 3)  # b, c, d
        self.assertEqual(metrics.escalated_cases, 2)  # c, d
        self.assertAlmostEqual(metrics.retry_rate, 3 / 4)
        self.assertAlmostEqual(metrics.escalation_rate, 2 / 4)
        self.assertAlmostEqual(metrics.retry_recovery_rate, 2 / 3)  # b, c passed of b,c,d
        self.assertAlmostEqual(metrics.escalation_recovery_rate, 1 / 2)  # c passed of c,d
        self.assertEqual(metrics.retries_used_distribution, {"0": 1, "1": 1, "2": 2, "NONE": 1})

    def test_undefined_rates_are_none(self):
        metrics = retry_escalation_metrics(
            [result("x", retries_used=None, strong_escalated=False, status=FinalResponseStatus.CLARIFICATION)]
        )
        self.assertEqual(metrics.executed_cases, 0)
        self.assertIsNone(metrics.retry_rate)
        self.assertIsNone(metrics.retry_recovery_rate)

    def test_confidence_bands_bucket_only_observationally(self):
        results = [
            result("low", retries_used=1, strong_escalated=False, status=FinalResponseStatus.ABSTAIN),
            result("high", retries_used=0, strong_escalated=False, status=FinalResponseStatus.PASS),
        ]
        metrics = retry_escalation_metrics(
            results, confidence_by_case={"low": 0.3, "high": 1.0}
        )
        bands = metrics.confidence_bands
        self.assertEqual(bands["[0.00,0.50)"]["cases"], 1)
        self.assertEqual(bands["[0.00,0.50)"]["retried"], 1)
        self.assertEqual(bands["[0.90,1.00]"]["cases"], 1)
        self.assertEqual(bands["[0.90,1.00]"]["retried"], 0)

    def test_runs_over_real_fixtures(self):
        report = evaluate_e2e_cases(e2e_fixture_cases())
        metrics = retry_escalation_metrics(report.case_results)
        payload = metrics.to_dict()
        self.assertEqual(payload["production_measurement_blocker"], "GPU_PRODUCTION_VALIDATION_PENDING")
        self.assertEqual(
            payload["routing_source_of_truth"], "src.verification.retry.build_retry_directive"
        )
        self.assertEqual(metrics.total_cases, len(report.case_results))


if __name__ == "__main__":
    unittest.main()
