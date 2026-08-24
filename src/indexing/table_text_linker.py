"""Deterministic one-to-one immediate-adjacency links for TASK-02B."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

from src.indexing.ids import make_table_text_link_id
from src.indexing.schemas import (
    Paragraph,
    ParsedDocument,
    ReportSource,
    SourceTable,
    TableTextLink,
    TableTextLinkBasis,
    TableTextRelation,
)
from src.understanding.schemas import SchemaValidationError


@dataclass(frozen=True)
class _AdjacencyCandidate:
    table: SourceTable
    paragraph: Paragraph
    basis: TableTextLinkBasis


def _validate_inputs(
    source: ReportSource,
    document: ParsedDocument,
    paragraphs: List[Paragraph],
) -> None:
    if not isinstance(source, ReportSource):
        raise SchemaValidationError("source must be a ReportSource")
    if not isinstance(document, ParsedDocument):
        raise SchemaValidationError("document must be a ParsedDocument")
    if document.report_id != source.report_id or document.content_sha256 != source.content_sha256:
        raise SchemaValidationError("document must originate from source")
    page_ids = {page.page_id for page in document.pages}
    for paragraph in paragraphs:
        if not isinstance(paragraph, Paragraph):
            raise SchemaValidationError("paragraphs must contain Paragraph values")
        if paragraph.report_id != source.report_id or paragraph.page_id not in page_ids:
            raise SchemaValidationError("paragraph provenance must match source document")
        if source.raw_text[paragraph.source_span.start : paragraph.source_span.end] != paragraph.raw_text:
            raise SchemaValidationError("paragraph span must round-trip to raw source")


def _whitespace_only(value: str) -> bool:
    return not value or value.isspace()


def _candidates(
    source: ReportSource,
    document: ParsedDocument,
    paragraphs: List[Paragraph],
) -> List[_AdjacencyCandidate]:
    paragraphs_by_page: Dict[str, List[Paragraph]] = {}
    for paragraph in paragraphs:
        paragraphs_by_page.setdefault(paragraph.page_id, []).append(paragraph)

    candidates: List[_AdjacencyCandidate] = []
    for page in document.pages:
        page_paragraphs = paragraphs_by_page.get(page.page_id, [])
        for table in page.tables:
            for paragraph in page_paragraphs:
                if paragraph.source_span.end <= table.source_span.start:
                    gap = source.raw_text[
                        paragraph.source_span.end : table.source_span.start
                    ]
                    basis = TableTextLinkBasis.IMMEDIATE_BEFORE
                elif table.source_span.end <= paragraph.source_span.start:
                    gap = source.raw_text[
                        table.source_span.end : paragraph.source_span.start
                    ]
                    basis = TableTextLinkBasis.IMMEDIATE_AFTER
                else:
                    continue
                if _whitespace_only(gap):
                    candidates.append(_AdjacencyCandidate(table, paragraph, basis))
    return candidates


def link_tables_to_paragraphs(
    source: ReportSource,
    document: ParsedDocument,
    paragraphs: List[Paragraph],
) -> List[TableTextLink]:
    """Emit only adjacency pairs unique for both table and paragraph."""

    _validate_inputs(source, document, paragraphs)
    candidates = _candidates(source, document, paragraphs)
    table_counts: Dict[str, int] = {}
    paragraph_counts: Dict[str, int] = {}
    for candidate in candidates:
        table_counts[candidate.table.table_id] = table_counts.get(candidate.table.table_id, 0) + 1
        paragraph_counts[candidate.paragraph.paragraph_id] = paragraph_counts.get(candidate.paragraph.paragraph_id, 0) + 1

    emitted = [
        candidate
        for candidate in candidates
        if table_counts[candidate.table.table_id] == 1
        and paragraph_counts[candidate.paragraph.paragraph_id] == 1
    ]
    emitted.sort(
        key=lambda candidate: (
            candidate.table.source_span.start,
            candidate.paragraph.source_span.start,
        )
    )
    links: List[TableTextLink] = []
    for candidate in emitted:
        relation = TableTextRelation.ADJACENT_CONTEXT
        links.append(
            TableTextLink(
                link_id=make_table_text_link_id(
                    candidate.table.table_id,
                    candidate.paragraph.paragraph_id,
                    relation.value,
                    candidate.basis.value,
                ),
                table_id=candidate.table.table_id,
                paragraph_id=candidate.paragraph.paragraph_id,
                relation=relation,
                basis=candidate.basis,
                evidence_span=candidate.paragraph.source_span,
                issues=[],
            )
        )
    return links
