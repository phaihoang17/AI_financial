"""TASK-032 dense-only BGE-M3 query embeddings compatible with M2B."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math
import struct
from typing import Any, List, Optional, Protocol, Sequence

from src.indexing.embedding_artifact_builder import APPROVED_EMBEDDING_FINGERPRINT
from src.indexing.embedding_indexer import (
    EmbeddingError,
    EMBEDDING_ALGORITHM_VERSION,
    _l2_normalize,
    load_bge_m3_encoder,
    make_embedding_config_fingerprint,
)
from src.indexing.embedding_schemas import (
    EMBEDDING_DIMENSION,
    EMBEDDING_DTYPE,
    EMBEDDING_HARD_MAX_TOKENS,
)
from src.retrieval.schemas import RetrievalContractError, RetrievalQuery


QUERY_EMBEDDING_SCHEMA_VERSION = "m3-query-embedding-v1"


class QueryEmbeddingError(RetrievalContractError):
    """A typed invalid-input, model, or vector failure for TASK-032."""


class QueryEncoder(Protocol):
    def count_tokens(self, texts: Sequence[str], *, max_length: int) -> Sequence[int]: ...

    def encode(self, texts: Sequence[str], *, batch_size: int, max_length: int) -> Any: ...


def make_retrieval_query_id(query: RetrievalQuery) -> str:
    """Derive a stable identity from the full approved retrieval request."""

    if not isinstance(query, RetrievalQuery):
        raise TypeError("query must be a RetrievalQuery")
    payload = {
        "schema_version": QUERY_EMBEDDING_SCHEMA_VERSION,
        "retrieval_query": query.to_dict(),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(raw).hexdigest()


def make_query_embedding_id(
    *, query_id: str, query_text: str, query_text_index: int, embedding_fingerprint: str
) -> str:
    payload = {
        "schema_version": QUERY_EMBEDDING_SCHEMA_VERSION,
        "query_id": query_id,
        "query_text": query_text,
        "query_text_index": query_text_index,
        "embedding_fingerprint": embedding_fingerprint,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(raw).hexdigest()


def _require_sha256(value: str, path: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise QueryEmbeddingError("INVALID_FINGERPRINT", f"{path} must be SHA-256")
    try:
        int(value, 16)
    except ValueError as error:
        raise QueryEmbeddingError("INVALID_FINGERPRINT", f"{path} must be SHA-256") from error
    if value.lower() != value:
        raise QueryEmbeddingError("INVALID_FINGERPRINT", f"{path} must be SHA-256")
    return value


def _to_float32(values: Sequence[float]) -> List[float]:
    try:
        return [struct.unpack("!f", struct.pack("!f", float(value)))[0] for value in values]
    except (TypeError, ValueError, OverflowError, struct.error) as error:
        raise QueryEmbeddingError("INVALID_VECTOR", "query vector cannot be represented as float32") from error


@dataclass
class QueryEmbedding:
    query_embedding_id: str
    query_id: str
    query_text: str
    query_text_index: int
    vector: List[float]
    dimension: int
    dtype: str
    embedding_fingerprint: str

    def __post_init__(self) -> None:
        self.query_id = _require_sha256(self.query_id, "query_id")
        if not isinstance(self.query_text, str):
            raise QueryEmbeddingError("INVALID_QUERY_TEXT", "query_text must be a string")
        if (
            isinstance(self.query_text_index, bool)
            or not isinstance(self.query_text_index, int)
            or self.query_text_index < 0
        ):
            raise QueryEmbeddingError("INVALID_QUERY_TEXT_INDEX", "query_text_index must be non-negative")
        self.embedding_fingerprint = _require_sha256(
            self.embedding_fingerprint, "embedding_fingerprint"
        )
        if self.embedding_fingerprint != APPROVED_EMBEDDING_FINGERPRINT:
            raise QueryEmbeddingError(
                "EMBEDDING_FINGERPRINT_MISMATCH", "query embedding is not compatible with M2B"
            )
        if self.dimension != EMBEDDING_DIMENSION:
            raise QueryEmbeddingError(
                "EMBEDDING_DIMENSION_MISMATCH", f"expected {EMBEDDING_DIMENSION} dimensions"
            )
        if self.dtype != EMBEDDING_DTYPE:
            raise QueryEmbeddingError("EMBEDDING_DTYPE_MISMATCH", "query vectors must be float32")
        if not isinstance(self.vector, list) or len(self.vector) != self.dimension:
            raise QueryEmbeddingError("EMBEDDING_DIMENSION_MISMATCH", "vector length is incompatible")
        try:
            self.vector = _to_float32(self.vector)
        except QueryEmbeddingError:
            raise
        if not all(math.isfinite(value) for value in self.vector):
            raise QueryEmbeddingError("INVALID_VECTOR", "query vector must be finite")
        norm = math.sqrt(sum(value * value for value in self.vector))
        if not math.isclose(norm, 1.0, rel_tol=1e-5, abs_tol=1e-5):
            raise QueryEmbeddingError("QUERY_VECTOR_NOT_NORMALIZED", "query vector must be L2 normalized")
        expected_id = make_query_embedding_id(
            query_id=self.query_id,
            query_text=self.query_text,
            query_text_index=self.query_text_index,
            embedding_fingerprint=self.embedding_fingerprint,
        )
        if self.query_embedding_id != expected_id:
            raise QueryEmbeddingError(
                "QUERY_EMBEDDING_ID_MISMATCH", "query_embedding_id is not deterministic"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_embedding_id": self.query_embedding_id,
            "query_id": self.query_id,
            "query_text": self.query_text,
            "query_text_index": self.query_text_index,
            "vector": list(self.vector),
            "dimension": self.dimension,
            "dtype": self.dtype,
            "embedding_fingerprint": self.embedding_fingerprint,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "QueryEmbedding":
        if not isinstance(value, dict):
            raise QueryEmbeddingError("INVALID_TYPE", "QueryEmbedding must be an object")
        expected = {
            "query_embedding_id", "query_id", "query_text", "query_text_index", "vector",
            "dimension", "dtype", "embedding_fingerprint",
        }
        if set(value) != expected:
            raise QueryEmbeddingError("INVALID_FIELDS", "QueryEmbedding fields are incompatible")
        return cls(**value)


def _validate_model_fingerprint(embedding_fingerprint: Optional[str]) -> str:
    current = make_embedding_config_fingerprint(
        algorithm_version=EMBEDDING_ALGORITHM_VERSION
    )
    if current != APPROVED_EMBEDDING_FINGERPRINT:
        raise QueryEmbeddingError(
            "EMBEDDING_FINGERPRINT_MISMATCH", "local BGE-M3 configuration is not approved"
        )
    requested = APPROVED_EMBEDDING_FINGERPRINT if embedding_fingerprint is None else embedding_fingerprint
    _require_sha256(requested, "embedding_fingerprint")
    if requested != APPROVED_EMBEDDING_FINGERPRINT:
        raise QueryEmbeddingError(
            "EMBEDDING_FINGERPRINT_MISMATCH", "requested fingerprint is not compatible with M2B"
        )
    return requested


def embed_query_texts(
    query: RetrievalQuery,
    *,
    encoder: Optional[QueryEncoder] = None,
    batch_size: int = 8,
    device: Optional[str] = None,
    embedding_fingerprint: Optional[str] = None,
) -> List[QueryEmbedding]:
    """Embed every query text in source order with no retrieval side effects."""

    if not isinstance(query, RetrievalQuery):
        raise TypeError("query must be a RetrievalQuery")
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
        raise QueryEmbeddingError("INVALID_BATCH_SIZE", "batch_size must be positive")
    fingerprint = _validate_model_fingerprint(embedding_fingerprint)
    if encoder is None:
        encoder = load_bge_m3_encoder(device=device)
    if not hasattr(encoder, "count_tokens") or not hasattr(encoder, "encode"):
        raise QueryEmbeddingError(
            "QUERY_ENCODER_INVALID", "encoder must count tokens and encode query texts"
        )
    texts = list(query.query_texts)
    try:
        token_counts = encoder.count_tokens(texts, max_length=EMBEDDING_HARD_MAX_TOKENS)
    except EmbeddingError as error:
        raise QueryEmbeddingError(error.code, str(error)) from error
    except Exception as error:
        raise QueryEmbeddingError("QUERY_TOKENIZATION_FAILED", str(error)) from error
    if len(token_counts) != len(texts):
        raise QueryEmbeddingError("QUERY_TOKENIZATION_FAILED", "encoder returned the wrong token count")
    for index, count in enumerate(token_counts):
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise QueryEmbeddingError("QUERY_TOKENIZATION_FAILED", "token counts must be non-negative integers")
        if count > EMBEDDING_HARD_MAX_TOKENS:
            raise QueryEmbeddingError(
                "EMBEDDING_INPUT_TOO_LONG", f"query_texts[{index}] exceeds {EMBEDDING_HARD_MAX_TOKENS} tokens"
            )
    try:
        raw_vectors = encoder.encode(texts, batch_size=batch_size, max_length=EMBEDDING_HARD_MAX_TOKENS)
    except EmbeddingError as error:
        raise QueryEmbeddingError(error.code, str(error)) from error
    except Exception as error:
        raise QueryEmbeddingError("QUERY_EMBEDDING_FAILED", str(error)) from error
    if not hasattr(raw_vectors, "__len__") or len(raw_vectors) != len(texts):
        raise QueryEmbeddingError("QUERY_EMBEDDING_FAILED", "encoder returned the wrong vector count")
    query_id = make_retrieval_query_id(query)
    embeddings: List[QueryEmbedding] = []
    for index, (text, raw_vector) in enumerate(zip(texts, raw_vectors)):
        try:
            normalized = _l2_normalize(raw_vector)
        except (EmbeddingError, TypeError, ValueError) as error:
            raise QueryEmbeddingError("INVALID_VECTOR", f"query_texts[{index}] is not a valid vector") from error
        if len(normalized) != EMBEDDING_DIMENSION:
            raise QueryEmbeddingError(
                "EMBEDDING_DIMENSION_MISMATCH", f"query_texts[{index}] has wrong dimension"
            )
        vector = _to_float32(normalized)
        embeddings.append(
            QueryEmbedding(
                query_embedding_id=make_query_embedding_id(
                    query_id=query_id,
                    query_text=text,
                    query_text_index=index,
                    embedding_fingerprint=fingerprint,
                ),
                query_id=query_id,
                query_text=text,
                query_text_index=index,
                vector=vector,
                dimension=EMBEDDING_DIMENSION,
                dtype=EMBEDDING_DTYPE,
                embedding_fingerprint=fingerprint,
            )
        )
    return embeddings
