from src.evaluation.broad_gold import (
    answer,
    column_orientation,
    cross_checked,
    find_statement_rows,
    fold,
    question,
    ratio_answer,
    raw_vnd,
)


def _table(header, rows):
    def tr(cells):
        return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"
    return "<table>" + tr(header) + "".join(tr(r) for r in rows) + "</table>"


def _balance(closing="3.077.615", opening="1.954.764", header=("TÀI SẢN", "Mã số", "Thuyết minh",
                                                                "31/12/2016", "01/01/2016"), unit="Đơn vị tính: VND"):
    return unit + "\n" + _table(list(header), [
        ["A. TÀI SẢN NGẮN HẠN", "100", "", "1.361.646", "1.071.561"],
        ["TỔNG CỔNG TÀI SẢN", "270", "", closing, opening],
        ["D. VỐN CHỦ SỞ HỮU", "400", "", "954.752", "819.485"],
    ])


def _income(revenue="2.143.769", header=("CHỈ TIÊU", "Mã số", "Thuyết minh", "Năm 2016", "Năm 2015")):
    return "Đơn vị tính: triệu đồng\n" + _table(list(header), [
        ["3. Doanh thu thuần về bán hàng và cung cấp dịch vụ", "10", "6.1", revenue, "1.614.548"],
        ["5. Lợi nhuận gộp về bán hàng và cung cấp dịch vụ", "20", "", "307.035", "189.807"],
    ])


def _cash_flow():
    return "Đơn vị tính: VND\n" + _table(["CHỈ TIÊU", "Mã số", "Năm nay", "Năm trước"], [
        ["(Tăng)/Giám hàng tồn kho", "10", "(236.404)", "(76.664)"],
        ["Lưu chuyển tiền thuần từ hoạt động kinh doanh", "20", "80.169", "(73.349)"],
    ])


def test_fold_is_ocr_tolerant():
    assert fold("TỔNG CỔNG TÀI SẢN") == "tong cong tai san"
    assert fold("C. NỘ PHẢI TRẢ") == "c no phai tra"
    assert fold("Lưu chuyển tiền thuần") == "luu chuyen tien thuan"


def test_balance_sheet_rows_use_dates_not_bare_year():
    rows, _ = find_statement_rows(_balance(), 2016)
    assert set(rows) == {"CURRENT_ASSETS", "TOTAL_ASSETS", "EQUITY"}
    (total,) = rows["TOTAL_ASSETS"]
    assert (total.current, total.prior, total.scale) == ("3077615", "1954764", "RAW")
    swapped = ("TÀI SẢN", "Mã số", "Thuyết minh", "Số đầu năm", "Số cuối năm")
    (total,) = find_statement_rows(_balance(header=swapped), 2016)[0]["TOTAL_ASSETS"]
    assert (total.current, total.prior) == ("1954764", "3077615")


def test_orientation_requires_a_stated_period():
    assert column_orientation("31/12/2016 | 01/01/2016", 2016) is True
    assert column_orientation("31.12.2015 | 31.12.2016", 2016) is False
    assert column_orientation("Năm nay | Năm trước", 2016) is True
    assert column_orientation("2015 | 2016", 2016) is False
    assert column_orientation("Số tiền | Số tiền", 2016) is None
    rows, reasons = find_statement_rows(_balance(header=("TÀI SẢN", "Mã số", "", "VND", "VND")), 2016)
    assert rows == {} and reasons["TOTAL_ASSETS.ORIENTATION_UNKNOWN"] == 1


def test_code_and_label_must_agree_across_statements():
    rows, _ = find_statement_rows(_income() + _cash_flow(), 2016)
    assert set(rows) == {"NET_REVENUE", "GROSS_PROFIT", "CFO"}
    assert raw_vnd(rows["NET_REVENUE"][0]) == "2143769000000"
    assert (rows["CFO"][0].current, rows["CFO"][0].prior) == ("80169", "-73349")


