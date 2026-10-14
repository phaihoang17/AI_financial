from src.evaluation.wide_gold import _answer, cross_checked, find_lnst_rows, raw_vnd


def _table(header, rows):
    def tr(cells):
        return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"
    return "<table>" + tr(header) + "".join(tr(r) for r in rows) + "</table>"


TT200_HEADER = ["CHỈ TIÊU", "Mã số", "Thuyết minh", "Năm nay", "Năm trước"]


def _tt200(current="1.234.567", prior="(1.000)", header=TT200_HEADER, unit="Đơn vị tính: VND"):
    return unit + "\n" + _table(header, [
        ["15. Chi phí thuế TNDN hiện hành", "51", "", "10.000", "20.000"],
        ["17. Lợi nhuận sau thuế TNDN (60 = 50 - 51 - 52)", "60", "", current, prior],
    ])


def test_tt200_row_uses_current_and_prior_columns():
    rows, reason = find_lnst_rows(_tt200(), 2023)
    assert reason is None
    (row,) = rows
    assert (row.current, row.prior, row.scale, row.template) == ("1234567", "-1000", "RAW", "TT200")
    assert row.current_column == 3


def test_year_headers_set_orientation():
    header = ["Mã số", "CHỈ TIÊU", "Thuyết minh", "Năm 2022", "Năm 2023"]
    text = "Đơn vị tính: VND\n" + _table(header, [["60", "Lợi nhuận sau thuế TNDN", "", "1.000", "2.000"]])
    (row,), _ = find_lnst_rows(text, 2023)
    assert (row.current, row.prior) == ("2000", "1000")


def test_bank_template_in_millions():
    text = "Đơn vị: triệu đồng\n" + _table(["", "", "Thuyết minh", "2020", "2019"], [
        ["XII", "Chi phí thuế TNDN", "", "(100)", "(90)"],
        ["XIII", "Lợi nhuận sau thuế", "", "7.493.361", "5.917.720"],
    ])
    (row,), reason = find_lnst_rows(text, 2020)
    assert reason is None
    assert (row.template, row.scale) == ("BANK", "MILLION")
    assert raw_vnd(row) == "7493361000000"


def test_restatement_partial_period_unknown_unit_and_conflicts_are_skipped():
    restated = ["CHỈ TIÊU", "Mã số", "Số đã trình bày", "Điều chỉnh", "Trình bày lại"]
    assert find_lnst_rows(_tt200(header=restated), 2023)[1] == "RESTATEMENT_TABLE"
    partial = ["CHỈ TIÊU", "Mã số", "Thuyết minh", "Từ ngày 15/01/2015 đến 31/12/2015", "Năm trước"]
    assert find_lnst_rows(_tt200(header=partial), 2015)[1] == "PARTIAL_PERIOD"
    assert find_lnst_rows(_tt200(unit=""), 2023)[1] == "UNIT_UNKNOWN"
    conflicting = _tt200() + _tt200(current="9.999")
    assert find_lnst_rows(conflicting, 2023)[1] == "CONFLICTING_LNST_ROWS"
    assert find_lnst_rows("<p>no tables</p>", 2023)[1] == "NO_LNST_ROW"


def test_cross_check_requires_next_report_agreement():
    def entry(text, year):
        return {"rows": find_lnst_rows(text, year)[0]}
    values = {
        ("AAA", "RIENG", 2022): entry(_tt200(current="1.000", prior="5.000"), 2022),
        ("AAA", "RIENG", 2023): entry(_tt200(current="2.000", prior="1.000"), 2023),
        ("BBB", "RIENG", 2022): entry(_tt200(current="1.000", prior="5.000"), 2022),
        ("BBB", "RIENG", 2023): entry(_tt200(current="2.000", prior="1.100.000"), 2023),
    }
    kept, skips = cross_checked(values)
    assert set(kept) == {("AAA", "RIENG", 2022)}
    assert skips == {"NO_NEXT_YEAR_REPORT": 2, "RESTATED_IN_NEXT_REPORT": 1}


def test_answers_use_exact_decimal_arithmetic():
    assert _answer("LOOKUP", ["-1000"]) == "-1000"
    assert _answer("AVERAGE", ["1", "2"]) == "1.5"
    assert _answer("GROWTH_RATE", ["200", "250"]) == "25"
