"""Deterministic TASK-020 extraction for page-local inline HTML tables."""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import re
from typing import List, Optional, Sequence, Tuple

from src.indexing.ids import make_cell_id, make_page_id, make_table_id
from src.indexing.schemas import (
    IssueSeverity,
    Page,
    ParseIssue,
    ParsedDocument,
    ReportSource,
    SourceCell,
    SourceSpan,
    SourceTable,
    TableParseStatus,
)
from src.understanding.schemas import SchemaValidationError


_PAGE_MARKER = re.compile(
    r"^===== PAGE ([0-9]+) =====[ \t]*(?:\r\n|\r|\n|$)", re.MULTILINE
)
_TABLE_TAG = re.compile(r"<\s*(/?)\s*table\b", re.IGNORECASE)
_BLANK_LINE = re.compile(r"(?:\r\n|\r|\n)[ \t]*(?:\r\n|\r|\n)+")


def _issue(
    code: str,
    severity: IssueSeverity,
    start: Optional[int],
    end: Optional[int],
    message: str,
) -> ParseIssue:
    span = None if start is None or end is None else SourceSpan(start, end)
    return ParseIssue(code=code, severity=severity, source_span=span, message=message)


def _find_tag_end(text: str, start: int, limit: int) -> Optional[int]:
    quote: Optional[str] = None
    index = start + 1
    while index < limit:
        character = text[index]
        if quote is not None:
            if character == quote:
                quote = None
        elif character in {'"', "'"}:
            quote = character
        elif character == ">":
            return index + 1
        index += 1
    return None


def _unclosed_table_end(text: str, start: int, page_end: int) -> int:
    blank = _BLANK_LINE.search(text, start, page_end)
    if blank is not None:
        return blank.start()
    newline_positions = [
        position
        for position in (text.find("\n", start, page_end), text.find("\r", start, page_end))
        if position != -1
    ]
    return min(newline_positions) if newline_positions else page_end


