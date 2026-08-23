"""Canonical M2A contracts for raw and normalized financial evidence."""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from hashlib import sha256
import re
from typing import Any, Dict, List, Mapping, Optional, Set, Type, TypeVar

from src.evidence.schemas import Scale
from src.supervisor.schemas import EvidenceSource
from src.understanding.schemas import SchemaValidationError, StatementScope


M2A_SCHEMA_VERSION = "m2a.v1"
NORMALIZATION_VERSION = "m2a-normalization-v1"
REPRESENTATION_VERSION = "m2a-representation-v1"


class IssueSeverity(str, Enum):
    WARNING = "WARNING"
    ERROR = "ERROR"
    FATAL = "FATAL"


class TableParseStatus(str, Enum):
    VALID = "VALID"
    RECOVERED = "RECOVERED"
    UNPARSEABLE = "UNPARSEABLE"


class HeaderAxis(str, Enum):
    ROW = "ROW"
    COLUMN = "COLUMN"


class HeaderResolution(str, Enum):
    EXPLICIT = "EXPLICIT"
    INFERRED = "INFERRED"
    AMBIGUOUS = "AMBIGUOUS"


class NumericParseStatus(str, Enum):
    PARSED = "PARSED"
    MISSING = "MISSING"
    NOT_NUMERIC = "NOT_NUMERIC"
    AMBIGUOUS = "AMBIGUOUS"
    MALFORMED = "MALFORMED"


class CellRole(str, Enum):
    DATA = "DATA"
    ROW_HEADER = "ROW_HEADER"
    COLUMN_HEADER = "COLUMN_HEADER"
    CORNER = "CORNER"
    UNKNOWN = "UNKNOWN"


class ParagraphKind(str, Enum):
    HEADING = "HEADING"
    BODY = "BODY"
    LIST = "LIST"
    OTHER = "OTHER"


class TableTextRelation(str, Enum):
    CAPTION = "CAPTION"
    EXPLICIT_REFERENCE = "EXPLICIT_REFERENCE"
    NOTE = "NOTE"
    ADJACENT_CONTEXT = "ADJACENT_CONTEXT"


class TableTextLinkBasis(str, Enum):
    UNIQUE_TEXT_REFERENCE = "UNIQUE_TEXT_REFERENCE"
    IMMEDIATE_BEFORE = "IMMEDIATE_BEFORE"
    IMMEDIATE_AFTER = "IMMEDIATE_AFTER"


class ScaleHintSource(str, Enum):
    HEADER = "HEADER"
    CELL = "CELL"
    CAPTION = "CAPTION"
    TEXT = "TEXT"


class ScaleHintStatus(str, Enum):
    EXTRACTED = "EXTRACTED"
    AMBIGUOUS = "AMBIGUOUS"


class RepresentationGranularity(str, Enum):
    TABLE = "TABLE"
    PARAGRAPH = "PARAGRAPH"


EnumT = TypeVar("EnumT", bound=Enum)


def _require_mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SchemaValidationError(f"{path} must be an object")
    return value


def _require_exact_keys(
    data: Mapping[str, Any], expected: Set[str], path: str
) -> None:
    actual = set(data.keys())
    missing = expected - actual
    unknown = actual - expected
    if missing:
        raise SchemaValidationError(
            f"{path} is missing fields: {', '.join(sorted(missing))}"
        )
    if unknown:
        raise SchemaValidationError(
            f"{path} has unknown fields: {', '.join(sorted(map(str, unknown)))}"
        )


