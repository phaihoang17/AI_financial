"""Offline BGE-M3 document embedding for M2B EmbeddingChunks."""

from __future__ import annotations

from hashlib import sha256
import json
import math
from typing import Any, Iterable, List, Mapping, Optional, Protocol, Sequence

from src.indexing.embedding_chunker import BGE_M3_MODEL_ID, BGE_M3_REVISION
from src.indexing.embedding_schemas import (
    EMBEDDING_DTYPE,
    EMBEDDING_DIMENSION,
    EMBEDDING_HARD_MAX_TOKENS,
    EMBEDDING_RECORD_SCHEMA_VERSION,
    EmbeddingChunk,
    EmbeddingRecord,
)
from src.understanding.schemas import SchemaValidationError


BGE_POOLING = "attention_mask_mean_last_hidden_state"
EMBEDDING_ALGORITHM_VERSION = "m2b-bge-m3-document-v1"


class EmbeddingError(SchemaValidationError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class DocumentEncoder(Protocol):
    def encode(self, texts: Sequence[str], *, batch_size: int, max_length: int) -> Any: ...


def _fingerprint_payload(
    *,
    model_id: str = BGE_M3_MODEL_ID,
    model_revision: str = BGE_M3_REVISION,
    tokenizer_revision: str = BGE_M3_REVISION,
    dtype: str = EMBEDDING_DTYPE,
    max_length: int = EMBEDDING_HARD_MAX_TOKENS,
    pooling: str = BGE_POOLING,
    algorithm_version: str = EMBEDDING_ALGORITHM_VERSION,
) -> Mapping[str, Any]:
    return {
        "algorithm_version": algorithm_version,
        "dtype": dtype,
        "max_length": max_length,
        "model_id": model_id,
        "model_revision": model_revision,
        "normalization": "l2",
        "pooling": pooling,
        "tokenizer_revision": tokenizer_revision,
    }


def make_embedding_config_fingerprint(**kwargs: Any) -> str:
    payload = _fingerprint_payload(**kwargs)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


def make_embedding_id(chunk_id: str, model_fingerprint: str) -> str:
    digest = sha256()
    for part in (EMBEDDING_RECORD_SCHEMA_VERSION, model_fingerprint, chunk_id):
        encoded = str(part).encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _l2_normalize(vector: Sequence[float]) -> List[float]:
    values = [float(value) for value in vector]
    norm = math.sqrt(sum(value * value for value in values))
    if not math.isfinite(norm) or norm == 0:
        raise EmbeddingError("INVALID_VECTOR", "embedding vector has zero or non-finite norm")
    return [value / norm for value in values]


class TransformersBGEEncoder:
    """Small, explicit Transformers adapter with no implicit truncation."""

    def __init__(self, model: Any, tokenizer: Any, device: str) -> None:
        self.model = model
        self.tokenizer = tokenizer
        self.device = device

    def encode(self, texts: Sequence[str], *, batch_size: int, max_length: int) -> Any:
        try:
            import torch
        except ImportError as error:  # pragma: no cover - deployment dependent
            raise EmbeddingError("TORCH_DEPENDENCY_MISSING", "torch is required for BGE-M3") from error
        vectors: List[Any] = []
        for start in range(0, len(texts), batch_size):
            batch = list(texts[start:start + batch_size])
            encoded = self.tokenizer(
                batch,
                padding=True,
                truncation=False,
                return_tensors="pt",
                return_attention_mask=True,
            )
            input_ids = encoded["input_ids"]
            if input_ids.shape[1] > max_length:
                raise EmbeddingError(
                    "EMBEDDING_INPUT_TOO_LONG",
                    f"tokenized input exceeds {max_length} tokens",
                )
            encoded = {key: value.to(self.device) for key, value in encoded.items()}
            with torch.no_grad():
                output = self.model(**encoded)
            hidden = output.last_hidden_state
            mask = encoded["attention_mask"].unsqueeze(-1).to(hidden.dtype)
            pooled = (hidden * mask).sum(dim=1) / mask.sum(dim=1).clamp_min(1e-12)
            normalized = torch.nn.functional.normalize(pooled, p=2, dim=1)
            vectors.extend(normalized.detach().cpu().float().tolist())
        return vectors


def load_bge_m3_encoder(
    *,
    device: Optional[str] = None,
    cache_dir: Optional[str] = None,
) -> TransformersBGEEncoder:
    try:
        import torch
        from transformers import AutoModel, AutoTokenizer
    except ImportError as error:  # pragma: no cover - deployment dependent
        raise EmbeddingError(
            "EMBEDDING_DEPENDENCY_MISSING",
            "torch and transformers are required for BGE-M3",
        ) from error
    resolved_device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    kwargs = {"revision": BGE_M3_REVISION, "trust_remote_code": True}
    if cache_dir is not None:
        kwargs["cache_dir"] = cache_dir
    try:
        tokenizer = AutoTokenizer.from_pretrained(BGE_M3_MODEL_ID, **kwargs)
        model = AutoModel.from_pretrained(BGE_M3_MODEL_ID, **kwargs)
        model.eval()
        model.to(resolved_device)
    except Exception as error:  # pragma: no cover - network/cache dependent
        raise EmbeddingError("EMBEDDING_MODEL_LOAD_FAILED", str(error)) from error
    return TransformersBGEEncoder(model, tokenizer, resolved_device)


def embed_chunks(
    chunks: Sequence[EmbeddingChunk],
    *,
    encoder: Optional[DocumentEncoder] = None,
    model_fingerprint: Optional[str] = None,
    batch_size: int = 8,
    device: Optional[str] = None,
) -> List[EmbeddingRecord]:
    if batch_size < 1:
        raise EmbeddingError("INVALID_BATCH_SIZE", "batch_size must be positive")
    if any(not isinstance(chunk, EmbeddingChunk) for chunk in chunks):
        raise EmbeddingError("INVALID_CHUNK", "all inputs must be EmbeddingChunk values")
    if any(chunk.content_token_count > EMBEDDING_HARD_MAX_TOKENS for chunk in chunks):
        raise EmbeddingError("EMBEDDING_INPUT_TOO_LONG", "chunk exceeds the model hard limit")
    if len({chunk.chunk_id for chunk in chunks}) != len(chunks):
        raise EmbeddingError("DUPLICATE_CHUNK_ID", "chunk IDs must be unique")
    if model_fingerprint is None:
        model_fingerprint = make_embedding_config_fingerprint()
    if encoder is None:
        encoder = load_bge_m3_encoder(device=device)
    try:
        raw_vectors = encoder.encode(
            [chunk.content for chunk in chunks],
            batch_size=batch_size,
            max_length=EMBEDDING_HARD_MAX_TOKENS,
        )
    except EmbeddingError:
        raise
    except Exception as error:
        raise EmbeddingError("EMBEDDING_BATCH_FAILED", str(error)) from error
    try:
        vector_count = len(raw_vectors)
    except TypeError as error:
        raise EmbeddingError("EMBEDDING_OUTPUT_INVALID", "encoder did not return a vector sequence") from error
    if vector_count != len(chunks):
        raise EmbeddingError("EMBEDDING_OUTPUT_INVALID", "encoder returned the wrong number of vectors")
    records: List[EmbeddingRecord] = []
    for chunk, raw_vector in zip(chunks, raw_vectors):
        try:
            vector = _l2_normalize(raw_vector)
        except (TypeError, ValueError) as error:
            raise EmbeddingError("INVALID_VECTOR", f"unable to normalize {chunk.chunk_id}") from error
        if len(vector) != EMBEDDING_DIMENSION:
            raise EmbeddingError(
                "EMBEDDING_DIMENSION_MISMATCH",
                f"expected {EMBEDDING_DIMENSION}, got {len(vector)}",
            )
        records.append(
            EmbeddingRecord(
                schema_version=EMBEDDING_RECORD_SCHEMA_VERSION,
                embedding_id=make_embedding_id(chunk.chunk_id, model_fingerprint),
                chunk_id=chunk.chunk_id,
                representation_id=chunk.representation_id,
                model_fingerprint=model_fingerprint,
                dimension=EMBEDDING_DIMENSION,
                dtype=EMBEDDING_DTYPE,
                normalized=True,
                vector=vector,
            )
        )
    return records
