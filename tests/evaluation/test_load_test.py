import unittest

from src.evaluation.load_test import MAX_CONCURRENCY, run_load_test
from src.evaluation.telemetry import MeasurementSource
from src.understanding.schemas import SchemaValidationError


class LoadTestTests(unittest.TestCase):
    def test_all_requests_accounted_on_success(self):
        result = run_load_test(lambda i: i * 2, total_requests=10, concurrency=3)
        self.assertEqual(result.succeeded, 10)
        self.assertEqual(result.failed, 0)
        self.assertEqual(result.timeouts, 0)
        self.assertIsNotNone(result.latency)
        self.assertEqual(result.latency.count, 10)
        self.assertGreater(result.throughput_rps, 0.0)
        payload = result.to_dict()
        self.assertEqual(payload["measurement_source"], "CPU_FIXTURE")
        self.assertEqual(payload["production_load_status"], "LIVE_PENDING")
        self.assertEqual(payload["error_rate"], 0.0)

    def test_failures_are_counted(self):
        def task(index):
            if index % 2 == 0:
                raise RuntimeError("boom")

        result = run_load_test(task, total_requests=10, concurrency=4)
        self.assertEqual(result.succeeded, 5)
        self.assertEqual(result.failed, 5)
        self.assertEqual(result.to_dict()["error_rate"], 0.5)

    def test_source_label_can_be_live(self):
        result = run_load_test(
            lambda i: None, total_requests=2, concurrency=1, source=MeasurementSource.LIVE
        )
        self.assertEqual(result.to_dict()["measurement_source"], "LIVE")

    def test_bounds_validation(self):
        with self.assertRaises(SchemaValidationError):
            run_load_test(lambda i: None, total_requests=0, concurrency=1)
        with self.assertRaises(SchemaValidationError):
            run_load_test(lambda i: None, total_requests=1, concurrency=0)
        with self.assertRaises(SchemaValidationError):
            run_load_test(lambda i: None, total_requests=1, concurrency=MAX_CONCURRENCY + 1)


if __name__ == "__main__":
    unittest.main()
