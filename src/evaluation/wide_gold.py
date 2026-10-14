"""Build the wide real-data LNST gold set from raw annual statements.

Gold values never come from the pipeline (ADR-066). Each value is the LNST total
row read from raw HTML: Thông tư 200 ``Mã số 60`` or the bank template's Roman
numeral "Lợi nhuận sau thuế" line. Year columns are oriented by their headers,
the unit note scales it to RAW VND, and it is cross-checked: the current-year
value in report Y must equal the prior-year column of report Y+1 (same ticker
and scope). Rows that are missing, conflicting, of unknown unit, partial-period,
in restatement tables or restated later are skipped and counted, never guessed.

The sample is deterministic (seeded, round-robin over tickers) across LOOKUP,
GROWTH_RATE and AVERAGE. A separate expected-non-answer set covers questions
v1 must not answer (unsupported metric, missing period/scope, unknown company,
period outside the corpus).
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, getcontext
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path
import random
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.indexing.document_parser import parse_document
from src.indexing.provenance_sidecar import _source_from_inventory

WIDE_GOLD_VERSION = "m10-wide-lnst-gold-v1"
SOURCE_NAME = "HuuDong03uet/ViFinQA raw annual financial statements"
_VALUE = re.compile(r"\(?-?\d{1,3}(?:\.\d{3})+\)?")
_CODE_HEADER = re.compile(r"^m[ãa]\s*s[ốo]$", re.IGNORECASE)
_LNST_LABEL = re.compile(r"lợi nhuận sau thuế", re.IGNORECASE)
# Bank (TT49-style) income statements: a Roman-numeral line "Lợi nhuận sau thuế".
_BANK_LABEL = re.compile(r"^(?:[IVXL]+[.)]?\s*)?lợi nhuận sau thuế$", re.IGNORECASE)
_ROMAN = re.compile(r"^[IVXL]+[.)]?$")
_RESTATEMENT = re.compile(r"điều chỉnh|trình bày lại|đã báo cáo|số liệu trước|thay đổi", re.IGNORECASE)
_PARTIAL_PERIOD = re.compile(r"từ ngày|từ \d|\bquý\b|6 tháng|sáu tháng", re.IGNORECASE)
_MILLION = re.compile(r"triệu", re.IGNORECASE)
_THOUSAND = re.compile(r"nghìn|ngàn", re.IGNORECASE)
_UNIT_NOTE = re.compile(r"đơn vị(?: tính)?\s*:?\s*([^<\n]{0,30})", re.IGNORECASE)
_SCOPE_WORD = {"HOP_NHAT": "hợp nhất", "RIENG": "riêng"}


class _Rows(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: List[List[str]] = []
        self._row: List[str] = []
        self._cell: Optional[List[str]] = None

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag == "tr":
            self._row = []
        if tag in ("td", "th"):
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._cell is not None:
            self._row.append("".join(self._cell))
            self._cell = None
        if tag == "tr":
            self.rows.append(self._row)


def _clean(text: str) -> str:
    return " ".join(text.split())


def _to_decimal(text: str) -> Optional[str]:
    raw = _clean(text)
    if not _VALUE.fullmatch(raw):
        return None
    negative = raw.startswith("(") or raw.startswith("-")
    digits = raw.strip("()-").replace(".", "")
    return ("-" if negative and int(digits) else "") + str(int(digits))


@dataclass(frozen=True)
class LnstRow:
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
    template: str


def _scale(header_text: str, before: str) -> Optional[str]:
    """RAW/THOUSAND/MILLION from the table header, else the nearest unit note."""
    notes = _UNIT_NOTE.findall(before)
    for text in (header_text, notes[-1] if notes else ""):
        if _MILLION.search(text):
            return "MILLION"
        if _THOUSAND.search(text):
            return "THOUSAND"
        if re.search(r"vnd|đồng", text, re.IGNORECASE):
            return "RAW"
    return None


def _orientation(header_text: str, report_year: int) -> Optional[bool]:
    """True when the current year precedes the prior year, None when unstated."""
    text = header_text.casefold()
    pairs = ((str(report_year), str(report_year - 1)), ("năm nay", "năm trước"))
    for current, prior in pairs:
        if current in text and prior in text:
            return text.index(current) < text.index(prior)
    return None


def find_lnst_rows(raw_text: str, report_year: int) -> Tuple[List[LnstRow], Optional[str]]:
    """Every LNST total row (TT200 Mã số 60, or the bank template) in annual tables."""
    found: List[LnstRow] = []
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
            if "60" in cells and any(_LNST_LABEL.search(c) for c in cells[:cells.index("60") + 2] if c != "60"):
                template = "TT200"
                label = next(c for c in cells if c != "60" and _LNST_LABEL.search(c))
            elif any(_BANK_LABEL.match(c) for c in cells) and (
                    any(_ROMAN.match(c) for c in cells) or any(_ROMAN.match(c.split()[0]) for c in cells if c.split())):
                template = "BANK"
                label = next(c for c in cells if _BANK_LABEL.match(c))
            else:
                continue
            if _RESTATEMENT.search(header_text) and "năm nay" not in header_text.casefold():
                reasons["RESTATEMENT_TABLE"] += 1
                continue
            if _PARTIAL_PERIOD.search(header_text):
                reasons["PARTIAL_PERIOD"] += 1
                continue
            columns = [i for i, c in enumerate(cells) if _VALUE.fullmatch(c)]
            if len(columns) != 2:
                reasons["VALUE_COLUMNS"] += 1
                continue
            orientation = _orientation(header_text, report_year)
            current, prior = columns if orientation in (True, None) else columns[::-1]
            scale = _scale(header_text, raw_text[max(0, match.start() - 3000):match.start()])
            if scale is None:
                reasons["UNIT_UNKNOWN"] += 1
                continue
            found.append(LnstRow(match.start(), row_index, current, prior, parser.rows[row_index][current],
                                 parser.rows[row_index][prior], _to_decimal(cells[current]),
                                 _to_decimal(cells[prior]), label, scale, template))
    if any(row.template == "TT200" for row in found):
        found = [row for row in found if row.template == "TT200"]
    if not found:
        return [], (reasons.most_common(1)[0][0] if reasons else "NO_LNST_ROW")
    if len({(row.current, row.prior, row.scale) for row in found}) != 1:
        return [], "CONFLICTING_LNST_ROWS"
    return found, None


_MULTIPLIER = {"RAW": 1, "THOUSAND": 10**3, "MILLION": 10**6}


def raw_vnd(row: LnstRow, column: str = "current") -> str:
    """The row value converted to RAW VND (exact integer arithmetic)."""
    return str(int(getattr(row, column)) * _MULTIPLIER[row.scale])


def _locate_cells(source: Any, rows: Sequence[LnstRow]) -> Optional[Tuple[str, List[str]]]:
    document = parse_document(source)
    tables = {table.source_span.start: table for page in document.pages for table in page.tables}
    table_id, cell_ids = None, []
    for row in rows:
        table = tables.get(row.table_start)
        if table is None:
            return None
        cells = [cell for cell in table.cells if cell.source_row_index == row.row_index
                 and cell.source_cell_index == row.current_column and cell.extracted_text == row.current_raw]
        if len(cells) != 1:
            return None
        table_id = table_id or table.table_id
        cell_ids.append(cells[0].cell_id)
    return table_id, cell_ids


def extract_report_values(manifest: Dict[str, Any], raw_root: Path) -> Tuple[Dict[Tuple[str, str, int], Dict[str, Any]], Counter]:
    values: Dict[Tuple[str, str, int], Dict[str, Any]] = {}
    skips: Counter = Counter()
    for report in manifest["reports"]:
        if report["statement_scope"] not in _SCOPE_WORD:
            skips["UNSUPPORTED_SCOPE"] += 1
            continue
        source = _source_from_inventory(raw_root, manifest["corpus_id"], report)
        rows, reason = find_lnst_rows(source.raw_text, int(report["report_year"]))
        if reason:
            skips[reason] += 1
            continue
        key = (report["ticker"], report["statement_scope"], int(report["report_year"]))
        if key in values:
            skips["DUPLICATE_REPORT_KEY"] += 1
            values[key]["duplicate"] = True
            continue
        values[key] = {"report": report, "rows": rows, "source": None, "duplicate": False}
    for key in [key for key, value in values.items() if value["duplicate"]]:
        del values[key]
    return values, skips


def cross_checked(values: Dict[Tuple[str, str, int], Dict[str, Any]]) -> Tuple[Dict[Tuple[str, str, int], Dict[str, Any]], Counter]:
    """Keep report-year values confirmed by the next report's prior-year column."""
    kept, skips = {}, Counter()
    for (ticker, scope, year), value in values.items():
        later = values.get((ticker, scope, year + 1))
        if later is None:
            skips["NO_NEXT_YEAR_REPORT"] += 1
        elif raw_vnd(later["rows"][0], "prior") != raw_vnd(value["rows"][0]):
            skips["RESTATED_IN_NEXT_REPORT"] += 1
        else:
            kept[(ticker, scope, year)] = value
    return kept, skips


