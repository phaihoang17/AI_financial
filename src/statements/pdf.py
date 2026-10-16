"""PDF rendering of one reconstructed statement set (ADR-075).

The PDF prints the statements as they were read: printed labels, line codes,
note references and figures (text as printed, in the printed unit). Lines of a
failed identity are marked, a status banner states whether every identity
holds, and an appendix lists every check, warning and the data provenance.
fpdf2 is imported lazily so the rest of the service runs without it.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
import logging
from pathlib import Path
from typing import Any

FONT_DIR = Path(__file__).with_name("fonts")
TITLES = {
    "BALANCE_SHEET": "BẢNG CÂN ĐỐI KẾ TOÁN",
    "INCOME_STATEMENT": "BÁO CÁO KẾT QUẢ HOẠT ĐỘNG KINH DOANH",
    "CASH_FLOW_STATEMENT": "BÁO CÁO LƯU CHUYỂN TIỀN TỆ",
}
SCOPES = {"HOP_NHAT": "HỢP NHẤT", "RIENG": "RIÊNG"}
UNITS = {"RAW": "VND", "THOUSAND": "nghìn VND", "MILLION": "triệu VND", "BILLION": "tỷ VND"}
STATUSES = {"PASS": "Đạt", "FAIL": "Không đạt", "MISSING": "Không đủ dữ liệu"}
COLUMNS = {"current": "Kỳ này", "prior": "Kỳ trước"}
DISCLAIMER = (
    "Bộ báo cáo này được dựng lại tự động từ bản OCR của báo cáo tài chính đã công bố. Tên chỉ tiêu, mã số "
    "và số liệu được giữ nguyên như bản in; hệ thống không sửa số. Đây không phải bản gốc có chữ ký và dấu; "
    "khi dùng cho mục đích chính thức, hãy đối chiếu với bản gốc."
)
RED, GREEN, GREY = (176, 0, 32), (0, 110, 60), (90, 90, 90)


def _vnd(value: Any) -> str:
    return f"{Decimal(str(value)):,.0f}".replace(",", ".")


def _flagged(statement_set: Mapping[str, Any]) -> set[tuple[str, str]]:
    """(statement, code) of every line taking part in a failed identity."""
    return {(statement, code) for check in statement_set["checks"] if check["status"] == "FAIL"
            for statement, code in check.get("lines", [])}


def render_pdf(statement_set: Mapping[str, Any], *, statements_id: str = "", draft: Any = None) -> bytes:
    from fpdf import FPDF
    from fpdf.fonts import FontFace

    logging.getLogger("fpdf").setLevel(logging.ERROR)  # OCR text may hold glyphs the font lacks
    scope = SCOPES.get(statement_set["statement_scope"], statement_set["statement_scope"])
    footer_text = f"{statement_set['ticker']} · {statement_set['fiscal_year']} · {scope}"

    class _Pdf(FPDF):
        def footer(self) -> None:
            self.set_y(-12)
            self.set_font("Noto", "", 7)
            self.set_text_color(*GREY)
            self.cell(0, 5, f"{footer_text} · Bản dựng lại tự động · Trang {self.page_no()}/{{nb}}", align="C")
            self.set_text_color(0, 0, 0)

    pdf = _Pdf(format="A4")
    pdf.set_margins(15, 15, 15)
    pdf.set_auto_page_break(True, margin=16)
    pdf.add_font("Noto", "", FONT_DIR / "NotoSans-Regular.ttf")
    pdf.add_font("Noto", "B", FONT_DIR / "NotoSans-Bold.ttf")
    pdf.set_title(f"Báo cáo tài chính {scope.lower()} {statement_set['ticker']} {statement_set['fiscal_year']}")
    width = pdf.epw

    def heading(text: str, size: float = 13) -> None:
        pdf.set_font("Noto", "B", size)
        pdf.multi_cell(width, size * 0.55, text, align="C", new_x="LMARGIN", new_y="NEXT")

    def paragraph(text: str, size: float = 9, color: tuple[int, int, int] = (0, 0, 0)) -> None:
        pdf.set_font("Noto", "", size)
        pdf.set_text_color(*color)
        pdf.multi_cell(width, size * 0.5, text, new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)

    # Cover.
    pdf.add_page()
    pdf.ln(30)
    heading(f"BÁO CÁO TÀI CHÍNH {scope}", 18)
    if draft is not None:
        heading("BẢN NHÁP AI", 11)
    pdf.ln(4)
    heading(str(statement_set["company_name"] or statement_set["ticker"]), 14)
    pdf.ln(2)
    paragraph(f"Mã chứng khoán: {statement_set['ticker']}    Năm tài chính: {statement_set['fiscal_year']}", 11)
    pdf.ln(8)
    failed = [check for check in statement_set["checks"] if check["status"] == "FAIL"]
    reconciled = statement_set["status"] == "RECONCILED"
    banner = ("ĐÃ ĐỐI CHIẾU: mọi đẳng thức kế toán kiểm tra được đều khớp." if reconciled else
              f"CHƯA ĐỐI CHIẾU ĐỦ: {len(failed)} phép kiểm tra không đạt, "
              f"{len(statement_set['missing_required'])} chỉ tiêu tổng bắt buộc bị thiếu. Xem Phụ lục.")
    pdf.set_fill_color(*(GREEN if reconciled else RED))
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Noto", "B", 10)
    pdf.multi_cell(width, 7, banner, align="C", fill=True, padding=2, new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.set_fill_color(255, 255, 255)  # tables fill with the current colour
    pdf.ln(6)
    paragraph(DISCLAIMER, 9, GREY)
    if draft is not None:
        pdf.ln(4)
        paragraph(draft.preface, 9)
    pdf.ln(6)
    paragraph("Nội dung: B01-DN Bảng cân đối kế toán · B02-DN Báo cáo kết quả hoạt động kinh doanh · "
              "B03-DN Báo cáo lưu chuyển tiền tệ · B09-DN Thuyết minh báo cáo tài chính · Phụ lục đối chiếu", 9)

    # B01 / B02 / B03.
    flagged = _flagged(statement_set)
    totals = {(statement, code) for check in statement_set["checks"] for statement, code in check.get("lines", [])[:1]}
    for statement in TITLES:  # B01, B02, B03 in form order
        sheet = statement_set["statements"][statement]
        pdf.add_page()
        heading(f"{TITLES[statement]} {scope}")
        paragraph(f"Mẫu số {sheet['form']}    Năm tài chính {statement_set['fiscal_year']}", 9)
        scales = sheet["scales"]
        unit = UNITS.get(scales[0], scales[0]) if len(scales) == 1 else "nhiều đơn vị, xem Phụ lục"
        paragraph(f"Đơn vị tính: {unit}", 8, GREY)
        pdf.ln(2)
        if not sheet["lines"]:
            paragraph("Không đọc được báo cáo này từ bản nguồn. Xem Phụ lục.", 10, RED)
            continue
        columns = sheet["columns"] or {"current": "Kỳ này", "prior": "Kỳ trước"}
        pdf.set_font("Noto", "", 7.5)
        with pdf.table(col_widths=(92, 13, 13, 31, 31), text_align=("LEFT", "CENTER", "CENTER", "RIGHT", "RIGHT"),
                       line_height=4, padding=1, headings_style=FontFace(emphasis="BOLD")) as table:
            table.row(["Chỉ tiêu", "Mã số", "TM", columns["current"] or "Kỳ này", columns["prior"] or "Kỳ trước"])
            for line in sheet["lines"]:
                key = (statement, line["code"])
                style = None
                if line["code"] and key in flagged:
                    style = FontFace(emphasis="BOLD", color=RED)
                elif line["code"] is None or key in totals:
                    style = FontFace(emphasis="BOLD")
                label = line["label"] + (" (*)" if line["code"] and key in flagged else "")
                row = table.row()
                for text in (label, line["code"] or "", line["note"],
                             (line["current"] or {}).get("text", ""), (line["prior"] or {}).get("text", "")):
                    row.cell(text, style=style)
        if any((statement, line["code"]) in flagged for line in sheet["lines"] if line["code"]):
            pdf.ln(2)
            paragraph("(*) Dòng tham gia một đẳng thức kế toán không khớp; số liệu vẫn giữ như bản in. "
                      "Xem Phụ lục.", 8, RED)

    # B09.
    pdf.add_page()
    heading(f"THUYẾT MINH BÁO CÁO TÀI CHÍNH {scope}")
    paragraph("Mẫu số B09-DN. Trích nguyên văn từ bản OCR của báo cáo nguồn, chưa chuẩn hóa.", 8, GREY)
    pdf.ln(2)
    if not statement_set["notes"]:
        paragraph("Không tìm thấy phần thuyết minh trong bản nguồn.", 10, RED)
    for index, block in enumerate(statement_set["notes"]):
        if block["kind"] == "text":
            if draft is not None and index in draft.heading_indexes:
                pdf.set_font("Noto", "B", 9)
                pdf.multi_cell(width, 5, block["text"], new_x="LMARGIN", new_y="NEXT")
            else:
                paragraph(block["text"], 8)
            pdf.ln(1)
            continue
        rows = block["rows"]
        count = max(sum(cell["span"] for cell in row) for row in rows) if rows else 0
        if count == 0:
            continue
        try:
            pdf.set_font("Noto", "", 6.5)
            # ponytail: wide notes tables wrap in narrow columns on A4 portrait; landscape pages if it matters.
            with pdf.table(line_height=3.2, padding=0.8, first_row_as_headings=False) as table:
                for cells in rows:
                    row = table.row()
                    for cell in cells:
                        row.cell(cell["text"], colspan=cell["span"])
                    for _ in range(count - sum(cell["span"] for cell in cells)):
                        row.cell("")
        except Exception:  # a table fpdf2 cannot lay out is printed as text, never dropped
            paragraph("\n".join(" | ".join(cell["text"] for cell in cells) for cells in rows), 7)
        pdf.ln(2)

    # Appendix.
    pdf.add_page()
    heading("PHỤ LỤC: KẾT QUẢ ĐỐI CHIẾU")
    pdf.ln(2)
    pdf.set_font("Noto", "", 7.5)
    with pdf.table(col_widths=(70, 18, 30, 62), text_align=("LEFT", "CENTER", "CENTER", "RIGHT"), line_height=4,
                   padding=1, headings_style=FontFace(emphasis="BOLD")) as table:
        table.row(["Đẳng thức (theo mã số)", "Cột", "Kết quả", "Chênh lệch (VND)"])
        for check in statement_set["checks"]:
            style = FontFace(emphasis="BOLD", color=RED) if check["status"] == "FAIL" else None
            table.row([check["identity"], COLUMNS.get(check["column"], check["column"]),
                       STATUSES.get(check["status"], check["status"]),
                       _vnd(check["difference"]) if "difference" in check else ""], style=style)
    pdf.ln(3)
    if statement_set["missing_required"]:
        paragraph("Chỉ tiêu tổng bắt buộc bị thiếu: " + ", ".join(statement_set["missing_required"]), 8, RED)
    if statement_set["warnings"]:
        paragraph("Cảnh báo: " + "; ".join(statement_set["warnings"]), 8)
    for skipped in statement_set["skipped_tables"]:
        why = ("nằm trong phần thuyết minh, in ở B09" if skipped["reason"] == "INSIDE_NOTES"
               else f"không đọc được ({skipped['reason']})")
        paragraph(f"Bảng {skipped['statement']} {why}: {skipped['table_id']}", 7, GREY)
    pdf.ln(3)
    paragraph(f"Nguồn: {statement_set['source_ref']}\nreport_id: {statement_set['report_id']}\n"
              f"statements_id: {statements_id or '-'}\nSchema: {statement_set['schema_version']}", 7, GREY)
    return bytes(pdf.output())
