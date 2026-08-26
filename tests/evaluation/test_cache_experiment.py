import unittest

from src.evaluation.cache_experiment import run_cache_experiment


class CacheExperimentTests(unittest.TestCase):
    def test_experiment_compares_policies_and_preserves_correctness(self):
        report = run_cache_experiment()
        payload = report.to_dict()
        policies = {item["policy"] for item in payload["policy_results"]}
        self.assertEqual(policies, {"NONE", "QUERY", "FIELD", "ADAPTIVE"})
        # No policy may change a verified answer.
        self.assertTrue(report.correctness_preserved)
        for item in payload["policy_results"]:
            self.assertEqual(item["correctness_regressions"], 0)
        self.assertEqual(payload["production_measurement_blocker"], "GPU_PRODUCTION_VALIDATION_PENDING")

    def test_no_cache_policy_never_hits_but_query_policy_does(self):
        report = run_cache_experiment()
        by_policy = {item.policy: item for item in report.policy_results}
        self.assertEqual(by_policy["NONE"].warm_hits, 0)
        self.assertEqual(by_policy["NONE"].stored, 0)
        # PASS fixtures are cacheable, so an exact-key policy hits every one on the warm pass.
        self.assertGreater(by_policy["QUERY"].cacheable_cases, 0)
        self.assertEqual(by_policy["QUERY"].warm_hits, by_policy["QUERY"].cacheable_cases)
        self.assertEqual(by_policy["QUERY"].hit_rate, 1.0)


if __name__ == "__main__":
    unittest.main()