def _question(operation: str, template: int, name: str, ticker: str, scope: str, years: Sequence[int]) -> str:
    word = _SCOPE_WORD[scope]
    if operation == "LOOKUP":
        (year,) = years
        return [
            f"Lợi nhuận sau thuế TNDN theo báo cáo {word} của {name} ({ticker}) năm {year} là bao nhiêu đồng?",
            f"Theo báo cáo tài chính {word} năm {year}, {name} ({ticker}) có lợi nhuận sau thuế thu nhập doanh nghiệp là bao nhiêu?",
            f"LNST năm {year} của {ticker} theo báo cáo {word} là bao nhiêu?",
        ][template % 3]
    first, second = years
    if operation == "GROWTH_RATE":
        return [
            f"Tốc độ tăng trưởng lợi nhuận sau thuế TNDN theo báo cáo {word} của {name} ({ticker}) năm {second} so với năm {first} là bao nhiêu phần trăm?",
            f"Lợi nhuận sau thuế của {ticker} theo báo cáo {word} năm {second} tăng trưởng bao nhiêu % so với năm {first}?",
        ][template % 2]
    return [
        f"Trung bình lợi nhuận sau thuế TNDN theo báo cáo {word} của {name} ({ticker}) trong năm {first} và năm {second} là bao nhiêu đồng?",
        f"LNST trung bình của {ticker} theo báo cáo {word} giai đoạn năm {first} và năm {second} là bao nhiêu?",
    ][template % 2]


