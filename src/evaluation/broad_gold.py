"""Build the independent multi-metric gold set from raw annual statements.

The ADR-066 wide set measures one row (LNST). This set applies the same
independent method to other Thông tư 200 lines across all three statements and
to the ADR-069 derived ratios, so Text-to-Pandas is measured beyond the row it
was accepted on. Values never come from the pipeline or its catalog: each is
the raw-HTML row whose ``Mã số`` and accent-folded label both match, read from
header-located current/prior columns and scaled by the unit note to RAW VND. A
value is kept only when report Y+1 repeats it as its prior-year (or opening)
value for the same ticker and scope. Ratios take their definitions from
``src/formulas/ratios.py`` (the single formula source of truth) and compute
with exact Decimal arithmetic over verified inputs only.

Bank templates carry no TT200 line codes for these rows and are not sampled.
Missing, conflicting, unoriented, unknown-unit, partial-period, restatement
and later-restated values are skipped and counted, never guessed. Agent-built,
not human-reviewed.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, getcontext
from hashlib import sha256
import json
from pathlib import Path
import random
import re
from typing import Any
import unicodedata

from src.evaluation.wide_gold import (
    _MULTIPLIER,
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
from src.formulas.ratios import AVERAGE_OPENING_CLOSING, RATIOS_BY_ID
from src.indexing.provenance_sidecar import _source_from_inventory

BROAD_GOLD_VERSION = "m10-broad-gold-v1"
INCOME, BALANCE, CASH_FLOW = "INCOME_STATEMENT", "BALANCE_SHEET", "CASH_FLOW_STATEMENT"


def fold(text: str) -> str:
    """Lowercase, strip Vietnamese diacritics and punctuation (OCR-tolerant matching)."""
    text = unicodedata.normalize("NFD", text.casefold().replace("đ", "d"))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^0-9a-z]+", " ", text).split())


@dataclass(frozen=True)
class MetricSpec:
    metric_id: str
    statement: str
    code: str
    label: str  # regex over the folded label
    names: tuple[str, ...]  # question phrasings
    growth: bool = True  # sampled for growth only when values are positive


SPECS: tuple[MetricSpec, ...] = (
    MetricSpec("NET_REVENUE", INCOME, "10", r"doanh thu thuan",
               ("doanh thu thuần", "doanh thu thuần về bán hàng và cung cấp dịch vụ")),
    MetricSpec("GROSS_PROFIT", INCOME, "20", r"loi nhuan gop",
               ("lợi nhuận gộp", "lợi nhuận gộp về bán hàng và cung cấp dịch vụ")),
    MetricSpec("PROFIT_BEFORE_TAX", INCOME, "50", r"truoc thue",
               ("lợi nhuận trước thuế", "tổng lợi nhuận kế toán trước thuế")),
    MetricSpec("LNST", INCOME, "60", r"loi nhuan sau thue", ("lợi nhuận sau thuế",)),
    MetricSpec("CURRENT_ASSETS", BALANCE, "100", r"tai san ngan han",
               ("tài sản ngắn hạn", "tổng tài sản ngắn hạn")),
    MetricSpec("TOTAL_ASSETS", BALANCE, "270", r"tong .*tai san", ("tổng tài sản", "tổng cộng tài sản")),
    MetricSpec("LIABILITIES", BALANCE, "300", r"no phai tra", ("nợ phải trả", "tổng nợ phải trả")),
    MetricSpec("CURRENT_LIABILITIES", BALANCE, "310", r"no ngan han", ("nợ ngắn hạn", "nợ phải trả ngắn hạn")),
    MetricSpec("EQUITY", BALANCE, "400", r"von chu so huu", ("vốn chủ sở hữu", "tổng vốn chủ sở hữu")),
    MetricSpec("CFO", CASH_FLOW, "20", r"luu chuyen tien thuan tu hoat dong kinh doanh",
               ("lưu chuyển tiền thuần từ hoạt động kinh doanh", "dòng tiền thuần từ hoạt động kinh doanh"),
               growth=False),
    MetricSpec("CFI", CASH_FLOW, "30", r"luu chuyen tien thuan tu hoat dong dau tu",
               ("lưu chuyển tiền thuần từ hoạt động đầu tư", "dòng tiền thuần từ hoạt động đầu tư"),
               growth=False),
)
SPECS_BY_ID = {spec.metric_id: spec for spec in SPECS}
# Whole-word match: "tài sản - ngân hàng" must not read as "tài sản ngắn hạn".
_LABELS = {spec.metric_id: re.compile(rf"(?<![a-z0-9])(?:{spec.label})(?![a-z0-9])") for spec in SPECS}
# LNST questions belong to the wide set; here it is only a ratio input.
QUESTION_METRICS = tuple(spec.metric_id for spec in SPECS if spec.metric_id != "LNST")

RATIO_NAMES = {
    "ROE": ("ROE", "tỷ suất lợi nhuận trên vốn chủ sở hữu bình quân (ROE)"),
    "ROA": ("ROA", "tỷ suất lợi nhuận trên tổng tài sản bình quân (ROA)"),
    "GROSS_MARGIN": ("biên lợi nhuận gộp", "tỷ suất lợi nhuận gộp trên doanh thu thuần"),
    "NET_MARGIN": ("biên lợi nhuận ròng", "tỷ suất lợi nhuận sau thuế trên doanh thu thuần"),
    "DEBT_TO_EQUITY": ("hệ số nợ phải trả trên vốn chủ sở hữu", "tỷ lệ nợ/vốn chủ sở hữu (D/E)"),
    "CURRENT_RATIO": ("hệ số thanh toán hiện hành", "khả năng thanh toán hiện hành (current ratio)"),
}


@dataclass(frozen=True)
class StatementRow:
    """One matched row; field names match ``wide_gold._locate_cells``."""

    metric_id: str
    table_start: int
    row_index: int
    current_column: int
    prior_column: int
    current_raw: str
    prior_raw: str
    current: str
    prior: str
    label: str
    scale: str


def _first(patterns: Sequence[str], text: str) -> int | None:
    found = [match.start() for pattern in patterns for match in [re.search(pattern, text)] if match]
    return min(found) if found else None


def column_orientation(header_text: str, year: int) -> bool | None:
    """True when the current (closing) column precedes the prior (opening) one.

    Balance sheets print ``31/12/Y | 01/01/Y`` or ``Số cuối năm | Số đầu năm``,
    so a bare year cannot orient them; dates and words are tried first and the
    bare year pair only as a last resort. ``None`` means unstated (skipped).
    """
    text = fold(header_text)
    y, p = str(year), str(year - 1)
    closing = (rf"(?<!\d)31 12 {y}(?!\d)", rf"31 thang 12 nam {y}(?!\d)", r"cuoi nam", r"cuoi ky",
               r"nam nay", r"ky nay")
    opening = (rf"(?<!\d)0?1 0?1 {y}(?!\d)", rf"(?<!\d)31 12 {p}(?!\d)", rf"0?1 thang 0?1 nam {y}(?!\d)",
               rf"31 thang 12 nam {p}(?!\d)", r"dau nam", r"dau ky", r"nam truoc", r"ky truoc")
    close_at, open_at = _first(closing, text), _first(opening, text)
    if close_at is None or open_at is None:
        close_at, open_at = _first((rf"(?<!\d){y}(?!\d)",), text), _first((rf"(?<!\d){p}(?!\d)",), text)
    if close_at is None or open_at is None or close_at == open_at:
        return None
    return close_at < open_at


def find_statement_rows(raw_text: str, report_year: int) -> tuple[dict[str, list[StatementRow]], Counter]:
    """Rows per metric (code and label agree) in annual tables, minus conflicts."""
    found: dict[str, list[StatementRow]] = defaultdict(list)
    reasons: Counter = Counter()
    for match in re.finditer(r"<table.*?</table>", raw_text, re.S):
        parser = _Rows()
        parser.feed(match.group())
        rows = [[_clean(cell) for cell in row] for row in parser.rows]
        first_data = next((i for i, row in enumerate(rows) if sum(bool(_VALUE.fullmatch(c)) for c in row) >= 2), None)
        if first_data is None:
            continue
        header_text = " | ".join(" | ".join(row) for row in rows[:first_data])
        for row_index, cells in enumerate(rows[first_data:], first_data):
            columns = [i for i, c in enumerate(cells) if _VALUE.fullmatch(c)]
            metrics = []
            for spec in SPECS:
                if spec.code not in cells:
                    continue
                at = cells.index(spec.code)
                if columns and at > columns[0]:
                    continue  # a line code precedes the amounts; a later cell is a value (e.g. 100%)
                labels = [c for c in cells[:at + 2] if c and c != spec.code and not _VALUE.fullmatch(c)]
                if any(_LABELS[spec.metric_id].search(fold(label)) for label in labels):
                    metrics.append((spec, next(label for label in labels if _LABELS[spec.metric_id].search(fold(label)))))
            if len(metrics) > 1:
                reasons["AMBIGUOUS_ROW"] += 1
            if len(metrics) != 1:
                continue
            spec, label = metrics[0]
            if _RESTATEMENT.search(header_text) and "năm nay" not in header_text.casefold():
                reasons[f"{spec.metric_id}.RESTATEMENT_TABLE"] += 1
                continue
            if _PARTIAL_PERIOD.search(header_text):
                reasons[f"{spec.metric_id}.PARTIAL_PERIOD"] += 1
                continue
            if len(columns) != 2:
                reasons[f"{spec.metric_id}.VALUE_COLUMNS"] += 1
                continue
            orientation = column_orientation(header_text, report_year)
            if orientation is None:
                reasons[f"{spec.metric_id}.ORIENTATION_UNKNOWN"] += 1
                continue
            current, prior = columns if orientation else columns[::-1]
            scale = _scale(header_text, raw_text[max(0, match.start() - 3000):match.start()])
            if scale is None:
                reasons[f"{spec.metric_id}.UNIT_UNKNOWN"] += 1
                continue
            found[spec.metric_id].append(StatementRow(
                spec.metric_id, match.start(), row_index, current, prior, parser.rows[row_index][current],
                parser.rows[row_index][prior], _to_decimal(cells[current]), _to_decimal(cells[prior]), label, scale))
    kept: dict[str, list[StatementRow]] = {}
    for metric_id, rows in found.items():
        if len({raw_vnd(row) + "|" + raw_vnd(row, "prior") for row in rows}) != 1:
            reasons[f"{metric_id}.CONFLICTING_ROWS"] += 1
            continue
        kept[metric_id] = rows
    return kept, reasons


def raw_vnd(row: StatementRow, column: str = "current") -> str:
    return str(int(getattr(row, column)) * _MULTIPLIER[row.scale])


Key = tuple[str, str, int]  # ticker, scope, report year


def extract_report_values(manifest: dict[str, Any], raw_root: Path) -> tuple[dict[Key, dict[str, Any]], Counter]:
    values: dict[Key, dict[str, Any]] = {}
    skips: Counter = Counter()
    duplicates = set()
    for report in manifest["reports"]:
        if report["statement_scope"] not in _SCOPE_WORD:
            skips["UNSUPPORTED_SCOPE"] += 1
            continue
        key = (report["ticker"], report["statement_scope"], int(report["report_year"]))
        if key in values or key in duplicates:
            duplicates.add(key)
            continue
        source = _source_from_inventory(raw_root, manifest["corpus_id"], report)
        rows, reasons = find_statement_rows(source.raw_text, key[2])
        skips.update(reasons)
        values[key] = {"report": report, "rows": rows, "sources": {}}
    for key in duplicates:
        skips["DUPLICATE_REPORT_KEY"] += 1
        values.pop(key, None)
    return values, skips


def cross_checked(values: dict[Key, dict[str, Any]]) -> tuple[dict[tuple[str, Key], list[StatementRow]], Counter]:
    """(metric, key) values confirmed by report Y+1's prior-year/opening column."""
    kept: dict[tuple[str, Key], list[StatementRow]] = {}
    skips: Counter = Counter()
    for key, value in values.items():
        later = values.get((key[0], key[1], key[2] + 1))
        for metric_id, rows in value["rows"].items():
            later_rows = None if later is None else later["rows"].get(metric_id)
            if later_rows is None:
                skips[f"{metric_id}.NO_NEXT_YEAR_VALUE"] += 1
            elif raw_vnd(later_rows[0], "prior") != raw_vnd(rows[0]):
                skips[f"{metric_id}.RESTATED_IN_NEXT_REPORT"] += 1
            else:
                kept[(metric_id, key)] = rows
    return kept, skips


