"""Small stage diagnostics reserved for later retrieval evaluation work."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import List

from src.retrieval.schemas import RetrievalCandidate, RetrievalContractError


@dataclass(frozen=True)
class RetrievalSearchObservation:
    query_id: str
    query_text_index: int
    stage: str
    candidate_id: str
    rank: int
    score: float
    artifact_fingerprint: str
    embedding_fingerprint: str

    def __post_init__(self) -> None:
        if not isinstance(self.query_id, str) or not self.query_id:
            raise RetrievalContractError("INVALID_QUERY_ID", "query_id must be a non-empty string")
        if isinstance(self.query_text_index, bool) or not isinstance(self.query_text_index, int) or self.query_text_index < 0:
            raise RetrievalContractError("INVALID_QUERY_TEXT_INDEX", "query_text_index must be non-negative")
        if not isinstance(self.stage, str) or not self.stage:
            raise RetrievalContractError("INVALID_STAGE", "stage must be a non-empty string")
        if not isinstance(self.candidate_id, str) or not self.candidate_id:
            raise RetrievalContractError("INVALID_CANDIDATE_ID", "candidate_id must be a non-empty string")
        if isinstance(self.rank, bool) or not isinstance(self.rank, int) or self.rank < 1:
            raise RetrievalContractError("INVALID_RANK", "rank must be positive")
        if isinstance(self.score, bool) or not isinstance(self.score, (int, float)) or not math.isfinite(float(self.score)):
            raise RetrievalContractError("INVALID_SCORE", "score must be finite")
        if not isinstance(self.artifact_fingerprint, str) or not self.artifact_fingerprint:
            raise RetrievalContractError("INVALID_ARTIFACT_FINGERPRINT", "artifact_fingerprint is required")
        if not isinstance(self.embedding_fingerprint, str) or not self.embedding_fingerprint:
            raise RetrievalContractError("INVALID_EMBEDDING_FINGERPRINT", "embedding_fingerprint is required")


@dataclass(frozen=True)
class RetrievalSearchResult:
    candidates: List[RetrievalCandidate]
    observations: List[RetrievalSearchObservation]
