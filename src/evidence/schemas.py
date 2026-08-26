"""Validated contracts for grounded financial evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
import re
from typing import Any, Dict, List, Optional, Union

from src.supervisor.schemas import EvidenceSource
from src.understanding.schemas import (
    SchemaValidationError,
    StatementScope,
    _parse_enum,
    _require_enum,
    _require_exact_keys,
    _require_mapping,
    _require_optional_string,
    _require_string,
    _require_string_list,
)


class Scale(str, Enum):
    RAW = "RAW"
    THOUSAND = "THOUSAND"
    MILLION = "MILLION"
    BILLION = "BILLION"
    PERCENT = "PERCENT"
    OTHER = "OTHER"


class ScaleSource(str, Enum):
    HEADER = "HEADER"
    CELL = "CELL"
    CAPTION = "CAPTION"
    TEXT = "TEXT"
    QUESTION = "QUESTION"


Number = Union[int, float]
RawValue = Union[str, int, float]


class CanonicalDecimal(str):
    """Validated JSON-string decimal emitted unchanged by M2 numeric parsing."""

    _PATTERN = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?\Z")

    def __new__(cls, value: str) -> "CanonicalDecimal":
        if not isinstance(value, str) or not cls._PATTERN.fullmatch(value):
            raise SchemaValidationError("normalized_value must be a canonical decimal string")
        return str.__new__(cls, value)


def _require_optional_number(value: Any, path: str) -> Optional[Number]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SchemaValidationError(f"{path} must be a number or null")
    if not math.isfinite(float(value)):
        raise SchemaValidationError(f"{path} must be finite")
    return value


def _require_optional_float(value: Any, path: str) -> Optional[float]:
    number = _require_optional_number(value, path)
    return None if number is None else float(number)


def _require_optional_canonical_decimal(value: Any) -> Optional[CanonicalDecimal]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SchemaValidationError("normalized_value must be a canonical decimal string or null")
    return CanonicalDecimal(value)


def _require_raw_value(value: Any) -> Optional[RawValue]:
    if value is None or isinstance(value, str):
        return value
    number = _require_optional_number(value, "raw_value")
    return number


@dataclass
class EvidenceItem:
    evidence_id: str
    source_type: EvidenceSource
    report_ref: str
    page_ref: Optional[str]
    table_ref: Optional[str]
    associated_table_ref: Optional[str]
    statement_scope: Optional[StatementScope]
    period: Optional[str]
    metric: Optional[str]
    row_label: Optional[str]
    column_label: Optional[str]
    row_path: List[str]
    column_path: List[str]
    text_span: Optional[str]
    raw_value: Optional[RawValue]
    normalized_value: Optional[CanonicalDecimal]
    unit: Optional[str]
    scale: Optional[Scale]
    scale_source: Optional[ScaleSource]
    retrieval_score: Optional[float]
    rerank_score: Optional[float]
    ticker: Optional[str] = None
    company_name: Optional[str] = None
    report_year: Optional[int] = None
    paragraph_ref: Optional[str] = None
    provenance_link_ids: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.evidence_id = _require_string(self.evidence_id, "evidence_id")
        self.source_type = _require_enum(
            self.source_type, EvidenceSource, "source_type"
        )
        self.report_ref = _require_string(self.report_ref, "report_ref")
        self.page_ref = _require_optional_string(self.page_ref, "page_ref")
        self.table_ref = _require_optional_string(self.table_ref, "table_ref")
        self.associated_table_ref = _require_optional_string(
            self.associated_table_ref, "associated_table_ref"
        )
        if self.statement_scope is not None:
            self.statement_scope = _require_enum(
                self.statement_scope, StatementScope, "statement_scope"
            )
        self.period = _require_optional_string(self.period, "period")
        self.metric = _require_optional_string(self.metric, "metric")
        self.row_label = _require_optional_string(self.row_label, "row_label")
        self.column_label = _require_optional_string(
            self.column_label, "column_label"
        )
        self.row_path = _require_string_list(self.row_path, "row_path")
        self.column_path = _require_string_list(self.column_path, "column_path")
        self.text_span = _require_optional_string(self.text_span, "text_span")
        self.raw_value = _require_raw_value(self.raw_value)
        self.normalized_value = _require_optional_canonical_decimal(self.normalized_value)
        self.unit = _require_optional_string(self.unit, "unit")
        if self.scale is not None:
            self.scale = _require_enum(self.scale, Scale, "scale")
        if self.scale_source is not None:
            self.scale_source = _require_enum(
                self.scale_source, ScaleSource, "scale_source"
            )
        self.retrieval_score = _require_optional_float(
            self.retrieval_score, "retrieval_score"
        )
        self.rerank_score = _require_optional_float(
            self.rerank_score, "rerank_score"
        )
        self.ticker = _require_optional_string(self.ticker, "ticker")
        self.company_name = _require_optional_string(
            self.company_name, "company_name"
        )
        if self.report_year is not None and (
            isinstance(self.report_year, bool)
            or not isinstance(self.report_year, int)
            or not 1000 <= self.report_year <= 9999
        ):
            raise SchemaValidationError("report_year must be a four-digit integer or null")
        self.paragraph_ref = _require_optional_string(
            self.paragraph_ref, "paragraph_ref"
        )
        self.provenance_link_ids = _require_string_list(
            self.provenance_link_ids, "provenance_link_ids"
        )
        if len(self.provenance_link_ids) != len(set(self.provenance_link_ids)):
            raise SchemaValidationError("provenance_link_ids must not contain duplicates")

    @classmethod
    def from_dict(cls, value: Any) -> EvidenceItem:
        data = _require_mapping(value, "EvidenceItem")
        _require_exact_keys(
            data,
            {
                "evidence_id",
                "source_type",
                "report_ref",
                "page_ref",
                "table_ref",
                "associated_table_ref",
                "statement_scope",
                "period",
                "metric",
                "row_label",
                "column_label",
                "row_path",
                "column_path",
                "text_span",
                "raw_value",
                "normalized_value",
                "unit",
                "scale",
                "scale_source",
                "retrieval_score",
                "rerank_score",
                "ticker",
                "company_name",
                "report_year",
                "paragraph_ref",
                "provenance_link_ids",
            },
            "EvidenceItem",
        )

        statement_scope = data["statement_scope"]
        scale = data["scale"]
        scale_source = data["scale_source"]
        return cls(
            evidence_id=data["evidence_id"],
            source_type=_parse_enum(
                data["source_type"], EvidenceSource, "source_type"
            ),
            report_ref=data["report_ref"],
            page_ref=data["page_ref"],
            table_ref=data["table_ref"],
            associated_table_ref=data["associated_table_ref"],
            statement_scope=(
                None
                if statement_scope is None
                else _parse_enum(
                    statement_scope, StatementScope, "statement_scope"
                )
            ),
            period=data["period"],
            metric=data["metric"],
            row_label=data["row_label"],
            column_label=data["column_label"],
            row_path=data["row_path"],
            column_path=data["column_path"],
            text_span=data["text_span"],
            raw_value=data["raw_value"],
            normalized_value=data["normalized_value"],
            unit=data["unit"],
            scale=(None if scale is None else _parse_enum(scale, Scale, "scale")),
            scale_source=(
                None
                if scale_source is None
                else _parse_enum(scale_source, ScaleSource, "scale_source")
            ),
            retrieval_score=data["retrieval_score"],
            rerank_score=data["rerank_score"],
            ticker=data["ticker"],
            company_name=data["company_name"],
            report_year=data["report_year"],
            paragraph_ref=data["paragraph_ref"],
            provenance_link_ids=data["provenance_link_ids"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "source_type": self.source_type.value,
            "report_ref": self.report_ref,
            "page_ref": self.page_ref,
            "table_ref": self.table_ref,
            "associated_table_ref": self.associated_table_ref,
            "statement_scope": (
                None if self.statement_scope is None else self.statement_scope.value
            ),
            "period": self.period,
            "metric": self.metric,
            "row_label": self.row_label,
            "column_label": self.column_label,
            "row_path": list(self.row_path),
            "column_path": list(self.column_path),
            "text_span": self.text_span,
            "raw_value": self.raw_value,
            "normalized_value": self.normalized_value,
            "unit": self.unit,
            "scale": None if self.scale is None else self.scale.value,
            "scale_source": (
                None if self.scale_source is None else self.scale_source.value
            ),
            "retrieval_score": self.retrieval_score,
            "rerank_score": self.rerank_score,
            "ticker": self.ticker,
            "company_name": self.company_name,
            "report_year": self.report_year,
            "paragraph_ref": self.paragraph_ref,
            "provenance_link_ids": list(self.provenance_link_ids),
        }
