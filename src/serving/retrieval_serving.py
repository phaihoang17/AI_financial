"""TASK-109 retrieval model serving adapters.

Two adapters bridge the ADR-049 serving client to the *existing* retrieval
contracts without duplicating their logic:

- :class:`ServingQueryEncoder` implements the ``QueryEncoder`` protocol consumed
  by :func:`src.retrieval.query_embedding.embed_query_texts`. Normalization,
  dimension/dtype checks, and the M2B fingerprint gate stay in that function, so
  a served vector is accepted only if it is index-compatible.
- :class:`ServingBGEReranker` subclasses the pinned ``BGEReranker`` and only
  replaces inference with a remote ``/score`` call. The deterministic ranking,
  tie-breaking, and ``top_k`` logic stay in
  :func:`src.retrieval.reranker.rerank_candidates`, which the subclass reuses
  unchanged.
"""

from __future__ import annotations

from typing import Any, List, Sequence

from src.retrieval.reranker import (
    BGEReranker,
    RERANKER_MAX_PAIR_TOKENS,
    RerankerError,
)
from src.serving.client import OpenAICompatibleClient
from src.serving.schemas import ServingError, ServingTask


class ServingQueryEncoder:
    """QueryEncoder backed by a BGE-M3 EMBED endpoint.

    Returns *raw* vectors and *full* token counts; ``embed_query_texts`` performs
    L2 normalization, float32/dimension validation, and the fingerprint gate.
    """

    def __init__(self, client: OpenAICompatibleClient) -> None:
        if not isinstance(client, OpenAICompatibleClient):
            raise ServingError("SERVING_ADAPTER_INVALID", "client must be an OpenAICompatibleClient")
        if client.endpoint.task is not ServingTask.EMBED:
            raise ServingError("SERVING_TASK_MISMATCH", "encoder needs an EMBED endpoint")
        self.client = client

    def count_tokens(self, texts: Sequence[str], *, max_length: int) -> List[int]:
        return [self.client.count_tokens(text) for text in texts]

    def encode(self, texts: Sequence[str], *, batch_size: int, max_length: int) -> List[List[float]]:
        if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
            raise ServingError("SERVING_INPUT_INVALID", "batch_size must be positive")
        items = list(texts)
        vectors: List[List[float]] = []
        for start in range(0, len(items), batch_size):
            vectors.extend(self.client.embed(items[start:start + batch_size]))
        return vectors


def _remote_only(*_args: Any, **_kwargs: Any) -> Any:
    """Placeholder for the parent dataclass callables; inference is remote."""

    raise ServingError("SERVING_ADAPTER_INVALID", "served reranker performs no local inference")


class ServingBGEReranker(BGEReranker):
    """Pinned cross-encoder served over an HTTP ``/score`` endpoint.

    Only inference and pair-token counting are remote; every other behavior
    (config fingerprint, max-pair-token guard, and downstream ranking through
    ``rerank_candidates``) is inherited from the pinned contract.
    """

    def __init__(self, client: OpenAICompatibleClient) -> None:
        if not isinstance(client, OpenAICompatibleClient):
            raise ServingError("SERVING_ADAPTER_INVALID", "client must be an OpenAICompatibleClient")
        if client.endpoint.task is not ServingTask.SCORE:
            raise ServingError("SERVING_TASK_MISMATCH", "reranker needs a SCORE endpoint")
        self._client = client
        super().__init__(tokenizer=_remote_only, model=_remote_only, device="serving")

    def count_pair_tokens(self, query: str, document: str) -> int:
        return self._client.count_pair_tokens(query, document)

    def score_pairs(self, query: str, documents: Sequence[str], *, batch_size: int) -> List[float]:
        if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
            raise RerankerError("RERANKER_BATCH_SIZE_INVALID", "batch_size must be positive")
        if not isinstance(query, str) or any(not isinstance(document, str) for document in documents):
            raise RerankerError("RERANKER_INPUT_INVALID", "query and documents must be strings")
        docs = list(documents)
        for index, document in enumerate(docs):
            count = self.count_pair_tokens(query, document)
            if count > RERANKER_MAX_PAIR_TOKENS:
                raise RerankerError(
                    "RERANKER_INPUT_TOO_LONG",
                    f"candidate[{index}] has {count} tokens; max is {RERANKER_MAX_PAIR_TOKENS}",
                )
        scores: List[float] = []
        for start in range(0, len(docs), batch_size):
            batch = docs[start:start + batch_size]
            try:
                batch_scores = self._client.score(query, batch)
            except ServingError as error:
                raise RerankerError("RERANKER_INFERENCE_FAILED", str(error)) from error
            if len(batch_scores) != len(batch):
                raise RerankerError("RERANKER_INFERENCE_FAILED", "model returned wrong score count")
            scores.extend(float(score) for score in batch_scores)
        return scores


def serving_query_encoder(client: OpenAICompatibleClient) -> ServingQueryEncoder:
    return ServingQueryEncoder(client)


def serving_bge_reranker(client: OpenAICompatibleClient) -> ServingBGEReranker:
    return ServingBGEReranker(client)
