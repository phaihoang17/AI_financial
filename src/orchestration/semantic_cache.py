"""TASK-105 optional semantic answer cache.

ADR-024 makes caching an optimization, never a correctness mechanism. This cache
therefore obeys four hard rules:

1. **Optional** — disabled by default; a disabled cache never stores or hits.
2. **Verified-only** — only a PASS ``FinalResponse`` (already verified) may be
   stored, so the cache can never hold or return an unverified answer and never
   bypasses verification.
3. **Versioned + invalidatable** — every entry is bound to a ``cache_version`` and
   a caller-supplied invalidation fingerprint (e.g. the Plan / retrieval-policy /
   artifact fingerprint). A version bump or fingerprint mismatch is a miss.
4. **Deterministic keys** — exact ``QUERY`` (normalized question) or structural
   ``FIELD`` (semantic understanding) signatures. ``ADAPTIVE`` caches only stable,
   standardized inputs; unstable inputs decline the cache (ADR-024).

Real embedding-similarity ("semantic") fuzzy matching and its hit-rate/quality
measurement require the retrieval models and are ``GPU_PRODUCTION_VALIDATION_PENDING``;
this module implements and tests the deterministic cache structure only.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
from typing import Any, Dict, Optional

from src.orchestration.schemas import FinalAnswer, FinalResponse, FinalResponseStatus
from src.understanding.schemas import QueryUnderstanding, SchemaValidationError


SEMANTIC_CACHE_VERSION = "m10-semantic-cache-v1"
_VOLATILE_KEYS = frozenset({"raw_question", "confidence"})


class SemanticCacheError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class CacheKeyPolicy(str, Enum):
    NONE = "NONE"
    QUERY = "QUERY"
    FIELD = "FIELD"
    ADAPTIVE = "ADAPTIVE"


@dataclass(frozen=True)
class CachedEntry:
    signature: str
    invalidation_fingerprint: str
    answer: FinalAnswer


def _strip_volatile(value: Any) -> Any:
    """Drop raw phrasing and confidence so FIELD keys are purely structural."""
    if isinstance(value, dict):
        return {k: _strip_volatile(v) for k, v in value.items() if k not in _VOLATILE_KEYS}
    if isinstance(value, list):
        return [_strip_volatile(item) for item in value]
    return value


def _normalize_question(raw_question: str) -> str:
    return " ".join(raw_question.split()).casefold()


class SemanticCache:
    """Process-local verified-answer cache. Not shared across viewers/processes."""

    def __init__(
        self,
        policy: CacheKeyPolicy = CacheKeyPolicy.NONE,
        *,
        enabled: bool = False,
        cache_version: str = SEMANTIC_CACHE_VERSION,
        stability_min_confidence: float = 1.0,
    ) -> None:
        if not isinstance(policy, CacheKeyPolicy):
            raise SemanticCacheError("INVALID_POLICY", "policy must be a CacheKeyPolicy")
        self.policy = policy
        self.enabled = bool(enabled) and policy is not CacheKeyPolicy.NONE
        self.cache_version = cache_version
        self.stability_min_confidence = float(stability_min_confidence)
        self._store: Dict[str, CachedEntry] = {}
        self.puts = 0
        self.hits = 0
        self.misses = 0

    # -- key derivation -----------------------------------------------------

    def _is_stable(self, understanding: QueryUnderstanding) -> bool:
        return understanding.confidence >= self.stability_min_confidence

    def _signature(self, understanding: QueryUnderstanding) -> Optional[str]:
        if not self.enabled:
            return None
        if self.policy is CacheKeyPolicy.QUERY:
            body: Any = {"query": _normalize_question(understanding.raw_question)}
        elif self.policy is CacheKeyPolicy.FIELD:
            body = {"fields": _strip_volatile(understanding.to_dict())}
        elif self.policy is CacheKeyPolicy.ADAPTIVE:
            if not self._is_stable(understanding):
                return None
            body = {"fields": _strip_volatile(understanding.to_dict())}
        else:  # NONE
            return None
        raw = json.dumps(
            {"cache_version": self.cache_version, "policy": self.policy.value, **body},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return sha256(raw).hexdigest()

    # -- public API ---------------------------------------------------------

    def put(
        self,
        understanding: QueryUnderstanding,
        invalidation_fingerprint: str,
        final_response: FinalResponse,
    ) -> bool:
        """Store a verified answer. Returns whether it was cached."""
        if not isinstance(understanding, QueryUnderstanding):
            raise SchemaValidationError("understanding must be a QueryUnderstanding")
        if not isinstance(final_response, FinalResponse):
            raise SchemaValidationError("final_response must be a FinalResponse")
        if not isinstance(invalidation_fingerprint, str) or not invalidation_fingerprint:
            raise SemanticCacheError("INVALID_FINGERPRINT", "invalidation fingerprint required")
        if final_response.status is not FinalResponseStatus.PASS or final_response.answer is None:
            raise SemanticCacheError(
                "UNVERIFIED_ANSWER_NOT_CACHEABLE",
                "only a verified PASS response may be cached",
            )
        signature = self._signature(understanding)
        if signature is None:
            return False
        self._store[signature] = CachedEntry(
            signature=signature,
            invalidation_fingerprint=invalidation_fingerprint,
            answer=final_response.answer,
        )
        self.puts += 1
        return True

    def get(
        self, understanding: QueryUnderstanding, invalidation_fingerprint: str
    ) -> Optional[FinalAnswer]:
        """Return a previously-verified answer for an identical, current input."""
        if not isinstance(understanding, QueryUnderstanding):
            raise SchemaValidationError("understanding must be a QueryUnderstanding")
        if not isinstance(invalidation_fingerprint, str) or not invalidation_fingerprint:
            raise SemanticCacheError("INVALID_FINGERPRINT", "invalidation fingerprint required")
        signature = self._signature(understanding)
        if signature is None:
            self.misses += 1
            return None
        entry = self._store.get(signature)
        if entry is None:
            self.misses += 1
            return None
        if entry.invalidation_fingerprint != invalidation_fingerprint:
            # Stale under a new fingerprint/version: evict and miss.
            del self._store[signature]
            self.misses += 1
            return None
        self.hits += 1
        return entry.answer

    def invalidate_all(self) -> None:
        self._store.clear()

    def stats(self) -> Dict[str, Any]:
        lookups = self.hits + self.misses
        return {
            "policy": self.policy.value,
            "enabled": self.enabled,
            "cache_version": self.cache_version,
            "entries": len(self._store),
            "puts": self.puts,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": None if lookups == 0 else self.hits / lookups,
        }
