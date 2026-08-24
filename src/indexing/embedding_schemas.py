"""Canonical M2B embedding-boundary and embedding-record contracts."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
from typing import Any, Dict, List, Mapping, Optional

from src.supervisor.schemas import EvidenceSource
from src.understanding.schemas import SchemaValidationError


EMBEDDING_CHUNK_SCHEMA_VERSION = "m2b-embedding-chunk-v1"
EMBEDDING_RECORD_SCHEMA_VERSION = "m2b-embedding-record-v1"
EMBEDDING_DIMENSION = 1024
EMBEDDING_DTYPE = "float32"
EMBEDDING_TARGET_TOKENS = 7168
EMBEDDING_HARD_MAX_TOKENS = 8192

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _require_mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SchemaValidationError(f"{path} must be an object")
    return value


def _require_exact_keys(data: Mapping[str, Any], expected: set[str], path: str) -> None:
    missing = expected - set(data)
    unknown = set(data) - expected
    if missing:
        raise SchemaValidationError(f"{path} is missing fields: {', '.join(sorted(missing))}")
    if unknown:
        raise SchemaValidationError(f"{path} has unknown fields: {', '.join(sorted(unknown))}")


def _string(value: Any, path: str, *, non_empty: bool = False) -> str:
    if not isinstance(value, str) or (non_empty and not value):
        raise SchemaValidationError(f"{path} must be a {'non-empty ' if non_empty else ''}string")
    return value


def _string_list(value: Any, path: str) -> List[str]:
    if not isinstance(value, list):
        raise SchemaValidationError(f"{path} must be a list")
    return [_string(item, f"{path}[{index}]") for index, item in enumerate(value)]


def _integer(value: Any, path: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise SchemaValidationError(f"{path} must be an integer >= {minimum}")
    return value


def _optional_integer(value: Any, path: str) -> Optional[int]:
    if value is None:
        return None
    return _integer(value, path)


def _sha256(value: Any, path: str) -> str:
    value = _string(value, path, non_empty=True)
    if not _SHA256.fullmatch(value):
        raise SchemaValidationError(f"{path} must be lowercase SHA-256 hex")
    return value


def _enum(value: Any, enum_type: Any, path: str) -> Any:
    if not isinstance(value, enum_type):
        raise SchemaValidationError(f"{path} must be a {enum_type.__name__}")
    return value


def _parse_enum(value: Any, enum_type: Any, path: str) -> Any:
    if not isinstance(value, str):
        raise SchemaValidationError(f"{path} must be a string enum value")
    try:
        return enum_type(value)
    except ValueError as error:
        raise SchemaValidationError(f"{path} is not a valid {enum_type.__name__}") from error


def _canonical(value: Any) -> Any:
    if isinstance(value, EvidenceSource):
        return value.value
    if isinstance(value, CellFragment):
        return value.to_dict()
    if isinstance(value, EmbeddingChunk):
        return value.to_dict()
    if isinstance(value, EmbeddingRecord):
        return value.to_dict()
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    return value


@dataclass
class CellFragment:
    source_cell_id: str
    fragment_index: int
    fragment_count: int
    text: str
    content_token_count: int

    def __post_init__(self) -> None:
        self.source_cell_id = _string(self.source_cell_id, "source_cell_id", non_empty=True)
        self.fragment_index = _integer(self.fragment_index, "fragment_index")
        self.fragment_count = _integer(self.fragment_count, "fragment_count", minimum=1)
        if self.fragment_index >= self.fragment_count:
            raise SchemaValidationError("fragment_index must be less than fragment_count")
        self.text = _string(self.text, "text")
        self.content_token_count = _integer(self.content_token_count, "content_token_count")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_cell_id": self.source_cell_id,
            "fragment_index": self.fragment_index,
            "fragment_count": self.fragment_count,
            "text": self.text,
            "content_token_count": self.content_token_count,
        }

    @classmethod
    def from_dict(cls, value: Any, path: str = "CellFragment") -> "CellFragment":
        data = _require_mapping(value, path)
        _require_exact_keys(data, {
            "source_cell_id", "fragment_index", "fragment_count", "text", "content_token_count"
        }, path)
        return cls(**dict(data))


@dataclass
class EmbeddingChunk:
    schema_version: str
    chunk_id: str
    representation_id: str
    source_type: EvidenceSource
    chunk_index: int
    chunk_count: int
    primary_source_cell_ids: List[str]
    context_source_cell_ids: List[str]
    anchor_row_start: Optional[int]
    anchor_row_end: Optional[int]
    anchor_column_start: Optional[int]
    anchor_column_end: Optional[int]
    content: str
    content_token_count: int
    chunking_config_fingerprint: str
    cell_fragment: Optional[CellFragment] = None

    def __post_init__(self) -> None:
        if self.schema_version != EMBEDDING_CHUNK_SCHEMA_VERSION:
            raise SchemaValidationError(
                f"schema_version must be {EMBEDDING_CHUNK_SCHEMA_VERSION}"
            )
        self.chunk_id = _sha256(self.chunk_id, "chunk_id")
        self.representation_id = _string(self.representation_id, "representation_id", non_empty=True)
        self.source_type = _enum(self.source_type, EvidenceSource, "source_type")
        if self.source_type not in {EvidenceSource.TABLE, EvidenceSource.TEXT}:
            raise SchemaValidationError("source_type must be TABLE or TEXT")
        self.chunk_index = _integer(self.chunk_index, "chunk_index")
        self.chunk_count = _integer(self.chunk_count, "chunk_count", minimum=1)
        if self.chunk_index >= self.chunk_count:
            raise SchemaValidationError("chunk_index must be less than chunk_count")
        self.primary_source_cell_ids = _string_list(self.primary_source_cell_ids, "primary_source_cell_ids")
        self.context_source_cell_ids = _string_list(self.context_source_cell_ids, "context_source_cell_ids")
        if set(self.primary_source_cell_ids) & set(self.context_source_cell_ids):
            raise SchemaValidationError("primary and context source cell IDs must be disjoint")
        ranges = (
            self.anchor_row_start, self.anchor_row_end,
            self.anchor_column_start, self.anchor_column_end,
        )
        if any(value is None for value in ranges) and not all(value is None for value in ranges):
            raise SchemaValidationError("all anchor ranges must be null or all populated")
        for name, value in zip(
            ("anchor_row_start", "anchor_row_end", "anchor_column_start", "anchor_column_end"),
            ranges,
        ):
            setattr(self, name, _optional_integer(value, name))
        if self.anchor_row_start is not None and self.anchor_row_end < self.anchor_row_start:
            raise SchemaValidationError("anchor row range is reversed")
        if self.anchor_column_start is not None and self.anchor_column_end < self.anchor_column_start:
            raise SchemaValidationError("anchor column range is reversed")
        self.content = _string(self.content, "content", non_empty=True)
        self.content_token_count = _integer(self.content_token_count, "content_token_count")
        if self.content_token_count > EMBEDDING_TARGET_TOKENS:
            raise SchemaValidationError("content_token_count exceeds the embedding target budget")
        self.chunking_config_fingerprint = _sha256(
            self.chunking_config_fingerprint, "chunking_config_fingerprint"
        )
        if self.cell_fragment is not None and not isinstance(self.cell_fragment, CellFragment):
            raise SchemaValidationError("cell_fragment must be a CellFragment or null")
        if self.cell_fragment is not None:
            if self.source_type is not EvidenceSource.TABLE:
                raise SchemaValidationError("cell fragments are valid only for TABLE chunks")
            if self.primary_source_cell_ids != [self.cell_fragment.source_cell_id]:
                raise SchemaValidationError("fragment chunks must have the fragmented cell as their only primary cell")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "chunk_id": self.chunk_id,
            "representation_id": self.representation_id,
            "source_type": self.source_type.value,
            "chunk_index": self.chunk_index,
            "chunk_count": self.chunk_count,
            "primary_source_cell_ids": list(self.primary_source_cell_ids),
            "context_source_cell_ids": list(self.context_source_cell_ids),
            "anchor_row_start": self.anchor_row_start,
            "anchor_row_end": self.anchor_row_end,
            "anchor_column_start": self.anchor_column_start,
            "anchor_column_end": self.anchor_column_end,
            "content": self.content,
            "content_token_count": self.content_token_count,
            "chunking_config_fingerprint": self.chunking_config_fingerprint,
            "cell_fragment": None if self.cell_fragment is None else self.cell_fragment.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Any, path: str = "EmbeddingChunk") -> "EmbeddingChunk":
        data = _require_mapping(value, path)
        expected = {
            "schema_version", "chunk_id", "representation_id", "source_type", "chunk_index",
            "chunk_count", "primary_source_cell_ids", "context_source_cell_ids", "anchor_row_start",
            "anchor_row_end", "anchor_column_start", "anchor_column_end", "content",
            "content_token_count", "chunking_config_fingerprint", "cell_fragment",
        }
        _require_exact_keys(data, expected, path)
        return cls(
            schema_version=data["schema_version"], chunk_id=data["chunk_id"],
            representation_id=data["representation_id"],
            source_type=_parse_enum(data["source_type"], EvidenceSource, f"{path}.source_type"),
            chunk_index=data["chunk_index"], chunk_count=data["chunk_count"],
            primary_source_cell_ids=data["primary_source_cell_ids"],
            context_source_cell_ids=data["context_source_cell_ids"],
            anchor_row_start=data["anchor_row_start"], anchor_row_end=data["anchor_row_end"],
            anchor_column_start=data["anchor_column_start"], anchor_column_end=data["anchor_column_end"],
            content=data["content"], content_token_count=data["content_token_count"],
            chunking_config_fingerprint=data["chunking_config_fingerprint"],
            cell_fragment=(None if data["cell_fragment"] is None else CellFragment.from_dict(data["cell_fragment"], f"{path}.cell_fragment")),
        )


@dataclass
class EmbeddingRecord:
    schema_version: str
    embedding_id: str
    chunk_id: str
    representation_id: str
    model_fingerprint: str
    dimension: int
    dtype: str
    normalized: bool
    vector: List[float]

    def __post_init__(self) -> None:
        if self.schema_version != EMBEDDING_RECORD_SCHEMA_VERSION:
            raise SchemaValidationError(
                f"schema_version must be {EMBEDDING_RECORD_SCHEMA_VERSION}"
            )
        self.embedding_id = _sha256(self.embedding_id, "embedding_id")
        self.chunk_id = _string(self.chunk_id, "chunk_id", non_empty=True)
        self.representation_id = _string(self.representation_id, "representation_id", non_empty=True)
        self.model_fingerprint = _sha256(self.model_fingerprint, "model_fingerprint")
        self.dimension = _integer(self.dimension, "dimension", minimum=1)
        self.dtype = _string(self.dtype, "dtype", non_empty=True)
        if self.dtype != EMBEDDING_DTYPE:
            raise SchemaValidationError(f"dtype must be {EMBEDDING_DTYPE}")
        if not isinstance(self.normalized, bool) or not self.normalized:
            raise SchemaValidationError("normalized must be true")
        if not isinstance(self.vector, list) or len(self.vector) != self.dimension:
            raise SchemaValidationError("vector length must equal dimension")
        for index, value in enumerate(self.vector):
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
                raise SchemaValidationError(f"vector[{index}] must be a finite number")
            self.vector[index] = float(value)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "embedding_id": self.embedding_id,
            "chunk_id": self.chunk_id,
            "representation_id": self.representation_id,
            "model_fingerprint": self.model_fingerprint,
            "dimension": self.dimension,
            "dtype": self.dtype,
            "normalized": self.normalized,
            "vector": list(self.vector),
        }

    @classmethod
    def from_dict(cls, value: Any, path: str = "EmbeddingRecord") -> "EmbeddingRecord":
        data = _require_mapping(value, path)
        _require_exact_keys(data, {
            "schema_version", "embedding_id", "chunk_id", "representation_id", "model_fingerprint",
            "dimension", "dtype", "normalized", "vector",
        }, path)
        return cls(**dict(data))


def canonical_contract_json(value: Any) -> str:
    """Serialize an M2B contract deterministically for manifests and hashes."""

    return json.dumps(_canonical(value), ensure_ascii=False, separators=(",", ":"))
