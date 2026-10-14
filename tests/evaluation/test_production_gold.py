import json
from pathlib import Path

import pytest

from src.evaluation.production_gold import (
    DEV_TEMPLATES,
    HOLDOUT_TEMPLATES,
    find_bank_rows,
    label_key,
    question,
)

ROOT = Path(__file__).resolve().parents[2]
GOLD = ROOT / "gold" / "production-v1"


def _reports(audit_path):
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    return {source["report_id"] for case in audit["cases"] for source in case["sources"]}


@pytest.mark.parametrize("label,key", [
    ("VIII. Lợi nhuận sau thuế", "loi nhuan sau thue"), ("TỔNG TÀI SẢN CÓ", "tong tai san co"),
    ("I. Thu nhập lãi thuần", "thu nhap lai thuan"), ("1. Vốn của TCTD", "von cua tctd"),
])
def test_bank_labels_drop_ordinals(label, key):
    assert label_key(label) == key


def test_bank_rows_need_the_bank_signature_and_agreeing_years():
    table = ("<table><tr><td>Chỉ tiêu</td><td>Thuyết minh</td><td>31/12/2023 Triệu VND</td>"
             "<td>31/12/2022 Triệu VND</td></tr>{rows}</table>")
    rows = ("<tr><td>I. Thu nhập lãi thuần</td><td>5</td><td>1.234.567</td><td>1.000.000</td></tr>"
            "<tr><td>TỔNG TÀI SẢN</td><td></td><td>9.876.543</td><td>8.000.000</td></tr>")
    found, reasons = find_bank_rows(table.format(rows=rows), 2023)
    assert reasons["BANK_REPORTS"] == 1
    assert found["TOTAL_ASSETS"][0].current == "9876543" and found["TOTAL_ASSETS"][0].scale == "MILLION"
    assert found["NET_INTEREST_INCOME"][0].prior == "1000000"
    corporate = table.format(rows="<tr><td>TỔNG TÀI SẢN</td><td></td><td>9.876.543</td><td>8.000.000</td></tr>")
    assert find_bank_rows(corporate, 2023) == ({}, {})


def test_holdout_phrasings_are_its_own():
    assert not set().union(*DEV_TEMPLATES.values()) & set().union(*HOLDOUT_TEMPLATES.values())
    dev = question("dev", "LOOKUP", 0, "TOTAL_ASSETS", "CTCP A", "AAA", "HOP_NHAT", [2023])
    holdout = question("holdout", "LOOKUP", 0, "TOTAL_ASSETS", "CTCP A", "AAA", "HOP_NHAT", [2023])
    assert dev != holdout and "AAA" in dev and "2023" in holdout


@pytest.mark.skipif(not (GOLD / "holdout" / "audit.json").is_file(), reason="production gold not built")
def test_built_splits_are_independent_and_sized_for_the_gate():
    dev, holdout = _reports(GOLD / "dev" / "audit.json"), _reports(GOLD / "holdout" / "audit.json")
    earlier = set()
    for path in ("gold/wide/wide-lnst-audit.json", "gold/broad/broad-audit.json", "gold/broad-h2/broad-audit.json",
                 "gold/retrieval/strong-strict-source-audit.json"):
        earlier |= _reports(ROOT / path)
    assert not dev & holdout and not (dev | holdout) & earlier
    summary = json.loads((GOLD / "build-summary.json").read_text(encoding="utf-8"))["splits"]
    total = summary["dev"]["case_count"] + summary["holdout"]["case_count"]
    assert 200 <= total <= 300 and summary["holdout"]["case_count"] >= 100
    frozen = json.loads((GOLD / "holdout" / "FROZEN.json").read_text(encoding="utf-8"))
    from hashlib import sha256
    assert frozen["files"]["gold.jsonl"] == sha256((GOLD / "holdout" / "gold.jsonl").read_bytes()).hexdigest()
    for line in (GOLD / "holdout" / "gold.jsonl").read_text(encoding="utf-8").splitlines():
        assert json.loads(line)["annotation_status"] == "AUTO_DERIVABLE"  # not human-verified yet
