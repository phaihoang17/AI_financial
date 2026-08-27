import unittest
from collections import OrderedDict

from src.evaluation.e2e_fixtures import e2e_fixture_cases
from src.evaluation.latency import measure_e2e_latency, profile_stages
from src.evaluation.telemetry import MeasurementSource
from src.understanding.schemas import SchemaValidationError


class ProfileStagesTests(unittest.TestCase):
    def test_times_each_stage_and_total(self):
        calls = {"a": 0, "b": 0}

        def make(name):
            def _run():
                calls[name] += 1
            return _run

        stages = OrderedDict((("a", make("a")), ("b", make("b"))))
        profile = profile_stages(stages, repeats=3)
        self.assertEqual(calls, {"a": 3, "b": 3})
        self.assertEqual(profile.e2e.count, 3)
        self.assertEqual(set(profile.stages), {"a", "b"})
        for dist in profile.stages.values():
            self.assertEqual(dist.count, 3)
            self.assertLessEqual(dist.p50_ms, dist.p99_ms)

    def test_validation(self):
        with self.assertRaises(SchemaValidationError):
            profile_stages(OrderedDict(), repeats=1)
        with self.assertRaises(SchemaValidationError):
            profile_stages(OrderedDict((("a", lambda: None),)), repeats=0)


class MeasureE2ELatencyTests(unittest.TestCase):
    def test_real_cpu_fixture_measurement(self):
        cases = e2e_fixture_cases()[:2]
        report = measure_e2e_latency(cases, repeats=2)
        payload = report.to_dict()
        self.assertEqual(report.measurement_source, MeasurementSource.CPU_FIXTURE)
        self.assertEqual(report.e2e.count, 4)  # 2 cases x 2 repeats
        self.assertEqual(payload["measurement_source"], "CPU_FIXTURE")
        self.assertEqual(payload["stage_latency_status"], "LIVE_PENDING")
        self.assertEqual(payload["stage_latency_blocker"], "GPU_PRODUCTION_VALIDATION_PENDING")
        self.assertGreaterEqual(payload["e2e"]["p99_ms"], payload["e2e"]["p50_ms"])


if __name__ == "__main__":
    unittest.main()