def test_unit_restatement_and_conflicts_are_skipped():
    assert find_statement_rows(_balance(unit=""), 2016)[1]["TOTAL_ASSETS.UNIT_UNKNOWN"] == 1
    restated = ("TÀI SẢN", "Mã số", "Số đã báo cáo", "Phân loại lại", "Số sau phân loại lại")
    assert find_statement_rows(_balance(header=restated), 2016)[1]["TOTAL_ASSETS.RESTATEMENT_TABLE"] == 1
    rows, reasons = find_statement_rows(_income() + _income(revenue="9.999"), 2016)
    assert "NET_REVENUE" not in rows and reasons["NET_REVENUE.CONFLICTING_ROWS"] == 1
    assert "GROSS_PROFIT" in rows  # an agreeing repeated statement keeps both cells


def test_cross_check_requires_next_report_prior_column():
    values = {
        ("AAA", "RIENG", 2016): {"rows": find_statement_rows(_balance(), 2016)[0]},
        ("AAA", "RIENG", 2017): {"rows": find_statement_rows(_balance(
            closing="4.000.000", opening="3.077.615",
            header=("TÀI SẢN", "Mã số", "Thuyết minh", "31/12/2017", "01/01/2017")), 2017)[0]},
    }
    kept, skips = cross_checked(values)
    assert ("TOTAL_ASSETS", ("AAA", "RIENG", 2016)) in kept
    # The 2017 fixture reprints equity opening as 819.485, not 2016's closing 954.752.
    assert ("EQUITY", ("AAA", "RIENG", 2016)) not in kept
    assert skips["EQUITY.RESTATED_IN_NEXT_REPORT"] == 1
    assert skips["TOTAL_ASSETS.NO_NEXT_YEAR_VALUE"] == 1


def test_ratio_uses_average_denominator_and_rejects_non_positive():
    def rows(value):
        return find_statement_rows("Đơn vị tính: VND\n" + _table(
            ["CHỈ TIÊU", "Mã số", "Năm nay", "Năm trước"],
            [["Lợi nhuận sau thuế", "60", value, "1.000"], ["D. VỐN CHỦ SỞ HỮU", "400", value, "1.000"]]), 2016)[0]

    key, prior = ("AAA", "RIENG", 2016), ("AAA", "RIENG", 2015)
    verified = {("LNST", key): rows("3.000")["LNST"], ("EQUITY", key): rows("5.000")["EQUITY"],
                ("EQUITY", prior): rows("1.000")["EQUITY"]}
    assert ratio_answer("ROE", key, verified) == "100"  # 3000 / ((5000 + 1000) / 2) * 100
    assert ratio_answer("ROA", key, verified) is None
    verified[("EQUITY", prior)] = rows("(9.000)")["EQUITY"]
    assert ratio_answer("ROE", key, verified) is None


def test_answers_and_questions_are_deterministic():
    assert answer("GROWTH_RATE", ["200", "250"]) == "25"
    assert answer("AVERAGE", ["1", "2"]) == "1.5"
    text = question("LOOKUP", 0, ("tổng tài sản",), "BALANCE_SHEET", "CTCP A", "AAA", "HOP_NHAT", [2020])
    assert text == "Tổng tài sản của CTCP A (AAA) tại ngày 31/12/2020 theo báo cáo tài chính hợp nhất là bao nhiêu?"
    assert "2019" in question("GROWTH_RATE", 1, ("doanh thu thuần",), "INCOME_STATEMENT", "CTCP A", "AAA",
                              "RIENG", [2019, 2020])


def test_note_rows_cannot_pose_as_statement_lines():
    # A subsidiary-investment note: "100" is an ownership percentage, and
    # "Tài sản - Ngân hàng" folds to a superstring of "tai san ngan han".
    note = "Đơn vị tính: triệu đồng\n" + _table(["", "Số cuối năm", "", "Số đầu năm", ""], [
        ["Công ty TNHH MTV Quản lý Nợ và Khai thác Tài sản - Ngân hàng TMCP Nam Á", "500.000", "100", "100.000", "100"],
    ])
    assert find_statement_rows(note, 2023)[0] == {}
    assert fold("Tài sản - Ngân hàng") == "tai san ngan hang"


def test_earlier_gold_units_are_excluded(tmp_path):
    from src.evaluation.broad_gold import excluded_units
    path = tmp_path / "old.jsonl"
    path.write_text('{"question_id": "broad-001-growth_rate-equity-aaa-rieng-2020-2021", "metric": "EQUITY", '
                    '"company": {"ticker": "AAA"}, "statement_scope": "RIENG"}\n')
    assert excluded_units([path]) == {"EQUITY": {("AAA", "RIENG", 2021)}}
