"""Production gold v1: a development split and an independent holdout (ADR-072).

The ADR-066/070/071 builders measure one row (wide) or one sampling of the
broad metrics. The production gates need 200–300 cases, at least 100 of them
in a holdout that nothing was fitted on. This builder reuses the independent
raw-HTML method of ``src/evaluation/broad_gold.py`` (agreeing ``Mã số`` and
label, header-oriented columns, unit-note scale, confirmation by report Y+1)
and adds:

- bank (TT49-style) statements, whose rows carry no line codes: a whole,
  ordinal-stripped label must name the line, and only reports whose income
  statement prints "Thu nhập lãi thuần" use this path;
- LNST and bank metrics as question targets, so the critical business groups
  (operation, statement, corporate vs bank) each get enough cases;
- two splits with different seeds. Neither reuses a report (ticker, scope,
  year) cited by any earlier gold file; the holdout also shares no report with
  the development split, and its questions use phrasing templates and metric
  names written for it alone. Fixes may be derived from ``dev`` only; the
  holdout is scored once per frozen system;
- must-refuse controls: the earlier kinds plus a missing statement scope for
  an existing year, statement lines a bank does not report, future periods and
  a question that injects a number for a period without data.

Cases are ``AUTO_DERIVABLE`` (agent-built, deterministic source pointers).
They become ``VERIFIED`` only through recorded human review
(``src/evaluation/gold_review.py``).
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import random
import re
from typing import Any

from src.evaluation.broad_gold import (
    BALANCE,
    INCOME,
    SPECS_BY_ID,
    StatementRow,
    _decimal,
    answer,
    column_orientation,
    cross_checked,
    find_statement_rows,
    fold,
    ratio_answer,
    ratio_inputs,
    raw_vnd,
)
from src.evaluation.wide_gold import (
    _PARTIAL_PERIOD,
    _RESTATEMENT,
    _SCOPE_WORD,
    _VALUE,
    SOURCE_NAME,
    _chunk_ids,
    _clean,
    _locate_cells,
    _Rows,
    _sample,
    _scale,
    _to_decimal,
)
from src.formulas.ratios import RATIOS_BY_ID
from src.indexing.provenance_sidecar import _source_from_inventory

PRODUCTION_GOLD_VERSION = "m10-production-gold-v1"
SPLITS = ("dev", "holdout", "holdout2", "holdout3")
Key = tuple[str, str, int]  # ticker, scope, report year

# ---- bank statements --------------------------------------------------------

_ORDINAL = re.compile(r"^(?:(?:[ivxl]+|\d+|[a-h])\s+)+")
# A statement line continued on the next page keeps its label plus this note.
_PAGE_CARRY = re.compile(r"\s+(?:mang|chuyen) sang (?:tu )?trang (?:sau|truoc)$")
_BANK_SIGNATURE = "thu nhap lai thuan"
BANK_LABELS: dict[str, tuple[str, re.Pattern]] = {
    "NET_INTEREST_INCOME": (INCOME, re.compile(r"thu nhap lai thuan")),
    "PROFIT_BEFORE_TAX": (INCOME, re.compile(r"(?:tong )?loi nhuan truoc thue")),
    "LNST": (INCOME, re.compile(r"(?:tong )?loi nhuan sau thue(?: thu nhap doanh nghiep)?")),
    "TOTAL_ASSETS": (BALANCE, re.compile(r"tong (?:cong )?tai san(?: co)?")),
    "LIABILITIES": (BALANCE, re.compile(r"tong no phai tra")),
    "EQUITY": (BALANCE, re.compile(r"(?:tong )?von chu so huu")),
}


def label_key(text: str) -> str:
    return _ORDINAL.sub("", fold(text)).strip()


def carry_alternatives(raw_text: str, report_year: int, row: StatementRow) -> list[StatementRow]:
    """Page carry-over copies ("... (mang sang trang sau)") of a verified bank row.

    They print the same line and value on the statement page before a page
    break, so they are alternative cells of the same source. Sampling and
    verification never see them; they only widen the accepted source cells.
    """
    found = []
    pattern = BANK_LABELS[row.metric_id][1]
    for match in re.finditer(r"<table.*?</table>", raw_text, re.S):
        parser = _Rows()
        parser.feed(match.group())
        rows = [[_clean(cell) for cell in cells] for cells in parser.rows]
        first_data = next((i for i, cells in enumerate(rows) if sum(bool(_VALUE.fullmatch(c)) for c in cells) >= 2),
                          None)
        if first_data is None:
            continue
        header_text = " | ".join(" | ".join(cells) for cells in rows[:first_data])
        for row_index, cells in enumerate(rows[first_data:], first_data):
            labels = [cell for cell in cells if cell and not _VALUE.fullmatch(cell)]
            if not any(_PAGE_CARRY.search(label_key(label)) and pattern.fullmatch(_PAGE_CARRY.sub("", label_key(label)))
                       for label in labels):
                continue
            columns = [i for i, c in enumerate(cells) if _VALUE.fullmatch(c)]
            orientation = column_orientation(header_text, report_year)
            if len(columns) != 2 or orientation is None:
                continue
            current, prior = columns if orientation else columns[::-1]
            if (_to_decimal(cells[current]), _to_decimal(cells[prior])) != (row.current, row.prior):
                continue
            found.append(StatementRow(row.metric_id, match.start(), row_index, current, prior,
                                      parser.rows[row_index][current], parser.rows[row_index][prior],
                                      row.current, row.prior, row.label, row.scale))
    return found


def find_bank_rows(raw_text: str, report_year: int) -> tuple[dict[str, list[StatementRow]], Counter]:
    """Whole-label bank rows per metric, minus conflicts; empty for non-bank reports."""
    reasons: Counter = Counter()
    tables = []
    for match in re.finditer(r"<table.*?</table>", raw_text, re.S):
        parser = _Rows()
        parser.feed(match.group())
        tables.append((match, parser.rows, [[_clean(cell) for cell in row] for row in parser.rows]))
    if not any(label_key(cell) == _BANK_SIGNATURE for _, _, rows in tables for row in rows for cell in row):
        return {}, reasons
    found: dict[str, list[StatementRow]] = defaultdict(list)
    for match, raw_rows, rows in tables:
        first_data = next((i for i, row in enumerate(rows) if sum(bool(_VALUE.fullmatch(c)) for c in row) >= 2), None)
        if first_data is None:
            continue
        header_text = " | ".join(" | ".join(row) for row in rows[:first_data])
        for row_index, cells in enumerate(rows[first_data:], first_data):
            labels = [cell for cell in cells if cell and not _VALUE.fullmatch(cell)]
            metrics = {metric_id for metric_id, (_, pattern) in BANK_LABELS.items()
                       for label in labels if pattern.fullmatch(label_key(label))}
            if len(metrics) != 1:
                reasons["BANK_AMBIGUOUS_ROW"] += len(metrics) > 1
                continue
            metric_id = metrics.pop()
            if _RESTATEMENT.search(header_text) and "năm nay" not in header_text.casefold():
                reasons[f"BANK.{metric_id}.RESTATEMENT_TABLE"] += 1
                continue
            if _PARTIAL_PERIOD.search(header_text):
                reasons[f"BANK.{metric_id}.PARTIAL_PERIOD"] += 1
                continue
            columns = [i for i, c in enumerate(cells) if _VALUE.fullmatch(c)]
            if len(columns) != 2:
                reasons[f"BANK.{metric_id}.VALUE_COLUMNS"] += 1
                continue
            orientation = column_orientation(header_text, report_year)
            if orientation is None:
                reasons[f"BANK.{metric_id}.ORIENTATION_UNKNOWN"] += 1
                continue
            current, prior = columns if orientation else columns[::-1]
            scale = _scale(header_text, raw_text[max(0, match.start() - 3000):match.start()])
            if scale is None:
                reasons[f"BANK.{metric_id}.UNIT_UNKNOWN"] += 1
                continue
            label = next(label for label in labels if BANK_LABELS[metric_id][1].fullmatch(label_key(label)))
            found[metric_id].append(StatementRow(
                metric_id, match.start(), row_index, current, prior, raw_rows[row_index][current],
                raw_rows[row_index][prior], _to_decimal(cells[current]), _to_decimal(cells[prior]), label, scale))
    kept = {}
    for metric_id, rows in found.items():
        if len({raw_vnd(row) + "|" + raw_vnd(row, "prior") for row in rows}) != 1:
            reasons[f"BANK.{metric_id}.CONFLICTING_ROWS"] += 1
            continue
        kept[metric_id] = rows
    reasons["BANK_REPORTS"] += 1
    return kept, reasons


def extract_values(manifest: dict[str, Any], raw_root: Path) -> tuple[dict[Key, dict[str, Any]], Counter]:
    values: dict[Key, dict[str, Any]] = {}
    skips: Counter = Counter()
    duplicates: set[Key] = set()
    for report in manifest["reports"]:
        if report.get("statement_scope") not in _SCOPE_WORD or not report.get("ticker") or not report.get("report_year"):
            skips["UNIDENTIFIED_OR_UNSUPPORTED_SCOPE"] += 1
            continue
        key = (report["ticker"], report["statement_scope"], int(report["report_year"]))
        if key in values or key in duplicates:
            duplicates.add(key)
            continue
        source = _source_from_inventory(raw_root, manifest["corpus_id"], report)
        rows, reasons = find_statement_rows(source.raw_text, key[2])
        bank_rows, bank_reasons = find_bank_rows(source.raw_text, key[2])
        skips.update(reasons)
        skips.update(bank_reasons)
        template = "BANK" if bank_reasons["BANK_REPORTS"] else "CORPORATE"
        if template == "BANK":
            rows = bank_rows  # TT49 forms: code-matched rows would be note numbers
        values[key] = {"report": report, "rows": rows, "sources": {}, "template": template}
    for key in duplicates:
        skips["DUPLICATE_REPORT_KEY"] += 1
        values.pop(key, None)
    return values, skips


# ---- phrasing ---------------------------------------------------------------
# Development phrasings: the ADR-070 templates and names, plus variants.

DEV_NAMES = {
    "NET_REVENUE": ("doanh thu thuần", "doanh thu thuần về bán hàng và cung cấp dịch vụ"),
    "GROSS_PROFIT": ("lợi nhuận gộp", "lợi nhuận gộp về bán hàng và cung cấp dịch vụ"),
    "PROFIT_BEFORE_TAX": ("lợi nhuận trước thuế", "tổng lợi nhuận kế toán trước thuế"),
    "LNST": ("lợi nhuận sau thuế", "lợi nhuận sau thuế thu nhập doanh nghiệp"),
    "CURRENT_ASSETS": ("tài sản ngắn hạn", "tổng tài sản ngắn hạn"),
    "TOTAL_ASSETS": ("tổng tài sản", "tổng cộng tài sản"),
    "LIABILITIES": ("nợ phải trả", "tổng nợ phải trả"),
    "CURRENT_LIABILITIES": ("nợ ngắn hạn", "nợ phải trả ngắn hạn"),
    "EQUITY": ("vốn chủ sở hữu", "tổng vốn chủ sở hữu"),
    "CFO": ("lưu chuyển tiền thuần từ hoạt động kinh doanh", "dòng tiền thuần từ hoạt động kinh doanh"),
    "CFI": ("lưu chuyển tiền thuần từ hoạt động đầu tư", "dòng tiền thuần từ hoạt động đầu tư"),
    "NET_INTEREST_INCOME": ("thu nhập lãi thuần",),
}
DEV_RATIO_NAMES = {
    "ROE": ("ROE", "tỷ suất lợi nhuận trên vốn chủ sở hữu bình quân (ROE)"),
    "ROA": ("ROA", "tỷ suất lợi nhuận trên tổng tài sản bình quân (ROA)"),
    "GROSS_MARGIN": ("biên lợi nhuận gộp", "tỷ suất lợi nhuận gộp trên doanh thu thuần"),
    "NET_MARGIN": ("biên lợi nhuận ròng", "tỷ suất lợi nhuận sau thuế trên doanh thu thuần"),
    "DEBT_TO_EQUITY": ("hệ số nợ phải trả trên vốn chủ sở hữu", "tỷ lệ nợ/vốn chủ sở hữu (D/E)"),
    "CURRENT_RATIO": ("hệ số thanh toán hiện hành", "khả năng thanh toán hiện hành (current ratio)"),
}
DEV_TEMPLATES = {
    "LOOKUP_FLOW": (
        "{Name} của {company} ({ticker}) năm {year} theo báo cáo tài chính {scope} là bao nhiêu đồng?",
        "Theo BCTC {scope} năm {year}, {ticker} ghi nhận {name} bao nhiêu?",
        "Cho biết {name} năm {year} của {company} ({ticker}) trên báo cáo {scope}.",
        "{ticker} báo cáo {name} năm {year} là bao nhiêu (BCTC {scope})?",
    ),
    "LOOKUP_BALANCE": (
        "{Name} của {company} ({ticker}) tại ngày 31/12/{year} theo báo cáo tài chính {scope} là bao nhiêu?",
        "Cuối năm {year}, {ticker} có {name} là bao nhiêu đồng theo báo cáo {scope}?",
        "Theo bảng cân đối kế toán {scope} năm {year}, {name} của {company} ({ticker}) là bao nhiêu?",
        "{Name} cuối kỳ năm {year} của {ticker} trên BCTC {scope} bằng bao nhiêu?",
    ),
    "GROWTH": (
        ("{Name} của {company} ({ticker}) năm {second} tăng trưởng bao nhiêu phần trăm so với năm {first} "
         "theo báo cáo {scope}?"),
        "Tốc độ tăng trưởng {name} năm {second} so với năm {first} của {ticker} (BCTC {scope}) là bao nhiêu %?",
        "Tăng trưởng {name} của {ticker} giữa năm {first} và năm {second} theo báo cáo {scope} là bao nhiêu phần trăm?",
    ),
    "AVERAGE": (
        "{Name} bình quân của {ticker} theo báo cáo {scope} trong hai năm {first} và {second} là bao nhiêu?",
        "Trung bình {name} của {company} ({ticker}) năm {first} và năm {second} theo BCTC {scope} là bao nhiêu đồng?",
        "Tính {name} trung bình của {ticker} cho hai năm {first}, {second} (báo cáo {scope}).",
    ),
    "RATIO": (
        "{Name} năm {year} của {company} ({ticker}) theo báo cáo {scope} là bao nhiêu?",
        "Theo BCTC {scope}, {name} của {ticker} năm {year} là bao nhiêu?",
        "Hãy tính {name} của {ticker} năm {year} theo số liệu báo cáo {scope}.",
    ),
}
# Holdout phrasings: written for the holdout only, never used for any fix.
HOLDOUT_NAMES = {
    "NET_REVENUE": ("doanh thu thuần", "doanh thu thuần từ bán hàng và cung cấp dịch vụ"),
    "GROSS_PROFIT": ("lợi nhuận gộp", "lãi gộp"),
    "PROFIT_BEFORE_TAX": ("lợi nhuận kế toán trước thuế", "lợi nhuận trước thuế"),
    "LNST": ("lợi nhuận sau thuế", "lãi sau thuế"),
    "CURRENT_ASSETS": ("tài sản ngắn hạn",),
    "TOTAL_ASSETS": ("tổng tài sản", "tổng giá trị tài sản"),
    "LIABILITIES": ("tổng nợ phải trả", "nợ phải trả"),
    "CURRENT_LIABILITIES": ("nợ ngắn hạn",),
    "EQUITY": ("vốn chủ sở hữu", "nguồn vốn chủ sở hữu"),
    "CFO": ("lưu chuyển tiền thuần từ hoạt động kinh doanh", "dòng tiền từ hoạt động kinh doanh"),
    "CFI": ("lưu chuyển tiền thuần từ hoạt động đầu tư", "dòng tiền từ hoạt động đầu tư"),
    "NET_INTEREST_INCOME": ("thu nhập lãi thuần",),
}
HOLDOUT_RATIO_NAMES = {
    "ROE": ("ROE", "lợi nhuận trên vốn chủ sở hữu bình quân (ROE)"),
    "ROA": ("ROA", "lợi nhuận trên tổng tài sản bình quân (ROA)"),
    "GROSS_MARGIN": ("biên lợi nhuận gộp", "tỷ suất lợi nhuận gộp"),
    "NET_MARGIN": ("biên lợi nhuận ròng", "tỷ suất lợi nhuận ròng"),
    "DEBT_TO_EQUITY": ("hệ số nợ trên vốn chủ sở hữu", "tỷ số nợ trên vốn chủ sở hữu (D/E)"),
    "CURRENT_RATIO": ("hệ số thanh toán hiện hành", "tỷ số thanh toán hiện hành"),
}
HOLDOUT_TEMPLATES = {
    "LOOKUP_FLOW": (
        "Năm {year}, {company} ({ticker}) đạt {name} bao nhiêu theo số liệu báo cáo tài chính {scope}?",
        "Xin cho biết {name} mà {ticker} công bố trong báo cáo tài chính {scope} năm {year}.",
        "{ticker} có {name} năm tài chính {year} (báo cáo {scope}) bằng bao nhiêu đồng?",
    ),
    "LOOKUP_BALANCE": (
        "Tính đến ngày 31/12/{year}, {company} ({ticker}) ghi nhận {name} bao nhiêu trên báo cáo {scope}?",
        "Số dư {name} của {ticker} vào cuối năm tài chính {year} theo BCTC {scope} là bao nhiêu?",
        "Trên bảng cân đối kế toán {scope} của {ticker}, {name} tại thời điểm cuối năm {year} là bao nhiêu?",
    ),
    "GROWTH": (
        "So với năm {first}, {name} của {ticker} năm {second} tăng trưởng bao nhiêu % (báo cáo {scope})?",
        "Từ năm {first} đến năm {second}, {name} của {company} ({ticker}) tăng trưởng bao nhiêu phần trăm theo BCTC {scope}?",
    ),
    "AVERAGE": (
        "Giá trị trung bình của {name} hai năm {first} và {second} của {ticker} trên BCTC {scope} là bao nhiêu?",
        "{Name} bình quân giai đoạn {first}–{second} của {company} ({ticker}) theo báo cáo {scope} là bao nhiêu?",
    ),
    "RATIO": (
        "Dựa trên báo cáo tài chính {scope}, {name} của {company} ({ticker}) năm {year} là bao nhiêu?",
        "{Name} của {ticker} trong năm {year} theo số liệu {scope} bằng bao nhiêu?",
    ),
}
# Second holdout (built after the first was scored once and became development
# data): its own phrasings and names, frozen before any fix informed by holdout 1.
HOLDOUT2_NAMES = {
    "NET_REVENUE": ("doanh thu thuần", "doanh thu thuần bán hàng và cung cấp dịch vụ"),
    "GROSS_PROFIT": ("lợi nhuận gộp", "lãi gộp bán hàng"),
    "PROFIT_BEFORE_TAX": ("lợi nhuận trước thuế", "tổng lợi nhuận trước thuế"),
    "LNST": ("lợi nhuận sau thuế", "lợi nhuận ròng"),
    "CURRENT_ASSETS": ("tài sản ngắn hạn", "tổng tài sản ngắn hạn"),
    "TOTAL_ASSETS": ("tổng tài sản", "quy mô tổng tài sản"),
    "LIABILITIES": ("nợ phải trả", "tổng nợ"),
    "CURRENT_LIABILITIES": ("nợ ngắn hạn", "nợ phải trả ngắn hạn"),
    "EQUITY": ("vốn chủ sở hữu", "vốn chủ"),
    "CFO": ("dòng tiền thuần từ hoạt động kinh doanh", "lưu chuyển tiền từ hoạt động kinh doanh"),
    "CFI": ("dòng tiền thuần từ hoạt động đầu tư", "lưu chuyển tiền từ hoạt động đầu tư"),
    "NET_INTEREST_INCOME": ("thu nhập lãi thuần", "thu nhập lãi thuần (NII)"),
}
HOLDOUT2_RATIO_NAMES = {
    "ROE": ("ROE", "tỷ suất sinh lời trên vốn chủ sở hữu (ROE)"),
    "ROA": ("ROA", "tỷ suất sinh lời trên tổng tài sản (ROA)"),
    "GROSS_MARGIN": ("biên lợi nhuận gộp", "biên lãi gộp"),
    "NET_MARGIN": ("biên lợi nhuận ròng", "biên lợi nhuận sau thuế"),
    "DEBT_TO_EQUITY": ("hệ số nợ trên vốn chủ sở hữu", "tỷ lệ nợ phải trả/vốn chủ sở hữu"),
    "CURRENT_RATIO": ("hệ số thanh toán hiện hành", "hệ số thanh toán ngắn hạn"),
}
HOLDOUT2_TEMPLATES = {
    "LOOKUP_FLOW": (
        "Trong năm {year}, {name} của {ticker} trên BCTC {scope} là bao nhiêu?",
        "{company} ({ticker}) ghi nhận {name} năm {year} ở mức nào, theo báo cáo {scope}?",
        "Cho tôi số liệu {name} năm {year} của {ticker} (báo cáo tài chính {scope}).",
    ),
    "LOOKUP_BALANCE": (
        "{Name} của {ticker} tại thời điểm 31/12/{year} trên BCTC {scope} là bao nhiêu?",
        "Cuối năm tài chính {year}, {company} ({ticker}) có {name} bao nhiêu theo báo cáo {scope}?",
        "Cho tôi số dư {name} của {ticker} cuối năm {year} (BCTC {scope}).",
    ),
    "GROWTH": (
        "{Name} của {ticker} năm {second} tăng trưởng bao nhiêu % so với năm {first} (BCTC {scope})?",
        "Mức tăng trưởng {name} của {company} ({ticker}) năm {second} so với năm {first} theo báo cáo {scope} là bao nhiêu phần trăm?",
    ),
    "AVERAGE": (
        "Trung bình {name} của {ticker} hai năm {first} và {second} trên BCTC {scope} là bao nhiêu?",
        "{Name} bình quân hai năm {first}, {second} của {company} ({ticker}) theo báo cáo {scope} là bao nhiêu?",
    ),
    "RATIO": (
        "{Name} của {company} ({ticker}) năm {year} theo BCTC {scope} là bao nhiêu?",
        "Năm {year}, {ticker} có {name} bằng bao nhiêu theo số liệu báo cáo {scope}?",
    ),
}
# Third holdout: neutral seed questions that ``src/evaluation/paraphrase_gold.py``
# rewrites with an LLM, so its wording comes from an author other than the one
# fixing the understanding.
HOLDOUT3_SEED_NAMES = {metric: names[:1] for metric, names in DEV_NAMES.items()}
HOLDOUT3_SEED_RATIO_NAMES = {ratio: names[:1] for ratio, names in DEV_RATIO_NAMES.items()}
HOLDOUT3_SEED_TEMPLATES = {
    "LOOKUP_FLOW": ("{Name} của {company} ({ticker}) năm {year} theo báo cáo tài chính {scope} là bao nhiêu?",),
    "LOOKUP_BALANCE": ("{Name} của {company} ({ticker}) tại ngày 31/12/{year} theo báo cáo tài chính {scope} là bao nhiêu?",),
    "GROWTH": ("{Name} của {company} ({ticker}) năm {second} tăng trưởng bao nhiêu phần trăm so với năm {first} "
               "theo báo cáo tài chính {scope}?",),
    "AVERAGE": ("Trung bình {name} của {company} ({ticker}) hai năm {first} và {second} theo báo cáo tài chính "
                "{scope} là bao nhiêu?",),
    "RATIO": ("{Name} của {company} ({ticker}) năm {year} theo báo cáo tài chính {scope} là bao nhiêu?",),
}
PHRASING = {"dev": (DEV_NAMES, DEV_RATIO_NAMES, DEV_TEMPLATES),
            "holdout": (HOLDOUT_NAMES, HOLDOUT_RATIO_NAMES, HOLDOUT_TEMPLATES),
            "holdout2": (HOLDOUT2_NAMES, HOLDOUT2_RATIO_NAMES, HOLDOUT2_TEMPLATES),
            "holdout3": (HOLDOUT3_SEED_NAMES, HOLDOUT3_SEED_RATIO_NAMES, HOLDOUT3_SEED_TEMPLATES)}

CORPORATE_METRICS = ("NET_REVENUE", "GROSS_PROFIT", "PROFIT_BEFORE_TAX", "LNST", "CURRENT_ASSETS", "TOTAL_ASSETS",
                     "LIABILITIES", "CURRENT_LIABILITIES", "EQUITY", "CFO", "CFI")
BANK_METRICS = ("NET_INTEREST_INCOME", "PROFIT_BEFORE_TAX", "LNST", "TOTAL_ASSETS", "LIABILITIES", "EQUITY")
CORPORATE_RATIOS = ("ROE", "ROA", "GROSS_MARGIN", "NET_MARGIN", "DEBT_TO_EQUITY", "CURRENT_RATIO")
BANK_RATIOS = ("ROE", "ROA")
NO_GROWTH = {"CFO", "CFI"}  # signed flows: growth of a sign change is not meaningful
STATEMENT_OF = {**{metric: spec.statement for metric, spec in SPECS_BY_ID.items()},
                **{metric: statement for metric, (statement, _) in BANK_LABELS.items()}}


@dataclass(frozen=True)
class Counts:
    lookup: int = 3
    growth: int = 2
    average: int = 2
    ratio: int = 4
    bank_lookup: int = 2
    bank_growth: int = 1
    bank_average: int = 1
    bank_ratio: int = 3


def question(split: str, operation: str, choice: int, target: str, company: str, ticker: str, scope: str,
             years: Sequence[int]) -> str:
    names, ratio_names, templates = PHRASING[split]
    options = ratio_names[target] if operation == "RATIO" else names[target]
    name = options[choice % len(options)]
    if operation == "LOOKUP":
        group = templates["LOOKUP_BALANCE" if STATEMENT_OF[target] == BALANCE else "LOOKUP_FLOW"]
    else:
        group = templates[{"GROWTH_RATE": "GROWTH", "AVERAGE": "AVERAGE", "RATIO": "RATIO"}[operation]]
    fields = {"name": name, "Name": name[0].upper() + name[1:], "company": company, "ticker": ticker,
              "scope": _SCOPE_WORD[scope], "year": years[-1], "first": years[0], "second": years[-1]}
    return group[(choice // len(options)) % len(group)].format(**fields)


def earlier_gold_reports(audits: Iterable[Path], manifest: dict[str, Any]) -> set[Key]:
    """Every report cited by an earlier gold case, as (ticker, scope, year)."""
    by_id = {report["report_id"]: report for report in manifest["reports"]}
    used: set[Key] = set()
    for path in audits:
        for case in json.loads(Path(path).read_text(encoding="utf-8"))["cases"]:
            for source in case["sources"]:
                report = by_id.get(source["report_id"])
                if report and report.get("ticker"):
                    used.add((report["ticker"], report["statement_scope"], int(report["report_year"])))
    return used


def plan_split(verified: dict[tuple[str, Key], list[StatementRow]], templates: dict[Key, str], counts: Counts,
               rng: random.Random, excluded: set[Key]) -> list[tuple[str, str, list[tuple[str, Key]]]]:
    """(operation, target, inputs) per case; no input report is in ``excluded``."""
    plan: list[tuple[str, str, list[tuple[str, Key]]]] = []
    by_metric: dict[tuple[str, str], list[Key]] = defaultdict(list)
    for metric_id, key in sorted(verified):
        if key not in excluded:
            by_metric[(templates[key], metric_id)].append(key)

    def free(inputs):
        return all(key not in excluded and (metric, key) in verified for metric, key in inputs)

    for template, metrics, ratios, lookup, growth, average, ratio_count in (
            ("CORPORATE", CORPORATE_METRICS, CORPORATE_RATIOS, counts.lookup, counts.growth, counts.average,
             counts.ratio),
            ("BANK", BANK_METRICS, BANK_RATIOS, counts.bank_lookup, counts.bank_growth, counts.bank_average,
             counts.bank_ratio)):
        for metric_id in metrics:
            keys = by_metric[(template, metric_id)]
            for key in _sample(keys, lookup, rng, set()):
                plan.append(("LOOKUP", metric_id, [(metric_id, key)]))
            pairs = [key for key in keys if free([(metric_id, (key[0], key[1], key[2] - 1))])]
            positive = [key for key in pairs if _decimal(verified, metric_id, (key[0], key[1], key[2] - 1)) > 0
                        and _decimal(verified, metric_id, key) > 0]
            if metric_id not in NO_GROWTH:
                for key in _sample(positive, growth, rng, set()):
                    plan.append(("GROWTH_RATE", metric_id, [(metric_id, (key[0], key[1], key[2] - 1)),
                                                            (metric_id, key)]))
            for key in _sample(pairs, average, rng, set()):
                plan.append(("AVERAGE", metric_id, [(metric_id, (key[0], key[1], key[2] - 1)), (metric_id, key)]))
        for ratio_id in ratios:
            numerator = RATIOS_BY_ID[ratio_id].numerator
            keys = [key for key in by_metric[(template, numerator)]
                    if free(ratio_inputs(ratio_id, key)) and ratio_answer(ratio_id, key, verified) is not None]
            for key in _sample(keys, ratio_count, rng, set()):
                plan.append(("RATIO", ratio_id, ratio_inputs(ratio_id, key)))
    return plan


def negative_cases(split: str, manifest: dict[str, Any], templates: dict[Key, str], rng: random.Random,
                   excluded: set[Key]) -> list[dict[str, Any]]:
    """Must-refuse controls: two of each kind per split."""
    names = {report["ticker"]: report["company_name"] for report in manifest["reports"] if report.get("ticker")}
    years: dict[str, set[int]] = defaultdict(set)
    scopes: dict[tuple[str, int], set[str]] = defaultdict(set)
    for report in manifest["reports"]:
        if report.get("ticker") and report.get("report_year"):
            years[report["ticker"]].add(int(report["report_year"]))
            scopes[(report["ticker"], int(report["report_year"]))].add(report["statement_scope"])
    keys = sorted(key for key in templates if key not in excluded)
    rng.shuffle(keys)
    corporate = [key for key in keys if templates[key] == "CORPORATE"]
    banks = [key for key in keys if templates[key] == "BANK"]
    one_scope = [(ticker, year) for (ticker, year), present in sorted(scopes.items())
                 if len(present & set(_SCOPE_WORD)) == 1 and min(years[ticker]) < year < max(years[ticker])]
    rng.shuffle(one_scope)
    rows: list[dict[str, Any]] = []

    def add(kind: str, text: str) -> None:
        rows.append({"question_id": f"prod-{split}-neg-{len(rows):03d}-{kind.lower()}", "question": text,
                     "expected": "NO_ANSWER", "kind": kind})

    for (ticker, scope, year), metric in zip(corporate[0:2], ("Giá đóng cửa cổ phiếu", "Số lượng nhân viên")):
        add("UNSUPPORTED_METRIC", f"{metric} của {names[ticker]} ({ticker}) cuối năm {year} theo báo cáo "
                                  f"{_SCOPE_WORD[scope]} là bao nhiêu?")
    for ticker, scope, year in corporate[2:4]:
        add("SUB_ANNUAL_PERIOD", f"Lợi nhuận sau thuế 6 tháng đầu năm {year} của {ticker} theo báo cáo "
                                 f"{_SCOPE_WORD[scope]} là bao nhiêu?")
    for ticker, scope, _ in corporate[4:6]:
        add("MISSING_PERIOD", f"Vốn chủ sở hữu của {names[ticker]} ({ticker}) theo báo cáo {_SCOPE_WORD[scope]} "
                              "là bao nhiêu?")
    both = [key for key in corporate[6:] if set(_SCOPE_WORD) <= scopes[(key[0], key[2])]]
    for ticker, _, year in both[:2]:
        add("MISSING_SCOPE", f"Doanh thu thuần của {ticker} năm {year} là bao nhiêu?")
    unknown = {"dev": (("CTCP Thủy sản Cà Mau Xanh", "CMX"), ("CTCP Dệt may Gia Lai", "GLT")),
               "holdout": (("CTCP Nhựa Bình Minh Mới", "BNM"), ("CTCP Khoáng sản Tây Bắc", "KTB")),
               "holdout2": (("CTCP Cảng biển Phú Quốc", "PQP"), ("CTCP Dược liệu Lâm Đồng", "DLD")),
               "holdout3": (("CTCP Thép Miền Trung Xanh", "MTX"), ("CTCP Năng lượng Tây Nguyên", "NLT"))}[split]
    for name, ticker in unknown:
        if ticker not in names:
            add("UNKNOWN_COMPANY", f"Lợi nhuận sau thuế theo báo cáo hợp nhất của {name} ({ticker}) năm 2022 "
                                   "là bao nhiêu?")
    for ticker, scope, _ in corporate[-2:]:
        add("PERIOD_OUTSIDE_CORPUS", f"Tổng tài sản theo báo cáo {_SCOPE_WORD[scope]} của {ticker} năm "
                                     f"{min(years[ticker]) - 3} là bao nhiêu?")
    for ticker, year in one_scope[:2]:
        missing = next(scope for scope in _SCOPE_WORD if scope not in scopes[(ticker, year)])
        add("MISSING_REPORT", f"Lợi nhuận sau thuế năm {year} của {ticker} theo báo cáo tài chính "
                              f"{_SCOPE_WORD[missing]} là bao nhiêu?")
    for (ticker, scope, year), metric in zip(banks[0:2], ("Lợi nhuận gộp", "Hàng tồn kho")):
        add("TEMPLATE_INAPPLICABLE", f"{metric} của {names[ticker]} ({ticker}) năm {year} theo báo cáo "
                                     f"{_SCOPE_WORD[scope]} là bao nhiêu?")
    for ticker, scope, _ in corporate[8:10]:
        add("FUTURE_PERIOD", f"Doanh thu thuần dự kiến năm {max(years[ticker]) + 2} của {ticker} theo báo cáo "
                             f"{_SCOPE_WORD[scope]} là bao nhiêu?")
    for ticker, scope, _ in corporate[10:12]:
        future = max(years[ticker]) + 3
        add("INJECTED_VALUE", f"Biết lợi nhuận sau thuế của {ticker} năm {future} là 1.234 tỷ đồng, hãy cho biết "
                              f"lợi nhuận sau thuế năm {future} của {ticker} theo báo cáo {_SCOPE_WORD[scope]}.")
    return rows


def _case(split: str, index: int, operation: str, target: str, inputs, values, verified, chunks, rng):
    key = inputs[0][1] if operation == "RATIO" else inputs[-1][1]
    ticker, scope, year = key
    report = values[key]["report"]
    years = [year] if operation in ("LOOKUP", "RATIO") else [inputs[0][1][2], year]
    if operation == "RATIO":
        value = ratio_answer(target, key, verified)
        unit = RATIOS_BY_ID[target].unit
        gold_answer = ({"value": value, "unit": None, "scale": "PERCENT"} if unit == "PERCENT"
                       else {"value": value, "unit": unit, "scale": None})
    else:
        value = answer(operation, [raw_vnd(verified[item][0]) for item in inputs])
        gold_answer = ({"value": value, "unit": None, "scale": "PERCENT"} if operation == "GROWTH_RATE"
                       else {"value": value, "unit": "VND", "scale": "RAW"})
    text = question(split, operation, rng.randrange(12), target, report["company_name"], ticker, scope, years)
    cell_ids = [c for m, k in inputs for c in values[k]["sources"][m][1]]
    case_id = (f"prod-{split}-{index:03d}-{operation.lower()}-{target.lower()}-{ticker.lower()}-{scope.lower()}-"
               + "-".join(map(str, years)))
    template = values[key]["template"]
    case = {
        "question_id": case_id, "question": text,
        "company": {"ticker": ticker, "name": report["company_name"]},
        "period": str(year) if operation in ("LOOKUP", "RATIO") else None, "period_kind": "NAM",
        "statement_scope": scope, "metric": target,
        "operation": {"LOOKUP": "NONE", "GROWTH_RATE": "GROWTH", "AVERAGE": "AVERAGE", "RATIO": "RATIO"}[operation],
        "gold_report_id": report["report_id"],
        "gold_table_id": values[key]["sources"][inputs[0][0] if operation == "RATIO" else target][0],
        "gold_paragraph_id": None,
        "gold_chunk_ids": sorted({chunk for cell in cell_ids for chunk in chunks.get(cell, [])}),
        "gold_source_cell_ids": cell_ids,
        "gold_answer": gold_answer,
        "provenance": {"source": SOURCE_NAME, "method": "AUTO_DERIVED", "notes": (
            f"{PRODUCTION_GOLD_VERSION} {split}: {template.lower()} statement row read from raw HTML (TT200 code "
            "and label, or a whole bank label), header-located columns, unit note scaled to VND, confirmed by the "
            "next report's prior-year column; ratios use src/formulas/ratios.py. Pending human review.")},
        "annotation_status": "AUTO_DERIVABLE",
    }
    audit = {
        "question_id": case_id, "split": split, "operation": operation, "target": target, "years": years,
        "template": template, "statement": "RATIO" if operation == "RATIO" else STATEMENT_OF[target],
        "answer": value,
        "sources": [{
            "metric": m, "report_id": values[k]["report"]["report_id"], "source_ref": values[k]["report"]["source_ref"],
            "year": k[2], "label": verified[(m, k)][0].label, "source_scale": verified[(m, k)][0].scale,
            "current_raw": verified[(m, k)][0].current_raw,
            "next_report_prior_raw": values[(k[0], k[1], k[2] + 1)]["rows"][m][0].prior_raw,
            "value_raw_vnd": raw_vnd(verified[(m, k)][0]),
            "table_id": values[k]["sources"][m][0], "cell_ids": values[k]["sources"][m][1]} for m, k in inputs],
    }
    return case, audit


def build(corpus: Path, raw_root: Path, output: Path, *, seeds: dict[str, int], counts: Counts,
          earlier_audits: Sequence[Path], write_splits: Sequence[str] | None = None) -> dict[str, Any]:
    """Build the splits in order; every split excludes the reports of the ones before it.

    ``write_splits`` limits which splits are written, so a later split can be added
    without rewriting (or re-dating) frozen ones; earlier splits are still planned
    to reproduce their exclusions exactly.
    """
    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    values, extraction_skips = extract_values(manifest, raw_root)
    verified, cross_skips = cross_checked(values)
    templates = {key: value["template"] for key, value in values.items()}
    excluded = earlier_gold_reports(earlier_audits, manifest)
    summary: dict[str, Any] = {
        "version": PRODUCTION_GOLD_VERSION, "corpus_artifact_id": manifest["artifact_id"], "seeds": seeds,
        "earlier_gold_reports_excluded": len(excluded), "extracted_reports": len(values),
        "bank_reports": sum(template == "BANK" for template in templates.values()),
        "cross_checked_values": len(verified),
        "cross_checked_values_by_metric": dict(sorted(Counter(m for m, _ in verified).items())),
        "extraction_skips": dict(sorted(extraction_skips.items())),
        "cross_check_skips": dict(sorted(cross_skips.items())), "human_reviewed": False, "splits": {},
    }
    if write_splits is None:  # the original two-split build keeps its exact bytes
        summary["seeds"] = {split: seeds[split] for split in ("dev", "holdout")}
    output.mkdir(parents=True, exist_ok=True)
    for split in [split for split in SPLITS if split in seeds]:
        rng = random.Random(seeds[split])
        plan = plan_split(verified, templates, counts, rng, excluded)
        for _, _, inputs in plan:
            for metric_id, key in inputs:
                entry = values[key]
                if metric_id not in entry["sources"]:
                    source = _source_from_inventory(raw_root, manifest["corpus_id"], entry["report"])
                    located = _locate_cells(source, verified[(metric_id, key)])
                    if located and entry["template"] == "BANK":
                        extra = carry_alternatives(source.raw_text, key[2], verified[(metric_id, key)][0])
                        cells = _locate_cells(source, extra) if extra else None
                        if cells:
                            located = (located[0], located[1] + [c for c in cells[1] if c not in located[1]])
                    entry["sources"][metric_id] = located
        unlocated = sum(1 for _, _, inputs in plan if not all(values[k]["sources"][m] for m, k in inputs))
        plan = [item for item in plan if all(values[k]["sources"][m] for m, k in item[2])]
        chunks = _chunk_ids(corpus, manifest, {c for _, _, inputs in plan for m, k in inputs
                                               for c in values[k]["sources"][m][1]})
        cases, audits = [], []
        for index, (operation, target, inputs) in enumerate(plan):
            case, audit = _case(split, index, operation, target, inputs, values, verified, chunks, rng)
            cases.append(case)
            audits.append(audit)
        negatives = negative_cases(split, manifest, templates, rng, excluded)
        used = {key for _, _, inputs in plan for _, key in inputs}
        excluded |= used  # a later split shares no report with an earlier one
        if write_splits is not None and split not in write_splits:
            summary["splits"][split] = {"seed": seeds[split], "reports_used": len(used), "case_count": len(cases)}
            continue
        directory = output / split
        directory.mkdir(parents=True, exist_ok=True)
        gold_bytes = "".join(json.dumps(case, ensure_ascii=False) + "\n" for case in cases).encode()
        negative_bytes = "".join(json.dumps(case, ensure_ascii=False) + "\n" for case in negatives).encode()
        (directory / "gold.jsonl").write_bytes(gold_bytes)
        (directory / "negative.jsonl").write_bytes(negative_bytes)
        (directory / "all-questions.jsonl").write_bytes(gold_bytes + negative_bytes)
        split_summary = {
            "seed": seeds[split], "case_count": len(cases), "negative_case_count": len(negatives),
            "unlocated_cases_dropped": unlocated, "reports_used": len(used),
            "case_count_by_operation": dict(Counter(a["operation"] for a in audits)),
            "case_count_by_statement": dict(Counter(a["statement"] for a in audits)),
            "case_count_by_template": dict(Counter(a["template"] for a in audits)),
            "case_count_by_target": dict(Counter(a["target"] for a in audits)),
            "negative_count_by_kind": dict(Counter(n["kind"] for n in negatives)),
            "ticker_count": len({case["company"]["ticker"] for case in cases}),
            "gold_sha256": sha256(gold_bytes).hexdigest(), "negative_sha256": sha256(negative_bytes).hexdigest(),
        }
        (directory / "audit.json").write_text(json.dumps({**summary, "split": split, **split_summary, "cases": audits},
                                                         ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        summary["splits"][split] = split_summary
    name = "build-summary.json" if write_splits is None else f"build-summary-{'-'.join(write_splits)}.json"
    (output / name).write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--corpus-artifact", required=True)
    parser.add_argument("--raw-corpus", required=True)
    parser.add_argument("--output", default="gold/production-v1")
    parser.add_argument("--dev-seed", type=int, default=20261016)
    parser.add_argument("--holdout-seed", type=int, default=20261017)
    parser.add_argument("--holdout2-seed", type=int, default=None,
                        help="also plan the second holdout; with --only, write just that split")
    parser.add_argument("--holdout3-seed", type=int, default=None)
    parser.add_argument("--only", action="append", choices=SPLITS, help="write only these splits")
    parser.add_argument("--earlier-audit", action="append", default=[],
                        help="audit JSON of earlier gold; its reports are excluded from both splits")
    args = parser.parse_args(argv)
    seeds = {"dev": args.dev_seed, "holdout": args.holdout_seed}
    if args.holdout2_seed is not None:
        seeds["holdout2"] = args.holdout2_seed
    if args.holdout3_seed is not None:
        seeds["holdout3"] = args.holdout3_seed
    summary = build(Path(args.corpus_artifact), Path(args.raw_corpus), Path(args.output), seeds=seeds,
                    counts=Counts(), earlier_audits=[Path(path) for path in args.earlier_audit],
                    write_splits=args.only)
    print(json.dumps({key: summary[key] for key in ("cross_checked_values", "bank_reports", "splits")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
