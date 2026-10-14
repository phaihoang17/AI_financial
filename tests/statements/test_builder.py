"""The statement-set builder re-reads printed statements with both period columns (ADR-075)."""

import gzip
import json

from src.indexing.ids import content_sha256, make_report_id
from src.indexing.schemas import M2A_SCHEMA_VERSION, ReportSource
from src.statements.builder import build_report, build_statements
from src.text_to_pandas.catalog_builder import extract_report_facts
from src.understanding.schemas import StatementScope


def _table(header, rows):
    cells = "".join(f"<td>{cell}</td>" for cell in header)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<table><tr>{cells}</tr>{body}</table>"


BALANCE = _table(["TÀI SẢN", "Mã số", "Thuyết minh", "31/12/2024", "01/01/2024"], [
    ["A. TÀI SẢN NGẮN HẠN", "100", "", "1.000.000.000", "800.000.000"],
    ["I. Tiền và các khoản tương đương tiền", "110", "5.1", "400.000.000", "300.000.000"],
    ["II. Các khoản phải thu ngắn hạn", "130", "", "600.000.000", "500.000.000"],
    ["B. TÀI SẢN DÀI HẠN", "200", "", "500.000.000", "-"],
    ["I. Tài sản cố định", "220", "", "500.000.000", "-"],
    ["TỔNG CỘNG TÀI SẢN", "270", "", "1.500.000.000", "800.000.000"],
    ["C. NỢ PHẢI TRẢ", "300", "", "900.000.000", "300.000.000"],
    ["I. Nợ ngắn hạn", "310", "", "900.000.000", "300.000.000"],
    ["D. VỐN CHỦ SỞ HỮU", "400", "", "600.000.000", "500.000.000"],
    ["I. Vốn chủ sở hữu", "410", "", "600.000.000", "500.000.000"],
    ["TỔNG CỘNG NGUỒN VỐN", "440", "", "1.500.000.000", "800.000.000"],
])
INCOME = _table(["CHỈ TIÊU", "Mã số", "Thuyết minh", "Năm 2024", "Năm 2023"], [
    ["1. Doanh thu bán hàng và cung cấp dịch vụ", "01", "", "2.000.000.000", "1.800.000.000"],
    ["3. Doanh thu thuần về bán hàng và cung cấp dịch vụ", "10", "", "2.000.000.000", "1.800.000.000"],
    ["4. Giá vốn hàng bán", "11", "", "1.500.000.000", "1.400.000.000"],
    ["5. Lợi nhuận gộp về bán hàng và cung cấp dịch vụ", "20", "", "500.000.000", "400.000.000"],
    ["10. Chi phí quản lý doanh nghiệp", "26", "", "250.000.000", "200.000.000"],
    ["11. Lợi nhuận thuần từ hoạt động kinh doanh", "30", "", "250.000.000", "200.000.000"],
    ["15. Tổng lợi nhuận kế toán trước thuế", "50", "", "250.000.000", "200.000.000"],
    ["16. Chi phí thuế TNDN hiện hành", "51", "", "50.000.000", "40.000.000"],
    ["18. Lợi nhuận sau thuế thu nhập doanh nghiệp", "60", "", "200.000.000", "160.000.000"],
])
CASH = _table(["CHỈ TIÊU", "Mã số", "Thuyết minh", "Năm 2024", "Năm 2023"], [
    ["I. LƯU CHUYỂN TIỀN TỪ HOẠT ĐỘNG KINH DOANH", "", "", "", ""],
    ["1. Lợi nhuận trước thuế", "01", "", "250.000.000", "200.000.000"],
    ["Lưu chuyển tiền thuần từ hoạt động kinh doanh", "20", "", "300.000.000", "100.000.000"],
    ["Lưu chuyển tiền thuần từ hoạt động đầu tư", "30", "", "(500.000.000)", "-"],
    ["Lưu chuyển tiền thuần từ hoạt động tài chính", "40", "", "300.000.000", "-"],
    ["Lưu chuyển tiền thuần trong năm", "50", "", "100.000.000", "100.000.000"],
])
# A continuation page without a header row keeps the previous page's columns.
CASH_NEXT = "<table>" + "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in [
    ["Tiền và tương đương tiền đầu năm", "60", "", "300.000.000", "200.000.000"],
    ["Tiền và tương đương tiền cuối năm", "70", "", "400.000.000", "300.000.000"],
]) + "</table>"
NOTES = _table(["Khoản mục", "31/12/2024", "01/01/2024"], [["Tiền mặt", "400.000.000", "300.000.000"]])

RAW = (
    "===== PAGE 1 =====\nBÁO CÁO CỦA KIỂM TOÁN VIÊN\n\nBản thuyết minh báo cáo tài chính kèm theo.\n\n"
    f"===== PAGE 2 =====\nBẢNG CÂN ĐỐI KẾ TOÁN\nTại ngày 31 tháng 12 năm 2024\nĐơn vị tính: VND\n\n{BALANCE}\n\n"
    f"===== PAGE 3 =====\nBÁO CÁO KẾT QUẢ HOẠT ĐỘNG KINH DOANH\nĐơn vị tính: VND\n\n{INCOME}\n\n"
    f"===== PAGE 4 =====\nBÁO CÁO LƯU CHUYỂN TIỀN TỆ\nĐơn vị tính: VND\n\n{CASH}\n\n"
    "Báo cáo này phải được đọc cùng với Bản thuyết minh Báo cáo tài chính hợp nhất\n\n"
    f"===== PAGE 5 =====\nBÁO CÁO LƯU CHUYỂN TIỀN TỆ (tiếp theo)\nĐơn vị tính: VND\n\n{CASH_NEXT}\n\n"
    "===== PAGE 6 =====\nTHUYẾT MINH BÁO CÁO TÀI CHÍNH\nMẪU SỐ B09 - DN\n\n1. Thông tin chung\n\n"
    f"5.1 Tiền\n\n{NOTES}\n"
)


