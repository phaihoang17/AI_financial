"""Offline builder for the Text-to-Pandas statement datasets (ADR-069).

Each primary-statement table of every M2 corpus report is read through the
existing M2 components (document parser, logical grid, TableClass registry,
numeric parser, ADR-062 scale/unit hints) into long-format facts:

    one row = one statement line of one report, current fiscal-year column.

Policies (conservative; skipped cases are counted, never guessed):

- only the current-period column is emitted ("period P comes only from report
  year P", TASK-030); comparative columns are never re-dated;
- a table's scale comes only from labelled currency-unit hints (``Đơn vị tính:
  VND``, ``Triệu đồng``), in order: the table's own hints, the current value
  column's header ("2018 Triệu đồng"), the nearest
  declaration hint above it on its page, an OCR-tolerant read of the page text
  above it ("Don vị: Triệu VND"), the previous same-class table of the report,
  then the report's consensus (all currency hints, or >= 95% of >= 20, agree);
  conflicting magnitudes or a mixed report skip the table; the basis is recorded;
- a canonical metric needs its TT200 code and an agreeing label
  (``src/text_to_pandas/metrics.py``); bank tables without a code column use
  whole-label rules; conflicting values for one metric in a report drop it;
- securities-company statements (forms B0x-CTCK) are recognised here, without
  changing the pinned M2 TableClass registry, and map their statement totals
  first; a balance sheet titled "Báo cáo tình hình tài chính" without a CTCK
  form (banks since TT49's amendment) keeps the TT200/bank rules; off-balance-
  sheet and notes (B09) tables are never statements;
- a table printing group and parent-company figures side by side ("Tập đoàn
  2015 | Công ty 2015") uses the current column of the report's own scope;
- a restatement table ("Số đã báo cáo | Điều chỉnh | Số sau điều chỉnh")
  never inherits the previous page's column layout;
- when a table's printed title and its coded rows disagree (an OCR page header
  from the neighbouring statement), the coded rows decide, only when the title's
  statement has no agreeing coded row and exactly one other statement has two;
- per-share lines keep ``VND/share`` and are never scaled;
- statement identities guard each report: when one fails (e.g. CFO + CFI + CFF
  != line 50, current + non-current assets != total assets), its metrics are
  cleared unless another identity that passes confirms them, so a misread
  figure abstains instead of answering;
- a current cell that is a note reference rather than a figure is skipped: when
  the "Thuyết minh" header sits over the labels (the grid lost the note column),
  a bare one-to-three digit number followed by a million-sized grouped figure
  (the row's note shifted over a lost value); and any nonzero amount below
  1,000 VND in a VND-scale table;
- expense metrics are positive costs: when a report prints deductions in
  parentheses (its expense lines are mostly negative) their sign is flipped and
  ``sign_basis`` records it; unmapped lines keep the printed sign.

Gold values (``gold/wide``) are never read here; they come from an independent
raw-HTML reader with cross-year confirmation and score this catalog.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
import csv
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json
from multiprocessing import Pool
from pathlib import Path
import re
import sys
from typing import Any

from src.evidence.schemas import Scale
from src.indexing.cell_normalizer import normalize_cell_text
from src.indexing.document_parser import parse_document
from src.indexing.grid_builder import build_logical_table_grid
from src.indexing.numeric_parser import parse_numeric_string
from src.indexing.provenance_sidecar import _extract_report_provenance, _source_from_inventory
from src.indexing.schemas import NumericParseStatus, TableParseStatus
from src.indexing.table_class_sidecar import _preceding_window_bounds, classify_table
from src.text_to_pandas.metrics import (
    BALANCE_SHEET,
    CASH_FLOW_STATEMENT,
    DATASET_BY_STATEMENT,
    INCOME_STATEMENT,
    STATEMENTS,
    fold,
    is_per_share_label,
    metric_for_bank_label,
    metric_for_code,
)

CATALOG_SCHEMA_VERSION = "t2p-catalog-v1"
CATALOG_BUILD_VERSION = "t2p-catalog-build-v4"
FACT_COLUMNS = (
    "fact_id", "ticker", "company_name", "fiscal_year", "statement_scope",
    "line_code", "line_item", "metric", "value", "unit", "source_scale",
)
COMPANY_COLUMNS = ("ticker", "company_name", "template", "first_year", "last_year", "statement_scopes")
PROVENANCE_FIELDS = (
    "fact_id", "statement", "report_id", "source_ref", "page_id", "table_id",
    "source_cell_id", "grid_row", "grid_column", "raw_text", "label_text",
    "period_basis", "scale_basis", "sign_basis", "table_class_basis",
)

_MULTIPLIER = {Scale.RAW: 1, Scale.THOUSAND: 10**3, Scale.MILLION: 10**6, Scale.BILLION: 10**9}
_MAGNITUDES = {Scale.THOUSAND, Scale.MILLION, Scale.BILLION}
_GROUPED_NUMBER = re.compile(r"\(?-?\d{1,3}(?:\.\d{3})+(?:,\d+)?\)?")
_CODE = re.compile(r"\d{2,3}[a-z]?")
_BARE_SMALL = re.compile(r"\d{1,3}")
_MILLION_GROUPED = re.compile(r"\(?-?\d{1,3}(?:\.\d{3}){2,}(?:,\d+)?\)?")
_PURE_ORDINAL = re.compile(r"(?:[0-9]+(?:\.[0-9]+)*|[IVXLC]+|[a-zđ])[.)]?", re.IGNORECASE)
_DATE = re.compile(r"(\d{1,2})\s*[/.\-]\s*(\d{1,2})\s*[/.\-]\s*((?:19|20)\d{2})")
_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_CURRENT_WORDS = ("namnay", "kynay", "socuoinam", "socuoiky", "cuoinam", "cuoiky")
_PRIOR_WORDS = ("namtruoc", "kytruoc", "sodaunam", "sodauky", "daunam", "dauky")


# Folded preceding-window cues (see ``classify_table``'s window).
_SECURITIES_FORM = re.compile(r"\bb\s*0?([123])[ab]?\s*[-–]?\s*ctck\b")
_SECURITIES_BALANCE_TITLE = re.compile(r"bao cao tinh hinh tai chinh")
_OFF_BALANCE = re.compile(r"chi tieu ngoai (?:bao cao|bang)")
_NOTES_FORM = re.compile(r"\bb\s*0?9[a-z]?\s*[-–]?\s*(?:dn|ctck|tctd)\b|thuyet minh bao cao tai chinh"
                         r"|thong tin bo sung cho cac khoan muc")
_SECURITIES_STATEMENT = {"1": BALANCE_SHEET, "2": INCOME_STATEMENT, "3": CASH_FLOW_STATEMENT}
# An adjustment column, or "as previously reported" beside "restated"; a lone
# "(Trình bày lại)" prior-year header is an ordinary restated comparative.
_RESTATEMENT_HEADER = re.compile(r"dieu chinh|(?=.*trinh bay lai)(?=.*(?:da bao cao|truoc day))")
_SCOPE_COLUMN = {"HOP_NHAT": re.compile(r"tap doan|hop nhat"), "RIENG": re.compile(r"cong t[yv]|rieng")}  # OCR "Công tv"
_CONTENT_MIN_ROWS = 2


class CatalogBuildError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def make_fact_id(report_id: str, table_id: str, source_cell_id: str) -> str:
    digest = sha256(f"{report_id}|{table_id}|{source_cell_id}".encode()).hexdigest()
    return f"f_{digest[:20]}"


@dataclass(frozen=True)
class _Layout:
    column_count: int
    code_column: int | None
    code_named: bool
    current_column: int
    label_columns: tuple[int, ...]


def _column_period(header: str, report_year: int) -> str | None:
    """CURRENT/PRIOR for one value-column header, None when unstated or both."""
    folded = fold(header)
    compact = folded.replace(" ", "")
    current = any(word in compact for word in _CURRENT_WORDS)
    prior = any(word in compact for word in _PRIOR_WORDS)
    for day, month, year in _DATE.findall(folded):
        if int(year) == report_year and not (int(day) == 1 and int(month) == 1):
            current = True
        elif int(year) in (report_year, report_year - 1):
            prior = True
    for year in _YEAR.findall(_DATE.sub(" ", folded)):
        current |= int(year) == report_year
        prior |= int(year) == report_year - 1
    if current == prior:
        return None
    return "CURRENT" if current else "PRIOR"


def _scale_for_table(hints: Sequence[Any]) -> tuple[Scale | None, str]:
    """Scale from labelled currency-unit hints; RAW never outranks a magnitude (ADR-062)."""
    scales = {hint.scale_candidate for hint in hints if hint.unit_candidate == "VND"}
    magnitudes = scales & _MAGNITUDES
    if len(magnitudes) > 1:
        return None, "AMBIGUOUS"
    if magnitudes:
        return next(iter(magnitudes)), "UNIT_HINT"
    if Scale.RAW in scales:
        return Scale.RAW, "UNIT_HINT"
    return None, "NONE"


def _page_declaration(hints: Sequence[Any], page_id: str, table_start: int) -> tuple[Scale | None, str]:
    """The nearest labelled unit declaration printed above the table on its page."""
    above = [
        hint for hint in hints
        if hint.page_id == page_id
        and hint.unit_candidate == "VND"
        and hint.source_kind.value == "TEXT"
        and hint.source_span.end <= table_start
    ]
    if not above:
        return None, "NONE"
    nearest = max(above, key=lambda hint: hint.source_span.start)
    return nearest.scale_candidate, "PAGE_DECLARATION"


# OCR-tolerant unit declaration on folded page text ("Don vị: Triệu VND", "Đơn vi tính").
_FOLDED_DECLARATION = re.compile(r"(?:don vi(?: tinh)?|dvt)\s*:?\s*(?:(trieu|nghin|ngan|ty)\s*)?(?:vnd|dong)\b")
_FOLDED_SCALE = {None: Scale.RAW, "trieu": Scale.MILLION, "nghin": Scale.THOUSAND, "ngan": Scale.THOUSAND,
                 "ty": Scale.BILLION}


_FOLDED_COLUMN_UNIT = re.compile(r"(?:(trieu|nghin|ngan|ty)\s*)?(?:vnd|dong)(?![a-z])")


def _column_header_unit(header: str) -> tuple[Scale | None, str]:
    """A unit written in the value column's own header ("2018 Triệu đồng", "Năm nay VND")."""
    scales = {_FOLDED_SCALE[match.group(1)] for match in _FOLDED_COLUMN_UNIT.finditer(fold(header))}
    if len(scales) == 1:
        return next(iter(scales)), "COLUMN_HEADER"
    return None, "NONE" if not scales else "AMBIGUOUS"


