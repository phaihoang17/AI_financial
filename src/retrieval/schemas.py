"""Canonical contracts shared by every M3 retrieval stage.

These contracts deliberately keep retrieval scores independent.  A stage may
populate only the score it owns; later stages must not overwrite it.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from enum import Enum
from hashlib import sha256
import json
import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set

from src.evidence.schemas import Scale
from src.indexing.schemas import (
    HeaderPathEntry,
    ScaleHintSource,
    ScaleHintStatus,
    SourceSpan,
)
from src.supervisor.schemas import EvidenceSource
from src.understanding.schemas import (
    PeriodKind,
    SchemaValidationError,
    StatementScope,
    _parse_enum,
    _require_enum,
)


RETRIEVAL_CONTRACT_SCHEMA_VERSION = "m3-retrieval-contract-v1"
RETRIEVAL_SCORE_FIELDS = (
    "bm25_score",
    "vector_score",
    "rrf_score",
    "rerank_score",
)


class HintAssociation(str, Enum):
    """Whether a retrieved hint belongs to the candidate or a persisted link."""

    DIRECT = "DIRECT"
    LINKED = "LINKED"


class RetrievalContractError(SchemaValidationError):
    """Raised when a retrieval contract is malformed or internally inconsistent."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def resolve_eligible_source_types(
    query: "RetrievalQuery", eligible_source_types: Optional[Sequence[EvidenceSource]] = None
) -> List[EvidenceSource]:
    """Resolve the exact source gate without changing TASK-030 metadata rules.

    Omitted explicit eligibility uses the already-approved source request on the
    query so existing callers stay additive.  Both forms reject an empty set.
    """

    if not isinstance(query, RetrievalQuery):
        raise TypeError("query must be a RetrievalQuery")
    values = query.eligible_source_types if eligible_source_types is None else eligible_source_types
    if not isinstance(values, (list, tuple)):
        raise RetrievalContractError("INVALID_SOURCE_ELIGIBILITY", "eligible_source_types must be a list")
    result: List[EvidenceSource] = []
    seen = set()
    for index, value in enumerate(values):
        source_type = _require_enum(value, EvidenceSource, f"eligible_source_types[{index}]")
        if source_type not in seen:
            seen.add(source_type)
            result.append(source_type)
    if not result:
        raise RetrievalContractError(
            "SOURCE_ELIGIBILITY_EMPTY", "eligible_source_types must contain TABLE and/or TEXT"
        )
    return result


def _require_mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RetrievalContractError("INVALID_TYPE", f"{path} must be an object")
    return value


def _require_exact_keys(data: Mapping[str, Any], expected: Set[str], path: str) -> None:
    actual = set(data.keys())
    missing = expected - actual
    unknown = actual - expected
    if missing:
        raise RetrievalContractError(
            "MISSING_FIELD", f"{path} is missing fields: {', '.join(sorted(missing))}"
        )
    if unknown:
        raise RetrievalContractError(
            "UNKNOWN_FIELD",
            f"{path} has unknown fields: {', '.join(sorted(map(str, unknown)))}",
        )


def _require_string(value: Any, path: str) -> str:
    if not isinstance(value, str):
        raise RetrievalContractError("INVALID_TYPE", f"{path} must be a string")
    return value


def _require_optional_string(value: Any, path: str) -> Optional[str]:
    if value is not None and not isinstance(value, str):
        raise RetrievalContractError("INVALID_TYPE", f"{path} must be a string or null")
    return value