def _answer(operation: str, values: Sequence[str]) -> str:
    getcontext().prec = 50
    numbers = [Decimal(value) for value in values]
    if operation == "LOOKUP":
        result = numbers[0]
    elif operation == "AVERAGE":
        result = sum(numbers) / len(numbers)
    else:
        result = (numbers[1] - numbers[0]) / numbers[0] * 100
    return format(result.normalize(), "f")


def _sample(units: Sequence[Tuple[str, str, int]], count: int, rng: random.Random, used: set) -> List[Tuple[str, str, int]]:
    by_ticker: Dict[str, List[Tuple[str, str, int]]] = {}
    for unit in units:
        by_ticker.setdefault(unit[0], []).append(unit)
    tickers = sorted(by_ticker)
    rng.shuffle(tickers)
    for ticker in tickers:
        rng.shuffle(by_ticker[ticker])
    picked: List[Tuple[str, str, int]] = []
    while len(picked) < count and any(by_ticker.values()):
        for ticker in tickers:
            while by_ticker[ticker] and by_ticker[ticker][-1] in used:
                by_ticker[ticker].pop()
            if by_ticker[ticker] and len(picked) < count:
                unit = by_ticker[ticker].pop()
                picked.append(unit)
                used.add(unit)
    return picked


def _chunk_ids(corpus: Path, manifest: Dict[str, Any], cells: set) -> Dict[str, List[str]]:
    found: Dict[str, List[str]] = {}
    for shard in manifest["shards"]:
        with (corpus / shard["file"]).open(encoding="utf-8") as handle:
            for line in handle:
                if '"TABLE"' not in line:
                    continue
                chunk = json.loads(line)["chunk"]
                for cell in cells.intersection(chunk["primary_source_cell_ids"]):
                    found.setdefault(cell, []).append(chunk["chunk_id"])
    return found