def _page_text_declaration(raw_text: str, page: Any, table_start: int) -> tuple[Scale | None, str]:
    """The last unit declaration in the page text above the table, read OCR-tolerantly."""
    above = fold(raw_text[page.source_span.start:table_start])
    matches = list(_FOLDED_DECLARATION.finditer(above))
    if not matches:
        return None, "NONE"
    return _FOLDED_SCALE[matches[-1].group(1)], "PAGE_TEXT"


def _report_consensus(hints: Sequence[Any]) -> tuple[Scale | None, str]:
    """The report's scale when its currency-unit hints agree (all, or >= 95% of >= 20)."""
    scales = Counter(hint.scale_candidate for hint in hints if hint.unit_candidate == "VND")
    if not scales:
        return None, "NONE"
    scale, count = scales.most_common(1)[0]
    total = sum(scales.values())
    if count == total:
        return scale, "REPORT_CONSENSUS"
    if total >= 20 and count >= 0.95 * total:
        return scale, "REPORT_DOMINANT"
    return None, "REPORT_MIXED"


def _statement_class(source: Any, page: Any, table: Any) -> tuple[str | None, bool, str]:
    """(statement, securities form, basis) of one table from the text printed above it."""
    start, end = _preceding_window_bounds(page, table)
    window = fold(source.raw_text[start:end]) if end > start else ""
    if _NOTES_FORM.search(window) or _OFF_BALANCE.search(window):
        return None, False, "NOTES_OR_OFF_BALANCE"
    form = _SECURITIES_FORM.search(window)
    hint = classify_table(source.report_id, source.raw_text, page, table)
    statement = None if hint is None else hint.table_class.value
    if form is not None:
        securities = _SECURITIES_STATEMENT[form.group(1)]
        if statement not in (None, securities):
            return None, True, "CONFLICTING_TITLE"
        return securities, True, "SECURITIES_FORM"
    if statement is None and _SECURITIES_BALANCE_TITLE.search(window):
        return BALANCE_SHEET, False, "FINANCIAL_POSITION_TITLE"
    return statement, False, "TITLE"