def _require_bool(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise RetrievalContractError("INVALID_TYPE", f"{path} must be a boolean")
    return value


def _require_sha256(value: Any, path: str) -> str:
    value = _require_string(value, path)
    if len(value) != 64:
        raise RetrievalContractError("INVALID_SHA256", f"{path} must be SHA-256")
    try:
        int(value, 16)
    except ValueError as error:
        raise RetrievalContractError("INVALID_SHA256", f"{path} must be SHA-256") from error
    if value != value.lower():
        raise RetrievalContractError("INVALID_SHA256", f"{path} must be SHA-256")
    return value


def _require_string_list(value: Any, path: str) -> List[str]:
    if not isinstance(value, list):
        raise RetrievalContractError("INVALID_TYPE", f"{path} must be a list")
    return [_require_string(item, f"{path}[{index}]") for index, item in enumerate(value)]


def _require_optional_score(value: Any, path: str) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RetrievalContractError("INVALID_TYPE", f"{path} must be a finite number or null")
    score = float(value)
    if not math.isfinite(score):
        raise RetrievalContractError("NONFINITE_SCORE", f"{path} must be finite")
    return score


def _require_optional_rank(value: Any, path: str) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RetrievalContractError("INVALID_RANK", f"{path} must be a positive integer or null")
    return value


def _require_year(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1000 <= value <= 9999:
        raise RetrievalContractError("INVALID_YEAR", f"{path} must be a four-digit integer")
    return value


def _parse_header_path_groups(value: Any, path: str) -> List[List[HeaderPathEntry]]:
    if not isinstance(value, list):
        raise RetrievalContractError("INVALID_TYPE", f"{path} must be a list")
    groups: List[List[HeaderPathEntry]] = []
    for group_index, group in enumerate(value):
        if not isinstance(group, list):
            raise RetrievalContractError("INVALID_TYPE", f"{path}[{group_index}] must be a list")
        groups.append(
            [
                (
                    item
                    if isinstance(item, HeaderPathEntry)
                    else HeaderPathEntry.from_dict(
                        item, f"{path}[{group_index}][{entry_index}]"
                    )
                )
                for entry_index, item in enumerate(group)
            ]
        )
    return groups


@dataclass
class RetrievalCompany:
    """Resolved company metadata used by a retrieval query."""

    name: str
    ticker: str

    def __post_init__(self) -> None:
        self.name = _require_string(self.name, "company.name")
        self.ticker = _require_string(self.ticker, "company.ticker")

    @classmethod
    def from_dict(cls, value: Any) -> "RetrievalCompany":
        data = _require_mapping(value, "company")
        _require_exact_keys(data, {"name", "ticker"}, "company")
        return cls(name=data["name"], ticker=data["ticker"])

    def to_dict(self) -> Dict[str, str]:
        return {"name": self.name, "ticker": self.ticker}


@dataclass
class RetrievalQuery:
    """A deterministic retrieval request derived from approved upstream state."""

    raw_question: str
    company: RetrievalCompany
    periods: List[str]
    period_kind: Optional[PeriodKind]
    statement_scope: Optional[StatementScope]
    target_metrics: List[str]
    derived_target: Optional[str]
    evidence_sources: List[EvidenceSource]
    requested_scale: Optional[str]
    requested_unit: Optional[str]
    query_texts: List[str]
    eligible_source_types: Optional[List[EvidenceSource]] = None

    def __post_init__(self) -> None:
        self.raw_question = _require_string(self.raw_question, "raw_question")
        if not isinstance(self.company, RetrievalCompany):
            raise RetrievalContractError("INVALID_TYPE", "company must be a RetrievalCompany")
        self.periods = _require_string_list(self.periods, "periods")
        if self.period_kind is not None:
            self.period_kind = _require_enum(self.period_kind, PeriodKind, "period_kind")
        if self.statement_scope is not None:
            self.statement_scope = _require_enum(
                self.statement_scope, StatementScope, "statement_scope"
            )
        self.target_metrics = _require_string_list(self.target_metrics, "target_metrics")
        self.derived_target = _require_optional_string(self.derived_target, "derived_target")
        if not isinstance(self.evidence_sources, list):
            raise RetrievalContractError("INVALID_TYPE", "evidence_sources must be a list")
        self.evidence_sources = [
            _require_enum(source, EvidenceSource, f"evidence_sources[{index}]")
            for index, source in enumerate(self.evidence_sources)
        ]
        source_eligibility = (
            self.evidence_sources
            if self.eligible_source_types is None
            else self.eligible_source_types
        )
        if not isinstance(source_eligibility, list):
            raise RetrievalContractError(
                "INVALID_SOURCE_ELIGIBILITY", "eligible_source_types must be a list"
            )
        self.eligible_source_types = []
        seen_source_types = set()
        for index, source in enumerate(source_eligibility):
            source_type = _require_enum(
                source, EvidenceSource, f"eligible_source_types[{index}]"
            )
            if source_type not in seen_source_types:
                seen_source_types.add(source_type)
                self.eligible_source_types.append(source_type)
        if not self.eligible_source_types:
            raise RetrievalContractError(
                "SOURCE_ELIGIBILITY_EMPTY",
                "eligible_source_types must contain TABLE and/or TEXT",
            )
        self.requested_scale = _require_optional_string(self.requested_scale, "requested_scale")
        self.requested_unit = _require_optional_string(self.requested_unit, "requested_unit")
        self.query_texts = _require_string_list(self.query_texts, "query_texts")
        if not self.query_texts:
            raise RetrievalContractError("QUERY_TEXTS_EMPTY", "query_texts must include raw_question")

    @classmethod
    def from_dict(cls, value: Any) -> "RetrievalQuery":
        data = _require_mapping(value, "RetrievalQuery")
        expected_keys = {field.name for field in fields(cls)}
        if "eligible_source_types" in data:
            _require_exact_keys(data, expected_keys, "RetrievalQuery")
            eligible_source_types = data["eligible_source_types"]
            if not isinstance(eligible_source_types, list):
                raise RetrievalContractError(
                    "INVALID_SOURCE_ELIGIBILITY", "eligible_source_types must be a list"
                )
        else:
            _require_exact_keys(data, expected_keys - {"eligible_source_types"}, "RetrievalQuery")
            eligible_source_types = None
        period_kind = data["period_kind"]
        statement_scope = data["statement_scope"]
        evidence_sources = data["evidence_sources"]
        if not isinstance(evidence_sources, list):
            raise RetrievalContractError("INVALID_TYPE", "evidence_sources must be a list")
        return cls(
            raw_question=data["raw_question"],
            company=RetrievalCompany.from_dict(data["company"]),
            periods=data["periods"],
            period_kind=(
                None
                if period_kind is None
                else _parse_enum(period_kind, PeriodKind, "period_kind")
            ),
            statement_scope=(
                None
                if statement_scope is None
                else _parse_enum(statement_scope, StatementScope, "statement_scope")
            ),
            target_metrics=data["target_metrics"],
            derived_target=data["derived_target"],
            evidence_sources=[
                _parse_enum(source, EvidenceSource, f"evidence_sources[{index}]")
                for index, source in enumerate(evidence_sources)
            ],
            requested_scale=data["requested_scale"],
            requested_unit=data["requested_unit"],
            query_texts=data["query_texts"],
            eligible_source_types=(
                None
                if eligible_source_types is None
                else [
                    _parse_enum(source, EvidenceSource, f"eligible_source_types[{index}]")
                    for index, source in enumerate(eligible_source_types)
                ]
            ),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "raw_question": self.raw_question,
            "company": self.company.to_dict(),
            "periods": list(self.periods),
            "period_kind": None if self.period_kind is None else self.period_kind.value,
            "statement_scope": (
                None if self.statement_scope is None else self.statement_scope.value
            ),
            "target_metrics": list(self.target_metrics),
            "derived_target": self.derived_target,
            "evidence_sources": [source.value for source in self.evidence_sources],
            "requested_scale": self.requested_scale,
            "requested_unit": self.requested_unit,
            "query_texts": list(self.query_texts),
            "eligible_source_types": [
                source.value for source in self.eligible_source_types
            ],
        }

    def to_json(self) -> str:
        return deterministic_json(self)


def make_candidate_id(
    source_type: EvidenceSource, representation_id: str, chunk_id: Optional[str]
) -> str:
    """Return the score- and rank-independent identity for one candidate."""

    source_type = _require_enum(source_type, EvidenceSource, "source_type")
    representation_id = _require_string(representation_id, "representation_id")
    chunk_id = _require_optional_string(chunk_id, "chunk_id")
    identity = chunk_id if chunk_id is not None else representation_id
    payload = {
        "schema_version": RETRIEVAL_CONTRACT_SCHEMA_VERSION,
        "source_type": source_type.value,
        "identity": identity,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return sha256(encoded).hexdigest()


@dataclass
class RetrievalCandidate:
    """A provenance-preserving candidate shared across all retrieval stages."""

    candidate_id: str
    representation_id: str
    chunk_id: Optional[str]
    source_type: EvidenceSource
    report_id: str
    page_ids: List[str]
    table_id: Optional[str]
    paragraph_id: Optional[str]
    ticker: str
    company_name: Optional[str]
    report_year: int
    statement_scope: Optional[StatementScope]
    period_labels: List[str]
    row_paths: List[List[HeaderPathEntry]]
    column_paths: List[List[HeaderPathEntry]]
    content: str
    bm25_score: Optional[float]
    vector_score: Optional[float]
    rrf_score: Optional[float]
    rerank_score: Optional[float]
    rank: Optional[int]

    def __post_init__(self) -> None:
        self.representation_id = _require_string(self.representation_id, "representation_id")
        self.chunk_id = _require_optional_string(self.chunk_id, "chunk_id")
        self.source_type = _require_enum(self.source_type, EvidenceSource, "source_type")
        expected_id = make_candidate_id(self.source_type, self.representation_id, self.chunk_id)
        self.candidate_id = _require_string(self.candidate_id, "candidate_id")
        if self.candidate_id != expected_id:
            raise RetrievalContractError(
                "CANDIDATE_ID_MISMATCH",
                "candidate_id must be derived from source_type and chunk_id or representation_id",
            )
        self.report_id = _require_string(self.report_id, "report_id")
        self.page_ids = _require_string_list(self.page_ids, "page_ids")
        self.table_id = _require_optional_string(self.table_id, "table_id")
        self.paragraph_id = _require_optional_string(self.paragraph_id, "paragraph_id")
        self.ticker = _require_string(self.ticker, "ticker")
        self.company_name = _require_optional_string(self.company_name, "company_name")
        self.report_year = _require_year(self.report_year, "report_year")
        if self.statement_scope is not None:
            self.statement_scope = _require_enum(
                self.statement_scope, StatementScope, "statement_scope"
            )
        self.period_labels = _require_string_list(self.period_labels, "period_labels")
        self.row_paths = _parse_header_path_groups(self.row_paths, "row_paths")
        self.column_paths = _parse_header_path_groups(self.column_paths, "column_paths")
        self.content = _require_string(self.content, "content")
        self.bm25_score = _require_optional_score(self.bm25_score, "bm25_score")
        self.vector_score = _require_optional_score(self.vector_score, "vector_score")
        self.rrf_score = _require_optional_score(self.rrf_score, "rrf_score")
        self.rerank_score = _require_optional_score(self.rerank_score, "rerank_score")
        self.rank = _require_optional_rank(self.rank, "rank")
        if self.source_type is EvidenceSource.TABLE:
            if self.table_id is None or self.paragraph_id is not None:
                raise RetrievalContractError(
                    "PROVENANCE_SHAPE_INVALID",
                    "TABLE candidates require table_id and no paragraph_id",
                )
        elif self.table_id is not None or self.paragraph_id is None:
            raise RetrievalContractError(
                "PROVENANCE_SHAPE_INVALID",
                "TEXT candidates require paragraph_id and no table_id",
            )

    @classmethod
    def from_dict(cls, value: Any) -> "RetrievalCandidate":
        data = _require_mapping(value, "RetrievalCandidate")
        _require_exact_keys(data, {field.name for field in fields(cls)}, "RetrievalCandidate")
        source_type = _parse_enum(data["source_type"], EvidenceSource, "source_type")
        scope = data["statement_scope"]
        return cls(
            candidate_id=data["candidate_id"],
            representation_id=data["representation_id"],
            chunk_id=data["chunk_id"],
            source_type=source_type,
            report_id=data["report_id"],
            page_ids=data["page_ids"],
            table_id=data["table_id"],
            paragraph_id=data["paragraph_id"],
            ticker=data["ticker"],
            company_name=data["company_name"],
            report_year=data["report_year"],
            statement_scope=(
                None
                if scope is None
                else _parse_enum(scope, StatementScope, "statement_scope")
            ),
            period_labels=data["period_labels"],
            row_paths=_parse_header_path_groups(data["row_paths"], "row_paths"),
            column_paths=_parse_header_path_groups(data["column_paths"], "column_paths"),
            content=data["content"],
            bm25_score=data["bm25_score"],
            vector_score=data["vector_score"],
            rrf_score=data["rrf_score"],
            rerank_score=data["rerank_score"],
            rank=data["rank"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "representation_id": self.representation_id,
            "chunk_id": self.chunk_id,
            "source_type": self.source_type.value,
            "report_id": self.report_id,
            "page_ids": list(self.page_ids),
            "table_id": self.table_id,
            "paragraph_id": self.paragraph_id,
            "ticker": self.ticker,
            "company_name": self.company_name,
            "report_year": self.report_year,
            "statement_scope": (
                None if self.statement_scope is None else self.statement_scope.value
            ),
            "period_labels": list(self.period_labels),
            "row_paths": [
                [entry.to_dict() for entry in group] for group in self.row_paths
            ],
            "column_paths": [
                [entry.to_dict() for entry in group] for group in self.column_paths
            ],
            "content": self.content,
            "bm25_score": self.bm25_score,
            "vector_score": self.vector_score,
            "rrf_score": self.rrf_score,
            "rerank_score": self.rerank_score,
            "rank": self.rank,
        }

    def to_json(self) -> str:
        return deterministic_json(self)


@dataclass
class RetrievalSubquery:
    """One deterministic TABLE-only retrieval request derived from a parent query."""

    subquery_id: str
    parent_query_id: str
    metric: Optional[str]
    period: Optional[str]
    statement_scope: StatementScope
    source_type: EvidenceSource
    query_texts: List[str]
    required: bool

    def __post_init__(self) -> None:
        self.subquery_id = _require_sha256(self.subquery_id, "subquery_id")
        self.parent_query_id = _require_sha256(self.parent_query_id, "parent_query_id")
        self.metric = _require_optional_string(self.metric, "metric")
        self.period = _require_optional_string(self.period, "period")
        if self.metric is None and self.period is None:
            raise RetrievalContractError(
                "SUBQUERY_DIMENSION_MISSING", "a subquery requires a metric or period"
            )
        self.statement_scope = _require_enum(
            self.statement_scope, StatementScope, "statement_scope"
        )
        self.source_type = _require_enum(self.source_type, EvidenceSource, "source_type")
        if self.source_type is not EvidenceSource.TABLE:
            raise RetrievalContractError("SUBQUERY_SOURCE_TYPE_INVALID", "subqueries must be TABLE")
        self.query_texts = _require_string_list(self.query_texts, "query_texts")
        if not self.query_texts:
            raise RetrievalContractError("QUERY_TEXTS_EMPTY", "subquery query_texts must be non-empty")
        self.required = _require_bool(self.required, "required")

    @classmethod
    def from_dict(cls, value: Any) -> "RetrievalSubquery":
        data = _require_mapping(value, "RetrievalSubquery")
        _require_exact_keys(data, {field.name for field in fields(cls)}, "RetrievalSubquery")
        return cls(
            subquery_id=data["subquery_id"],
            parent_query_id=data["parent_query_id"],
            metric=data["metric"],
            period=data["period"],
            statement_scope=_parse_enum(
                data["statement_scope"], StatementScope, "statement_scope"
            ),
            source_type=_parse_enum(data["source_type"], EvidenceSource, "source_type"),
            query_texts=data["query_texts"],
            required=data["required"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "subquery_id": self.subquery_id,
            "parent_query_id": self.parent_query_id,
            "metric": self.metric,
            "period": self.period,
            "statement_scope": self.statement_scope.value,
            "source_type": self.source_type.value,
            "query_texts": list(self.query_texts),
            "required": self.required,
        }


@dataclass
class SubqueryRetrievalResult:
    """The retained outcome for one independently executed TABLE subquery."""

    subquery_id: str
    candidates: List[RetrievalCandidate]
    failure_code: Optional[str]

    def __post_init__(self) -> None:
        self.subquery_id = _require_sha256(self.subquery_id, "subquery_id")
        if not isinstance(self.candidates, list) or not all(
            isinstance(candidate, RetrievalCandidate) for candidate in self.candidates
        ):
            raise RetrievalContractError(
                "INVALID_TYPE", "subquery candidates must be RetrievalCandidate values"
            )
        if any(candidate.source_type is not EvidenceSource.TABLE for candidate in self.candidates):
            raise RetrievalContractError(
                "SUBQUERY_RESULT_SOURCE_TYPE_INVALID", "subquery candidates must be TABLE"
            )
        self.failure_code = _require_optional_string(self.failure_code, "failure_code")
        if self.failure_code is not None and self.candidates:
            raise RetrievalContractError(
                "SUBQUERY_FAILURE_RESULT_INVALID", "failed subqueries cannot contain candidates"
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "subquery_id": self.subquery_id,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "failure_code": self.failure_code,
        }


@dataclass
class MultiTableRetrievalResult:
    """Aggregate result that exposes partial retrieval without masking it."""

    parent_query_id: str
    subquery_results: List[SubqueryRetrievalResult]
    complete: bool
    missing_subquery_ids: List[str]

    def __post_init__(self) -> None:
        self.parent_query_id = _require_sha256(self.parent_query_id, "parent_query_id")
        if not isinstance(self.subquery_results, list) or not all(
            isinstance(result, SubqueryRetrievalResult) for result in self.subquery_results
        ):
            raise RetrievalContractError(
                "INVALID_TYPE", "subquery_results must contain SubqueryRetrievalResult values"
            )
        result_ids = [result.subquery_id for result in self.subquery_results]
        if len(result_ids) != len(set(result_ids)):
            raise RetrievalContractError("DUPLICATE_SUBQUERY_RESULT", "subquery results must be unique")
        self.complete = _require_bool(self.complete, "complete")
        self.missing_subquery_ids = [_require_sha256(item, "missing_subquery_ids") for item in self.missing_subquery_ids]
        if len(self.missing_subquery_ids) != len(set(self.missing_subquery_ids)):
            raise RetrievalContractError("DUPLICATE_MISSING_SUBQUERY", "missing subqueries must be unique")
        if self.complete != (not self.missing_subquery_ids):
            raise RetrievalContractError(
                "MULTI_TABLE_COMPLETENESS_INVALID", "complete must exactly reflect missing_subquery_ids"
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "parent_query_id": self.parent_query_id,
            "subquery_results": [result.to_dict() for result in self.subquery_results],
            "complete": self.complete,
            "missing_subquery_ids": list(self.missing_subquery_ids),
        }


@dataclass
class NarrativeRetrievalResult:
    """TEXT-only reranked candidates and only their persisted table links."""

    candidates: List[RetrievalCandidate]
    linked_table_ids_by_candidate: Dict[str, List[str]]

    def __post_init__(self) -> None:
        if not isinstance(self.candidates, list) or not all(
            isinstance(candidate, RetrievalCandidate) for candidate in self.candidates
        ):
            raise RetrievalContractError("INVALID_TYPE", "narrative candidates must be RetrievalCandidate values")
        candidate_ids = [candidate.candidate_id for candidate in self.candidates]
        if any(candidate.source_type is not EvidenceSource.TEXT for candidate in self.candidates):
            raise RetrievalContractError("NARRATIVE_SOURCE_TYPE_INVALID", "narrative candidates must be TEXT")
        if set(self.linked_table_ids_by_candidate) != set(candidate_ids):
            raise RetrievalContractError(
                "NARRATIVE_LINK_KEYS_INVALID", "links must be present for exactly every narrative candidate"
            )
        normalized: Dict[str, List[str]] = {}
        for candidate_id, table_ids in self.linked_table_ids_by_candidate.items():
            _require_sha256(candidate_id, "linked_table_ids_by_candidate key")
            normalized[candidate_id] = _require_string_list(
                table_ids, "linked_table_ids_by_candidate value"
            )
        self.linked_table_ids_by_candidate = normalized

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "linked_table_ids_by_candidate": {
                candidate_id: list(table_ids)
                for candidate_id, table_ids in self.linked_table_ids_by_candidate.items()
            },
        }


@dataclass
class RetrievedScaleUnitHint:
    """One persisted scale/unit hint associated with a retrieved candidate."""

    hint_id: str
    candidate_id: str
    source_kind: ScaleHintSource
    source_ref: str
    source_span: SourceSpan
    raw_hint_text: str
    scale_candidate: Optional[Scale]
    unit_candidate: Optional[str]
    status: ScaleHintStatus
    association: HintAssociation

    def __post_init__(self) -> None:
        self.hint_id = _require_string(self.hint_id, "hint_id")
        self.candidate_id = _require_sha256(self.candidate_id, "candidate_id")
        self.source_kind = _require_enum(self.source_kind, ScaleHintSource, "source_kind")
        self.source_ref = _require_string(self.source_ref, "source_ref")
        if not isinstance(self.source_span, SourceSpan):
            raise RetrievalContractError("INVALID_TYPE", "source_span must be a SourceSpan")
        self.raw_hint_text = _require_string(self.raw_hint_text, "raw_hint_text")
        if self.scale_candidate is not None:
            self.scale_candidate = _require_enum(self.scale_candidate, Scale, "scale_candidate")
        self.unit_candidate = _require_optional_string(self.unit_candidate, "unit_candidate")
        self.status = _require_enum(self.status, ScaleHintStatus, "status")
        self.association = _require_enum(self.association, HintAssociation, "association")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hint_id": self.hint_id,
            "candidate_id": self.candidate_id,
            "source_kind": self.source_kind.value,
            "source_ref": self.source_ref,
            "source_span": self.source_span.to_dict(),
            "raw_hint_text": self.raw_hint_text,
            "scale_candidate": (
                None if self.scale_candidate is None else self.scale_candidate.value
            ),
            "unit_candidate": self.unit_candidate,
            "status": self.status.value,
            "association": self.association.value,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "RetrievedScaleUnitHint":
        data = _require_mapping(value, "RetrievedScaleUnitHint")
        _require_exact_keys(
            data,
            {
                "hint_id",
                "candidate_id",
                "source_kind",
                "source_ref",
                "source_span",
                "raw_hint_text",
                "scale_candidate",
                "unit_candidate",
                "status",
                "association",
            },
            "RetrievedScaleUnitHint",
        )
        raw_scale = data["scale_candidate"]
        return cls(
            hint_id=data["hint_id"],
            candidate_id=data["candidate_id"],
            source_kind=_parse_enum(
                data["source_kind"], ScaleHintSource, "source_kind"
            ),
            source_ref=data["source_ref"],
            source_span=SourceSpan.from_dict(data["source_span"]),
            raw_hint_text=data["raw_hint_text"],
            scale_candidate=(
                None
                if raw_scale is None
                else _parse_enum(raw_scale, Scale, "scale_candidate")
            ),
            unit_candidate=data["unit_candidate"],
            status=_parse_enum(data["status"], ScaleHintStatus, "status"),
            association=_parse_enum(
                data["association"], HintAssociation, "association"
            ),
        )


@dataclass
class RetrievalObservation:
    """A stage-level record for later evaluation and failure-taxonomy logging."""

    query_id: str
    stage: str
    candidate_id: str
    rank: Optional[int]
    score: Optional[float]
    filter_reason: Optional[str]
    artifact_fingerprint: str
    model_fingerprint: str

    def __post_init__(self) -> None:
        self.query_id = _require_string(self.query_id, "query_id")
        self.stage = _require_string(self.stage, "stage")
        self.candidate_id = _require_string(self.candidate_id, "candidate_id")
        self.rank = _require_optional_rank(self.rank, "rank")
        self.score = _require_optional_score(self.score, "score")
        self.filter_reason = _require_optional_string(self.filter_reason, "filter_reason")
        self.artifact_fingerprint = _require_string(
            self.artifact_fingerprint, "artifact_fingerprint"
        )
        self.model_fingerprint = _require_string(self.model_fingerprint, "model_fingerprint")

    @classmethod
    def from_dict(cls, value: Any) -> "RetrievalObservation":
        data = _require_mapping(value, "RetrievalObservation")
        _require_exact_keys(data, {field.name for field in fields(cls)}, "RetrievalObservation")
        return cls(**dict(data))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query_id": self.query_id,
            "stage": self.stage,
            "candidate_id": self.candidate_id,
            "rank": self.rank,
            "score": self.score,
            "filter_reason": self.filter_reason,
            "artifact_fingerprint": self.artifact_fingerprint,
            "model_fingerprint": self.model_fingerprint,
        }


def deterministic_json(value: RetrievalQuery | RetrievalCandidate | RetrievalObservation) -> str:
    """Serialize an M3 contract in a stable, canonical JSON form."""

    return json.dumps(
        value.to_dict(), ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    )


def rank_candidates(
    candidates: List[RetrievalCandidate], *, score_field: str
) -> List[RetrievalCandidate]:
    """Assign deterministic ranks using score descending, then candidate ID ascending.

    This is intentionally backend-independent. It neither normalizes nor
    modifies the stage score; it only returns copies with their canonical rank.
    """

    if score_field not in RETRIEVAL_SCORE_FIELDS:
        raise RetrievalContractError("UNKNOWN_SCORE_FIELD", score_field)
    for candidate in candidates:
        if not isinstance(candidate, RetrievalCandidate):
            raise RetrievalContractError(
                "INVALID_TYPE", "candidates must contain RetrievalCandidate values"
            )
        if getattr(candidate, score_field) is None:
            raise RetrievalContractError(
                "STAGE_SCORE_MISSING", f"{score_field} is required to rank candidates"
            )
    ordered = sorted(
        candidates,
        key=lambda candidate: (-float(getattr(candidate, score_field)), candidate.candidate_id),
    )
    return [replace(candidate, rank=index) for index, candidate in enumerate(ordered, start=1)]
