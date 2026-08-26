import unittest

from src.serving.schemas import ServingError, ServingRole, ServingTask
from src.serving.topology import (
    SHARED_QWEN3_ROLES,
    build_topology,
    topology_from_env,
)
from src.supervisor.schemas import ModelTier


def topology():
    return build_topology(
        shared_llm_base_url="http://localhost:8001",
        strong_llm_base_url="http://localhost:8002",
        embedding_base_url="http://localhost:8003",
        reranker_base_url="http://localhost:8004",
    )


class ServingTopologyTests(unittest.TestCase):
    def test_nlu_supervisor_verifier_share_one_qwen3_endpoint(self):
        top = topology()
        shared = {
            top.endpoint_for_role(ServingRole.NLU),
            top.endpoint_for_role(ServingRole.SUPERVISOR),
            top.endpoint_for_role(ServingRole.VERIFIER),
        }
        self.assertEqual(len(shared), 1)
        self.assertEqual(next(iter(shared)), top.shared_llm)
        self.assertEqual(SHARED_QWEN3_ROLES, {ServingRole.NLU, ServingRole.SUPERVISOR, ServingRole.VERIFIER})

    def test_programmer_uses_separate_strong_endpoint(self):
        top = topology()
        self.assertEqual(top.endpoint_for_role(ServingRole.PROGRAMMER), top.strong_llm)
        self.assertNotEqual(top.strong_llm, top.shared_llm)

    def test_tier_routing_is_preserved_not_re_derived(self):
        top = topology()
        self.assertEqual(top.endpoint_for_tier(ModelTier.CHEAP), top.shared_llm)
        self.assertEqual(top.endpoint_for_tier(ModelTier.STRONG), top.strong_llm)

    def test_cheap_role_and_tier_resolve_to_same_shared_process(self):
        top = topology()
        self.assertEqual(
            top.endpoint_for_tier(ModelTier.CHEAP),
            top.endpoint_for_role(ServingRole.NLU),
        )

    def test_endpoint_tasks_are_fixed(self):
        top = topology()
        self.assertEqual(top.shared_llm.task, ServingTask.GENERATE)
        self.assertEqual(top.strong_llm.task, ServingTask.GENERATE)
        self.assertEqual(top.embedding.task, ServingTask.EMBED)
        self.assertEqual(top.reranker.task, ServingTask.SCORE)

    def test_shared_base_url_breaks_failure_isolation(self):
        with self.assertRaises(ServingError) as ctx:
            build_topology(
                shared_llm_base_url="http://localhost:8001",
                strong_llm_base_url="http://localhost:8001",
                embedding_base_url="http://localhost:8003",
                reranker_base_url="http://localhost:8004",
            )
        self.assertEqual(ctx.exception.code, "INVALID_TOPOLOGY")

    def test_from_env_requires_all_base_urls(self):
        env = {
            "SERVING_SHARED_LLM_URL": "http://localhost:8001",
            "SERVING_STRONG_LLM_URL": "http://localhost:8002",
            "SERVING_EMBEDDING_URL": "http://localhost:8003",
            "SERVING_RERANKER_URL": "http://localhost:8004",
        }
        self.assertEqual(topology_from_env(env).to_dict(), topology().to_dict())
        del env["SERVING_RERANKER_URL"]
        with self.assertRaises(ServingError) as ctx:
            topology_from_env(env)
        self.assertEqual(ctx.exception.code, "MISSING_CONFIG")


if __name__ == "__main__":
    unittest.main()
