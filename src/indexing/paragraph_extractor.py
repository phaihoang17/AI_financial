"""Deterministic TASK-02A paragraph extraction outside table source spans."""

from __future__ import annotations

import re
from typing import Iterable, List, Tuple

from src.indexing.ids import make_paragraph_id
from src.indexing.schemas import (
    Paragraph,
    ParagraphKind,
    ParsedDocument,
    ReportSource,
    SourceSpan,
)
from src.understanding.schemas import SchemaValidationError


_BLANK_BOUNDARY = re.compile(r"(?:\r\n|\r|\n)[ \t]*(?:\r\n|\r|\n)+")


def _trim_whitespace(text: str, start: int, end: int) -> Tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _paragraph_spans(text: str, start: int, end: int) -> Iterable[SourceSpan]:
    cursor = start
    for boundary in _BLANK_BOUNDARY.finditer(text, start, end):
        paragraph_start, paragraph_end = _trim_whitespace(
            text, cursor, boundary.start()
        )
        if paragraph_start < paragraph_end:
            yield SourceSpan(paragraph_start, paragraph_end)
        cursor = boundary.end()
    paragraph_start, paragraph_end = _trim_whitespace(text, cursor, end)
    if paragraph_start < paragraph_end:
        yield SourceSpan(paragraph_start, paragraph_end)


def extract_paragraphs(
    source: ReportSource, document: ParsedDocument
) -> List[Paragraph]:
    """Extract page-local paragraphs while preserving exact raw source slices."""

    if not isinstance(source, ReportSource):
        raise SchemaValidationError("source must be a ReportSource")
    if not isinstance(document, ParsedDocument):
        raise SchemaValidationError("document must be a ParsedDocument")
    if document.report_id != source.report_id or document.content_sha256 != source.content_sha256:
        raise SchemaValidationError("document must originate from source")

    paragraphs: List[Paragraph] = []
    for page in document.pages:
        excluded = sorted(
            (table.source_span for table in page.tables),
            key=lambda span: (span.start, span.end),
        )
        cursor = page.content_span.start
        page_spans: List[SourceSpan] = []
        for table_span in excluded:
            if table_span.start < cursor or table_span.end > page.content_span.end:
                raise SchemaValidationError("table spans must be ordered and contained in page content")
            page_spans.extend(
                _paragraph_spans(source.raw_text, cursor, table_span.start)
            )
            cursor = table_span.end
        page_spans.extend(
            _paragraph_spans(source.raw_text, cursor, page.content_span.end)
        )

        for paragraph_index, span in enumerate(page_spans):
            raw_text = source.raw_text[span.start : span.end]
            paragraphs.append(
                Paragraph(
                    paragraph_id=make_paragraph_id(
                        page.page_id, paragraph_index, span
                    ),
                    report_id=source.report_id,
                    page_id=page.page_id,
                    paragraph_index=paragraph_index,
                    source_span=span,
                    raw_text=raw_text,
                    normalized_text=raw_text,
                    section_ref=None,
                    kind=ParagraphKind.OTHER,
                )
            )

    return paragraphs
