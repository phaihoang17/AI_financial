"""Automatic quality checks of gold sources before scoring (ADR-072).

Some annual reports print group and parent-company figures side by side
("Tập đoàn 2015 | Tập đoàn 2014 | Công ty 2015 | Công ty 2014"). The independent
gold reader counts the grouped figures in a row, so where one scope prints "-"
it can take the other scope's columns; the next report repeats the same layout,
so the cross-year check cannot catch it. This check reads each source cell's
column header and flags a source whose column belongs to the other scope.

A flagged case is a gold defect: it is reported and excluded from scoring in
every split alike (the rule is fixed before any holdout is scored), and its
human review must correct or reject it.

    python -m src.evaluation.gold_qa --split dev --split holdout --split holdout2 --split holdout3
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
from pathlib import Path
from typing import Any

from src.indexing.document_parser import parse_document
from src.indexing.provenance_sidecar import _source_from_inventory
from src.text_to_pandas.catalog_builder import _TableReader
from src.text_to_pandas.metrics import fold

DEFAULT_ROOT = Path("gold/production-v1")
DEFAULT_CORPUS = Path("artifacts/m2-corpus-v1/4f39603a89e213129de6c57e1234456e88086832c8b61bd9821770d76a6dc78e")
DEFAULT_RAW = Path("data/vifinqa/financial_statements")
_MARKERS = {"HOP_NHAT": ("tap doan", "hop nhat"), "RIENG": ("cong ty", "cong tv", "rieng")}


def _scopes_named(header: str) -> set:
    folded = fold(header)
    return {scope for scope, words in _MARKERS.items() if any(word in folded for word in words)}


def check_split(root: Path, split: str, corpus: Path, raw_root: Path) -> dict[str, Any]:
    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    reports = {report["report_id"]: report for report in manifest["reports"]}
    audit = json.loads((root / split / "audit.json").read_text(encoding="utf-8"))
    documents: dict[str, Any] = {}
    defects: list[dict[str, Any]] = []
    for case in audit["cases"]:
        for source in case["sources"]:
            report = reports[source["report_id"]]
            if source["report_id"] not in documents:
                documents[source["report_id"]] = parse_document(
                    _source_from_inventory(raw_root, manifest["corpus_id"], report))
            table = next((t for page in documents[source["report_id"]].pages for t in page.tables
                          if t.table_id == source["table_id"]), None)
            if table is None:
                continue
            reader = _TableReader(table, int(source["year"]), report["statement_scope"])
            named = set().union(*(_scopes_named(reader.header_text(c)) for c in range(reader.width)))
            if len(named) < 2:
                continue  # one scope per table: nothing to confuse
            for cell_id in source["cell_ids"]:
                column = next((c for row in reader.grid.slots for c, slot in enumerate(row) if slot == cell_id), None)
                scopes = set() if column is None else _scopes_named(reader.header_text(column))
                if scopes and report["statement_scope"] not in scopes:
                    defects.append({"question_id": case["question_id"], "metric": source["metric"],
                                    "year": source["year"], "report_scope": report["statement_scope"],
                                    "column_header": reader.header_text(column), "kind": "WRONG_SCOPE_COLUMN"})
    result = {"split": split, "cases": len(audit["cases"]),
              "defective_case_ids": sorted({row["question_id"] for row in defects}), "defects": defects}
    (root / split / "qa.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def defective_ids(root: Path, split: str) -> set:
    path = root / split / "qa.json"
    return set(json.loads(path.read_text(encoding="utf-8"))["defective_case_ids"]) if path.is_file() else set()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--split", action="append", required=True)
    parser.add_argument("--corpus-artifact", default=str(DEFAULT_CORPUS))
    parser.add_argument("--raw-corpus", default=str(DEFAULT_RAW))
    args = parser.parse_args(argv)
    for split in args.split:
        result = check_split(Path(args.root), split, Path(args.corpus_artifact), Path(args.raw_corpus))
        print(json.dumps({"split": split, "cases": result["cases"], "defective": len(result["defective_case_ids"])}))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
