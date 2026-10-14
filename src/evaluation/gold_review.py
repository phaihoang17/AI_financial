"""Human review of the production gold (ADR-072).

Agent-built gold is ``AUTO_DERIVABLE``. A case counts as human-reviewed only
when a named person records a decision for it after checking the source rows.

    # 1. self-contained review packet: question, expected answer, and for every
    #    source the report file, page, printed row cells and next-year figure
    python -m src.evaluation.gold_review packet --split dev --split holdout
    # 2. a reviewer steps through the cases in a terminal (resumable)
    python -m src.evaluation.gold_review record --reviewer "Full Name"
    # 3. approved IDs, rejections and reviewers for the production gate
    python -m src.evaluation.gold_review summarize
    # decisions recorded on the shared review page, exported to JSON
    python -m src.evaluation.gold_review ingest --file review-export.json

Decisions are append-only JSON lines in ``review/decisions.jsonl``; the latest
decision per case wins. ``REJECT`` and ``CORRECT`` (with a corrected value)
keep the case out of the approved set until the gold is rebuilt or fixed.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence

from src.indexing.document_parser import parse_document
from src.indexing.provenance_sidecar import _source_from_inventory

DEFAULT_ROOT = Path("gold/production-v1")
DEFAULT_CORPUS = Path("artifacts/m2-corpus-v1/4f39603a89e213129de6c57e1234456e88086832c8b61bd9821770d76a6dc78e")
DEFAULT_RAW = Path("data/vifinqa/financial_statements")
DECISIONS = ("APPROVE", "REJECT", "CORRECT")
# holdout2 is the scored holdout of the production gate; holdout (scored once) is now
# development data and is reviewed like dev.
SPLITS = ("dev", "holdout", "holdout2")
CHECKLIST = (
    "Đúng công ty, năm tài chính và loại báo cáo (hợp nhất/riêng)?",
    "Dòng nguồn đúng chỉ tiêu (mã số/nhãn) và đúng cột năm hiện tại?",
    "Đơn vị tính (VND/nghìn/triệu) được quy đổi đúng?",
    "Báo cáo năm sau xác nhận cùng số liệu (cột năm trước)?",
    "Đáp án (tra cứu/tăng trưởng/bình quân/chỉ số) tính đúng từ các nguồn?",
)


def _vnd(value: str) -> str:
    number = Decimal(value)
    return f"{number:,.0f}".replace(",", ".") if number == number.to_integral() else str(number)


def _row_cells(document: Any, cell_ids: Sequence[str]) -> List[Dict[str, Any]]:
    rows = []
    for page in document.pages:
        for table in page.tables:
            by_id = {cell.cell_id: cell for cell in table.cells}
            for cell_id in cell_ids:
                if cell_id in by_id:
                    row = by_id[cell_id].source_row_index
                    rows.append({"page_id": page.page_id, "table_id": table.table_id, "row_index": row,
                                 "cells": [cell.extracted_text for cell in sorted(table.cells,
                                           key=lambda item: item.source_cell_index)
                                           if cell.source_row_index == row]})
    return rows


def build_packet(root: Path, split: str, corpus: Path, raw_root: Path) -> List[Dict[str, Any]]:
    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    reports = {report["report_id"]: report for report in manifest["reports"]}
    audit = json.loads((root / split / "audit.json").read_text(encoding="utf-8"))
    gold = {json.loads(line)["question_id"]: json.loads(line)
            for line in (root / split / "gold.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()}
    documents: Dict[str, Any] = {}
    packet = []
    for case in audit["cases"]:
        sources = []
        for source in case["sources"]:
            report_id = source["report_id"]
            if report_id not in documents:
                documents[report_id] = parse_document(
                    _source_from_inventory(raw_root, manifest["corpus_id"], reports[report_id]))
            sources.append({
                "metric": source["metric"], "year": source["year"], "source_ref": source["source_ref"],
                "printed_label": source["label"], "printed_value": source["current_raw"],
                "unit_scale": source["source_scale"], "value_vnd": _vnd(source["value_raw_vnd"]),
                "next_report_prior_value": source["next_report_prior_raw"],
                "source_rows": _row_cells(documents[report_id], source["cell_ids"]),
            })
        item = gold[case["question_id"]]
        packet.append({"question_id": case["question_id"], "split": split, "question": item["question"],
                       "operation": case["operation"], "target": case["target"], "template": case["template"],
                       "expected_answer": item["gold_answer"], "sources": sources, "checklist": list(CHECKLIST)})
    return packet


def load_decisions(path: Path) -> Dict[str, Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                latest[row["question_id"]] = row
    return latest


def record_decision(path: Path, question_id: str, decision: str, reviewer: str, *, note: str = "",
                    corrected_value: Optional[str] = None, now: Optional[str] = None) -> Dict[str, Any]:
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}")
    if not reviewer.strip():
        raise ValueError("a named reviewer is required")
    if decision == "CORRECT" and not corrected_value:
        raise ValueError("CORRECT needs the corrected value")
    row = {"question_id": question_id, "decision": decision, "reviewer": reviewer.strip(), "note": note,
           "corrected_value": corrected_value,
           "reviewed_at": now or datetime.now(timezone.utc).isoformat(timespec="seconds")}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def summarize(root: Path, splits: Iterable[str] = SPLITS) -> Dict[str, Any]:
    ids = [json.loads(line)["question_id"] for split in splits if (root / split / "gold.jsonl").is_file()
           for line in (root / split / "gold.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    decisions = load_decisions(root / "review" / "decisions.jsonl")
    approved = [question_id for question_id in ids if decisions.get(question_id, {}).get("decision") == "APPROVE"]
    summary = {"case_count": len(ids), "approved_case_ids": approved,
               "rejected": sum(decisions.get(q, {}).get("decision") in ("REJECT", "CORRECT") for q in ids),
               "pending": len(ids) - sum(question_id in decisions for question_id in ids),
               "reviewers": sorted({row["reviewer"] for row in decisions.values()})}
    summary["complete"] = len(approved) == len(ids)
    (root / "review").mkdir(parents=True, exist_ok=True)
    (root / "review" / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                                                  encoding="utf-8")
    return summary


def ingest(root: Path, rows: Iterable[Mapping[str, Any]], *, source: str) -> int:
    """Append decisions exported from the review page's store (one row per reviewer and case).

    The page stores an opaque reviewer id; it is kept as ``<source>:<id>`` so the
    gate can tell reviewers apart without storing names. Rows already ingested
    (same case, reviewer and time) are skipped.
    """
    path = root / "review" / "decisions.jsonl"
    seen = set()
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                seen.add((row["question_id"], row["reviewer"], row["reviewed_at"]))
    count = 0
    for row in sorted(rows, key=lambda item: str(item.get("reviewed_at"))):
        reviewer = f"{source}:{row['reviewer_id']}"
        if (row["question_id"], reviewer, row["reviewed_at"]) in seen:
            continue
        record_decision(path, row["question_id"], row["decision"], reviewer, note=row.get("note") or "",
                        corrected_value=row.get("corrected_value"), now=row["reviewed_at"])
        count += 1
    return count


PAGE_TEMPLATE = Path(__file__).resolve().parents[2] / "deploy" / "review" / "gold-review.template.html"


def build_page(root: Path, splits: Iterable[str] = SPLITS) -> str:
    """The shared review page (published as an Artifact) with every packet case embedded."""
    rows = []
    for split in splits:
        packet = root / "review" / f"packet-{split}.jsonl"
        if not packet.is_file():
            continue
        for line in packet.read_text(encoding="utf-8").splitlines():
            case = json.loads(line)
            for source in case["sources"]:
                source["source_rows"] = [{"cells": row["cells"]} for row in source["source_rows"]]
            rows.append(case)
    data = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return PAGE_TEMPLATE.read_text(encoding="utf-8").replace("__CASES__", data)


def interactive(root: Path, reviewer: str, ask: Callable[[str], str] = input, show: Callable[[str], None] = print) -> int:
    path = root / "review" / "decisions.jsonl"
    done = load_decisions(path)
    count = 0
    for packet_file in sorted((root / "review").glob("packet-*.jsonl")):
        for line in packet_file.read_text(encoding="utf-8").splitlines():
            case = json.loads(line)
            if case["question_id"] in done:
                continue
            show(f"\n[{case['split']}] {case['question_id']}\n{case['question']}\n"
                 f"Đáp án kỳ vọng: {case['expected_answer']}")
            for source in case["sources"]:
                show(f"  - {source['metric']} {source['year']}: {source['printed_label']} = {source['printed_value']} "
                     f"({source['unit_scale']}) → {source['value_vnd']} VND; năm sau ghi {source['next_report_prior_value']}"
                     f"\n    {source['source_ref']}")
                for row in source["source_rows"]:
                    show(f"    dòng: {' | '.join(row['cells'])}")
            for item in case["checklist"]:
                show(f"  [ ] {item}")
            answer = ask("APPROVE (a) / REJECT (r) / CORRECT (c) / quit (q): ").strip().lower()
            if answer == "q":
                break
            decision = {"a": "APPROVE", "r": "REJECT", "c": "CORRECT"}.get(answer)
            if decision is None:
                continue
            corrected = ask("Giá trị đúng: ").strip() if decision == "CORRECT" else None
            note = ask("Ghi chú (tuỳ chọn): ").strip()
            record_decision(path, case["question_id"], decision, reviewer, note=note, corrected_value=corrected)
            count += 1
        else:
            continue
        break
    return count


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    packet = sub.add_parser("packet")
    packet.add_argument("--split", action="append", choices=SPLITS)
    packet.add_argument("--corpus-artifact", default=str(DEFAULT_CORPUS))
    packet.add_argument("--raw-corpus", default=str(DEFAULT_RAW))
    record = sub.add_parser("record")
    record.add_argument("--reviewer", required=True)
    sub.add_parser("summarize")
    page = sub.add_parser("page", help="write the shared review page HTML")
    page.add_argument("--output", required=True)
    imported = sub.add_parser("ingest", help="decisions exported from the review page (JSON list)")
    imported.add_argument("--file", required=True)
    imported.add_argument("--source", default="artifact-user")
    for command in (packet, record):
        command.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    args = parser.parse_args(argv)
    root = Path(args.root)
    if args.command == "packet":
        for split in args.split or SPLITS:
            rows = build_packet(root, split, Path(args.corpus_artifact), Path(args.raw_corpus))
            (root / "review").mkdir(parents=True, exist_ok=True)
            (root / "review" / f"packet-{split}.jsonl").write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
            print(f"{split}: {len(rows)} cases")
    elif args.command == "page":
        Path(args.output).write_text(build_page(root), encoding="utf-8")
        print(args.output)
    elif args.command == "ingest":
        rows = json.loads(Path(args.file).read_text(encoding="utf-8"))
        print(f"ingested {ingest(root, rows, source=args.source)} decisions")
    elif args.command == "record":
        print(f"recorded {interactive(root, args.reviewer)} decisions")
    else:
        print(json.dumps({k: v for k, v in summarize(root).items() if k != "approved_case_ids"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