def _content_statement(reader: _TableReader, layout: _Layout, statement: str, securities: bool) -> str | None:
    """The statement named by the table's coded rows when its title disagrees, else None."""
    if layout.code_column is None:
        return None
    counts = Counter()
    for _row, label, code, _cell, _text in reader.rows(layout):
        for candidate in STATEMENTS:
            if metric_for_code(candidate, code, label, securities=securities):
                counts[candidate] += 1
    if counts[statement]:
        return None
    others = [candidate for candidate, count in counts.items() if count >= _CONTENT_MIN_ROWS]
    return others[0] if len(others) == 1 else None


class _TableReader:
    """Row reader over the existing M2 logical grid of one statement table."""

    def __init__(self, table: Any, report_year: int, scope: str | None = None) -> None:
        self.table = table
        self.scope = scope
        self.grid = build_logical_table_grid(table)
        self.texts = {cell.cell_id: normalize_cell_text(cell.extracted_text) for cell in table.cells}
        self.report_year = report_year
        self.width = self.grid.column_count
        self.header_end = next(
            (
                row
                for row in range(self.grid.row_count)
                if any(
                    slot and _GROUPED_NUMBER.fullmatch(self.texts.get(slot, ""))
                    for slot in self.grid.slots[row]
                )
            ),
            None,
        )

    def _row_cells(self, row: int) -> list[str | None]:
        return self.grid.slots[row]

    def _full_width(self, row: int, cell_id: str | None) -> bool:
        return cell_id is not None and all(slot == cell_id for slot in self.grid.slots[row])

    def header_text(self, column: int) -> str:
        parts: list[str] = []
        for row in range(self.header_end or 0):
            cell_id = self.grid.slots[row][column]
            if cell_id is None or self._full_width(row, cell_id):
                continue
            text = self.texts.get(cell_id, "")
            if text and text not in parts:
                parts.append(text)
        return " ".join(parts)

    def all_header_text(self) -> str:
        return " ".join(
            self.texts.get(cell_id, "")
            for row in range(self.header_end or 0)
            for cell_id in dict.fromkeys(self.grid.slots[row])
            if cell_id
        )

    def _is_note_column(self, column: int) -> bool:
        compact = fold(self.header_text(column)).replace(" ", "")
        return "thuyetminh" in compact and "maso" not in compact or compact == "tm"

    def shifted_note(self, row: int, layout: _Layout) -> bool:
        """The current cell holds this row's note number, shifted over a lost figure."""
        if not any(self._is_note_column(column) for column in layout.label_columns):
            return False
        return any(
            _MILLION_GROUPED.fullmatch(self.texts.get(slot or "", ""))
            for slot in self.grid.slots[row][layout.current_column + 1:]
        )

    def _mostly_words(self, column: int) -> bool:
        values = [
            self.texts.get(self.grid.slots[row][column] or "", "")
            for row in range(self.header_end or 0, self.grid.row_count)
        ]
        values = [value for value in values if value]
        words = sum(len(re.findall(r"[^\W\d_]{2,}", value)) >= 2 for value in values)
        return bool(values) and words > 0.5 * len(values)

    def _code_column(self) -> tuple[int | None, bool]:
        """(code column, named by a "Mã số" header); content-detected codes may be note numbers."""
        for column in range(self.width):
            compact = fold(self.header_text(column)).replace(" ", "")
            if "maso" in compact or compact in {"ma", "ms"}:
                return column, True
        best: int | None = None
        best_share = 0.5
        for column in range(self.width):
            if self._is_note_column(column):
                continue
            values = [
                self.texts.get(self.grid.slots[row][column] or "", "")
                for row in range(self.header_end or 0, self.grid.row_count)
            ]
            values = [value for value in values if value]
            if not values:
                continue
            share = sum(bool(_CODE.fullmatch(value)) for value in values) / len(values)
            if share > best_share:
                best, best_share = column, share
        return best, False

    def layout(self, inherited: _Layout | None) -> tuple[_Layout | None, str]:
        if self.header_end is None:
            return None, "NO_DATA_ROWS"
        periods = {column: _column_period(self.header_text(column), self.report_year)
                   for column in range(self.width)}
        current = [column for column, period in periods.items() if period == "CURRENT"]
        code_column, code_named = self._code_column()
        if len(current) > 1 and self.scope in _SCOPE_COLUMN:
            own = [column for column in current if _SCOPE_COLUMN[self.scope].search(fold(self.header_text(column)))]
            other = _SCOPE_COLUMN["RIENG" if self.scope == "HOP_NHAT" else "HOP_NHAT"]
            if len(own) == 1 and all(other.search(fold(self.header_text(column))) for column in current
                                     if column not in own):
                current = own
        if len(current) > 1:
            return None, "AMBIGUOUS_CURRENT_COLUMN"
        if current:
            basis = "HEADER"
            current_column = current[0]
        elif inherited is not None and _RESTATEMENT_HEADER.search(fold(self.all_header_text())):
            return None, "RESTATEMENT_TABLE"
        elif inherited is not None and not any(periods.values()):
            # A continuation page: keep the previous page's right-aligned current column.
            offset = inherited.column_count - inherited.current_column
            current_column = self.width - offset
            if current_column < 1:
                return None, "NO_CURRENT_COLUMN"
            basis = "INHERITED"
        else:
            return None, "NO_CURRENT_COLUMN"
        # OCR may put "Mã số" before the label, so labels are the non-code, non-note
        # columns left of the first value column.
        first_value = min(
            [current_column] + [column for column, period in periods.items() if period is not None]
        )
        notes = {column for column in range(self.width) if self._is_note_column(column)}
        label_columns = tuple(
            column for column in range(first_value)
            if column != code_column and column not in notes
        )
        if not label_columns:
            # OCR can shift the "Thuyết minh" header over the label column; a
            # "note" column whose cells are mostly words is the label column.
            label_columns = tuple(
                column for column in range(first_value)
                if column in notes and column != code_column and self._mostly_words(column)
            )
        if not label_columns:
            return None, "NO_LABEL_COLUMN"
        return _Layout(self.width, code_column, code_named, current_column, label_columns), basis

    def rows(self, layout: _Layout) -> Iterable[tuple[int, str, str | None, str, str]]:
        """(grid_row, label, code, current_cell_id, current_text) for data rows."""
        for row in range(self.header_end or 0, self.grid.row_count):
            slots = self._row_cells(row)
            if self._full_width(row, slots[0]):
                continue
            current_id = slots[layout.current_column]
            if current_id is None:
                continue
            if any(
                slots[column] == current_id
                for column in range(self.width)
                if column != layout.current_column
            ):
                continue  # merged across value columns: not one period's figure
            parts: list[str] = []
            for column in layout.label_columns:
                cell_id = slots[column]
                text = self.texts.get(cell_id or "", "")
                if not text or text in parts:
                    continue
                if not parts and _PURE_ORDINAL.fullmatch(text) and len(layout.label_columns) > 1:
                    continue
                parts.append(text)
            label = " ".join(parts)
            code = None
            if layout.code_column is not None:
                raw_code = self.texts.get(slots[layout.code_column] or "", "").replace(" ", "").lower()
                code = raw_code if _CODE.fullmatch(raw_code) else None
            if not label and code is None:
                continue
            yield row, label, code, current_id, self.texts.get(current_id, "")


