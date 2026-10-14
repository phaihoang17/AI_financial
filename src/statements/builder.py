"""Offline builder of reconstructed statement sets (ADR-075).

For every identified report of an M2 corpus it re-reads the printed B01/B02/B03
tables through the catalog's own reader (``statement_tables`` / ``lines``) with
both period columns and every printed line, takes the B09 notes as ordered text
and table blocks, checks the accounting identities and writes one gzipped JSON
per report plus a manifest. Nothing is re-dated, re-labelled or corrected: a
line keeps its printed label and figure; checks only report.

Only TT200/TT202 corporate forms are built. Securities-company (B0x-CTCK) and
bank (TT49, no "Mã số" column) reports are recorded as unsupported templates.

    python -m src.statements.builder --corpus-manifest M --raw-root R --out artifacts/statements-v1 \
        [--parity-catalog artifacts/t2p-catalog-v4]
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
import csv
from decimal import Decimal
import gzip
from hashlib import sha256
import json
from multiprocessing import Pool
from pathlib import Path
import re
import sys
from typing import Any

from src.formulas.identities import check_identities, expenses_parenthesized, missing_required
from src.indexing.cell_normalizer import normalize_cell_text
from src.indexing.document_parser import parse_document
from src.indexing.grid_builder import build_logical_table_grid
from src.indexing.paragraph_extractor import extract_paragraphs
from src.indexing.provenance_sidecar import _source_from_inventory
from src.indexing.schemas import TableParseStatus
from src.text_to_pandas.catalog_builder import _MULTIPLIER, _NOTES_FORM, figure_value, make_fact_id, statement_tables
from src.text_to_pandas.metrics import DATASET_BY_STATEMENT, STATEMENTS, fold, is_per_share_label

SCHEMA_VERSION = "statement-set-v1"
BUILD_VERSION = "statement-set-build-v1"
FORMS = {"BALANCE_SHEET": "B01-DN", "INCOME_STATEMENT": "B02-DN", "CASH_FLOW_STATEMENT": "B03-DN"}
SUPPORTED = "TT200"
_TAG = re.compile(r"<[^>]+>")


class StatementBuildError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def _decimal_text(value: Any) -> str:
    return format(value.normalize(), "f") if value != 0 else "0"


def _grid_rows(table: Any) -> list[list[dict[str, Any]]]:
    """Printed rows of a table: each cell once at its anchor, with its column span."""
    grid = build_logical_table_grid(table)
    texts = {cell.cell_id: normalize_cell_text(cell.extracted_text) for cell in table.cells}
    rows = []
    for row, slots in enumerate(grid.slots):
        cells, column = [], 0
        while column < len(slots):
            cell_id, span = slots[column], 1
            while cell_id is not None and column + span < len(slots) and slots[column + span] == cell_id:
                span += 1
            anchored = cell_id is not None and (row == 0 or grid.slots[row - 1][column] != cell_id)
            cells.append({"text": texts.get(cell_id, "") if anchored else "", "span": span})
            column += span
        rows.append(cells)
    return rows


def notes_blocks(source: Any, document: Any, after: int) -> list[dict[str, Any]]:
    """B09 as printed: paragraphs and tables in source order from its heading after ``after``."""
    paragraphs: dict[str, list[Any]] = {}
    for paragraph in extract_paragraphs(source, document):
        paragraphs.setdefault(paragraph.page_id, []).append(paragraph)
    blocks: list[dict[str, Any]] = []
    started = False
    for page in document.pages:
        if page.content_span.end <= after:
            continue
        items = [(p.source_span.start, "text", p) for p in paragraphs.get(page.page_id, [])]
        items += [(t.source_span.start, "table", t) for t in page.tables]
        for start, kind, item in sorted(items, key=lambda entry: entry[0]):
            if start < after:
                continue
            if not started:
                if kind != "text" or not _NOTES_FORM.search(fold(item.raw_text)):
                    continue
                started = True
            if kind == "text":
                blocks.append({"page": page.page_number, "kind": "text", "text": item.raw_text})
            elif item.parse_status is TableParseStatus.UNPARSEABLE:
                text = " ".join(_TAG.sub(" ", item.raw_html).split())
                blocks.append({"page": page.page_number, "kind": "text", "text": text, "unparseable_table": True})
            else:
                blocks.append({"page": page.page_number, "kind": "table", "rows": _grid_rows(item)})
    return blocks


def build_report(source: Any, report: Mapping[str, Any]) -> tuple[str, dict[str, Any] | None]:
    """(template, statement set) of one report; the set is None for an unsupported template."""
    stats: Counter = Counter()
    document = parse_document(source)
    tables = list(statement_tables(source, document, report, stats))
    read = [item for item in tables if item.skip_reason is None]
    if any(item.securities for item in tables):
        return "SECURITIES", None
    if not read:
        return "NO_STATEMENTS", None
    if not any(item.layout.code_named for item in read):
        return "BANK", None

    statements = {statement: {"form": FORMS[statement], "columns": None, "scales": [], "lines": []}
                  for statement in STATEMENTS}
    values: dict[tuple[str, str, str], tuple[Any, int]] = {}
    warnings: list[str] = []
    skipped = []
    for item in tables:
        if item.skip_reason is not None:
            skipped.append({"statement": item.statement, "page_id": item.page.page_id,
                            "table_id": item.table.table_id, "reason": item.skip_reason})
            continue
        sheet, reader, layout = statements[item.statement], item.reader, item.layout
        if sheet["columns"] is None and item.period_basis == "HEADER":
            sheet["columns"] = {"current": reader.header_text(layout.current_column),
                                "prior": "" if layout.prior_column is None else reader.header_text(layout.prior_column)}
        if item.scale.value not in sheet["scales"]:
            sheet["scales"].append(item.scale.value)
        for grid_row, label, code, note, current_id, prior_id in reader.lines(layout):
            line: dict[str, Any] = {"label": label, "code": code, "note": note, "page_id": item.page.page_id,
                                    "table_id": item.table.table_id, "grid_row": grid_row}
            per_share = is_per_share_label(label)
            unit = 1 if per_share else _MULTIPLIER[item.scale]
            for column, cell_id in (("current", current_id), ("prior", prior_id)):
                text = "" if cell_id is None else reader.texts.get(cell_id, "")
                value = figure_value(text, label, item.scale) if text else None
                line[column] = None if cell_id is None else {
                    "text": text, "value": None if value is None else _decimal_text(value),
                    "cell_id": cell_id, "fact_id": make_fact_id(source.report_id, item.table.table_id, cell_id)}
                # A small RAW figure is a note number misread as an amount (as in the catalog).
                if value is None or not code or per_share or (unit == 1 and value != 0 and abs(value) < 1000):
                    continue
                key = (item.statement, code, column)
                if key not in values:
                    values[key] = (value, unit)
                elif values[key][0] != value:
                    warnings.append(f"DUPLICATE_CODE {FORMS[item.statement]} {code} {column}")
            sheet["lines"].append(line)

    last_end = max(item.table.source_span.end for item in tables)
    notes = notes_blocks(source, document, last_end)
    if not notes:
        warnings.append("NOTES_NOT_FOUND")
    checks = check_identities(values)
    missing = missing_required(values)
    for statement, sheet in statements.items():
        if not sheet["lines"]:
            warnings.append(f"STATEMENT_NOT_FOUND {FORMS[statement]}")
        if len(sheet["scales"]) > 1:
            warnings.append(f"MIXED_SCALES {FORMS[statement]}")
    # Notes and scale warnings are shown but do not concern whether the figures add up.
    reconciled = (not missing and all(check["status"] != "FAIL" for check in checks)
                  and not any(warning.startswith(("DUPLICATE_CODE", "STATEMENT_NOT_FOUND")) for warning in warnings))
    return SUPPORTED, {
        "schema_version": SCHEMA_VERSION,
        "report_id": source.report_id,
        "source_ref": report["source_ref"],
        "ticker": report["ticker"],
        "company_name": report["company_name"],
        "fiscal_year": int(report["report_year"]),
        "statement_scope": report["statement_scope"],
        "template": SUPPORTED,
        "status": "RECONCILED" if reconciled else "NOT_RECONCILED",
        "expenses_parenthesized": expenses_parenthesized(values),
        "statements": statements,
        "notes": notes,
        "checks": checks,
        "missing_required": missing,
        "skipped_tables": skipped,
        "warnings": sorted(set(warnings)),
    }


def report_key(ticker: str, year: int, scope: str) -> str:
    return f"{ticker}|{int(year)}|{scope}"


def _process(args: tuple[str, str, str, dict[str, Any]]) -> dict[str, Any]:
    raw_root, corpus_id, output, report = args
    entry = {"report_id": report["report_id"], "key": report_key(report["ticker"], report["report_year"],
                                                                   report["statement_scope"])}
    try:
        template, statement_set = build_report(_source_from_inventory(Path(raw_root), corpus_id, report), report)
    except Exception as error:  # one unreadable report must not stop the build
        return {**entry, "error": f"{type(error).__name__}: {error}"}
    entry["template"] = template
    if statement_set is None:
        return entry
    data = gzip.compress(json.dumps(statement_set, ensure_ascii=False, sort_keys=True).encode("utf-8"), mtime=0)
    name = f"reports/{report['report_id']}.json.gz"
    (Path(output) / name).write_bytes(data)
    statuses = Counter(f"{check['identity']}|{check['column']}|{check['status']}" for check in statement_set["checks"])
    return {**entry, "file": name, "sha256": sha256(data).hexdigest(), "status": statement_set["status"],
            "checks": dict(statuses), "fact_values": _fact_values(statement_set)}


def _fact_values(statement_set: Mapping[str, Any]) -> dict[str, str]:
    """fact_id -> value of current-column figures, for the catalog parity check."""
    return {line["current"]["fact_id"]: line["current"]["value"]
            for sheet in statement_set["statements"].values() for line in sheet["lines"]
            if line["current"] and line["current"]["value"] is not None}


def parity(fact_values: Mapping[str, str], catalog_dir: Path, report_ids: set[str]) -> dict[str, Any]:
    """Every catalog fact of a built report must be read back with the same absolute value."""
    provenance = {}
    with (catalog_dir / "provenance.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row["report_id"] in report_ids:
                provenance[row["fact_id"]] = row
    counts: Counter = Counter()
    examples: list[str] = []
    for dataset in DATASET_BY_STATEMENT.values():
        with (catalog_dir / f"{dataset}.csv").open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                if row["fact_id"] not in provenance:
                    continue
                counts["checked"] += 1
                ours = fact_values.get(row["fact_id"])
                if ours is None:
                    counts["missing"] += 1
                elif abs(Decimal(ours)) != abs(Decimal(row["value"])):  # the catalog flips expense signs
                    counts["mismatched"] += 1
                else:
                    continue
                if len(examples) < 10:
                    examples.append(row["fact_id"])
    return {"checked": counts["checked"], "missing": counts["missing"], "mismatched": counts["mismatched"],
            "passed": counts["missing"] == 0 and counts["mismatched"] == 0, "examples": examples}


def build_statements(corpus_manifest: str | Path, raw_root: str | Path, output_root: str | Path, *,
                     workers: int = 4, limit: int | None = None, tickers: Sequence[str] | None = None,
                     parity_catalog: str | Path | None = None) -> dict[str, Any]:
    """Write per-report statement sets and a manifest into a new directory."""
    output = Path(output_root)
    if output.exists():
        raise StatementBuildError("STATEMENTS_OUTPUT_EXISTS", str(output))
    manifest_path = Path(corpus_manifest)
    corpus = json.loads(manifest_path.read_text(encoding="utf-8"))
    # Same report selection as the catalog: one identified report per ticker/year/scope.
    reports = sorted((report for report in corpus["reports"]
                      if report.get("ticker") and report.get("report_year") and report.get("statement_scope")),
                     key=lambda item: (item["ticker"], item["report_year"], item["statement_scope"]))
    if tickers:
        reports = [report for report in reports if report["ticker"] in set(tickers)]
    if limit is not None:
        reports = reports[:limit]
    (output / "reports").mkdir(parents=True)
    tasks = [(str(raw_root), corpus["corpus_id"], str(output), report) for report in reports]
    entries: dict[str, dict[str, Any]] = {}
    failures: dict[str, str] = {}
    fact_values: dict[str, str] = {}
    checks: Counter = Counter()
    with Pool(processes=max(1, workers)) as pool:
        for result in pool.imap(_process, tasks, chunksize=4):
            if "error" in result:
                failures[result["report_id"]] = result["error"]
                continue
            fact_values.update(result.pop("fact_values", {}))
            checks.update(result.pop("checks", {}))
            entries[result.pop("key")] = result
    summary: dict[str, dict[str, int]] = {}
    for key, count in checks.items():
        identity, column, status = key.rsplit("|", 2)
        summary.setdefault(f"{identity} [{column}]", Counter())[status] += count
    files = {entry["file"]: entry["sha256"] for entry in entries.values() if "file" in entry}
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "build_version": BUILD_VERSION,
        "source_corpus_artifact_id": corpus["corpus_id"],
        "source_corpus_manifest_sha256": sha256(manifest_path.read_bytes()).hexdigest(),
        "statements_id": sha256(json.dumps(files, sort_keys=True).encode("utf-8")).hexdigest(),
        "report_count": len(reports),
        "templates": dict(Counter(entry["template"] for entry in entries.values())),
        "statuses": dict(Counter(entry["status"] for entry in entries.values() if "status" in entry)),
        "failed_reports": failures,
        "check_summary": {name: dict(counts) for name, counts in sorted(summary.items())},
        "reports": dict(sorted(entries.items())),
    }
    if parity_catalog is not None:
        built = {entry["report_id"] for entry in entries.values() if "file" in entry}
        manifest["catalog_parity"] = parity(fact_values, Path(parity_catalog), built)
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                          encoding="utf-8")
    return manifest


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - CLI
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--corpus-manifest", required=True)
    parser.add_argument("--raw-root", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--tickers", nargs="*")
    parser.add_argument("--parity-catalog", help="catalog whose current-year facts must be read back unchanged")
    args = parser.parse_args(argv)
    manifest = build_statements(args.corpus_manifest, args.raw_root, args.out, workers=args.workers,
                                limit=args.limit, tickers=args.tickers, parity_catalog=args.parity_catalog)
    summary = {key: manifest[key] for key in ("statements_id", "report_count", "templates", "statuses",
                                              "check_summary")}
    summary["failed_reports"] = len(manifest["failed_reports"])
    if "catalog_parity" in manifest:
        summary["catalog_parity"] = manifest["catalog_parity"]
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if manifest.get("catalog_parity", {}).get("passed", True) else 1


if __name__ == "__main__":
    sys.exit(main())