def _decimal(verified: dict[tuple[str, Key], list[StatementRow]], metric_id: str, key: Key) -> Decimal:
    return Decimal(raw_vnd(verified[(metric_id, key)][0]))


def ratio_inputs(ratio_id: str, key: Key) -> list[tuple[str, Key]]:
    ratio = RATIOS_BY_ID[ratio_id]
    inputs = [(ratio.numerator, key), (ratio.denominator, key)]
    if ratio.denominator_basis == AVERAGE_OPENING_CLOSING:
        inputs.append((ratio.denominator, (key[0], key[1], key[2] - 1)))
    return inputs


def ratio_answer(ratio_id: str, key: Key, verified: dict[tuple[str, Key], list[StatementRow]]) -> str | None:
    """Exact ratio from verified inputs; None when an input is missing or the denominator is not positive."""
    ratio = RATIOS_BY_ID[ratio_id]
    inputs = ratio_inputs(ratio_id, key)
    if any(item not in verified for item in inputs):
        return None
    getcontext().prec = 50
    numerator = _decimal(verified, *inputs[0])
    denominators = [_decimal(verified, *item) for item in inputs[1:]]
    denominator = sum(denominators) / len(denominators)
    if any(value <= 0 for value in denominators):
        return None
    return format((numerator / denominator * Decimal(ratio.multiplier)).normalize(), "f")


