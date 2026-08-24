"""Exact-provenance offline scale hint extraction for TASK-02C."""

from __future__ import annotations

from dataclasses import dataclass
from html import unescape
import re
import unicodedata
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from src.evidence.schemas import Scale
from src.indexing.ids import make_scale_unit_hint_id
from src.indexing.schemas import (
    NormalizedTable,
    Paragraph,
    ParsedDocument,
    ReportSource,
    ScaleHintSource,
    ScaleHintStatus,
    ScaleUnitHint,
    SourceCell,
    SourceSpan,
    SourceTable,
    TableTextLink,
)
from src.understanding.schemas import SchemaValidationError


_ENTITY = re.compile(r"&(?:#[0-9]+|#x[0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]+);")
_SCALE_EXPRESSION = re.compile(
    r"(?P<word>(?<!\w)(?:phần trăm|nghìn|ngàn|triệu|tỷ)(?!\w))"
    r"|(?P<percent>(?<!%)%(?!%))"
)
_SCALE_BY_TEXT = {
    "nghìn": Scale.THOUSAND,
    "ngàn": Scale.THOUSAND,
    "triệu": Scale.MILLION,
    "tỷ": Scale.BILLION,
    "phần trăm": Scale.PERCENT,
    "%": Scale.PERCENT,
}


@dataclass(frozen=True)
class _VisibleChunk:
    raw_text: str
    absolute_start: int


@dataclass(frozen=True)
class _Occurrence:
    source_span: SourceSpan
    raw_text: str
    normalized_text: str
    scale: Scale


def _visible_html_chunks(raw_html: str, absolute_start: int) -> Iterable[_VisibleChunk]:
    cursor = 0
    text_start = 0
    while cursor < len(raw_html):
        if raw_html[cursor] != "<":
            cursor += 1
            continue
        if text_start < cursor:
            yield _VisibleChunk(raw_html[text_start:cursor], absolute_start + text_start)
        quote: Optional[str] = None
        cursor += 1
        while cursor < len(raw_html):
            character = raw_html[cursor]
            if quote is not None:
                if character == quote:
                    quote = None
            elif character in {'"', "'"}:
                quote = character
            elif character == ">":
                cursor += 1
                break
            cursor += 1
        text_start = cursor
    if text_start < len(raw_html):
        yield _VisibleChunk(raw_html[text_start:], absolute_start + text_start)


def _normalized_with_offsets(chunk: _VisibleChunk) -> Tuple[str, List[Tuple[int, int]]]:
    normalized: List[str] = []
    offsets: List[Tuple[int, int]] = []
    raw = chunk.raw_text
    index = 0
    while index < len(raw):
        entity = _ENTITY.match(raw, index)
        if entity is not None:
            end = entity.end()
            decoded = unescape(entity.group(0))
            unit = decoded if decoded != entity.group(0) else entity.group(0)
        else:
            end = index + 1
            while end < len(raw) and unicodedata.combining(raw[end]):
                end += 1
            unit = raw[index:end]
        unit = unicodedata.normalize("NFKC", unit).casefold()
        absolute_range = (chunk.absolute_start + index, chunk.absolute_start + end)
        for character in unit:
            if character.isspace():
                if normalized and normalized[-1] == " ":
                    offsets[-1] = (offsets[-1][0], absolute_range[1])
                else:
                    normalized.append(" ")
                    offsets.append(absolute_range)
            else:
                normalized.append(character)
                offsets.append(absolute_range)
        index = end
    return "".join(normalized), offsets


def _occurrences(source: ReportSource, chunks: Iterable[_VisibleChunk]) -> List[_Occurrence]:
    found: List[_Occurrence] = []
    for chunk in chunks:
        normalized, offsets = _normalized_with_offsets(chunk)
        for match in _SCALE_EXPRESSION.finditer(normalized):
            start = offsets[match.start()][0]
            end = offsets[match.end() - 1][1]
            normalized_text = match.group(0)
            found.append(
                _Occurrence(
                    source_span=SourceSpan(start, end),
                    raw_text=source.raw_text[start:end],
                    normalized_text=normalized_text,
                    scale=_SCALE_BY_TEXT[normalized_text],
                )
            )
    return found


def _cell_chunks(cell: SourceCell) -> Iterable[_VisibleChunk]:
    opening_end = cell.raw_html.find(">") + 1
    if opening_end <= 0 or cell.raw_html[
        opening_end : opening_end + len(cell.raw_inner_html)
    ] != cell.raw_inner_html:
        raise SchemaValidationError("source cell inner HTML must retain exact offsets")
    return _visible_html_chunks(
        cell.raw_inner_html, cell.source_span.start + opening_end
    )


