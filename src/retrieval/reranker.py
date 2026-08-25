"""TASK-035 pinned BGE reranking with no silent pair truncation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import json
from contextlib import nullcontext
from typing import Any, List, Mapping, Optional, Protocol, Sequence

from src.retrieval.schemas import RetrievalCandidate, RetrievalContractError, RetrievalQuery


RERANKER_SCHEMA_VERSION = "m3-bge-reranker-v1"
BGE_RERANKER_MODEL_ID = "BAAI/bge-reranker-v2-m3"
BGE_RERANKER_REVISION = "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"
BGE_RERANKER_TOKENIZER_CLASS = "XLMRobertaTokenizerFast"
RERANKER_MAX_PAIR_TOKENS = 8192


class RerankerError(RetrievalContractError):
    """A typed reranker configuration, input, or model failure."""


def make_reranker_config_fingerprint() -> str:
    """Fingerprint every behavior-affecting pinned reranker parameter."""

    payload = {
        "schema_version": RERANKER_SCHEMA_VERSION,
        "model_id": BGE_RERANKER_MODEL_ID,
        "revision": BGE_RERANKER_REVISION,
        "tokenizer_class": BGE_RERANKER_TOKENIZER_CLASS,
        "max_pair_tokens": RERANKER_MAX_PAIR_TOKENS,
        "add_special_tokens": True,
        "truncation": False,
        "score": "logits[:,0]",
    }
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


RERANKER_CONFIG_FINGERPRINT = make_reranker_config_fingerprint()


class PairTokenizer(Protocol):
    def __call__(self, *args: Any, **kwargs: Any) -> Any: ...


class SequenceClassifier(Protocol):
    def __call__(self, **kwargs: Any) -> Any: ...


def _input_ids_length(encoded: Any) -> int:
    if not isinstance(encoded, Mapping):
        raise RerankerError("RERANKER_TOKENIZATION_FAILED", "tokenizer must return a mapping")
    input_ids = encoded.get("input_ids")
    if input_ids is None:
        raise RerankerError("RERANKER_TOKENIZATION_FAILED", "tokenizer did not return input_ids")
    if hasattr(input_ids, "tolist"):
        input_ids = input_ids.tolist()
    if not isinstance(input_ids, list):
        raise RerankerError("RERANKER_TOKENIZATION_FAILED", "input_ids must be a list")
    if input_ids and isinstance(input_ids[0], list):
        if len(input_ids) != 1:
            raise RerankerError("RERANKER_TOKENIZATION_FAILED", "single pair count is invalid")
        input_ids = input_ids[0]
    if not all(isinstance(token, int) and not isinstance(token, bool) for token in input_ids):
        raise RerankerError("RERANKER_TOKENIZATION_FAILED", "input_ids must be integers")
    return len(input_ids)


@dataclass
class BGEReranker:
    """Small explicit adapter around the exact pinned cross-encoder."""

    tokenizer: PairTokenizer
    model: SequenceClassifier
    device: str = "cpu"
    config_fingerprint: str = RERANKER_CONFIG_FINGERPRINT
    torch_module: Optional[Any] = None

    def __post_init__(self) -> None:
        if not callable(self.tokenizer) or not callable(self.model):
            raise RerankerError("RERANKER_ADAPTER_INVALID", "tokenizer and model must be callable")
        if not isinstance(self.device, str) or not self.device:
            raise RerankerError("RERANKER_DEVICE_INVALID", "device must be non-empty")
        if self.config_fingerprint != RERANKER_CONFIG_FINGERPRINT:
            raise RerankerError("RERANKER_CONFIG_MISMATCH", "reranker configuration is not pinned")

    def count_pair_tokens(self, query: str, document: str) -> int:
        try:
            encoded = self.tokenizer(
                query,
                document,
                add_special_tokens=True,
                truncation=False,
                return_attention_mask=False,
            )
        except Exception as error:
            raise RerankerError("RERANKER_TOKENIZATION_FAILED", str(error)) from error
        return _input_ids_length(encoded)

    def score_pairs(self, query: str, documents: Sequence[str], *, batch_size: int) -> List[float]:
        if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
            raise RerankerError("RERANKER_BATCH_SIZE_INVALID", "batch_size must be positive")
        if not isinstance(query, str) or any(not isinstance(document, str) for document in documents):
            raise RerankerError("RERANKER_INPUT_INVALID", "query and documents must be strings")
        for index, document in enumerate(documents):
            count = self.count_pair_tokens(query, document)
            if count > RERANKER_MAX_PAIR_TOKENS:
                raise RerankerError(
                    "RERANKER_INPUT_TOO_LONG",
                    f"candidate[{index}] has {count} tokens; max is {RERANKER_MAX_PAIR_TOKENS}",
                )
        scores: List[float] = []
        if self.torch_module is None:
            try:
                import torch
            except ImportError as error:  # pragma: no cover - deployment dependent
                raise RerankerError("RERANKER_DEPENDENCY_MISSING", "torch is required") from error
        else:
            torch = self.torch_module
        for start in range(0, len(documents), batch_size):
            batch = list(documents[start:start + batch_size])
            try:
                encoded = self.tokenizer(
                    [query] * len(batch),
                    batch,
                    add_special_tokens=True,
                    truncation=False,
                    padding=True,
                    return_tensors="pt",
                    return_attention_mask=True,
                )
                input_ids = encoded.get("input_ids")
                if input_ids is None or getattr(input_ids, "shape", (0, 0))[1] > RERANKER_MAX_PAIR_TOKENS:
                    raise RerankerError("RERANKER_INPUT_TOO_LONG", "batched pair exceeds max tokens")
                encoded = {
                    key: value.to(self.device) if hasattr(value, "to") else value
                    for key, value in encoded.items()
                }
                no_grad = torch.no_grad() if hasattr(torch, "no_grad") else nullcontext()
                with no_grad:
                    logits = self.model(**encoded).logits
                raw = logits[:, 0].detach().cpu().float().tolist()
            except RerankerError:
                raise
            except Exception as error:
                raise RerankerError("RERANKER_INFERENCE_FAILED", str(error)) from error
            if len(raw) != len(batch):
                raise RerankerError("RERANKER_INFERENCE_FAILED", "model returned wrong score count")
            scores.extend(float(score) for score in raw)
        return scores


def load_bge_reranker(
    *, device: Optional[str] = None, cache_dir: Optional[str] = None
) -> BGEReranker:
    """Load only the pinned BGE reranker revision and expected fast tokenizer."""

    try:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
    except ImportError as error:  # pragma: no cover - deployment dependent
        raise RerankerError(
            "RERANKER_DEPENDENCY_MISSING", "torch and transformers are required"
        ) from error
    resolved_device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    options: dict[str, Any] = {
        "revision": BGE_RERANKER_REVISION,
        "trust_remote_code": True,
    }
    if cache_dir is not None:
        options["cache_dir"] = cache_dir
    try:
        tokenizer = AutoTokenizer.from_pretrained(BGE_RERANKER_MODEL_ID, **options)
        if type(tokenizer).__name__ != BGE_RERANKER_TOKENIZER_CLASS:
            raise RerankerError("RERANKER_TOKENIZER_MISMATCH", type(tokenizer).__name__)
        model = AutoModelForSequenceClassification.from_pretrained(
            BGE_RERANKER_MODEL_ID, **options
        )
        model.eval()
        model.to(resolved_device)
    except RerankerError:
        raise
    except Exception as error:  # pragma: no cover - network/cache dependent
        raise RerankerError("RERANKER_MODEL_LOAD_FAILED", str(error)) from error
    return BGEReranker(tokenizer=tokenizer, model=model, device=resolved_device)


def rerank_candidates(
    query: RetrievalQuery,
    candidates: Sequence[RetrievalCandidate],
    *,
    reranker: Optional[BGEReranker] = None,
    batch_size: int = 8,
    top_k: Optional[int] = None,
) -> List[RetrievalCandidate]:
    """Score RRF candidates with raw logits and deterministic tie-breaking."""

    if not isinstance(query, RetrievalQuery):
        raise TypeError("query must be a RetrievalQuery")
    if not isinstance(candidates, (list, tuple)) or not all(
        isinstance(candidate, RetrievalCandidate) for candidate in candidates
    ):
        raise RerankerError("RERANKER_INPUT_INVALID", "candidates must be RetrievalCandidate values")
    if top_k is not None and (
        isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1
    ):
        raise RerankerError("INVALID_TOP_K", "top_k must be a positive integer")
    if not candidates:
        return []
    if reranker is None:
        reranker = load_bge_reranker()
    if not isinstance(reranker, BGEReranker):
        raise RerankerError("RERANKER_ADAPTER_INVALID", "reranker must be BGEReranker")
    if any(candidate.rrf_score is None for candidate in candidates):
        raise RerankerError("RERANKER_RRF_SCORE_MISSING", "TASK-034 rrf_score is required")
    scores = reranker.score_pairs(
        query.raw_question, [candidate.content for candidate in candidates], batch_size=batch_size
    )
    if len(scores) != len(candidates):
        raise RerankerError("RERANKER_INFERENCE_FAILED", "model returned wrong score count")
    ranked = sorted(
        [
            (candidate, float(score))
            for candidate, score in zip(candidates, scores)
        ],
        key=lambda item: (-item[1], -float(item[0].rrf_score or 0.0), item[0].candidate_id),
    )
    result = [
        replace(candidate, rerank_score=score, rank=index)
        for index, (candidate, score) in enumerate(ranked, start=1)
    ]
    return result if top_k is None else result[:top_k]