def extract_report_facts(
    source: Any, report: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Counter]:
    """Facts, provenance rows and skip counters for one report."""
    stats: Counter = Counter()
    document = parse_document(source)
    hints, _links = _extract_report_provenance(source)
    hints_by_table: dict[str, list[Any]] = defaultdict(list)
    for hint in hints:
        if hint.table_id is not None:
            hints_by_table[hint.table_id].append(hint)
    report_scale = _report_consensus(hints)

    facts: list[dict[str, Any]] = []
    provenance: list[dict[str, Any]] = []
    last_layout: dict[str, _Layout] = {}
    last_scale: dict[str, Scale] = {}
    year = int(report["report_year"])
    for page in document.pages:
        for table in page.tables:
            if table.parse_status is TableParseStatus.UNPARSEABLE:
                continue
            statement, securities, class_basis = _statement_class(source, page, table)
            if statement not in STATEMENTS:
                if class_basis != "TITLE":
                    stats[f"skip.{class_basis}"] += 1
                continue
            reader = _TableReader(table, year, report["statement_scope"])
            layout, period_basis = reader.layout(last_layout.get(statement))
            if layout is not None and period_basis == "HEADER":
                content = _content_statement(reader, layout, statement, securities)
                if content is not None:
                    statement, class_basis = content, "CODED_ROWS"
                    stats["tables.reclassified_by_coded_rows"] += 1
            stats[f"tables.{statement}"] += 1
            if layout is None:
                stats[f"skip.{period_basis}"] += 1
                continue
            scale, scale_basis = _scale_for_table(hints_by_table.get(table.table_id, []))
            if scale is None and scale_basis == "NONE":
                scale, scale_basis = _column_header_unit(reader.header_text(layout.current_column))
            if scale is None and scale_basis == "NONE":
                scale, scale_basis = _page_declaration(hints, page.page_id, table.source_span.start)
            if scale is None and scale_basis == "NONE":
                scale, scale_basis = _page_text_declaration(source.raw_text, page, table.source_span.start)
            if scale is None and scale_basis == "NONE" and statement in last_scale:
                scale, scale_basis = last_scale[statement], "INHERITED"
            if scale is None and scale_basis == "NONE":
                scale, scale_basis = report_scale
            if scale is None:
                stats[f"skip.SCALE_{scale_basis}"] += 1
                continue
            last_layout[statement] = layout
            last_scale[statement] = scale
            # Bank (TT49) forms have no "Mã số" column; their whole-label rules apply
            # whenever the codes were not named by a header (they may be note numbers).
            label_rules = not layout.code_named
            emitted = 0
            for grid_row, label, code, cell_id, text in reader.rows(layout):
                numeric = parse_numeric_string(text)
                if numeric.status is not NumericParseStatus.PARSED or numeric.percent_literal:
                    stats["rows.value_not_parsed"] += 1
                    continue
                per_share = is_per_share_label(label)
                multiplier = 1 if per_share else _MULTIPLIER[scale]
                value = Decimal(numeric.decimal_value) * multiplier
                if not per_share and (
                    (_BARE_SMALL.fullmatch(text.strip()) and reader.shifted_note(grid_row, layout))
                    or (scale is Scale.RAW and value != 0 and abs(value) < 1000)
                ):
                    stats["rows.note_reference_as_value"] += 1
                    continue
                metric = metric_for_code(statement, code, label, securities=securities)
                if metric is None and label_rules and not securities:
                    metric = metric_for_bank_label(statement, label)
                fact_id = make_fact_id(source.report_id, table.table_id, cell_id)
                facts.append({
                    "fact_id": fact_id,
                    "ticker": report["ticker"],
                    "company_name": report["company_name"],
                    "fiscal_year": year,
                    "statement_scope": report["statement_scope"],
                    "statement": statement,
                    "line_code": code,
                    "line_item": label,
                    "metric": metric,
                    "value": format(value.normalize(), "f") if value != 0 else "0",
                    "unit": "VND/share" if per_share else "VND",
                    "source_scale": "RAW" if per_share else scale.value,
                    "report_id": source.report_id,
                })
                provenance.append({
                    "fact_id": fact_id,
                    "statement": statement,
                    "report_id": source.report_id,
                    "source_ref": report["source_ref"],
                    "page_id": page.page_id,
                    "table_id": table.table_id,
                    "source_cell_id": cell_id,
                    "grid_row": grid_row,
                    "grid_column": layout.current_column,
                    "raw_text": text,
                    "label_text": label,
                    "period_basis": period_basis,
                    "scale_basis": scale_basis,
                    "table_class_basis": class_basis,
                })
                emitted += 1
            stats[f"facts.{statement}"] += emitted
    _resolve_metric_duplicates(facts, stats)
    sign_basis = _normalize_expense_signs(facts, stats)
    _guard_identities(facts, stats)
    for row in provenance:
        row["sign_basis"] = sign_basis
    return facts, provenance, stats


