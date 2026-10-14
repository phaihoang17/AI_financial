import pytest

from src.sandbox.pandas_executor import execute_pandas_sandboxed
from src.sandbox.pandas_protocol import PandasFailureStage
from src.serving.schemas import ServingError
from src.text_to_pandas.catalog import frame_payload
from src.text_to_pandas.catalog_builder import _column_period
from src.text_to_pandas.metrics import metric_for_code, metric_for_bank_label
from src.text_to_pandas.understanding import understand_question
from tests.text_to_pandas.test_pipeline import catalog, envelope, FILTER, run


@pytest.mark.parametrize("header,expected", [
    ("Năm 2023", "CURRENT"), ("31/12/2023", "CURRENT"), ("01/01/2023", "PRIOR"),
    ("Năm 2022", "PRIOR"), ("2023 / 2022", None), ("VND", None),
])
def test_catalog_periods_do_not_redate_comparative_columns(header, expected):
    assert _column_period(header, 2023) == expected


def test_metric_requires_agreeing_code_and_label():
    assert metric_for_code("INCOME_STATEMENT", "60", "Lợi nhuận sau thuế TNDN") == "LNST"
    assert metric_for_code("INCOME_STATEMENT", "60", "Doanh thu thuần") is None
    assert metric_for_bank_label("INCOME_STATEMENT", "VIII. Lợi nhuận sau thuế") == "LNST"


def test_real_worker_enforces_cpu_budget(catalog):
    result = execute_pandas_sandboxed("while True:\n    pass\nresult = 1", {
        "income_statement": frame_payload(catalog.frames["income_statement"]),
    })
    assert not result.success
    assert result.failure.stage is PandasFailureStage.RESOURCE
    assert result.failure.code in {"CPU_TIME_LIMIT", "WALL_CLOCK_TIMEOUT"}


def test_model_transport_failure_is_recorded_without_repair(catalog, tmp_path):
    response, client = run(catalog, tmp_path, [ServingError("SERVING_TRANSPORT_FAILED", "HTTP 503")])
    assert response["status"] == "FAILED"
    assert response["reason_code"] == "SERVING_TRANSPORT_FAILED"
    assert len(client.requests) == 1


def test_truncated_response_retries_generation(catalog, tmp_path):
    response, client = run(catalog, tmp_path, [ServingError("SERVING_RESPONSE_TRUNCATED", "token limit"),
                                              envelope(FILTER + "result = evidence['value'].iloc[0]")])
    assert response["status"] == "ANSWERED"
    assert len(client.requests) == 2


@pytest.mark.parametrize("question, metrics, ratios, operation", [
    ("Dòng tiền thuần từ hoạt động kinh doanh của AAA năm 2023 theo báo cáo hợp nhất?", ["CFO"], [], None),
    ("Dòng tiền thuần từ hoạt động đầu tư của AAA năm 2023 theo báo cáo hợp nhất?", ["CFI"], [], None),
    ("Nợ phải trả ngắn hạn của AAA năm 2023 theo báo cáo hợp nhất?", ["CURRENT_LIABILITIES"], [], None),
    ("Nợ phải trả của AAA năm 2023 theo báo cáo hợp nhất?", ["LIABILITIES"], [], None),
    # "bình quân" inside the ratio's own name is not an averaging operation ...
    ("Tỷ suất lợi nhuận trên vốn chủ sở hữu bình quân (ROE) năm 2023 của AAA, báo cáo hợp nhất?",
     [], ["ROE"], "ratio"),
    # ... but an average of a ratio over years stays a compound operation that fails closed.
    ("ROE bình quân của AAA năm 2022 và 2023 theo báo cáo hợp nhất?", [], ["ROE"], "aggregate"),
    ("Tỷ suất lợi nhuận gộp trên doanh thu thuần năm 2023 của AAA, báo cáo hợp nhất?", [], ["GROSS_MARGIN"], None),
    ("Hệ số nợ phải trả trên vốn chủ sở hữu năm 2023 của AAA, báo cáo hợp nhất?", [], ["DEBT_TO_EQUITY"], None),
])
def test_common_metric_and_ratio_phrasings(catalog, question, metrics, ratios, operation):
    understanding = understand_question(question, catalog)
    assert list(understanding.metrics) == metrics
    assert list(understanding.ratios) == ratios
    if operation:
        assert understanding.operation == operation


