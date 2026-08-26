import unittest

from src.evaluation.telemetry import Distribution, MeasurementSource, Stopwatch, _percentile
from src.understanding.schemas import SchemaValidationError


class DistributionTests(unittest.TestCase):
    def test_summary_over_known_samples(self):
        dist = Distribution.from_samples([10, 1, 5, 3, 9, 2, 8, 4, 7, 6])
        self.assertEqual(dist.count, 10)
        self.assertEqual(dist.min_ms, 1.0)
        self.assertEqual(dist.max_ms, 10.0)
        self.assertEqual(dist.mean_ms, 5.5)
        self.assertEqual(dist.p50_ms, 5.0)  # nearest-rank ceil(0.5*10)=5 -> sorted[4]
        self.assertEqual(dist.p95_ms, 10.0)
        self.assertEqual(dist.p99_ms, 10.0)
        self.assertLessEqual(dist.p50_ms, dist.p95_ms)
        self.assertLessEqual(dist.p95_ms, dist.p99_ms)

    def test_single_sample(self):
        dist = Distribution.from_samples([42.0])
        self.assertEqual((dist.p50_ms, dist.p95_ms, dist.p99_ms), (42.0, 42.0, 42.0))

    def test_rejects_empty_and_negative(self):
        with self.assertRaises(SchemaValidationError):
            Distribution.from_samples([])
        with self.assertRaises(SchemaValidationError):
            Distribution.from_samples([1.0, -2.0])

    def test_percentile_bounds(self):
        with self.assertRaises(SchemaValidationError):
            _percentile([1.0], 0)
        with self.assertRaises(SchemaValidationError):
            _percentile([1.0], 101)


class StopwatchAndSourceTests(unittest.TestCase):
    def test_stopwatch_is_non_negative(self):
        watch = Stopwatch()
        self.assertGreaterEqual(watch.elapsed_ms(), 0.0)

    def test_measurement_sources(self):
        self.assertEqual(MeasurementSource.CPU_FIXTURE.value, "CPU_FIXTURE")
        self.assertEqual(MeasurementSource.LIVE.value, "LIVE")


if __name__ == "__main__":
    unittest.main()