_PAGE_CARRY = re.compile(r"\((?:mang|chuyen) sang (?:tu )?trang (?:sau|truoc)\)")


def _resolve_metric_duplicates(facts: list[dict[str, Any]], stats: Counter) -> None:
    """One value per (statement, metric) per report; agreeing repeats keep one.

    Conflicts drop every row, except that agreeing coded rows win over differing
    code-less rows.

    The kept repeat is the first row carrying a line code, else the first row, so a
    code-less summary table (e.g. a management letter quoting the result) never
    displaces the coded statement line as the fact's provenance. Within that order a
    page carry-over copy ("... (mang sang trang sau)") yields to the line itself.
    """
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for fact in facts:
        if fact["metric"] is not None:
            groups[(fact["statement"], fact["metric"])].append(fact)
    for (_, metric), group in groups.items():
        values = {fact["value"] for fact in group}
        coded = {fact["value"] for fact in group if fact["line_code"]}
        if len(values) > 1 and len(coded) == 1:
            # Coded statement lines agree; a code-less repeat that differs (an OCR
            # digit, a summary) is dropped rather than discarding the line itself.
            for fact in group:
                if not fact["line_code"]:
                    fact["metric"] = None
            group = [fact for fact in group if fact["line_code"]]
            stats[f"metric_conflict_coded_kept.{metric}"] += 1
        elif len(values) > 1:
            for fact in group:
                fact["metric"] = None
            stats[f"metric_conflict.{metric}"] += 1
            continue
        def carried(fact: dict[str, Any]) -> bool:
            return bool(_PAGE_CARRY.search(fold(fact.get("line_item") or "")))

        kept = next((fact for fact in group if fact["line_code"] and not carried(fact)),
                    next((fact for fact in group if fact["line_code"]),
                         next((fact for fact in group if not carried(fact)), group[0])))
        if kept is not group[0]:
            stats["metric_repeat_coded_preferred"] += 1
        for fact in group:
            if fact is not kept:
                fact["metric"] = None
                stats["metric_repeat_dropped"] += 1


