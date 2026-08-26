import unittest

from src.evaluation.e2e_fixtures import e2e_fixture_cases
from src.orchestration.schemas import FinalResponseStatus
from src.orchestration.semantic_cache import (
    CacheKeyPolicy,
    SemanticCache,
    SemanticCacheError,
)


def _run(case_id):
    case = next(c for c in e2e_fixture_cases() if c.case_id == case_id)
    state = case.graph_factory().run(request_id=f"t-{case_id}", raw_question=case.raw_question).state
    return state.query_understanding, state.final_response


class SemanticCacheComponentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.understanding, cls.response = _run("lookup")
        cls.clar_understanding, cls.clar_response = _run("clarification")
        assert cls.response.status is FinalResponseStatus.PASS
        assert cls.clar_response.status is not FinalResponseStatus.PASS

    def test_disabled_cache_never_stores_or_hits(self):
        cache = SemanticCache(CacheKeyPolicy.QUERY, enabled=False)
        self.assertFalse(cache.put(self.understanding, "fp", self.response))
        self.assertIsNone(cache.get(self.understanding, "fp"))
        self.assertEqual(cache.stats()["entries"], 0)

    def test_none_policy_is_always_disabled(self):
        cache = SemanticCache(CacheKeyPolicy.NONE, enabled=True)
        self.assertFalse(cache.enabled)
        self.assertFalse(cache.put(self.understanding, "fp", self.response))

    def test_query_and_field_hit_returns_verified_answer(self):
        for policy in (CacheKeyPolicy.QUERY, CacheKeyPolicy.FIELD):
            cache = SemanticCache(policy, enabled=True)
            self.assertIsNone(cache.get(self.understanding, "fp"))  # cold miss
            self.assertTrue(cache.put(self.understanding, "fp", self.response))
            hit = cache.get(self.understanding, "fp")
            self.assertIsNotNone(hit)
            self.assertEqual(hit.to_dict(), self.response.answer.to_dict())

    def test_unverified_answer_is_not_cacheable(self):
        cache = SemanticCache(CacheKeyPolicy.QUERY, enabled=True)
        with self.assertRaises(SemanticCacheError) as ctx:
            cache.put(self.clar_understanding, "fp", self.clar_response)
        self.assertEqual(ctx.exception.code, "UNVERIFIED_ANSWER_NOT_CACHEABLE")

    def test_fingerprint_mismatch_invalidates(self):
        cache = SemanticCache(CacheKeyPolicy.FIELD, enabled=True)
        cache.put(self.understanding, "fp-v1", self.response)
        self.assertIsNone(cache.get(self.understanding, "fp-v2"))  # stale -> miss+evict
        self.assertEqual(cache.stats()["entries"], 0)

    def test_version_bump_is_a_miss(self):
        old = SemanticCache(CacheKeyPolicy.FIELD, enabled=True, cache_version="v1")
        old.put(self.understanding, "fp", self.response)
        new = SemanticCache(CacheKeyPolicy.FIELD, enabled=True, cache_version="v2")
        # different version -> different signature namespace -> no cross-version hit
        self.assertIsNone(new.get(self.understanding, "fp"))

    def test_adaptive_declines_unstable_input(self):
        # Require confidence above the maximum so the stable predicate is false.
        cache = SemanticCache(
            CacheKeyPolicy.ADAPTIVE, enabled=True, stability_min_confidence=1.1
        )
        self.assertFalse(cache.put(self.understanding, "fp", self.response))
        self.assertIsNone(cache.get(self.understanding, "fp"))

    def test_adaptive_caches_stable_input(self):
        cache = SemanticCache(
            CacheKeyPolicy.ADAPTIVE, enabled=True, stability_min_confidence=1.0
        )
        self.assertTrue(cache.put(self.understanding, "fp", self.response))
        self.assertIsNotNone(cache.get(self.understanding, "fp"))


if __name__ == "__main__":
    unittest.main()