def _require_string(value: Any, path: str, *, non_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise SchemaValidationError(f"{path} must be a string")
    if non_empty and not value:
        raise SchemaValidationError(f"{path} must be non-empty")
    return value


def _require_optional_string(value: Any, path: str) -> Optional[str]:
    if value is not None and not isinstance(value, str):
        raise SchemaValidationError(f"{path} must be a string or null")
    return value


def _require_int(value: Any, path: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SchemaValidationError(f"{path} must be an integer")
    if value < minimum:
        raise SchemaValidationError(f"{path} must be >= {minimum}")
    return value


def _require_bool(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise SchemaValidationError(f"{path} must be a boolean")
    return value


def _require_enum(value: Any, enum_type: Type[EnumT], path: str) -> EnumT:
    if not isinstance(value, enum_type):
        raise SchemaValidationError(f"{path} must be a {enum_type.__name__}")
    return value


def _parse_enum(value: Any, enum_type: Type[EnumT], path: str) -> EnumT:
    if not isinstance(value, str):
        raise SchemaValidationError(f"{path} must be a string enum value")
    try:
        return enum_type(value)
    except ValueError as error:
        allowed = ", ".join(member.value for member in enum_type)
        raise SchemaValidationError(f"{path} must be one of: {allowed}") from error


def _require_list(value: Any, path: str) -> List[Any]:
    if not isinstance(value, list):
        raise SchemaValidationError(f"{path} must be a list")
    return value


def _require_string_list(value: Any, path: str) -> List[str]:
    return [
        _require_string(item, f"{path}[{index}]")
        for index, item in enumerate(_require_list(value, path))
    ]


def _require_instance_list(value: Any, expected: Type[Any], path: str) -> List[Any]:
    items = _require_list(value, path)
    for index, item in enumerate(items):
        if not isinstance(item, expected):
            raise SchemaValidationError(
                f"{path}[{index}] must be a {expected.__name__}"
            )
    return items


def _parse_list(
    value: Any, item_type: Type[Any], path: str
) -> List[Any]:
    return [
        item_type.from_dict(item, f"{path}[{index}]")
        for index, item in enumerate(_require_list(value, path))
    ]


def _serialize(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {field.name: _serialize(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    return value


class SerializableContract:
    def to_dict(self) -> Dict[str, Any]:
        return _serialize(self)


@dataclass
class SourceSpan(SerializableContract):
    start: int
    end: int

    def __post_init__(self) -> None:
        self.start = _require_int(self.start, "span.start")
        self.end = _require_int(self.end, "span.end")
        if self.end < self.start:
            raise SchemaValidationError("span.end must be >= span.start")

    @classmethod
    def from_dict(cls, value: Any, path: str = "span") -> SourceSpan:
        data = _require_mapping(value, path)
        _require_exact_keys(data, {"start", "end"}, path)
        return cls(start=data["start"], end=data["end"])


@dataclass
class ParseIssue(SerializableContract):
    code: str
    severity: IssueSeverity
    source_span: Optional[SourceSpan]
    message: str

    def __post_init__(self) -> None:
        self.code = _require_string(self.code, "issue.code", non_empty=True)
        self.severity = _require_enum(self.severity, IssueSeverity, "issue.severity")
        if self.source_span is not None and not isinstance(self.source_span, SourceSpan):
            raise SchemaValidationError("issue.source_span must be a SourceSpan or null")
        self.message = _require_string(self.message, "issue.message", non_empty=True)

    @classmethod
    def from_dict(cls, value: Any, path: str = "issue") -> ParseIssue:
        data = _require_mapping(value, path)
        _require_exact_keys(data, {"code", "severity", "source_span", "message"}, path)
        return cls(
            code=data["code"],
            severity=_parse_enum(data["severity"], IssueSeverity, f"{path}.severity"),
            source_span=(
                None
                if data["source_span"] is None
                else SourceSpan.from_dict(data["source_span"], f"{path}.source_span")
            ),
            message=data["message"],
        )


@dataclass
class HeaderPathEntry(SerializableContract):
    header_id: str
    label: str

    def __post_init__(self) -> None:
        self.header_id = _require_string(self.header_id, "header_path.header_id", non_empty=True)
        self.label = _require_string(self.label, "header_path.label")

    @classmethod
    def from_dict(cls, value: Any, path: str = "header_path") -> HeaderPathEntry:
        data = _require_mapping(value, path)
        _require_exact_keys(data, {"header_id", "label"}, path)
        return cls(header_id=data["header_id"], label=data["label"])


@dataclass
class ReportSource(SerializableContract):
    schema_version: str
    corpus_id: str
    report_id: str
    source_ref: str
    ticker: str
    company_name: Optional[str]
    report_year: int
    document_name: str
    statement_scope: Optional[StatementScope]
    raw_text: str
    content_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != M2A_SCHEMA_VERSION:
            raise SchemaValidationError(f"schema_version must be {M2A_SCHEMA_VERSION}")
        self.corpus_id = _require_string(self.corpus_id, "corpus_id", non_empty=True)
        self.report_id = _require_string(self.report_id, "report_id", non_empty=True)
        self.source_ref = _require_string(self.source_ref, "source_ref", non_empty=True)
        if self.source_ref.startswith("/") or "\\" in self.source_ref or any(
            part in {"", ".", ".."} for part in self.source_ref.split("/")
        ):
            raise SchemaValidationError("source_ref must be a normalized relative POSIX path")
        self.ticker = _require_string(self.ticker, "ticker", non_empty=True)
        self.company_name = _require_optional_string(self.company_name, "company_name")
        self.report_year = _require_int(self.report_year, "report_year", minimum=1000)
        if self.report_year > 9999:
            raise SchemaValidationError("report_year must be a four-digit year")
        self.document_name = _require_string(self.document_name, "document_name", non_empty=True)
        if self.statement_scope is not None:
            self.statement_scope = _require_enum(
                self.statement_scope, StatementScope, "statement_scope"
            )
        self.raw_text = _require_string(self.raw_text, "raw_text")
        self.content_sha256 = _require_string(
            self.content_sha256, "content_sha256", non_empty=True
        )
        if not re.fullmatch(r"[0-9a-f]{64}", self.content_sha256):
            raise SchemaValidationError("content_sha256 must be lowercase SHA-256 hex")
        actual_hash = sha256(self.raw_text.encode("utf-8")).hexdigest()
        if actual_hash != self.content_sha256:
            raise SchemaValidationError("content_sha256 does not match raw_text")

    @classmethod
    def from_dict(cls, value: Any, path: str = "ReportSource") -> ReportSource:
        data = _require_mapping(value, path)
        expected = {field.name for field in fields(cls)}
        _require_exact_keys(data, expected, path)
        scope = data["statement_scope"]
        return cls(
            schema_version=data["schema_version"],
            corpus_id=data["corpus_id"],
            report_id=data["report_id"],
            source_ref=data["source_ref"],
            ticker=data["ticker"],
            company_name=data["company_name"],
            report_year=data["report_year"],
            document_name=data["document_name"],
            statement_scope=(
                None if scope is None else _parse_enum(scope, StatementScope, f"{path}.statement_scope")
            ),
            raw_text=data["raw_text"],
            content_sha256=data["content_sha256"],
        )


@dataclass
class SourceCell(SerializableContract):
    cell_id: str
    table_id: str
    source_row_index: int
    source_cell_index: int
    source_span: SourceSpan
    raw_html: str
    raw_inner_html: str
    extracted_text: str
    rowspan: int
    colspan: int

    def __post_init__(self) -> None:
        self.cell_id = _require_string(self.cell_id, "cell_id", non_empty=True)
        self.table_id = _require_string(self.table_id, "table_id", non_empty=True)
        self.source_row_index = _require_int(self.source_row_index, "source_row_index")
        self.source_cell_index = _require_int(self.source_cell_index, "source_cell_index")
        if not isinstance(self.source_span, SourceSpan):
            raise SchemaValidationError("source_span must be a SourceSpan")
        self.raw_html = _require_string(self.raw_html, "raw_html")
        self.raw_inner_html = _require_string(self.raw_inner_html, "raw_inner_html")
        self.extracted_text = _require_string(self.extracted_text, "extracted_text")
        self.rowspan = _require_int(self.rowspan, "rowspan", minimum=1)
        self.colspan = _require_int(self.colspan, "colspan", minimum=1)

    @classmethod
    def from_dict(cls, value: Any, path: str = "SourceCell") -> SourceCell:
        data = _require_mapping(value, path)
        _require_exact_keys(data, {field.name for field in fields(cls)}, path)
        return cls(
            cell_id=data["cell_id"], table_id=data["table_id"],
            source_row_index=data["source_row_index"], source_cell_index=data["source_cell_index"],
            source_span=SourceSpan.from_dict(data["source_span"], f"{path}.source_span"),
            raw_html=data["raw_html"], raw_inner_html=data["raw_inner_html"],
            extracted_text=data["extracted_text"], rowspan=data["rowspan"], colspan=data["colspan"],
        )


@dataclass
class SourceTable(SerializableContract):
    table_id: str
    report_id: str
    page_id: str
    table_index: int
    source_span: SourceSpan
    raw_html: str
    parse_status: TableParseStatus
    inline_caption_span: Optional[SourceSpan]
    inline_caption_text: Optional[str]
    cells: List[SourceCell]
    issues: List[ParseIssue]

    def __post_init__(self) -> None:
        self.table_id = _require_string(self.table_id, "table_id", non_empty=True)
        self.report_id = _require_string(self.report_id, "report_id", non_empty=True)
        self.page_id = _require_string(self.page_id, "page_id", non_empty=True)
        self.table_index = _require_int(self.table_index, "table_index")
        if not isinstance(self.source_span, SourceSpan):
            raise SchemaValidationError("source_span must be a SourceSpan")
        self.raw_html = _require_string(self.raw_html, "raw_html")
        self.parse_status = _require_enum(self.parse_status, TableParseStatus, "parse_status")
        if self.inline_caption_span is not None and not isinstance(self.inline_caption_span, SourceSpan):
            raise SchemaValidationError("inline_caption_span must be a SourceSpan or null")
        self.inline_caption_text = _require_optional_string(
            self.inline_caption_text, "inline_caption_text"
        )
        if (self.inline_caption_span is None) != (self.inline_caption_text is None):
            raise SchemaValidationError("inline caption span and text must be both set or both null")
        self.cells = _require_instance_list(self.cells, SourceCell, "cells")
        self.issues = _require_instance_list(self.issues, ParseIssue, "issues")
        if self.parse_status is TableParseStatus.UNPARSEABLE and self.cells:
            raise SchemaValidationError("UNPARSEABLE tables must not expose parsed cells")
        if any(cell.table_id != self.table_id for cell in self.cells):
            raise SchemaValidationError("every cell.table_id must match table_id")

    @classmethod
    def from_dict(cls, value: Any, path: str = "SourceTable") -> SourceTable:
        data = _require_mapping(value, path)
        _require_exact_keys(data, {field.name for field in fields(cls)}, path)
        return cls(
            table_id=data["table_id"], report_id=data["report_id"], page_id=data["page_id"],
            table_index=data["table_index"], source_span=SourceSpan.from_dict(data["source_span"], f"{path}.source_span"),
            raw_html=data["raw_html"], parse_status=_parse_enum(data["parse_status"], TableParseStatus, f"{path}.parse_status"),
            inline_caption_span=(None if data["inline_caption_span"] is None else SourceSpan.from_dict(data["inline_caption_span"], f"{path}.inline_caption_span")),
            inline_caption_text=data["inline_caption_text"],
            cells=_parse_list(data["cells"], SourceCell, f"{path}.cells"),
            issues=_parse_list(data["issues"], ParseIssue, f"{path}.issues"),
        )


@dataclass
class Page(SerializableContract):
    page_id: str
    report_id: str
    page_index: int
    page_number: int
    source_span: SourceSpan
    content_span: SourceSpan
    raw_text: str
    tables: List[SourceTable]
    issues: List[ParseIssue]

    def __post_init__(self) -> None:
        self.page_id = _require_string(self.page_id, "page_id", non_empty=True)
        self.report_id = _require_string(self.report_id, "report_id", non_empty=True)
        self.page_index = _require_int(self.page_index, "page_index")
        self.page_number = _require_int(self.page_number, "page_number", minimum=1)
        if not isinstance(self.source_span, SourceSpan) or not isinstance(self.content_span, SourceSpan):
            raise SchemaValidationError("page spans must be SourceSpan values")
        if self.content_span.start < self.source_span.start or self.content_span.end > self.source_span.end:
            raise SchemaValidationError("content_span must be contained in source_span")
        self.raw_text = _require_string(self.raw_text, "raw_text")
        self.tables = _require_instance_list(self.tables, SourceTable, "tables")
        self.issues = _require_instance_list(self.issues, ParseIssue, "issues")
        if any(table.page_id != self.page_id or table.report_id != self.report_id for table in self.tables):
            raise SchemaValidationError("table references must match their page and report")

    @classmethod
    def from_dict(cls, value: Any, path: str = "Page") -> Page:
        data = _require_mapping(value, path)
        _require_exact_keys(data, {field.name for field in fields(cls)}, path)
        return cls(
            page_id=data["page_id"], report_id=data["report_id"], page_index=data["page_index"], page_number=data["page_number"],
            source_span=SourceSpan.from_dict(data["source_span"], f"{path}.source_span"),
            content_span=SourceSpan.from_dict(data["content_span"], f"{path}.content_span"),
            raw_text=data["raw_text"], tables=_parse_list(data["tables"], SourceTable, f"{path}.tables"),
            issues=_parse_list(data["issues"], ParseIssue, f"{path}.issues"),
        )


@dataclass
class ParsedDocument(SerializableContract):
    report_id: str
    content_sha256: str
    pages: List[Page]
    issues: List[ParseIssue]

    def __post_init__(self) -> None:
        self.report_id = _require_string(self.report_id, "report_id", non_empty=True)
        self.content_sha256 = _require_string(self.content_sha256, "content_sha256", non_empty=True)
        if not re.fullmatch(r"[0-9a-f]{64}", self.content_sha256):
            raise SchemaValidationError("content_sha256 must be lowercase SHA-256 hex")
        self.pages = _require_instance_list(self.pages, Page, "pages")
        self.issues = _require_instance_list(self.issues, ParseIssue, "issues")
        if any(page.report_id != self.report_id for page in self.pages):
            raise SchemaValidationError("every page.report_id must match report_id")
        if [page.page_index for page in self.pages] != list(range(len(self.pages))):
            raise SchemaValidationError("pages must use contiguous source-order page_index values")

    @classmethod
    def from_dict(cls, value: Any, path: str = "ParsedDocument") -> ParsedDocument:
        data = _require_mapping(value, path)
        _require_exact_keys(data, {field.name for field in fields(cls)}, path)
        return cls(
            report_id=data["report_id"], content_sha256=data["content_sha256"],
            pages=_parse_list(data["pages"], Page, f"{path}.pages"),
            issues=_parse_list(data["issues"], ParseIssue, f"{path}.issues"),
        )


@dataclass
class MergedCellAnchor(SerializableContract):
    source_cell_id: str
    anchor_row: int
    anchor_column: int
    rowspan: int
    colspan: int

    def __post_init__(self) -> None:
        self.source_cell_id = _require_string(self.source_cell_id, "source_cell_id", non_empty=True)
        self.anchor_row = _require_int(self.anchor_row, "anchor_row")
        self.anchor_column = _require_int(self.anchor_column, "anchor_column")
        self.rowspan = _require_int(self.rowspan, "rowspan", minimum=1)
        self.colspan = _require_int(self.colspan, "colspan", minimum=1)

    @classmethod
    def from_dict(cls, value: Any, path: str = "MergedCellAnchor") -> MergedCellAnchor:
        data = _require_mapping(value, path)
        _require_exact_keys(data, {field.name for field in fields(cls)}, path)
        return cls(**data)


@dataclass
class LogicalTableGrid(SerializableContract):
    row_count: int
    column_count: int
    anchors: List[MergedCellAnchor]
    slots: List[List[Optional[str]]]

    def __post_init__(self) -> None:
        self.row_count = _require_int(self.row_count, "row_count")
        self.column_count = _require_int(self.column_count, "column_count")
        self.anchors = _require_instance_list(self.anchors, MergedCellAnchor, "anchors")
        rows = _require_list(self.slots, "slots")
        if len(rows) != self.row_count:
            raise SchemaValidationError("slots row count must equal row_count")
        normalized_slots: List[List[Optional[str]]] = []
        for row_index, row in enumerate(rows):
            row_values = _require_list(row, f"slots[{row_index}]")
            if len(row_values) != self.column_count:
                raise SchemaValidationError("every slot row must equal column_count")
            normalized_slots.append([
                None if item is None else _require_string(item, f"slots[{row_index}][{column_index}]", non_empty=True)
                for column_index, item in enumerate(row_values)
            ])
        self.slots = normalized_slots
        anchor_ids = [anchor.source_cell_id for anchor in self.anchors]
        if len(anchor_ids) != len(set(anchor_ids)):
            raise SchemaValidationError("anchor source_cell_id values must be unique")
        anchor_set = set(anchor_ids)
        for row in self.slots:
            if any(item is not None and item not in anchor_set for item in row):
                raise SchemaValidationError("every non-null slot must reference an anchor")
        for anchor in self.anchors:
            if anchor.anchor_row + anchor.rowspan > self.row_count or anchor.anchor_column + anchor.colspan > self.column_count:
                raise SchemaValidationError("anchor span must fit inside the grid")
            for row_index in range(anchor.anchor_row, anchor.anchor_row + anchor.rowspan):
                for column_index in range(anchor.anchor_column, anchor.anchor_column + anchor.colspan):
                    if self.slots[row_index][column_index] != anchor.source_cell_id:
                        raise SchemaValidationError("every covered slot must reference its anchor")

    @classmethod
    def from_dict(cls, value: Any, path: str = "LogicalTableGrid") -> LogicalTableGrid:
        data = _require_mapping(value, path)
        _require_exact_keys(data, {field.name for field in fields(cls)}, path)
        return cls(
            row_count=data["row_count"], column_count=data["column_count"],
            anchors=_parse_list(data["anchors"], MergedCellAnchor, f"{path}.anchors"), slots=data["slots"],
        )


@dataclass
class HeaderNode(SerializableContract):
    header_id: str
    axis: HeaderAxis
    label: str
    source_cell_ids: List[str]
    parent_header_id: Optional[str]
    depth: int
    resolution: HeaderResolution

    def __post_init__(self) -> None:
        self.header_id = _require_string(self.header_id, "header_id", non_empty=True)
        self.axis = _require_enum(self.axis, HeaderAxis, "axis")
        self.label = _require_string(self.label, "label")
        self.source_cell_ids = _require_string_list(self.source_cell_ids, "source_cell_ids")
        if not self.source_cell_ids:
            raise SchemaValidationError("source_cell_ids must be non-empty")
        self.parent_header_id = _require_optional_string(self.parent_header_id, "parent_header_id")
        self.depth = _require_int(self.depth, "depth")
        self.resolution = _require_enum(self.resolution, HeaderResolution, "resolution")

    @classmethod
    def from_dict(cls, value: Any, path: str = "HeaderNode") -> HeaderNode:
        data = _require_mapping(value, path)
        _require_exact_keys(data, {field.name for field in fields(cls)}, path)
        return cls(
            header_id=data["header_id"], axis=_parse_enum(data["axis"], HeaderAxis, f"{path}.axis"),
            label=data["label"], source_cell_ids=data["source_cell_ids"], parent_header_id=data["parent_header_id"],
            depth=data["depth"], resolution=_parse_enum(data["resolution"], HeaderResolution, f"{path}.resolution"),
        )


@dataclass
class HeaderHierarchy(SerializableContract):
    nodes: List[HeaderNode]

    def __post_init__(self) -> None:
        self.nodes = _require_instance_list(self.nodes, HeaderNode, "nodes")
        by_id = {node.header_id: node for node in self.nodes}
        if len(by_id) != len(self.nodes):
            raise SchemaValidationError("header_id values must be unique")
        for node in self.nodes:
            if node.parent_header_id is None:
                continue
            parent = by_id.get(node.parent_header_id)
            if parent is None:
                raise SchemaValidationError("parent_header_id must reference a header node")
            if parent.axis is not node.axis or parent.depth + 1 != node.depth:
                raise SchemaValidationError("header parent must share axis and precede child depth")

    @classmethod
    def from_dict(cls, value: Any, path: str = "HeaderHierarchy") -> HeaderHierarchy:
        data = _require_mapping(value, path)
        _require_exact_keys(data, {"nodes"}, path)
        return cls(nodes=_parse_list(data["nodes"], HeaderNode, f"{path}.nodes"))


_CANONICAL_DECIMAL = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?\Z")


@dataclass
class NumericParseResult(SerializableContract):
    status: NumericParseStatus
    raw_text: str
    normalized_lexeme: Optional[str]
    decimal_value: Optional[str]
    percent_literal: bool

    def __post_init__(self) -> None:
        self.status = _require_enum(self.status, NumericParseStatus, "status")
        self.raw_text = _require_string(self.raw_text, "raw_text")
        self.normalized_lexeme = _require_optional_string(self.normalized_lexeme, "normalized_lexeme")
        self.decimal_value = _require_optional_string(self.decimal_value, "decimal_value")
        self.percent_literal = _require_bool(self.percent_literal, "percent_literal")
        if self.status is NumericParseStatus.PARSED:
            if self.normalized_lexeme is None or self.decimal_value is None:
                raise SchemaValidationError("PARSED requires normalized_lexeme and decimal_value")
            if not _CANONICAL_DECIMAL.fullmatch(self.decimal_value):
                raise SchemaValidationError("decimal_value must be a canonical base-10 decimal string")
        elif self.normalized_lexeme is not None or self.decimal_value is not None:
            raise SchemaValidationError("non-PARSED results must not contain normalized numeric values")

    @classmethod
    def from_dict(cls, value: Any, path: str = "NumericParseResult") -> NumericParseResult:
        data = _require_mapping(value, path)
        _require_exact_keys(data, {field.name for field in fields(cls)}, path)
        return cls(
            status=_parse_enum(data["status"], NumericParseStatus, f"{path}.status"), raw_text=data["raw_text"],
            normalized_lexeme=data["normalized_lexeme"], decimal_value=data["decimal_value"], percent_literal=data["percent_literal"],
        )


@dataclass
class NormalizedCell(SerializableContract):
    normalized_cell_id: str
    source_cell_id: str
    table_id: str
    anchor_row: int
    anchor_column: int
    rowspan: int
    colspan: int
    normalized_text: str
    role: CellRole
    numeric: NumericParseResult
    row_path: List[HeaderPathEntry]
    column_path: List[HeaderPathEntry]
    issues: List[ParseIssue]

    def __post_init__(self) -> None:
        self.normalized_cell_id = _require_string(self.normalized_cell_id, "normalized_cell_id", non_empty=True)
        self.source_cell_id = _require_string(self.source_cell_id, "source_cell_id", non_empty=True)
        self.table_id = _require_string(self.table_id, "table_id", non_empty=True)
        self.anchor_row = _require_int(self.anchor_row, "anchor_row")
        self.anchor_column = _require_int(self.anchor_column, "anchor_column")
        self.rowspan = _require_int(self.rowspan, "rowspan", minimum=1)
        self.colspan = _require_int(self.colspan, "colspan", minimum=1)
        self.normalized_text = _require_string(self.normalized_text, "normalized_text")
        self.role = _require_enum(self.role, CellRole, "role")
        if not isinstance(self.numeric, NumericParseResult):
            raise SchemaValidationError("numeric must be a NumericParseResult")
        self.row_path = _require_instance_list(self.row_path, HeaderPathEntry, "row_path")
        self.column_path = _require_instance_list(self.column_path, HeaderPathEntry, "column_path")
        self.issues = _require_instance_list(self.issues, ParseIssue, "issues")

    @classmethod
    def from_dict(cls, value: Any, path: str = "NormalizedCell") -> NormalizedCell:
        data = _require_mapping(value, path)
        _require_exact_keys(data, {field.name for field in fields(cls)}, path)
        return cls(
            normalized_cell_id=data["normalized_cell_id"], source_cell_id=data["source_cell_id"], table_id=data["table_id"],
            anchor_row=data["anchor_row"], anchor_column=data["anchor_column"], rowspan=data["rowspan"], colspan=data["colspan"],
            normalized_text=data["normalized_text"], role=_parse_enum(data["role"], CellRole, f"{path}.role"),
            numeric=NumericParseResult.from_dict(data["numeric"], f"{path}.numeric"),
            row_path=_parse_list(data["row_path"], HeaderPathEntry, f"{path}.row_path"),
            column_path=_parse_list(data["column_path"], HeaderPathEntry, f"{path}.column_path"),
            issues=_parse_list(data["issues"], ParseIssue, f"{path}.issues"),
        )


@dataclass
class NormalizedTable(SerializableContract):
    normalized_table_id: str
    source_table_id: str
    report_id: str
    page_id: str
    normalization_version: str
    grid: LogicalTableGrid
    header_hierarchy: HeaderHierarchy
    cells: List[NormalizedCell]
    period_labels: List[str]
    issues: List[ParseIssue]

    def __post_init__(self) -> None:
        self.normalized_table_id = _require_string(self.normalized_table_id, "normalized_table_id", non_empty=True)
        self.source_table_id = _require_string(self.source_table_id, "source_table_id", non_empty=True)
        self.report_id = _require_string(self.report_id, "report_id", non_empty=True)
        self.page_id = _require_string(self.page_id, "page_id", non_empty=True)
        if self.normalization_version != NORMALIZATION_VERSION:
            raise SchemaValidationError(f"normalization_version must be {NORMALIZATION_VERSION}")
        if not isinstance(self.grid, LogicalTableGrid) or not isinstance(self.header_hierarchy, HeaderHierarchy):
            raise SchemaValidationError("grid and header_hierarchy must use canonical contracts")
        self.cells = _require_instance_list(self.cells, NormalizedCell, "cells")
        self.period_labels = _require_string_list(self.period_labels, "period_labels")
        self.issues = _require_instance_list(self.issues, ParseIssue, "issues")
        if any(cell.table_id != self.source_table_id for cell in self.cells):
            raise SchemaValidationError("every normalized cell must reference source_table_id")
        source_cell_ids = [cell.source_cell_id for cell in self.cells]
        if len(source_cell_ids) != len(set(source_cell_ids)):
            raise SchemaValidationError("normalized cells must reference unique source cells")

    @classmethod
    def from_dict(cls, value: Any, path: str = "NormalizedTable") -> NormalizedTable:
        data = _require_mapping(value, path)
        _require_exact_keys(data, {field.name for field in fields(cls)}, path)
        return cls(
            normalized_table_id=data["normalized_table_id"], source_table_id=data["source_table_id"],
            report_id=data["report_id"], page_id=data["page_id"], normalization_version=data["normalization_version"],
            grid=LogicalTableGrid.from_dict(data["grid"], f"{path}.grid"),
            header_hierarchy=HeaderHierarchy.from_dict(data["header_hierarchy"], f"{path}.header_hierarchy"),
            cells=_parse_list(data["cells"], NormalizedCell, f"{path}.cells"), period_labels=data["period_labels"],
            issues=_parse_list(data["issues"], ParseIssue, f"{path}.issues"),
        )


@dataclass
class Paragraph(SerializableContract):
    paragraph_id: str
    report_id: str
    page_id: str
    paragraph_index: int
    source_span: SourceSpan
    raw_text: str
    normalized_text: str
    section_ref: Optional[str]
    kind: ParagraphKind

    def __post_init__(self) -> None:
        self.paragraph_id = _require_string(self.paragraph_id, "paragraph_id", non_empty=True)
        self.report_id = _require_string(self.report_id, "report_id", non_empty=True)
        self.page_id = _require_string(self.page_id, "page_id", non_empty=True)
        self.paragraph_index = _require_int(self.paragraph_index, "paragraph_index")
        if not isinstance(self.source_span, SourceSpan):
            raise SchemaValidationError("source_span must be a SourceSpan")
        self.raw_text = _require_string(self.raw_text, "raw_text", non_empty=True)
        self.normalized_text = _require_string(self.normalized_text, "normalized_text", non_empty=True)
        self.section_ref = _require_optional_string(self.section_ref, "section_ref")
        self.kind = _require_enum(self.kind, ParagraphKind, "kind")

    @classmethod
    def from_dict(cls, value: Any, path: str = "Paragraph") -> Paragraph:
        data = _require_mapping(value, path)
        _require_exact_keys(data, {field.name for field in fields(cls)}, path)
        return cls(
            paragraph_id=data["paragraph_id"], report_id=data["report_id"], page_id=data["page_id"],
            paragraph_index=data["paragraph_index"], source_span=SourceSpan.from_dict(data["source_span"], f"{path}.source_span"),
            raw_text=data["raw_text"], normalized_text=data["normalized_text"], section_ref=data["section_ref"],
            kind=_parse_enum(data["kind"], ParagraphKind, f"{path}.kind"),
        )


@dataclass
class TableTextLink(SerializableContract):
    link_id: str
    table_id: str
    paragraph_id: str
    relation: TableTextRelation
    basis: TableTextLinkBasis
    evidence_span: SourceSpan
    issues: List[ParseIssue]

    def __post_init__(self) -> None:
        self.link_id = _require_string(self.link_id, "link_id", non_empty=True)
        self.table_id = _require_string(self.table_id, "table_id", non_empty=True)
        self.paragraph_id = _require_string(self.paragraph_id, "paragraph_id", non_empty=True)
        self.relation = _require_enum(self.relation, TableTextRelation, "relation")
        self.basis = _require_enum(self.basis, TableTextLinkBasis, "basis")
        if not isinstance(self.evidence_span, SourceSpan):
            raise SchemaValidationError("evidence_span must be a SourceSpan")
        self.issues = _require_instance_list(self.issues, ParseIssue, "issues")

    @classmethod
    def from_dict(cls, value: Any, path: str = "TableTextLink") -> TableTextLink:
        data = _require_mapping(value, path)
        _require_exact_keys(data, {field.name for field in fields(cls)}, path)
        return cls(
            link_id=data["link_id"], table_id=data["table_id"], paragraph_id=data["paragraph_id"],
            relation=_parse_enum(data["relation"], TableTextRelation, f"{path}.relation"),
            basis=_parse_enum(data["basis"], TableTextLinkBasis, f"{path}.basis"),
            evidence_span=SourceSpan.from_dict(data["evidence_span"], f"{path}.evidence_span"),
            issues=_parse_list(data["issues"], ParseIssue, f"{path}.issues"),
        )


@dataclass
class ScaleUnitHint(SerializableContract):
    hint_id: str
    report_id: str
    page_id: str
    table_id: Optional[str]
    source_kind: ScaleHintSource
    source_ref: str
    source_span: SourceSpan
    raw_hint_text: str
    normalized_hint_text: str
    scale_candidate: Optional[Scale]
    unit_candidate: Optional[str]
    status: ScaleHintStatus

    def __post_init__(self) -> None:
        self.hint_id = _require_string(self.hint_id, "hint_id", non_empty=True)
        self.report_id = _require_string(self.report_id, "report_id", non_empty=True)
        self.page_id = _require_string(self.page_id, "page_id", non_empty=True)
        self.table_id = _require_optional_string(self.table_id, "table_id")
        self.source_kind = _require_enum(self.source_kind, ScaleHintSource, "source_kind")
        self.source_ref = _require_string(self.source_ref, "source_ref", non_empty=True)
        if not isinstance(self.source_span, SourceSpan):
            raise SchemaValidationError("source_span must be a SourceSpan")
        self.raw_hint_text = _require_string(self.raw_hint_text, "raw_hint_text", non_empty=True)
        self.normalized_hint_text = _require_string(self.normalized_hint_text, "normalized_hint_text", non_empty=True)
        if self.scale_candidate is not None:
            self.scale_candidate = _require_enum(self.scale_candidate, Scale, "scale_candidate")
            if self.scale_candidate not in {Scale.THOUSAND, Scale.MILLION, Scale.BILLION, Scale.PERCENT}:
                raise SchemaValidationError("scale_candidate must be a supported extracted scale")
        self.unit_candidate = _require_optional_string(self.unit_candidate, "unit_candidate")
        self.status = _require_enum(self.status, ScaleHintStatus, "status")
        if self.status is ScaleHintStatus.EXTRACTED and self.scale_candidate is None and self.unit_candidate is None:
            raise SchemaValidationError("EXTRACTED hints require a scale or unit candidate")

    @classmethod
    def from_dict(cls, value: Any, path: str = "ScaleUnitHint") -> ScaleUnitHint:
        data = _require_mapping(value, path)
        _require_exact_keys(data, {field.name for field in fields(cls)}, path)
        scale = data["scale_candidate"]
        return cls(
            hint_id=data["hint_id"], report_id=data["report_id"], page_id=data["page_id"], table_id=data["table_id"],
            source_kind=_parse_enum(data["source_kind"], ScaleHintSource, f"{path}.source_kind"), source_ref=data["source_ref"],
            source_span=SourceSpan.from_dict(data["source_span"], f"{path}.source_span"),
            raw_hint_text=data["raw_hint_text"], normalized_hint_text=data["normalized_hint_text"],
            scale_candidate=(None if scale is None else _parse_enum(scale, Scale, f"{path}.scale_candidate")),
            unit_candidate=data["unit_candidate"], status=_parse_enum(data["status"], ScaleHintStatus, f"{path}.status"),
        )


def _parse_header_path_groups(value: Any, path: str) -> List[List[HeaderPathEntry]]:
    groups = _require_list(value, path)
    return [
        _parse_list(group, HeaderPathEntry, f"{path}[{index}]")
        for index, group in enumerate(groups)
    ]


@dataclass
class RetrievalRepresentation(SerializableContract):
    representation_id: str
    representation_version: str
    source_type: EvidenceSource
    granularity: RepresentationGranularity
    source_ref: str
    report_id: str
    page_ids: List[str]
    table_id: Optional[str]
    paragraph_id: Optional[str]
    ticker: str
    company_name: Optional[str]
    report_year: int
    statement_scope: Optional[StatementScope]
    period_labels: List[str]
    content: str
    row_paths: List[List[HeaderPathEntry]]
    column_paths: List[List[HeaderPathEntry]]
    linked_source_ids: List[str]
    scale_unit_hint_ids: List[str]

    def __post_init__(self) -> None:
        self.representation_id = _require_string(self.representation_id, "representation_id", non_empty=True)
        if self.representation_version != REPRESENTATION_VERSION:
            raise SchemaValidationError(f"representation_version must be {REPRESENTATION_VERSION}")
        self.source_type = _require_enum(self.source_type, EvidenceSource, "source_type")
        self.granularity = _require_enum(self.granularity, RepresentationGranularity, "granularity")
        self.source_ref = _require_string(self.source_ref, "source_ref", non_empty=True)
        self.report_id = _require_string(self.report_id, "report_id", non_empty=True)
        self.page_ids = _require_string_list(self.page_ids, "page_ids")
        if not self.page_ids:
            raise SchemaValidationError("page_ids must be non-empty")
        self.table_id = _require_optional_string(self.table_id, "table_id")
        self.paragraph_id = _require_optional_string(self.paragraph_id, "paragraph_id")
        self.ticker = _require_string(self.ticker, "ticker", non_empty=True)
        self.company_name = _require_optional_string(self.company_name, "company_name")
        self.report_year = _require_int(self.report_year, "report_year", minimum=1000)
        if self.report_year > 9999:
            raise SchemaValidationError("report_year must be a four-digit year")
        if self.statement_scope is not None:
            self.statement_scope = _require_enum(self.statement_scope, StatementScope, "statement_scope")
        self.period_labels = _require_string_list(self.period_labels, "period_labels")
        self.content = _require_string(self.content, "content", non_empty=True)
        self.row_paths = [
            _require_instance_list(group, HeaderPathEntry, f"row_paths[{index}]")
            for index, group in enumerate(_require_list(self.row_paths, "row_paths"))
        ]
        self.column_paths = [
            _require_instance_list(group, HeaderPathEntry, f"column_paths[{index}]")
            for index, group in enumerate(_require_list(self.column_paths, "column_paths"))
        ]
        self.linked_source_ids = _require_string_list(self.linked_source_ids, "linked_source_ids")
        self.scale_unit_hint_ids = _require_string_list(self.scale_unit_hint_ids, "scale_unit_hint_ids")
        if self.source_type is EvidenceSource.TABLE:
            if self.granularity is not RepresentationGranularity.TABLE or self.table_id is None or self.paragraph_id is not None:
                raise SchemaValidationError("TABLE representations require TABLE granularity and only table_id")
        else:
            if self.granularity is not RepresentationGranularity.PARAGRAPH or self.paragraph_id is None or self.table_id is not None:
                raise SchemaValidationError("TEXT representations require PARAGRAPH granularity and only paragraph_id")
            if self.row_paths or self.column_paths:
                raise SchemaValidationError("TEXT representations must not contain table header paths")

    @classmethod
    def from_dict(cls, value: Any, path: str = "RetrievalRepresentation") -> RetrievalRepresentation:
        data = _require_mapping(value, path)
        _require_exact_keys(data, {field.name for field in fields(cls)}, path)
        scope = data["statement_scope"]
        return cls(
            representation_id=data["representation_id"], representation_version=data["representation_version"],
            source_type=_parse_enum(data["source_type"], EvidenceSource, f"{path}.source_type"),
            granularity=_parse_enum(data["granularity"], RepresentationGranularity, f"{path}.granularity"),
            source_ref=data["source_ref"], report_id=data["report_id"], page_ids=data["page_ids"],
            table_id=data["table_id"], paragraph_id=data["paragraph_id"], ticker=data["ticker"],
            company_name=data["company_name"], report_year=data["report_year"],
            statement_scope=(None if scope is None else _parse_enum(scope, StatementScope, f"{path}.statement_scope")),
            period_labels=data["period_labels"], content=data["content"],
            row_paths=_parse_header_path_groups(data["row_paths"], f"{path}.row_paths"),
            column_paths=_parse_header_path_groups(data["column_paths"], f"{path}.column_paths"),
            linked_source_ids=data["linked_source_ids"], scale_unit_hint_ids=data["scale_unit_hint_ids"],
        )