def test_agreeing_repeat_keeps_the_coded_statement_line():
    from collections import Counter

    from src.text_to_pandas.catalog_builder import _resolve_metric_duplicates

    def fact(fact_id, code):
        return {"fact_id": fact_id, "statement": "INCOME_STATEMENT", "metric": "LNST", "value": "21108412384",
                "line_code": code}

    summary, statement = fact("summary", None), fact("line-60", "60")
    stats = Counter()
    _resolve_metric_duplicates([summary, statement], stats)
    assert (summary["metric"], statement["metric"]) == (None, "LNST")
    assert stats == {"metric_repeat_coded_preferred": 1, "metric_repeat_dropped": 1}
    first, second = fact("a", None), fact("b", None)
    _resolve_metric_duplicates([first, second], Counter())
    assert (first["metric"], second["metric"]) == ("LNST", None)


@pytest.mark.parametrize("header,expected", [
    ("Tại ngày 31.12.2022 VND", "CURRENT"), ("Tại ngày 01.01.2022 VND", "PRIOR"), ("31-12-2022", "CURRENT"),
])
def test_dotted_and_dashed_dates_date_balance_sheet_columns(header, expected):
    assert _column_period(header, 2022) == expected


def test_securities_codes_and_tt200_fallback():
    assert metric_for_code("BALANCE_SHEET", "340", "II. Nợ phải trả dài hạn", securities=True) == "NON_CURRENT_LIABILITIES"
    assert metric_for_code("BALANCE_SHEET", "310", "I. Nợ phải trả ngắn hạn", securities=True) == "CURRENT_LIABILITIES"
    assert metric_for_code("INCOME_STATEMENT", "200", "XI. LỢI NHUẬN KẾ TOÁN SAU THUẾ TNDN", securities=True) == "LNST"
    assert metric_for_code("INCOME_STATEMENT", "90", "IX. TỔNG LỢI NHUẬN KẾ TOÁN TRƯỚC THUẾ", securities=True) == \
        "PROFIT_BEFORE_TAX"
    # Older CTCK forms follow TT200 numbering; the agreeing label still decides.
    assert metric_for_code("INCOME_STATEMENT", "10", "3. Doanh thu thuần", securities=True) == "NET_REVENUE"
    assert metric_for_code("INCOME_STATEMENT", "60", "Cộng chi phí tài chính", securities=True) is None
    assert metric_for_code("INCOME_STATEMENT", "200", "XI. LỢI NHUẬN KẾ TOÁN SAU THUẾ TNDN") is None
    # On TT200 forms "lợi nhuận kế toán sau thuế" names the parent's share, not line 60.
    assert metric_for_code("INCOME_STATEMENT", "60", "18. Lợi nhuận kế toán sau thuế của công ty mẹ") is None


@pytest.mark.parametrize("label,code,metric", [
    ("Lưu chuyển tiền thuần từ/(sử dụng vào) hoạt động kinh doanh", "20", "CFO"),
    ("Lưu chuyển tiền thuần (sử dụng vào)/từ hoạt động đầu tư", "30", "CFI"),
    ("Lưu chuyển tiền thuần sử dụng vào hoạt động tài chính", "40", "CFF"),
    ("TỔNG CỘNG TÀISẢN (270 = 100 + 200)", "270", "TOTAL_ASSETS"),
])
def test_printed_cash_flow_and_ocr_label_variants(label, code, metric):
    statement = "CASH_FLOW_STATEMENT" if metric.startswith("CF") else "BALANCE_SHEET"
    assert metric_for_code(statement, code, label) == metric