# (name, summands, total): summands add up to the total on every TT200/CTCK form.
_IDENTITIES = (
    ("TOTAL_ASSETS_CAPITAL", ("TOTAL_ASSETS",), "TOTAL_CAPITAL"),
    ("ASSETS_SPLIT", ("CURRENT_ASSETS", "NON_CURRENT_ASSETS"), "TOTAL_ASSETS"),
    ("LIABILITIES_EQUITY", ("LIABILITIES", "EQUITY"), "TOTAL_CAPITAL"),
    ("LIABILITIES_EQUITY_ASSETS", ("LIABILITIES", "EQUITY"), "TOTAL_ASSETS"),
    ("LIABILITIES_SPLIT", ("CURRENT_LIABILITIES", "NON_CURRENT_LIABILITIES"), "LIABILITIES"),
    ("CASH_FLOW_SECTIONS", ("CFO", "CFI", "CFF"), "NET_CASH_FLOW"),
)


def _guard_identities(facts: list[dict[str, Any]], stats: Counter) -> None:
    """Clear metrics of failed identities that no passing identity confirms."""
    kept = {fact["metric"]: fact for fact in facts if fact["metric"] is not None}
    anchored, suspect = set(), {}
    for name, summands, total in _IDENTITIES:
        members = (*summands, total)
        if not all(metric in kept for metric in members):
            continue
        # Two printed units of the coarsest source scale absorb per-line rounding.
        unit = max(_MULTIPLIER[Scale(kept[metric]["source_scale"])] for metric in members)
        left = sum(Decimal(kept[metric]["value"]) for metric in summands)
        right = Decimal(kept[total]["value"])
        if abs(left - right) <= 2 * unit + abs(right) * Decimal("1e-9"):
            anchored.update(members)
        else:
            stats[f"identity_failed.{name}"] += 1
            for metric in members:
                suspect.setdefault(metric, name)
    for metric in suspect:
        if metric not in anchored:
            kept[metric]["metric"] = None
            stats[f"identity_cleared.{metric}"] += 1