def _report(raw=RAW):
    name = "AAA/2024/report.txt"
    source = ReportSource(
        schema_version=M2A_SCHEMA_VERSION, corpus_id="test-corpus", report_id=make_report_id("test-corpus", name),
        source_ref=name, ticker="AAA", company_name="CTCP Thử nghiệm", report_year=2024, document_name="report",
        statement_scope=StatementScope("HOP_NHAT"), raw_text=raw, content_sha256=content_sha256(raw),
    )
    report = {"report_id": source.report_id, "source_ref": name, "ticker": "AAA", "company_name": "CTCP Thử nghiệm",
              "report_year": 2024, "statement_scope": "HOP_NHAT", "content_sha256": source.content_sha256}
    return source, report


def _line(statement_set, statement, code):
    return next(line for line in statement_set["statements"][statement]["lines"] if line["code"] == code)


def test_statement_set_keeps_both_columns_headings_and_dashes():
    template, statement_set = build_report(*_report())
    assert template == "TT200"
    balance = statement_set["statements"]["BALANCE_SHEET"]
    assert balance["columns"] == {"current": "31/12/2024", "prior": "01/01/2024"}
    cash = _line(statement_set, "BALANCE_SHEET", "110")
    assert (cash["current"]["value"], cash["prior"]["value"], cash["note"]) == ("400000000", "300000000", "5.1")
    long_term = _line(statement_set, "BALANCE_SHEET", "200")
    assert long_term["prior"]["text"] == "-" and long_term["prior"]["value"] is None
    flows = statement_set["statements"]["CASH_FLOW_STATEMENT"]["lines"]
    assert flows[0]["label"] == "I. LƯU CHUYỂN TIỀN TỪ HOẠT ĐỘNG KINH DOANH" and flows[0]["code"] is None
    assert _line(statement_set, "CASH_FLOW_STATEMENT", "30")["current"]["value"] == "-500000000"


def test_identities_pass_and_notes_start_after_the_statements():
    _template, statement_set = build_report(*_report())
    failed = [check for check in statement_set["checks"] if check["status"] == "FAIL"]
    assert failed == [] and statement_set["missing_required"] == []
    assert statement_set["status"] == "RECONCILED"
    notes = statement_set["notes"]
    assert "B09" in notes[0]["text"]  # not the auditor's mention on page 1
    table = next(block for block in notes if block["kind"] == "table")
    assert [cell["text"] for cell in table["rows"][1]] == ["Tiền mặt", "400.000.000", "300.000.000"]


def test_a_footer_naming_the_notes_does_not_end_the_cash_flow_statement():
    _template, statement_set = build_report(*_report())
    closing = _line(statement_set, "CASH_FLOW_STATEMENT", "70")  # read from the continuation page
    assert (closing["current"]["value"], closing["prior"]["value"]) == ("400000000", "300000000")
    assert statement_set["notes"][0]["text"].startswith("THUYẾT MINH")


def test_notes_of_the_other_scope_are_flagged():
    raw = RAW.replace("THUYẾT MINH BÁO CÁO TÀI CHÍNH\n", "THUYẾT MINH BÁO CÁO TÀI CHÍNH RIÊNG\n")
    _template, statement_set = build_report(*_report(raw))
    assert "NOTES_SCOPE_MISMATCH" in statement_set["warnings"] and statement_set["status"] == "NOT_RECONCILED"


def test_misread_total_is_flagged_never_corrected():
    _template, statement_set = build_report(*_report(RAW.replace("1.500.000.000</td><td>800.000.000</td></tr></table>",
                                                                 "1.600.000.000</td><td>800.000.000</td></tr></table>")))
    assert statement_set["status"] == "NOT_RECONCILED"
    assert _line(statement_set, "BALANCE_SHEET", "440")["current"]["text"] == "1.600.000.000"
    assert any(check["identity"] == "B01 270 = 440" and check["status"] == "FAIL" for check in statement_set["checks"])


def test_current_figures_match_the_catalog_facts():
    source, report = _report()
    facts, _provenance, _stats = extract_report_facts(source, report)
    _template, statement_set = build_report(source, report)
    ours = {line["current"]["fact_id"]: line["current"]["value"]
            for sheet in statement_set["statements"].values() for line in sheet["lines"]
            if line["current"] and line["current"]["value"] is not None}
    assert facts and all(abs(float(ours[fact["fact_id"]])) == abs(float(fact["value"])) for fact in facts)


def test_bank_form_without_line_codes_is_unsupported():
    bank = RAW.replace("<td>Mã số</td>", "<td>Ghi chú</td>")
    template, statement_set = build_report(*_report(bank))
    assert template == "BANK" and statement_set is None


def test_build_writes_hashed_reports_and_parity(tmp_path):
    source, report = _report()
    raw_root = tmp_path / "raw"
    (raw_root / "AAA/2024").mkdir(parents=True)
    (raw_root / report["source_ref"]).write_text(RAW, encoding="utf-8")
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps({"corpus_id": "test-corpus", "reports": [{**report, "company_name": "CTCP Thử nghiệm"}]}),
                      encoding="utf-8")
    manifest = build_statements(corpus, raw_root, tmp_path / "out", workers=1)
    entry = manifest["reports"]["AAA|2024|HOP_NHAT"]
    assert entry["template"] == "TT200" and entry["status"] == "RECONCILED"
    stored = json.loads(gzip.decompress((tmp_path / "out" / entry["file"]).read_bytes()))
    assert stored["report_id"] == source.report_id and manifest["failed_reports"] == {}