def test_failed_identity_clears_only_unconfirmed_metrics():
    from collections import Counter

    from src.text_to_pandas.catalog_builder import _guard_identities

    def facts(**values):
        return [{"metric": metric, "value": str(value), "source_scale": "RAW"} for metric, value in values.items()]

    # Total capital is misread; assets = liabilities + equity confirms the rest.
    rows = facts(TOTAL_ASSETS=100, TOTAL_CAPITAL=90, LIABILITIES=60, EQUITY=40, CURRENT_ASSETS=70,
                 NON_CURRENT_ASSETS=30)
    stats = Counter()
    _guard_identities(rows, stats)
    assert {row["metric"] for row in rows} == {None, "TOTAL_ASSETS", "LIABILITIES", "EQUITY", "CURRENT_ASSETS",
                                               "NON_CURRENT_ASSETS"}
    assert stats["identity_cleared.TOTAL_CAPITAL"] == 1
    # No other identity confirms cash-flow sections, so all four abstain.
    rows = facts(CFO=10, CFI=-5, CFF=7, NET_CASH_FLOW=-3)
    _guard_identities(rows, Counter())
    assert all(row["metric"] is None for row in rows)
    # Rounding of the coarsest printed unit is not a failure.
    rows = [{"metric": "CFO", "value": "10000000", "source_scale": "MILLION"},
            {"metric": "CFI", "value": "-5000000", "source_scale": "MILLION"},
            {"metric": "CFF", "value": "1000000", "source_scale": "MILLION"},
            {"metric": "NET_CASH_FLOW", "value": "7000000", "source_scale": "MILLION"}]
    _guard_identities(rows, Counter())
    assert all(row["metric"] for row in rows)


