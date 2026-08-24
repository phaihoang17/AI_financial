"""Model-independent canonical retrieval representations for TASK-027."""

from __future__ import annotations

import json
from typing import Dict, Iterable, List, Sequence, Tuple

from src.indexing.ids import make_representation_id
from src.indexing.schemas import (
    HeaderPathEntry,
    NormalizedTable,
    Paragraph,
    REPRESENTATION_VERSION,
    RepresentationGranularity,
    ReportSource,
    RetrievalRepresentation,
    ScaleHintSource,
    ScaleUnitHint,
    TableTextLink,
)
from src.supervisor.schemas import EvidenceSource
from src.understanding.schemas import SchemaValidationError


def _compact_json(payload: Dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _path_key(path: Sequence[HeaderPathEntry]) -> Tuple[Tuple[str, str], ...]:
    return tuple((entry.header_id, entry.label) for entry in path)


def _deduplicate_paths(
    paths: Iterable[Sequence[HeaderPathEntry]],
) -> List[List[HeaderPathEntry]]:
    seen = set()
    result: List[List[HeaderPathEntry]] = []
    for path in paths:
        if not path:
            continue
        key = _path_key(path)
        if key in seen:
            continue
        seen.add(key)
        result.append(list(path))
    return result


def _statement_scope_value(source: ReportSource) -> str | None:
    return None if source.statement_scope is None else source.statement_scope.value


def _table_content(source: ReportSource, table: NormalizedTable) -> str:
    cells = sorted(table.cells, key=lambda cell: (cell.anchor_row, cell.anchor_column))
    return _compact_json(
        {
            "ticker": source.ticker,
            "company_name": source.company_name,
            "report_year": source.report_year,
            "statement_scope": _statement_scope_value(source),
            "period_labels": list(table.period_labels),
            "cells": [
                {
                    "row_path": [entry.label for entry in cell.row_path],
                    "column_path": [entry.label for entry in cell.column_path],
                    "text": cell.normalized_text,
                }
                for cell in cells
            ],
        }
    )


def _text_content(source: ReportSource, paragraph: Paragraph) -> str:
    return _compact_json(
        {
            "ticker": source.ticker,
            "company_name": source.company_name,
            "report_year": source.report_year,
            "statement_scope": _statement_scope_value(source),
            "section_ref": paragraph.section_ref,
            "kind": paragraph.kind.value,
            "text": paragraph.normalized_text,
        }
    )


def build_retrieval_representations(
    source: ReportSource,
    normalized_tables: Sequence[NormalizedTable],
    paragraphs: Sequence[Paragraph],
    links: Sequence[TableTextLink],
    hints: Sequence[ScaleUnitHint],
) -> List[RetrievalRepresentation]:
    """Build one independent representation per table and paragraph."""

    if not isinstance(source, ReportSource):
        raise SchemaValidationError("source must be a ReportSource")
    tables_by_id: Dict[str, NormalizedTable] = {}
    table_order: Dict[str, int] = {}
    for index, table in enumerate(normalized_tables):
        if not isinstance(table, NormalizedTable) or table.report_id != source.report_id:
            raise SchemaValidationError("normalized table provenance must match source")
        if table.source_table_id in tables_by_id:
            raise SchemaValidationError("source table IDs must be unique")
        tables_by_id[table.source_table_id] = table
        table_order[table.source_table_id] = index

    paragraphs_by_id: Dict[str, Paragraph] = {}
    for paragraph in paragraphs:
        if not isinstance(paragraph, Paragraph) or paragraph.report_id != source.report_id:
            raise SchemaValidationError("paragraph provenance must match source")
        if paragraph.paragraph_id in paragraphs_by_id:
            raise SchemaValidationError("paragraph IDs must be unique")
        paragraphs_by_id[paragraph.paragraph_id] = paragraph

    links_by_table: Dict[str, List[TableTextLink]] = {}
    links_by_paragraph: Dict[str, List[TableTextLink]] = {}
    for link in links:
        if (
            not isinstance(link, TableTextLink)
            or link.table_id not in tables_by_id
            or link.paragraph_id not in paragraphs_by_id
        ):
            raise SchemaValidationError("links must reference represented sources")
        links_by_table.setdefault(link.table_id, []).append(link)
        links_by_paragraph.setdefault(link.paragraph_id, []).append(link)

    hints_by_table: Dict[str, List[ScaleUnitHint]] = {}
    hints_by_paragraph: Dict[str, List[ScaleUnitHint]] = {}
    for hint in hints:
        if not isinstance(hint, ScaleUnitHint) or hint.report_id != source.report_id:
            raise SchemaValidationError("hint provenance must match source")
        if hint.table_id is not None and hint.table_id in tables_by_id:
            hints_by_table.setdefault(hint.table_id, []).append(hint)
        if (
            hint.source_kind is ScaleHintSource.TEXT
            and hint.source_ref in paragraphs_by_id
        ):
            hints_by_paragraph.setdefault(hint.source_ref, []).append(hint)

    representations: List[RetrievalRepresentation] = []
    for table in normalized_tables:
        cells = sorted(table.cells, key=lambda cell: (cell.anchor_row, cell.anchor_column))
        table_links = sorted(
            links_by_table.get(table.source_table_id, []),
            key=lambda link: paragraphs_by_id[link.paragraph_id].source_span.start,
        )
        table_hints = sorted(
            hints_by_table.get(table.source_table_id, []),
            key=lambda hint: (hint.source_span.start, hint.source_span.end, hint.hint_id),
        )
        representations.append(
            RetrievalRepresentation(
                representation_id=make_representation_id(
                    EvidenceSource.TABLE.value,
                    table.normalized_table_id,
                    REPRESENTATION_VERSION,
                ),
                representation_version=REPRESENTATION_VERSION,
                source_type=EvidenceSource.TABLE,
                granularity=RepresentationGranularity.TABLE,
                source_ref=table.normalized_table_id,
                report_id=source.report_id,
                page_ids=[table.page_id],
                table_id=table.source_table_id,
                paragraph_id=None,
                ticker=source.ticker,
                company_name=source.company_name,
                report_year=source.report_year,
                statement_scope=source.statement_scope,
                period_labels=list(table.period_labels),
                content=_table_content(source, table),
                row_paths=_deduplicate_paths(cell.row_path for cell in cells),
                column_paths=_deduplicate_paths(cell.column_path for cell in cells),
                linked_source_ids=[link.paragraph_id for link in table_links],
                scale_unit_hint_ids=[hint.hint_id for hint in table_hints],
            )
        )

    for paragraph in paragraphs:
        paragraph_links = sorted(
            links_by_paragraph.get(paragraph.paragraph_id, []),
            key=lambda link: table_order[link.table_id],
        )
        paragraph_hints = sorted(
            hints_by_paragraph.get(paragraph.paragraph_id, []),
            key=lambda hint: (hint.source_span.start, hint.source_span.end, hint.hint_id),
        )
        representations.append(
            RetrievalRepresentation(
                representation_id=make_representation_id(
                    EvidenceSource.TEXT.value,
                    paragraph.paragraph_id,
                    REPRESENTATION_VERSION,
                ),
                representation_version=REPRESENTATION_VERSION,
                source_type=EvidenceSource.TEXT,
                granularity=RepresentationGranularity.PARAGRAPH,
                source_ref=paragraph.paragraph_id,
                report_id=source.report_id,
                page_ids=[paragraph.page_id],
                table_id=None,
                paragraph_id=paragraph.paragraph_id,
                ticker=source.ticker,
                company_name=source.company_name,
                report_year=source.report_year,
                statement_scope=source.statement_scope,
                period_labels=[],
                content=_text_content(source, paragraph),
                row_paths=[],
                column_paths=[],
                linked_source_ids=[link.table_id for link in paragraph_links],
                scale_unit_hint_ids=[hint.hint_id for hint in paragraph_hints],
            )
        )
    return representations