def _make_hint(
    source: ReportSource,
    page_id: str,
    table_id: Optional[str],
    source_kind: ScaleHintSource,
    source_ref: str,
    occurrence: _Occurrence,
) -> ScaleUnitHint:
    return ScaleUnitHint(
        hint_id=make_scale_unit_hint_id(
            source_kind.value,
            source_ref,
            occurrence.source_span,
            occurrence.normalized_text,
        ),
        report_id=source.report_id,
        page_id=page_id,
        table_id=table_id,
        source_kind=source_kind,
        source_ref=source_ref,
        source_span=occurrence.source_span,
        raw_hint_text=occurrence.raw_text,
        normalized_hint_text=occurrence.normalized_text,
        scale_candidate=occurrence.scale,
        unit_candidate=None,
        status=ScaleHintStatus.EXTRACTED,
    )


def extract_scale_unit_hints(
    source: ReportSource,
    document: ParsedDocument,
    normalized_tables: Sequence[NormalizedTable],
    paragraphs: Sequence[Paragraph],
    links: Sequence[TableTextLink],
) -> List[ScaleUnitHint]:
    """Extract occurrences without resolving a final table scale."""

    if not isinstance(source, ReportSource) or not isinstance(document, ParsedDocument):
        raise SchemaValidationError("source and document must use canonical contracts")
    if document.report_id != source.report_id or document.content_sha256 != source.content_sha256:
        raise SchemaValidationError("document must originate from source")

    source_tables = {
        table.table_id: table
        for page in document.pages
        for table in page.tables
    }
    page_by_table = {
        table.table_id: page.page_id
        for page in document.pages
        for table in page.tables
    }
    normalized_by_source: Dict[str, NormalizedTable] = {}
    for table in normalized_tables:
        if not isinstance(table, NormalizedTable) or table.source_table_id not in source_tables:
            raise SchemaValidationError("normalized tables must originate from document tables")
        if table.report_id != source.report_id or table.page_id != page_by_table[table.source_table_id]:
            raise SchemaValidationError("normalized table provenance must match source")
        if table.source_table_id in normalized_by_source:
            raise SchemaValidationError("normalized source table IDs must be unique")
        normalized_by_source[table.source_table_id] = table

    paragraphs_by_id: Dict[str, Paragraph] = {}
    for paragraph in paragraphs:
        if not isinstance(paragraph, Paragraph) or paragraph.report_id != source.report_id:
            raise SchemaValidationError("paragraph provenance must match source")
        if source.raw_text[paragraph.source_span.start:paragraph.source_span.end] != paragraph.raw_text:
            raise SchemaValidationError("paragraph span must round-trip to source")
        paragraphs_by_id[paragraph.paragraph_id] = paragraph

    links_by_paragraph: Dict[str, List[TableTextLink]] = {}
    for link in links:
        if (
            not isinstance(link, TableTextLink)
            or link.table_id not in source_tables
            or link.paragraph_id not in paragraphs_by_id
        ):
            raise SchemaValidationError("links must reference source tables and paragraphs")
        links_by_paragraph.setdefault(link.paragraph_id, []).append(link)

    hints: List[ScaleUnitHint] = []
    for table_id, table in source_tables.items():
        normalized = normalized_by_source.get(table_id)
        header_cell_ids = (
            set()
            if normalized is None
            else {
                source_cell_id
                for node in normalized.header_hierarchy.nodes
                for source_cell_id in node.source_cell_ids
            }
        )
        for cell in table.cells:
            source_kind = (
                ScaleHintSource.HEADER
                if cell.cell_id in header_cell_ids
                else ScaleHintSource.CELL
            )
            for occurrence in _occurrences(source, _cell_chunks(cell)):
                hints.append(
                    _make_hint(
                        source,
                        page_by_table[table_id],
                        table_id,
                        source_kind,
                        cell.cell_id,
                        occurrence,
                    )
                )
        if table.inline_caption_span is not None:
            caption_raw = source.raw_text[
                table.inline_caption_span.start : table.inline_caption_span.end
            ]
            for occurrence in _occurrences(
                source,
                _visible_html_chunks(caption_raw, table.inline_caption_span.start),
            ):
                hints.append(
                    _make_hint(
                        source,
                        page_by_table[table_id],
                        table_id,
                        ScaleHintSource.CAPTION,
                        table_id,
                        occurrence,
                    )
                )

    for paragraph in paragraphs:
        paragraph_links = links_by_paragraph.get(paragraph.paragraph_id, [])
        linked_table_id = paragraph_links[0].table_id if len(paragraph_links) == 1 else None
        chunks = [_VisibleChunk(paragraph.raw_text, paragraph.source_span.start)]
        for occurrence in _occurrences(source, chunks):
            hints.append(
                _make_hint(
                    source,
                    paragraph.page_id,
                    linked_table_id,
                    ScaleHintSource.TEXT,
                    paragraph.paragraph_id,
                    occurrence,
                )
            )

    hints.sort(key=lambda hint: (hint.source_span.start, hint.source_span.end, hint.hint_id))
    hints_by_table: Dict[str, List[ScaleUnitHint]] = {}
    for hint in hints:
        if hint.table_id is not None:
            hints_by_table.setdefault(hint.table_id, []).append(hint)
    for table_hints in hints_by_table.values():
        scales = {hint.scale_candidate for hint in table_hints}
        if len(scales) > 1:
            for hint in table_hints:
                hint.status = ScaleHintStatus.AMBIGUOUS
    return hints