def test_provenance_index_survives_fields_sorting_before_fact_id(tmp_path):
    import json

    from src.text_to_pandas.catalog import DatasetCatalog

    rows = [{"fact_id": "f_00000000000000000001", "report_id": "r1"},
            {"basis": "x", "fact_id": "f_00000000000000000002", "report_id": "r2"}]
    (tmp_path / "provenance.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    catalog = object.__new__(DatasetCatalog)
    catalog.root, catalog._provenance_offsets = tmp_path, None
    catalog.manifest = {"files": {"provenance": {"file": "provenance.jsonl"}}}
    found = catalog.provenance(["f_00000000000000000001", "f_00000000000000000002"])
    assert {fact_id: row["report_id"] for fact_id, row in found.items()} == {
        "f_00000000000000000001": "r1", "f_00000000000000000002": "r2"}


@pytest.mark.parametrize("header,restatement", [
    ("Chỉ tiêu Mã số Số đã báo cáo Điều chỉnh Số sau điều chỉnh", True),
    ("Mã số Khoản mục Số theo báo cáo trước đây VND Trình bày lại VND Số liệu trình bày lại VND", True),
    ("NGUỒN VỐN Mã số Thuyết minh Số cuối năm Số đầu năm (Trình bày lại)", False),
    ("Chỉ tiêu Mã số Thuyết minh", False),
])
def test_only_restatement_layouts_stop_layout_inheritance(header, restatement):
    from src.text_to_pandas.catalog_builder import _RESTATEMENT_HEADER
    from src.text_to_pandas.metrics import fold

    assert bool(_RESTATEMENT_HEADER.search(fold(header))) is restatement


def test_group_and_company_columns_follow_the_report_scope():
    from src.text_to_pandas.catalog_builder import _SCOPE_COLUMN
    from src.text_to_pandas.metrics import fold

    group, company = fold("Tập đoàn 2015 Triệu VND"), fold("Công ty 2015 Triệu VND")
    assert _SCOPE_COLUMN["HOP_NHAT"].search(group) and not _SCOPE_COLUMN["HOP_NHAT"].search(company)
    assert _SCOPE_COLUMN["RIENG"].search(company) and not _SCOPE_COLUMN["RIENG"].search(group)


def test_page_carry_over_copy_yields_to_the_statement_line():
    from collections import Counter

    from src.text_to_pandas.catalog_builder import _resolve_metric_duplicates

    def fact(fact_id, label, code=None):
        return {"fact_id": fact_id, "statement": "INCOME_STATEMENT", "metric": "PROFIT_BEFORE_TAX",
                "value": "2093853000000", "line_code": code, "line_item": label}

    carried, line = fact("carry", "Tổng lợi nhuận trước thuế (mang sang trang sau)"), fact("line", "Lợi nhuận trước thuế")
    _resolve_metric_duplicates([carried, line], Counter())
    assert (carried["metric"], line["metric"]) == (None, "PROFIT_BEFORE_TAX")
    # A coded carry-over row still outranks a code-less summary table.
    coded, summary = fact("coded", "Lợi nhuận trước thuế (mang sang trang sau)", "50"), fact("summary", "LNTT")
    _resolve_metric_duplicates([summary, coded], Counter())
    assert (summary["metric"], coded["metric"]) == (None, "PROFIT_BEFORE_TAX")


@pytest.mark.parametrize("question,metrics,ratios,operation", [
    ("Năm 2024, GAS đạt lợi nhuận kế toán trước thuế bao nhiêu theo báo cáo hợp nhất?", ["PROFIT_BEFORE_TAX"], [], None),
    ("VSF có lãi sau thuế năm 2023 (báo cáo hợp nhất) bằng bao nhiêu đồng?", ["LNST"], [], None),
    ("So với năm 2021, tổng giá trị tài sản của PLX năm 2022 tăng trưởng bao nhiêu % (báo cáo hợp nhất)?",
     ["TOTAL_ASSETS"], [], "growth"),
    ("Lợi nhuận trên vốn chủ sở hữu bình quân (ROE) của DNH năm 2024 theo số liệu hợp nhất bằng bao nhiêu?",
     [], ["ROE"], None),
    ("Dựa trên báo cáo riêng, lợi nhuận trên tổng tài sản bình quân (ROA) của NKG năm 2023 là bao nhiêu?",
     [], ["ROA"], None),
    ("Tỷ số thanh toán hiện hành của NVL năm 2022 theo báo cáo riêng là bao nhiêu?", [], ["CURRENT_RATIO"], None),
])
def test_wording_variants_name_the_same_line(catalog, question, metrics, ratios, operation):
    understanding = understand_question(question, catalog)
    assert list(understanding.metrics) == metrics and list(understanding.ratios) == ratios
    if operation:
        assert understanding.operation == operation
    else:
        assert understanding.operation != "aggregate"


def test_interest_and_per_share_lines_are_not_rewritten():
    from src.text_to_pandas.understanding import canonical_phrasing

    for text in ("chi phi lai vay", "thu nhap lai thuan", "lai co ban tren co phieu", "von chu so huu",
                 "loi nhuan thuan tu hoat dong kinh doanh", "tong no vay"):
        assert canonical_phrasing(text) == text
    assert canonical_phrasing("loi nhuan ke toan sau thue cua cong ty me") == "loi nhuan sau thue cua cong ty me"
    assert canonical_phrasing("lai gop") == "loi nhuan gop"


@pytest.mark.parametrize("text,canonical", [
    ("loi nhuan rong", "loi nhuan sau thue"), ("lai rong", "loi nhuan sau thue"), ("von chu cua", "von chu so huu cua"),
    ("luu chuyen tien tu hoat dong kinh doanh", "luu chuyen tien thuan tu hoat dong kinh doanh"),
    ("dong tien hoat dong dau tu", "luu chuyen tien thuan tu hoat dong dau tu"),
    ("he so thanh toan ngan han", "he so thanh toan hien hanh"), ("bien lai gop", "bien loi nhuan gop"),
    ("ty le no phai tra/von chu so huu", "no tren von chu so huu"),
    ("loi nhuan sau thue tren von chu so huu", "ty suat loi nhuan tren von chu so huu"),
    ("quy mo tong tai san", "tong tai san"),
])
def test_variant_families_share_one_canonical_form(text, canonical):
    from src.text_to_pandas.understanding import canonical_phrasing

    assert canonical_phrasing(text) == canonical


def test_parenthesised_abbreviation_after_a_term_is_not_a_ticker(catalog):
    understanding = understand_question("Thu nhập lãi thuần (NII) của AAA năm 2023 theo báo cáo hợp nhất?", catalog)
    assert understanding.unknown_tickers == () and understanding.tickers == ("AAA",)
    assert understand_question("Doanh thu thuần của XYZ năm 2023 hợp nhất?", catalog).unknown_tickers == ("XYZ",)


def test_unknown_company_after_a_metric_name_still_refuses(catalog):
    question = "Lợi nhuận sau thuế theo báo cáo hợp nhất của CTCP Thủy sản Cà Mau Xanh (CMX) năm 2022 là bao nhiêu?"
    assert understand_question(question, catalog).unknown_tickers == ("CMX",)