def answer(operation: str, values: Sequence[str]) -> str:
    getcontext().prec = 50
    numbers = [Decimal(value) for value in values]
    if operation == "LOOKUP":
        result = numbers[0]
    elif operation == "AVERAGE":
        result = sum(numbers) / len(numbers)
    else:
        result = (numbers[1] - numbers[0]) / numbers[0] * 100
    return format(result.normalize(), "f")


_LOOKUP_FLOW = (
    "{Name} của {company} ({ticker}) năm {year} theo báo cáo tài chính {scope} là bao nhiêu đồng?",
    "Theo BCTC {scope} năm {year}, {ticker} ghi nhận {name} bao nhiêu?",
    "Cho biết {name} năm {year} của {company} ({ticker}) trên báo cáo {scope}.",
)
_LOOKUP_BALANCE = (
    "{Name} của {company} ({ticker}) tại ngày 31/12/{year} theo báo cáo tài chính {scope} là bao nhiêu?",
    "Cuối năm {year}, {ticker} có {name} là bao nhiêu đồng theo báo cáo {scope}?",
    "Theo bảng cân đối kế toán {scope} năm {year}, {name} của {company} ({ticker}) là bao nhiêu?",
)
_GROWTH = (
    ("{Name} của {company} ({ticker}) năm {second} tăng trưởng bao nhiêu phần trăm so với năm {first} "
     "theo báo cáo {scope}?"),
    "Tốc độ tăng trưởng {name} năm {second} so với năm {first} của {ticker} (BCTC {scope}) là bao nhiêu %?",
)
_AVERAGE = (
    "{Name} bình quân của {ticker} theo báo cáo {scope} trong hai năm {first} và {second} là bao nhiêu?",
    "Trung bình {name} của {company} ({ticker}) năm {first} và năm {second} theo BCTC {scope} là bao nhiêu đồng?",
)
_RATIO = (
    "{Name} năm {year} của {company} ({ticker}) theo báo cáo {scope} là bao nhiêu?",
    "Theo BCTC {scope}, {name} của {ticker} năm {year} là bao nhiêu?",
)