# Expense lines whose printed sign is a presentation convention: some auditors print
# every deduction in parentheses. The real sign of these lines is an expense.
_EXPENSE_METRICS = ("COGS", "SELLING_EXPENSES", "GA_EXPENSES", "FINANCIAL_EXPENSES",
                    "INTEREST_EXPENSE", "OTHER_EXPENSES", "CURRENT_TAX", "DEFERRED_TAX")
_CONVENTION_PROBES = ("COGS", "SELLING_EXPENSES", "GA_EXPENSES", "FINANCIAL_EXPENSES")


def _normalize_expense_signs(facts: list[dict[str, Any]], stats: Counter) -> str:
    """Make expense metrics positive costs when the report prints deductions in parentheses.

    The convention is read from the expense lines themselves (a cost of goods sold
    is never negative), never from the income-statement identities that score the build.
    """
    probes = [
        Decimal(fact["value"]) for fact in facts
        if fact["statement"] == "INCOME_STATEMENT" and fact["metric"] in _CONVENTION_PROBES
        and Decimal(fact["value"]) != 0
    ]
    if not probes:
        return "AS_PRINTED"
    negative = sum(value < 0 for value in probes)
    if negative * 2 <= len(probes):
        return "AS_PRINTED"
    for fact in facts:
        if fact["statement"] == "INCOME_STATEMENT" and fact["metric"] in _EXPENSE_METRICS:
            value = -Decimal(fact["value"])
            fact["value"] = format(value.normalize(), "f") if value != 0 else "0"
    stats["reports.parenthesized_expenses"] += 1
    return "EXPENSES_PARENTHESIZED"


def _process(args: tuple[str, str, dict[str, Any]]) -> tuple[str, list[dict], list[dict], Counter, str | None]:
    raw_root, corpus_id, report = args
    try:
        source = _source_from_inventory(Path(raw_root), corpus_id, report)
        facts, provenance, stats = extract_report_facts(source, report)
        return report["report_id"], facts, provenance, stats, None
    except Exception as error:  # one unreadable report must not stop the build
        return report["report_id"], [], [], Counter(), f"{type(error).__name__}: {error}"