def build(corpus: Path, raw_root: Path, output: Path, *, seed: int, counts: Dict[str, int],
          exclude: Sequence[Tuple[str, str, int]] = ()) -> Dict[str, Any]:
    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    values, extraction_skips = extract_report_values(manifest, raw_root)
    verified, cross_skips = cross_checked(values)
    rng = random.Random(seed)
    used: set = set(exclude)
    single = sorted(verified)
    pairs = sorted(key for key in verified if (key[0], key[1], key[2] - 1) in verified)
    plan: List[Tuple[str, List[Tuple[str, str, int]]]] = []
    for unit in _sample(single, counts["LOOKUP"], rng, used):
        plan.append(("LOOKUP", [unit]))
    for operation in ("GROWTH_RATE", "AVERAGE"):
        candidates = [key for key in pairs if Decimal(raw_vnd(verified[(key[0], key[1], key[2] - 1)]["rows"][0])) != 0
                      or operation == "AVERAGE"]
        for unit in _sample(candidates, counts[operation], rng, used):
            plan.append((operation, [(unit[0], unit[1], unit[2] - 1), unit]))

    for _, units in plan:
        for unit in units:
            entry = verified[unit]
            if entry["source"] is None:
                source = _source_from_inventory(raw_root, manifest["corpus_id"], entry["report"])
                entry["source"] = _locate_cells(source, entry["rows"])
    plan = [(operation, units) for operation, units in plan if all(verified[u]["source"] for u in units)]
    chunks = _chunk_ids(corpus, manifest, {c for _, units in plan for u in units for c in verified[u]["source"][1]})

    cases, audits = [], []
    for index, (operation, units) in enumerate(plan):
        entries = [verified[unit] for unit in units]
        ticker, scope = units[0][0], units[0][1]
        name = entries[0]["report"]["company_name"]
        years = [unit[2] for unit in units]
        question = _question(operation, rng.randrange(6), name, ticker, scope, years)
        answer = _answer(operation, [raw_vnd(entry["rows"][0]) for entry in entries])
        cell_ids = [cell for entry in entries for cell in entry["source"][1]]
        last = entries[-1]
        case_id = f"wide-{index:03d}-{operation.lower()}-{ticker.lower()}-{scope.lower()}-{'-'.join(map(str, years))}"
        cases.append({
            "question_id": case_id, "question": question,
            "company": {"ticker": ticker, "name": name},
            "period": str(years[0]) if operation == "LOOKUP" else None, "period_kind": "NAM",
            "statement_scope": scope, "metric": "LNST",
            "operation": {"LOOKUP": "NONE", "GROWTH_RATE": "GROWTH", "AVERAGE": "AVERAGE"}[operation],
            "gold_report_id": last["report"]["report_id"], "gold_table_id": last["source"][0],
            "gold_paragraph_id": None,
            "gold_chunk_ids": sorted({chunk for cell in cell_ids for chunk in chunks.get(cell, [])}),
            "gold_source_cell_ids": cell_ids,
            "gold_answer": ({"value": answer, "unit": None, "scale": "PERCENT"} if operation == "GROWTH_RATE"
                            else {"value": answer, "unit": "VND", "scale": "RAW"}),
            "provenance": {"source": SOURCE_NAME, "method": "AUTO_DERIVED", "notes": (
                f"{WIDE_GOLD_VERSION}: TT200 Mã số 60 row from raw HTML, header-located year columns, "
                "VND unit, cross-checked against the next report's prior-year column. "
                "Not human-reviewed.")},
            "annotation_status": "VERIFIED",
        })
        audits.append({"question_id": case_id, "operation": operation, "years": years, "answer": answer,
                       "sources": [{"report_id": e["report"]["report_id"], "source_ref": e["report"]["source_ref"],
                                    "label": e["rows"][0].label, "template": e["rows"][0].template,
                                    "source_scale": e["rows"][0].scale, "current_raw": e["rows"][0].current_raw,
                                    "next_report_prior_raw": values[(u[0], u[1], u[2] + 1)]["rows"][0].prior_raw,
                                    "table_id": e["source"][0], "cell_ids": e["source"][1]}
                                   for u, e in zip(units, entries)]})

    negatives = _negatives(manifest, verified, rng)
    output.mkdir(parents=True, exist_ok=True)
    gold_bytes = "".join(json.dumps(case, ensure_ascii=False) + "\n" for case in cases).encode()
    (output / "wide-lnst.jsonl").write_bytes(gold_bytes)
    (output / "wide-negative.jsonl").write_text(
        "".join(json.dumps(case, ensure_ascii=False) + "\n" for case in negatives), encoding="utf-8")
    summary = {
        "version": WIDE_GOLD_VERSION, "seed": seed, "corpus_artifact_id": manifest["artifact_id"],
        "report_count": len(manifest["reports"]), "extracted_report_values": len(values),
        "cross_checked_report_values": len(verified), "extraction_skips": dict(extraction_skips),
        "cross_check_skips": dict(cross_skips), "case_count": len(cases),
        "case_count_by_operation": dict(Counter(operation for operation, _ in plan)),
        "ticker_count": len({case["company"]["ticker"] for case in cases}),
        "source_scale_counts": dict(Counter(verified[u]["rows"][0].scale for _, units in plan for u in units)),
        "template_counts": dict(Counter(verified[u]["rows"][0].template for _, units in plan for u in units)),
        "negative_case_count": len(negatives), "human_reviewed": False,
        "gold_sha256": sha256(gold_bytes).hexdigest(),
    }
    (output / "wide-lnst-audit.json").write_text(
        json.dumps({**summary, "cases": audits}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def _negatives(manifest: Dict[str, Any], verified: Dict[Tuple[str, str, int], Dict[str, Any]], rng: random.Random) -> List[Dict[str, Any]]:
    keys = sorted(verified)
    rng.shuffle(keys)
    years_by_ticker: Dict[str, set] = {}
    for report in manifest["reports"]:
        years_by_ticker.setdefault(report["ticker"], set()).add(int(report["report_year"]))
    rows: List[Dict[str, Any]] = []

    def add(kind: str, question: str) -> None:
        rows.append({"question_id": f"neg-{len(rows):03d}-{kind.lower()}", "question": question,
                     "expected": "NO_ANSWER", "kind": kind})

    for metric in ("Doanh thu thuần", "Tổng tài sản", "ROE", "Lợi nhuận gộp", "EPS", "Vốn chủ sở hữu"):
        ticker, scope, year = keys[len(rows)]
        name = verified[(ticker, scope, year)]["report"]["company_name"]
        add("UNSUPPORTED_METRIC", f"{metric} theo báo cáo {_SCOPE_WORD[scope]} của {name} ({ticker}) năm {year} là bao nhiêu?")
    for ticker, scope, _ in keys[6:9]:
        add("MISSING_PERIOD", f"Lợi nhuận sau thuế TNDN theo báo cáo {_SCOPE_WORD[scope]} của {ticker} là bao nhiêu?")
    scopes: Dict[Tuple[str, int], set] = {}
    for report in manifest["reports"]:
        scopes.setdefault((report["ticker"], int(report["report_year"])), set()).add(report["statement_scope"])
    # Only where both scopes exist, so asking back is the one correct behavior.
    both = [key for key in keys[9:] if {"HOP_NHAT", "RIENG"} <= scopes[(key[0], key[2])]]
    for ticker, _, year in both[:3]:
        add("MISSING_SCOPE", f"Lợi nhuận sau thuế TNDN của {ticker} năm {year} là bao nhiêu?")
    for name, ticker in (("CTCP Sao Băng Phương Nam", "SBP"), ("Tập đoàn Hoàng Long Quốc Tế", "HLQ")):
        add("UNKNOWN_COMPANY", f"Lợi nhuận sau thuế TNDN theo báo cáo hợp nhất của {name} ({ticker}) năm 2022 là bao nhiêu?")
    for ticker, scope, _ in keys[12:15]:
        missing = min(years_by_ticker[ticker]) - 3
        add("PERIOD_OUTSIDE_CORPUS", f"Lợi nhuận sau thuế TNDN theo báo cáo {_SCOPE_WORD[scope]} của {ticker} năm {missing} là bao nhiêu?")
    return rows


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-artifact", required=True)
    parser.add_argument("--raw-corpus", required=True)
    parser.add_argument("--output", default="gold/wide")
    parser.add_argument("--seed", type=int, default=20261013)
    parser.add_argument("--lookup", type=int, default=40)
    parser.add_argument("--growth", type=int, default=30)
    parser.add_argument("--average", type=int, default=30)
    args = parser.parse_args(argv)
    # The three-case STRONG/STRICT pilot stays held out of the wide set.
    pilot = [("AAA", "HOP_NHAT", 2023), ("AAA", "RIENG", 2023), ("FPT", "HOP_NHAT", 2023)]
    summary = build(Path(args.corpus_artifact), Path(args.raw_corpus), Path(args.output), seed=args.seed,
                    counts={"LOOKUP": args.lookup, "GROWTH_RATE": args.growth, "AVERAGE": args.average},
                    exclude=pilot)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