def question(operation: str, choice: int, names: Sequence[str], statement: str | None, company: str,
             ticker: str, scope: str, years: Sequence[int]) -> str:
    name = names[choice % len(names)]
    fields = {"name": name, "Name": name[0].upper() + name[1:], "company": company, "ticker": ticker,
              "scope": _SCOPE_WORD[scope], "year": years[-1], "first": years[0], "second": years[-1]}
    if operation == "LOOKUP":
        templates = _LOOKUP_BALANCE if statement == BALANCE else _LOOKUP_FLOW
    else:
        templates = {"GROWTH_RATE": _GROWTH, "AVERAGE": _AVERAGE, "RATIO": _RATIO}[operation]
    return templates[(choice // len(names)) % len(templates)].format(**fields)


def excluded_units(gold_paths: Sequence[Path]) -> dict[str, set]:
    """(ticker, scope, last year) already asked per metric/ratio in earlier gold files."""
    used: dict[str, set] = defaultdict(set)
    for path in gold_paths:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.strip():
                case = json.loads(line)
                year = int(case["question_id"].rsplit("-", 1)[1])
                used[case["metric"]].add((case["company"]["ticker"], case["statement_scope"], year))
    return used


def build(corpus: Path, raw_root: Path, output: Path, *, seed: int, counts: dict[str, int],
          exclude: dict[str, set] | None = None) -> dict[str, Any]:
    exclude = exclude or {}
    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    values, extraction_skips = extract_report_values(manifest, raw_root)
    verified, cross_skips = cross_checked(values)
    rng = random.Random(seed)
    by_metric: dict[str, list[Key]] = defaultdict(list)
    for metric_id, key in sorted(verified):
        by_metric[metric_id].append(key)

    plan: list[tuple[str, str, list[tuple[str, Key]]]] = []  # operation, metric/ratio, inputs
    for metric_id in QUESTION_METRICS:
        keys = by_metric[metric_id]
        for key in _sample(keys, counts["LOOKUP"], rng, set(exclude.get(metric_id, ()))):
            plan.append(("LOOKUP", metric_id, [(metric_id, key)]))
        pairs = [key for key in keys if (metric_id, (key[0], key[1], key[2] - 1)) in verified]
        for operation in ("GROWTH_RATE", "AVERAGE"):
            if operation == "GROWTH_RATE":
                if not SPECS_BY_ID[metric_id].growth:
                    continue
                candidates = [key for key in pairs if _decimal(verified, metric_id, (key[0], key[1], key[2] - 1)) > 0
                              and _decimal(verified, metric_id, key) > 0]
            else:
                candidates = pairs
            for key in _sample(candidates, counts[operation], rng, set(exclude.get(metric_id, ()))):
                plan.append((operation, metric_id, [(metric_id, (key[0], key[1], key[2] - 1)), (metric_id, key)]))
    for ratio_id in RATIO_NAMES:
        keys = sorted({key for metric_id, key in verified if metric_id == RATIOS_BY_ID[ratio_id].numerator})
        candidates = [key for key in keys if ratio_answer(ratio_id, key, verified) is not None]
        for key in _sample(candidates, counts["RATIO"], rng, set(exclude.get(ratio_id, ()))):
            plan.append(("RATIO", ratio_id, ratio_inputs(ratio_id, key)))

    for _, _, inputs in plan:
        for metric_id, key in inputs:
            entry = values[key]
            if metric_id not in entry["sources"]:
                source = _source_from_inventory(raw_root, manifest["corpus_id"], entry["report"])
                entry["sources"][metric_id] = _locate_cells(source, verified[(metric_id, key)])
    unlocated = sum(1 for _, _, inputs in plan if not all(values[k]["sources"][m] for m, k in inputs))
    plan = [item for item in plan if all(values[k]["sources"][m] for m, k in item[2])]
    chunks = _chunk_ids(corpus, manifest, {c for _, _, inputs in plan for m, k in inputs
                                           for c in values[k]["sources"][m][1]})

    cases, audits = [], []
    for index, (operation, target, inputs) in enumerate(plan):
        key = inputs[0][1] if operation == "RATIO" else inputs[-1][1]
        ticker, scope, year = key
        report = values[key]["report"]
        years = [year] if operation in ("LOOKUP", "RATIO") else [inputs[0][1][2], year]
        if operation == "RATIO":
            names, statement = RATIO_NAMES[target], None
            value = ratio_answer(target, key, verified)
            unit = RATIOS_BY_ID[target].unit
            gold_answer = ({"value": value, "unit": None, "scale": "PERCENT"} if unit == "PERCENT"
                           else {"value": value, "unit": unit, "scale": None})
        else:
            names, statement = SPECS_BY_ID[target].names, SPECS_BY_ID[target].statement
            value = answer(operation, [raw_vnd(verified[item][0]) for item in inputs])
            gold_answer = ({"value": value, "unit": None, "scale": "PERCENT"} if operation == "GROWTH_RATE"
                           else {"value": value, "unit": "VND", "scale": "RAW"})
        text = question(operation, rng.randrange(12), names, statement, report["company_name"], ticker, scope, years)
        cell_ids = [c for m, k in inputs for c in values[k]["sources"][m][1]]
        case_id = f"broad-{index:03d}-{operation.lower()}-{target.lower()}-{ticker.lower()}-{scope.lower()}-" \
                  + "-".join(map(str, years))
        cases.append({
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
                f"{BROAD_GOLD_VERSION}: TT200 row with agreeing Mã số and label from raw HTML, header-located "
                "columns, unit note scaled to VND, cross-checked against the next report's prior-year column; "
                "ratios use src/formulas/ratios.py definitions. Not human-reviewed.")},
            "annotation_status": "VERIFIED",
        })
        audits.append({"question_id": case_id, "operation": operation, "target": target, "years": years,
                       "answer": value, "sources": [{
                           "metric": m, "report_id": values[k]["report"]["report_id"],
                           "source_ref": values[k]["report"]["source_ref"], "year": k[2],
                           "label": verified[(m, k)][0].label, "source_scale": verified[(m, k)][0].scale,
                           "current_raw": verified[(m, k)][0].current_raw,
                           "next_report_prior_raw": values[(k[0], k[1], k[2] + 1)]["rows"][m][0].prior_raw,
                           "value_raw_vnd": raw_vnd(verified[(m, k)][0]),
                           "table_id": values[k]["sources"][m][0], "cell_ids": values[k]["sources"][m][1]}
                           for m, k in inputs]})

    negatives = negative_cases(manifest, verified, rng)
    output.mkdir(parents=True, exist_ok=True)
    gold_bytes = "".join(json.dumps(case, ensure_ascii=False) + "\n" for case in cases).encode()
    negative_bytes = "".join(json.dumps(case, ensure_ascii=False) + "\n" for case in negatives).encode()
    (output / "broad.jsonl").write_bytes(gold_bytes)
    (output / "broad-negative.jsonl").write_bytes(negative_bytes)
    (output / "broad-all-questions.jsonl").write_bytes(gold_bytes + negative_bytes)
    summary = {
        "version": BROAD_GOLD_VERSION, "seed": seed, "corpus_artifact_id": manifest["artifact_id"],
        **({"excluded_units": sum(len(units) for units in exclude.values())} if exclude else {}),
        "report_count": len(manifest["reports"]), "extracted_reports": len(values),
        "cross_checked_values": len(verified),
        "cross_checked_values_by_metric": dict(sorted(Counter(m for m, _ in verified).items())),
        "extraction_skips": dict(sorted(extraction_skips.items())), "cross_check_skips": dict(sorted(cross_skips.items())),
        "unlocated_cases_dropped": unlocated, "case_count": len(cases),
        "case_count_by_operation": dict(Counter(operation for operation, _, _ in plan)),
        "case_count_by_target": dict(Counter(target for _, target, _ in plan)),
        "ticker_count": len({case["company"]["ticker"] for case in cases}),
        "year_counts": dict(sorted(Counter(int(c["question_id"].rsplit("-", 1)[1]) for c in cases).items())),
        "source_scale_counts": dict(Counter(verified[(m, k)][0].scale for _, _, inputs in plan for m, k in inputs)),
        "negative_case_count": len(negatives), "human_reviewed": False,
        "gold_sha256": sha256(gold_bytes).hexdigest(), "negative_sha256": sha256(negative_bytes).hexdigest(),
    }
    (output / "broad-audit.json").write_text(
        json.dumps({**summary, "cases": audits}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def negative_cases(manifest: dict[str, Any], verified: dict[tuple[str, Key], list[StatementRow]],
                   rng: random.Random) -> list[dict[str, Any]]:
    """Questions the v1 catalog cannot answer: each kind three times."""
    keys = sorted({key for metric_id, key in verified if metric_id in ("TOTAL_ASSETS", "NET_REVENUE")})
    rng.shuffle(keys)
    names = {(r["ticker"]): r["company_name"] for r in manifest["reports"]}
    years: dict[str, set] = defaultdict(set)
    scopes: dict[tuple[str, int], set] = defaultdict(set)
    for report in manifest["reports"]:
        years[report["ticker"]].add(int(report["report_year"]))
        scopes[(report["ticker"], int(report["report_year"]))].add(report["statement_scope"])
    rows: list[dict[str, Any]] = []

    def add(kind: str, text: str) -> None:
        rows.append({"question_id": f"broad-neg-{len(rows):03d}-{kind.lower()}", "question": text,
                     "expected": "NO_ANSWER", "kind": kind})

    for (ticker, scope, year), metric in zip(keys[0:3], ("Giá đóng cửa cổ phiếu", "Vốn hóa thị trường",
                                                         "Số lượng nhân viên")):
        add("UNSUPPORTED_METRIC", f"{metric} của {names[ticker]} ({ticker}) cuối năm {year} theo báo cáo "
                                  f"{_SCOPE_WORD[scope]} là bao nhiêu?")
    for ticker, scope, year in keys[3:6]:
        add("SUB_ANNUAL_PERIOD", f"Doanh thu thuần quý 3 năm {year} của {ticker} theo báo cáo "
                                 f"{_SCOPE_WORD[scope]} là bao nhiêu?")
    for ticker, scope, _ in keys[6:9]:
        add("MISSING_PERIOD", f"Tổng tài sản của {names[ticker]} ({ticker}) theo báo cáo {_SCOPE_WORD[scope]} "
                              "là bao nhiêu?")
    both = [key for key in keys[9:] if {"HOP_NHAT", "RIENG"} <= scopes[(key[0], key[2])]]
    for ticker, _, year in both[:3]:
        add("MISSING_SCOPE", f"Vốn chủ sở hữu của {ticker} tại ngày 31/12/{year} là bao nhiêu?")
    for name, ticker in (("CTCP Thép Bắc Trung Bộ", "TBT"), ("CTCP Dược phẩm Hải Vân", "DHV"),
                         ("Tổng Công ty Cảng Sông Hồng", "SHP")):
        add("UNKNOWN_COMPANY", f"Doanh thu thuần theo báo cáo hợp nhất của {name} ({ticker}) năm 2021 là bao nhiêu?")
    for ticker, scope, _ in keys[-3:]:
        add("PERIOD_OUTSIDE_CORPUS", f"Tổng tài sản theo báo cáo {_SCOPE_WORD[scope]} của {ticker} năm "
                                     f"{min(years[ticker]) - 4} là bao nhiêu?")
    return rows


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-artifact", required=True)
    parser.add_argument("--raw-corpus", required=True)
    parser.add_argument("--output", default="gold/broad")
    parser.add_argument("--seed", type=int, default=20261014)
    parser.add_argument("--lookup", type=int, default=5, help="per metric")
    parser.add_argument("--growth", type=int, default=3, help="per positive-valued metric")
    parser.add_argument("--average", type=int, default=3, help="per metric")
    parser.add_argument("--ratio", type=int, default=6, help="per ratio")
    parser.add_argument("--exclude-gold", action="append", default=[],
                        help="earlier gold JSONL whose (metric, ticker, scope, year) units must not be reused")
    args = parser.parse_args(argv)
    summary = build(Path(args.corpus_artifact), Path(args.raw_corpus), Path(args.output), seed=args.seed,
                    counts={"LOOKUP": args.lookup, "GROWTH_RATE": args.growth, "AVERAGE": args.average,
                            "RATIO": args.ratio},
                    exclude=excluded_units([Path(path) for path in args.exclude_gold]))
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