def _find_table_ranges(
    text: str, page_start: int, page_end: int
) -> Tuple[List[Tuple[int, int, bool]], List[ParseIssue]]:
    ranges: List[Tuple[int, int, bool]] = []
    issues: List[ParseIssue] = []
    cursor = page_start

    while cursor < page_end:
        match = _TABLE_TAG.search(text, cursor, page_end)
        if match is None:
            break
        tag_end = _find_tag_end(text, match.start(), page_end)
        if tag_end is None:
            end = _unclosed_table_end(text, match.start(), page_end)
            issues.append(
                _issue(
                    "UNCLOSED_TABLE_START_TAG",
                    IssueSeverity.ERROR,
                    match.start(),
                    end,
                    "table start tag has no closing angle bracket",
                )
            )
            ranges.append((match.start(), end, False))
            cursor = max(end, match.end())
            continue
        if match.group(1):
            issues.append(
                _issue(
                    "UNMATCHED_TABLE_END",
                    IssueSeverity.ERROR,
                    match.start(),
                    tag_end,
                    "table end tag has no page-local start tag",
                )
            )
            cursor = tag_end
            continue

        start = match.start()
        depth = 1
        search_cursor = tag_end
        closed_end: Optional[int] = None
        while search_cursor < page_end:
            nested = _TABLE_TAG.search(text, search_cursor, page_end)
            if nested is None:
                break
            nested_end = _find_tag_end(text, nested.start(), page_end)
            if nested_end is None:
                break
            if nested.group(1):
                depth -= 1
                if depth == 0:
                    closed_end = nested_end
                    break
            else:
                depth += 1
            search_cursor = nested_end

        if closed_end is None:
            end = _unclosed_table_end(text, start, page_end)
            issues.append(
                _issue(
                    "UNCLOSED_TABLE",
                    IssueSeverity.ERROR,
                    start,
                    end,
                    "table has no page-local closing tag",
                )
            )
            ranges.append((start, end, False))
            cursor = max(end, tag_end)
        else:
            ranges.append((start, closed_end, True))
            cursor = closed_end

    return ranges, issues


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _extract_text(raw_inner_html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(raw_inner_html)
        parser.close()
    except Exception:
        return raw_inner_html
    return "".join(parser.parts)


@dataclass
class _OpenCell:
    tag: str
    start: int
    content_start: int
    row_index: int
    cell_index: int
    rowspan: int
    colspan: int


@dataclass
class _OpenCaption:
    start: int
    content_start: int


class _TableStructureParser(HTMLParser):
    def __init__(self, raw_html: str, absolute_start: int, table_id: str) -> None:
        super().__init__(convert_charrefs=False)
        self.raw_html = raw_html
        self.absolute_start = absolute_start
        self.table_id = table_id
        self.line_starts = [0]
        self.line_starts.extend(
            match.end() for match in re.finditer(r"\r\n|\r|\n", raw_html)
        )
        self.cells: List[SourceCell] = []
        self.issues: List[ParseIssue] = []
        self.current_row = -1
        self.current_cell_index = 0
        self.in_row = False
        self.open_cell: Optional[_OpenCell] = None
        self.open_caption: Optional[_OpenCaption] = None
        self.caption_span: Optional[SourceSpan] = None
        self.caption_text: Optional[str] = None
        self.table_depth = 0
        self.fatal_structure = False
        self.recovered = False

    def _position(self) -> int:
        line, column = self.getpos()
        if line < 1 or line > len(self.line_starts):
            return 0
        return self.line_starts[line - 1] + column

    def _add_issue(
        self,
        code: str,
        message: str,
        *,
        fatal: bool,
        start: Optional[int] = None,
        end: Optional[int] = None,
    ) -> None:
        relative_start = self._position() if start is None else start
        relative_end = relative_start + 1 if end is None else end
        self.issues.append(
            _issue(
                code,
                IssueSeverity.ERROR if fatal else IssueSeverity.WARNING,
                self.absolute_start + relative_start,
                self.absolute_start + relative_end,
                message,
            )
        )
        self.fatal_structure = self.fatal_structure or fatal
        self.recovered = self.recovered or not fatal

    def _parse_span(self, attrs: Sequence[Tuple[str, Optional[str]]], name: str) -> Optional[int]:
        values = [value for key, value in attrs if key.lower() == name]
        if not values:
            return 1
        if len(values) != 1 or values[0] is None or not values[0].strip().isdigit():
            self._add_issue(
                "INVALID_CELL_SPAN",
                f"{name} must be one positive integer",
                fatal=True,
            )
            return None
        parsed = int(values[0].strip())
        if parsed < 1:
            self._add_issue(
                "INVALID_CELL_SPAN",
                f"{name} must be positive",
                fatal=True,
            )
            return None
        return parsed

    def handle_starttag(
        self, tag: str, attrs: List[Tuple[str, Optional[str]]]
    ) -> None:
        tag = tag.lower()
        position = self._position()
        raw_tag = self.get_starttag_text() or ""
        tag_end = position + len(raw_tag)

        if tag == "table":
            self.table_depth += 1
            if self.table_depth > 1:
                self._add_issue(
                    "NESTED_TABLE",
                    "nested tables are not supported by M2A v1",
                    fatal=True,
                    start=position,
                    end=tag_end,
                )
            return

        if tag == "tr":
            if self.in_row or self.open_cell is not None:
                self._add_issue("NESTED_ROW", "row started before prior row closed", fatal=True, start=position, end=tag_end)
            self.current_row += 1
            self.current_cell_index = 0
            self.in_row = True
            return

        if tag in {"td", "th"}:
            if not self.in_row or self.open_cell is not None:
                self._add_issue("INVALID_CELL_NESTING", "cell is not uniquely nested in one row", fatal=True, start=position, end=tag_end)
                return
            rowspan = self._parse_span(attrs, "rowspan")
            colspan = self._parse_span(attrs, "colspan")
            if rowspan is None or colspan is None:
                return
            self.open_cell = _OpenCell(
                tag=tag,
                start=position,
                content_start=tag_end,
                row_index=self.current_row,
                cell_index=self.current_cell_index,
                rowspan=rowspan,
                colspan=colspan,
            )
            self.current_cell_index += 1
            return

        if tag == "caption":
            if self.open_caption is not None or self.caption_span is not None:
                self._add_issue("MULTIPLE_CAPTIONS", "table caption is not unique", fatal=True, start=position, end=tag_end)
                return
            self.open_caption = _OpenCaption(start=position, content_start=tag_end)

    def handle_startendtag(
        self, tag: str, attrs: List[Tuple[str, Optional[str]]]
    ) -> None:
        if tag.lower() in {"table", "tr", "td", "th", "caption"}:
            position = self._position()
            raw_tag = self.get_starttag_text() or ""
            self._add_issue(
                "SELF_CLOSING_TABLE_STRUCTURE",
                f"self-closing {tag} is not a recoverable table structure",
                fatal=True,
                start=position,
                end=position + len(raw_tag),
            )

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        position = self._position()
        tag_end = _find_tag_end(self.raw_html, position, len(self.raw_html))
        tag_end = position + len(f"</{tag}>") if tag_end is None else tag_end

        if tag == "table":
            self.table_depth -= 1
            if self.table_depth < 0:
                self._add_issue("UNMATCHED_TABLE_END", "unexpected table end tag", fatal=True, start=position, end=tag_end)
            return

        if tag == "tr":
            if not self.in_row or self.open_cell is not None:
                self._add_issue("INVALID_ROW_END", "row closed without a complete open row", fatal=True, start=position, end=tag_end)
            self.in_row = False
            return

        if tag in {"td", "th"}:
            cell = self.open_cell
            if cell is None or cell.tag != tag:
                self._add_issue("UNMATCHED_CELL_END", "cell end tag has no matching start", fatal=True, start=position, end=tag_end)
                return
            raw_html = self.raw_html[cell.start:tag_end]
            raw_inner = self.raw_html[cell.content_start:position]
            absolute_span = SourceSpan(
                self.absolute_start + cell.start,
                self.absolute_start + tag_end,
            )
            self.cells.append(
                SourceCell(
                    cell_id=make_cell_id(
                        self.table_id,
                        cell.row_index,
                        cell.cell_index,
                        absolute_span,
                    ),
                    table_id=self.table_id,
                    source_row_index=cell.row_index,
                    source_cell_index=cell.cell_index,
                    source_span=absolute_span,
                    raw_html=raw_html,
                    raw_inner_html=raw_inner,
                    extracted_text=_extract_text(raw_inner),
                    rowspan=cell.rowspan,
                    colspan=cell.colspan,
                )
            )
            self.open_cell = None
            return

        if tag == "caption":
            caption = self.open_caption
            if caption is None:
                self._add_issue("UNMATCHED_CAPTION_END", "caption end tag has no matching start", fatal=True, start=position, end=tag_end)
                return
            raw_inner = self.raw_html[caption.content_start:position]
            self.caption_span = SourceSpan(
                self.absolute_start + caption.start,
                self.absolute_start + tag_end,
            )
            self.caption_text = _extract_text(raw_inner)
            self.open_caption = None

    def finish(self) -> None:
        if self.open_cell is not None:
            self._add_issue("UNCLOSED_CELL", "cell has no closing tag", fatal=True, start=self.open_cell.start, end=len(self.raw_html))
        if self.in_row:
            self._add_issue("UNCLOSED_ROW", "row has no closing tag", fatal=True, start=len(self.raw_html) - 1, end=len(self.raw_html))
        if self.open_caption is not None:
            self._add_issue("UNCLOSED_CAPTION", "caption has no closing tag", fatal=True, start=self.open_caption.start, end=len(self.raw_html))
        if self.table_depth != 0:
            self._add_issue("UNBALANCED_TABLE", "table tags are not balanced", fatal=True, start=max(len(self.raw_html) - 1, 0), end=len(self.raw_html))


def _parse_source_table(
    raw_text: str,
    report_id: str,
    page_id: str,
    table_index: int,
    start: int,
    end: int,
    closed: bool,
) -> SourceTable:
    source_span = SourceSpan(start, end)
    table_id = make_table_id(page_id, table_index, source_span)
    raw_html = raw_text[start:end]
    if not closed:
        return SourceTable(
            table_id=table_id,
            report_id=report_id,
            page_id=page_id,
            table_index=table_index,
            source_span=source_span,
            raw_html=raw_html,
            parse_status=TableParseStatus.UNPARSEABLE,
            inline_caption_span=None,
            inline_caption_text=None,
            cells=[],
            issues=[
                _issue(
                    "UNCLOSED_TABLE",
                    IssueSeverity.ERROR,
                    start,
                    end,
                    "table has no page-local closing tag",
                )
            ],
        )

    parser = _TableStructureParser(raw_html, start, table_id)
    try:
        parser.feed(raw_html)
        parser.close()
        parser.finish()
    except Exception as error:
        return SourceTable(
            table_id=table_id,
            report_id=report_id,
            page_id=page_id,
            table_index=table_index,
            source_span=source_span,
            raw_html=raw_html,
            parse_status=TableParseStatus.UNPARSEABLE,
            inline_caption_span=None,
            inline_caption_text=None,
            cells=[],
            issues=[
                _issue(
                    "HTML_PARSE_FAILURE",
                    IssueSeverity.ERROR,
                    start,
                    end,
                    f"table parser failed: {type(error).__name__}",
                )
            ],
        )

    if parser.fatal_structure:
        status = TableParseStatus.UNPARSEABLE
        cells: List[SourceCell] = []
        caption_span = None
        caption_text = None
    else:
        status = TableParseStatus.RECOVERED if parser.recovered else TableParseStatus.VALID
        cells = parser.cells
        caption_span = parser.caption_span
        caption_text = parser.caption_text

    return SourceTable(
        table_id=table_id,
        report_id=report_id,
        page_id=page_id,
        table_index=table_index,
        source_span=source_span,
        raw_html=raw_html,
        parse_status=status,
        inline_caption_span=caption_span,
        inline_caption_text=caption_text,
        cells=cells,
        issues=parser.issues,
    )


def parse_document(source: ReportSource) -> ParsedDocument:
    """Extract page-local raw table/cell structures without normalization."""

    if not isinstance(source, ReportSource):
        raise SchemaValidationError("source must be a ReportSource")

    raw_text = source.raw_text
    marker_matches = list(_PAGE_MARKER.finditer(raw_text))
    document_issues: List[ParseIssue] = []
    if not marker_matches:
        document_issues.append(
            _issue(
                "FATAL_NO_PAGE_MARKERS",
                IssueSeverity.FATAL,
                0,
                len(raw_text),
                "report contains no canonical page markers",
            )
        )
        return ParsedDocument(
            report_id=source.report_id,
            content_sha256=source.content_sha256,
            pages=[],
            issues=document_issues,
        )

    if raw_text[: marker_matches[0].start()].strip():
        document_issues.append(
            _issue(
                "TEXT_BEFORE_FIRST_PAGE_MARKER",
                IssueSeverity.WARNING,
                0,
                marker_matches[0].start(),
                "non-whitespace text precedes the first page marker",
            )
        )

    pages: List[Page] = []
    seen_numbers = set()
    previous_number: Optional[int] = None
    for marker_index, marker in enumerate(marker_matches):
        page_number = int(marker.group(1))
        source_end = (
            marker_matches[marker_index + 1].start()
            if marker_index + 1 < len(marker_matches)
            else len(raw_text)
        )
        if page_number < 1:
            document_issues.append(
                _issue(
                    "INVALID_PAGE_NUMBER",
                    IssueSeverity.ERROR,
                    marker.start(),
                    marker.end(),
                    "page number must be positive",
                )
            )
            continue

        source_span = SourceSpan(marker.start(), source_end)
        content_span = SourceSpan(marker.end(), source_end)
        page_index = len(pages)
        page_id = make_page_id(
            source.report_id,
            source.content_sha256,
            page_index,
            source_span,
        )
        page_issues: List[ParseIssue] = []
        if page_number in seen_numbers:
            page_issues.append(
                _issue(
                    "DUPLICATE_PAGE_NUMBER",
                    IssueSeverity.WARNING,
                    marker.start(),
                    marker.end(),
                    "page number was already used earlier in the report",
                )
            )
        if previous_number is not None and page_number <= previous_number:
            page_issues.append(
                _issue(
                    "NON_MONOTONIC_PAGE_NUMBER",
                    IssueSeverity.WARNING,
                    marker.start(),
                    marker.end(),
                    "page number does not increase in source order",
                )
            )
        seen_numbers.add(page_number)
        previous_number = page_number

        table_ranges, discovery_issues = _find_table_ranges(
            raw_text, content_span.start, content_span.end
        )
        page_issues.extend(discovery_issues)
        tables = [
            _parse_source_table(
                raw_text,
                source.report_id,
                page_id,
                table_index,
                start,
                end,
                closed,
            )
            for table_index, (start, end, closed) in enumerate(table_ranges)
        ]
        pages.append(
            Page(
                page_id=page_id,
                report_id=source.report_id,
                page_index=page_index,
                page_number=page_number,
                source_span=source_span,
                content_span=content_span,
                raw_text=raw_text[source_span.start : source_span.end],
                tables=tables,
                issues=page_issues,
            )
        )

    return ParsedDocument(
        report_id=source.report_id,
        content_sha256=source.content_sha256,
        pages=pages,
        issues=document_issues,
    )
