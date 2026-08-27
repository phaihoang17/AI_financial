import unittest

from src.evaluation.cost_dashboard import (
    ModelInvocation,
    TokenPrice,
    build_cost_dashboard,
)
from src.serving.schemas import ServingRole
from src.understanding.schemas import SchemaValidationError


def invocation(model="Qwen/Qwen3-8B", role=ServingRole.NLU, prompt=1000, completion=500, requests=1):
    return ModelInvocation(model, role, prompt, completion, requests)


class ModelInvocationTests(unittest.TestCase):
    def test_total_tokens_and_validation(self):
        self.assertEqual(invocation().total_tokens, 1500)
        with self.assertRaises(SchemaValidationError):
            invocation(prompt=-1)
        with self.assertRaises(SchemaValidationError):
            ModelInvocation("m", ServingRole.NLU, 1, 1, 0)


class CostDashboardTests(unittest.TestCase):
    def test_priced_aggregation_math(self):
        dash = build_cost_dashboard(
            [invocation(prompt=1000, completion=500)],
            queries=1,
            pricing={"Qwen/Qwen3-8B": TokenPrice(prompt_per_1k=1.0, completion_per_1k=2.0)},
        )
        payload = dash.to_dict()
        self.assertEqual(payload["token_measurement_status"], "LIVE")
        self.assertEqual(payload["total_tokens"], 1500)
        self.assertEqual(payload["tokens_per_query"], 1500.0)
        self.assertEqual(payload["cost_per_query"], 2.0)  # 1000/1k*1 + 500/1k*2
        self.assertEqual(payload["per_model"]["Qwen/Qwen3-8B"]["cost"], 2.0)

    def test_empty_invocations_is_pending(self):
        dash = build_cost_dashboard([], queries=5, cache_hit_rate=0.5)
        payload = dash.to_dict()
        self.assertEqual(payload["token_measurement_status"], "LIVE_PENDING")
        self.assertEqual(payload["token_cost_live_blocker"], "GPU_PRODUCTION_VALIDATION_PENDING")
        self.assertIsNone(payload["tokens_per_query"])
        self.assertIsNone(payload["cost_per_query"])
        self.assertEqual(payload["cache_hit_rate"], 0.5)

    def test_unpriced_model_has_no_cost(self):
        dash = build_cost_dashboard([invocation()], queries=1)
        payload = dash.to_dict()
        self.assertEqual(payload["token_measurement_status"], "LIVE")
        self.assertIsNone(payload["cost_per_query"])
        self.assertIsNone(payload["per_model"]["Qwen/Qwen3-8B"]["cost"])

    def test_escalation_cost_is_real_priced_subset(self):
        strong = invocation(model="Qwen/Qwen2.5-Coder-14B-Instruct", role=ServingRole.PROGRAMMER, prompt=2000, completion=0)
        dash = build_cost_dashboard(
            [invocation(), strong],
            queries=2,
            pricing={
                "Qwen/Qwen3-8B": TokenPrice(1.0, 2.0),
                "Qwen/Qwen2.5-Coder-14B-Instruct": TokenPrice(3.0, 6.0),
            },
            escalation_requests=1,
            escalation_invocations=[strong],
        )
        self.assertEqual(dash.escalation_requests, 1)
        self.assertEqual(dash.escalation_cost, 6.0)  # 2000/1k*3

    def test_queries_validation(self):
        with self.assertRaises(SchemaValidationError):
            build_cost_dashboard([], queries=0)


if __name__ == "__main__":
    unittest.main()
