"""Typed, serialization-safe diagnostics for M3 retrieval.

Events are observational: constructing or serializing one never changes a
retrieval result and this module deliberately contains no retry policy.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
from typing import Iterable, Optional

from src.retrieval.schemas import RetrievalContractError


class RetrievalFailureStage(str, Enum):
    QUERY_BUILD = "QUERY_BUILD"; METADATA_FILTER = "METADATA_FILTER"; BM25 = "BM25"
    QUERY_EMBEDDING = "QUERY_EMBEDDING"; VECTOR_SEARCH = "VECTOR_SEARCH"; RRF = "RRF"
    RERANKER = "RERANKER"; MULTI_TABLE = "MULTI_TABLE"; NARRATIVE_RETRIEVAL = "NARRATIVE_RETRIEVAL"
    SCALE_UNIT_RETRIEVAL = "SCALE_UNIT_RETRIEVAL"; CELL_LOCATOR = "CELL_LOCATOR"
    EVIDENCE_BUILD = "EVIDENCE_BUILD"; EVIDENCE_COMPLETENESS = "EVIDENCE_COMPLETENESS"
    ARTIFACT_COMPATIBILITY = "ARTIFACT_COMPATIBILITY"; PROVENANCE = "PROVENANCE"; UNKNOWN = "UNKNOWN"


def _text(value: object, field: str, *, optional: bool = False) -> Optional[str]:
    if value is None and optional: return None
    if not isinstance(value, str) or not value: raise RetrievalContractError("FAILURE_EVENT_INVALID", f"{field} must be a non-empty string")
    return value


@dataclass(frozen=True)
class RetrievalFailureEvent:
    """A deterministic failure record; messages cannot carry stack traces."""
    query_id: str; stage: RetrievalFailureStage; code: str; message: str; retryable: bool
    case_id: Optional[str] = None; candidate_id: Optional[str] = None; subquery_id: Optional[str] = None
    artifact_fingerprint: Optional[str] = None; model_fingerprint: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "query_id", _text(self.query_id, "query_id"))
        if not isinstance(self.stage, RetrievalFailureStage): object.__setattr__(self, "stage", RetrievalFailureStage(self.stage))
        object.__setattr__(self, "code", _text(self.code, "code"))
        object.__setattr__(self, "message", _text(self.message, "message"))
        if "traceback" in self.message.casefold() or "\n  file " in self.message.casefold():
            raise RetrievalContractError("FAILURE_EVENT_STACK_TRACE", "message must not include a stack trace")
        if not isinstance(self.retryable, bool): raise RetrievalContractError("FAILURE_EVENT_INVALID", "retryable must be boolean")
        for name in ("case_id", "candidate_id", "subquery_id", "artifact_fingerprint", "model_fingerprint"):
            object.__setattr__(self, name, _text(getattr(self, name), name, optional=True))

    @property
    def event_id(self) -> str:
        return sha256(json.dumps(self.to_dict(include_event_id=False), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def to_dict(self, *, include_event_id: bool = True) -> dict:
        value = {"query_id": self.query_id, "case_id": self.case_id, "stage": self.stage.value,
                 "code": self.code, "message": self.message, "candidate_id": self.candidate_id,
                 "subquery_id": self.subquery_id, "artifact_fingerprint": self.artifact_fingerprint,
                 "model_fingerprint": self.model_fingerprint, "retryable": self.retryable}
        return ({"event_id": self.event_id} | value) if include_event_id else value

    @classmethod
    def from_dict(cls, value: object) -> "RetrievalFailureEvent":
        if not isinstance(value, dict): raise RetrievalContractError("FAILURE_EVENT_INVALID", "event must be an object")
        data=dict(value); event_id=data.pop("event_id", None)
        expected={"query_id","case_id","stage","code","message","candidate_id","subquery_id","artifact_fingerprint","model_fingerprint","retryable"}
        if set(data) != expected: raise RetrievalContractError("FAILURE_EVENT_INVALID", "event fields do not match the canonical schema")
        event=cls(**data)
        if event_id is not None and event_id != event.event_id: raise RetrievalContractError("FAILURE_EVENT_ID_MISMATCH", "event_id does not match canonical payload")
        return event


_CODE_STAGES = (("ARTIFACT_", RetrievalFailureStage.ARTIFACT_COMPATIBILITY), ("SIDECAR_", RetrievalFailureStage.PROVENANCE),
    ("PROVENANCE_", RetrievalFailureStage.PROVENANCE), ("EVIDENCE_SOURCE_", RetrievalFailureStage.PROVENANCE),
    ("BM25_", RetrievalFailureStage.BM25), ("VECTOR_", RetrievalFailureStage.VECTOR_SEARCH), ("FAISS_", RetrievalFailureStage.VECTOR_SEARCH),
    ("RRF_", RetrievalFailureStage.RRF), ("RERANK", RetrievalFailureStage.RERANKER), ("MULTI_TABLE_", RetrievalFailureStage.MULTI_TABLE),
    ("SCALE_HINT_", RetrievalFailureStage.SCALE_UNIT_RETRIEVAL), ("EVIDENCE_PARAGRAPH_", RetrievalFailureStage.EVIDENCE_BUILD),
    ("EVIDENCE_CHUNK_", RetrievalFailureStage.PROVENANCE), ("EVIDENCE_", RetrievalFailureStage.CELL_LOCATOR))


def stage_for_error(code: str, *, default: Optional[RetrievalFailureStage] = None) -> RetrievalFailureStage:
    """Map known typed codes without silently treating them as UNKNOWN."""
    code = _text(code, "code")
    for prefix, stage in _CODE_STAGES:
        if code.startswith(prefix): return stage
    if default is not None: return default
    raise RetrievalContractError("FAILURE_STAGE_UNMAPPED", f"no failure stage mapping for {code}")


def attribute_earliest_failure(outcomes: Iterable[tuple[RetrievalFailureStage, bool]]) -> Optional[RetrievalFailureStage]:
    """Return the first failed boundary in caller-supplied pipeline order."""
    for stage, recovered in outcomes:
        if not isinstance(stage, RetrievalFailureStage) or not isinstance(recovered, bool):
            raise RetrievalContractError("FAILURE_ATTRIBUTION_INVALID", "outcomes must be typed stage/boolean pairs")
        if not recovered: return stage
    return None