def _quality_checks(facts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Accounting identities that independently test extraction quality."""
    by_key: dict[tuple[str, int, str], dict[str, Decimal]] = defaultdict(dict)
    for fact in facts:
        if fact["metric"] is not None:
            key = (fact["ticker"], fact["fiscal_year"], fact["statement_scope"])
            by_key[key][fact["metric"]] = Decimal(fact["value"])

    def check(name, required, predicate):
        counter = Counter()
        for metrics in by_key.values():
            if all(metric in metrics for metric in required):
                counter["pass" if predicate(metrics) else "fail"] += 1
        total = counter["pass"] + counter["fail"]
        return {"checked": total, "pass": counter["pass"], "fail": counter["fail"],
                "pass_rate": None if total == 0 else round(counter["pass"] / total, 4), "identity": name}

    def close(left: Decimal, right: Decimal) -> bool:
        return abs(left - right) <= max(Decimal(1), abs(right) * Decimal("1e-9"))

    return {
        "balance_sheet": check("TOTAL_ASSETS == TOTAL_CAPITAL", ("TOTAL_ASSETS", "TOTAL_CAPITAL"),
                               lambda m: close(m["TOTAL_ASSETS"], m["TOTAL_CAPITAL"])),
        "liabilities_equity": check("LIABILITIES + EQUITY == TOTAL_CAPITAL",
                                    ("LIABILITIES", "EQUITY", "TOTAL_CAPITAL"),
                                    lambda m: close(m["LIABILITIES"] + m["EQUITY"], m["TOTAL_CAPITAL"])),
        "income_tax": check("PROFIT_BEFORE_TAX - CURRENT_TAX - DEFERRED_TAX == LNST",
                            ("PROFIT_BEFORE_TAX", "CURRENT_TAX", "DEFERRED_TAX", "LNST"),
                            lambda m: close(m["PROFIT_BEFORE_TAX"] - m["CURRENT_TAX"] - m["DEFERRED_TAX"], m["LNST"])),
        "lnst_split": check("LNST_PARENT + LNST_MINORITY == LNST", ("LNST_PARENT", "LNST_MINORITY", "LNST"),
                            lambda m: close(m["LNST_PARENT"] + m["LNST_MINORITY"], m["LNST"])),
        "gross_profit": check("NET_REVENUE - COGS == GROSS_PROFIT", ("NET_REVENUE", "COGS", "GROSS_PROFIT"),
                              lambda m: close(m["NET_REVENUE"] - m["COGS"], m["GROSS_PROFIT"])),
    }


def _companies(facts: Sequence[Mapping[str, Any]], reports: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    names: dict[str, Counter] = defaultdict(Counter)
    years: dict[str, list[int]] = defaultdict(list)
    scopes: dict[str, set] = defaultdict(set)
    for report in reports:
        names[report["ticker"]][report["company_name"]] += 1
        years[report["ticker"]].append(int(report["report_year"]))
        scopes[report["ticker"]].add(report["statement_scope"])
    banks = {fact["ticker"] for fact in facts if fact["metric"] == "NET_INTEREST_INCOME"}
    return [
        {
            "ticker": ticker,
            "company_name": names[ticker].most_common(1)[0][0],
            "template": "BANK" if ticker in banks else "CORPORATE",
            "first_year": min(years[ticker]),
            "last_year": max(years[ticker]),
            "statement_scopes": ",".join(sorted(scopes[ticker])),
        }
        for ticker in sorted(names)
    ]


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def build_catalog(
    corpus_manifest: str | Path,
    raw_root: str | Path,
    output_root: str | Path,
    *,
    workers: int = 8,
    limit: int | None = None,
    tickers: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Write CSV datasets, provenance JSONL and a manifest into a new directory."""
    output = Path(output_root)
    if output.exists():
        raise CatalogBuildError("CATALOG_OUTPUT_EXISTS", str(output))
    manifest_path = Path(corpus_manifest)
    corpus = json.loads(manifest_path.read_text(encoding="utf-8"))
    # A report without ticker, year and scope cannot be grounded by those filters.
    identified = [
        report for report in corpus["reports"]
        if report.get("ticker") and report.get("report_year") and report.get("statement_scope")
    ]
    skipped_unidentified = len(corpus["reports"]) - len(identified)
    reports = sorted(identified, key=lambda item: (item["ticker"], item["report_year"], item["statement_scope"]))
    if tickers:
        wanted = set(tickers)
        reports = [report for report in reports if report["ticker"] in wanted]
    if limit is not None:
        reports = reports[:limit]
    keys = [(report["ticker"], report["report_year"], report["statement_scope"]) for report in reports]
    if len(keys) != len(set(keys)):
        raise CatalogBuildError("CATALOG_DUPLICATE_REPORT", "one report per ticker/year/scope is required")

    tasks = [(str(raw_root), corpus["corpus_id"], report) for report in reports]
    all_facts: list[dict[str, Any]] = []
    all_provenance: list[dict[str, Any]] = []
    stats: Counter = Counter()
    failures: dict[str, str] = {}
    with Pool(processes=max(1, workers)) as pool:
        for report_id, facts, provenance, report_stats, error in pool.imap(_process, tasks, chunksize=4):
            if error is not None:
                failures[report_id] = error
                continue
            all_facts.extend(facts)
            all_provenance.extend(provenance)
            stats.update(report_stats)
            stats["reports.with_facts"] += bool(facts)

    output.mkdir(parents=True)
    files: dict[str, dict[str, Any]] = {}
    for statement in STATEMENTS:
        dataset = DATASET_BY_STATEMENT[statement]
        rows = [fact for fact in all_facts if fact["statement"] == statement]
        path = output / f"{dataset}.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FACT_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        files[dataset] = {"file": path.name, "rows": len(rows), "sha256": _sha256_file(path)}
    companies = _companies(all_facts, reports)
    path = output / "companies.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COMPANY_COLUMNS)
        writer.writeheader()
        writer.writerows(companies)
    files["companies"] = {"file": path.name, "rows": len(companies), "sha256": _sha256_file(path)}
    path = output / "provenance.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for row in all_provenance:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    files["provenance"] = {"file": path.name, "rows": len(all_provenance), "sha256": _sha256_file(path)}

    metric_coverage = Counter(fact["metric"] for fact in all_facts if fact["metric"] is not None)
    manifest = {
        "catalog_schema_version": CATALOG_SCHEMA_VERSION,
        "build_version": CATALOG_BUILD_VERSION,
        "source_corpus_artifact_id": corpus["artifact_id"],
        "source_corpus_manifest_sha256": _sha256_file(manifest_path),
        "report_count": len(reports),
        "skipped_unidentified_reports": skipped_unidentified,
        "failed_reports": failures,
        "files": files,
        "metric_coverage": dict(sorted(metric_coverage.items())),
        "build_stats": dict(sorted(stats.items())),
        "quality_checks": _quality_checks(all_facts),
    }
    manifest["catalog_id"] = sha256(
        json.dumps({name: item["sha256"] for name, item in files.items()}, sort_keys=True).encode()
    ).hexdigest()
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - CLI
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--corpus-manifest", required=True)
    parser.add_argument("--raw-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--tickers", nargs="*")
    args = parser.parse_args(argv)
    manifest = build_catalog(args.corpus_manifest, args.raw_root, args.output, workers=args.workers,
                             limit=args.limit, tickers=args.tickers)
    summary = {key: manifest[key] for key in ("catalog_id", "report_count", "metric_coverage", "quality_checks")}
    summary["files"] = {name: item["rows"] for name, item in manifest["files"].items()}
    summary["failed_reports"] = len(manifest["failed_reports"])
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
