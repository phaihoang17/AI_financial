import json

import pandas as pd
import pytest

from src.text_to_pandas.catalog import DatasetCatalog, DATASET_NAMES
from src.text_to_pandas.catalog_builder import FACT_COLUMNS, COMPANY_COLUMNS
from src.text_to_pandas.generator import PandasCodeGenerator, ScriptedClient
from src.text_to_pandas.pipeline import PandasPipeline
from src.text_to_pandas.understanding import understand_question
from src.text_to_pandas.verifier import reference_answer


@pytest.fixture
def catalog():
    rows, origins = [], {}
    for year, value in ((2022, 100), (2023, 125)):
        for metric, amount in (("LNST", value), ("EQUITY", 1000)):
            fact_id = f"{metric}-{year}"
            rows.append(dict(zip(FACT_COLUMNS, (fact_id, "AAA", "Nhựa An Phát", year, "HOP_NHAT", "60",
                                               metric, metric, amount, "VND", "RAW"))))
            origins[fact_id] = {"fact_id": fact_id, "report_id": f"report-{year}", "table_id": "table",
                                "source_cell_id": fact_id, "source_ref": f"AAA/{year}/report.txt",
                                "raw_text": str(amount), "period_basis": "HEADER", "scale_basis": "UNIT_HINT"}
    frames = {name: pd.DataFrame(columns=FACT_COLUMNS) for name in DATASET_NAMES if name != "companies"}
    frames["income_statement"] = pd.DataFrame([row for row in rows if row["metric"] == "LNST"], columns=FACT_COLUMNS)
    frames["balance_sheet"] = pd.DataFrame([row for row in rows if row["metric"] == "EQUITY"], columns=FACT_COLUMNS)
    frames["companies"] = pd.DataFrame([["AAA", "Nhựa An Phát", "CORPORATE", 2022, 2023, "HOP_NHAT"]], columns=COMPANY_COLUMNS)
    return DatasetCatalog.from_frames(frames, provenance=origins)


FILTER = "evidence = income_statement[(income_statement['fiscal_year'] == 2023) & (income_statement['metric'] == 'LNST')]\n"
QUESTION = "LNST của AAA hợp nhất năm 2023 là bao nhiêu?"


def envelope(code, unit="VND", datasets=None, kind="scalar"):
    return {"status": "OK", "selected_datasets": datasets or ["income_statement"], "code": code,
            "expected_result_type": kind, "result_unit": unit}


def run(catalog, tmp_path, responses, question=QUESTION, **options):
    client = ScriptedClient(responses)
    response = PandasPipeline(catalog, PandasCodeGenerator(client), history_root=tmp_path, **options).ask(question)
    return response, client


def test_question_to_answer_uses_real_isolated_worker_and_persists_history(catalog, tmp_path):
    response, _ = run(catalog, tmp_path, [envelope(FILTER + "result = evidence['value'].iloc[0]")])
    assert response["status"] == "ANSWERED", response
    assert response["result"]["value"] == 125
    assert response["sources"][0]["source_cell_id"] == "LNST-2023"
    directory = tmp_path / response["run_id"]
    assert json.loads((directory / "response.json").read_text()) == response
    assert json.loads((directory / "attempt-1.json").read_text())["verification"]["passed"]


@pytest.mark.parametrize("bad", ["result = income_statement['missing'].sum()", "result = 999", "result = ("])
def test_bounded_repair_preserves_failed_attempt_and_sends_feedback(catalog, tmp_path, bad):
    response, client = run(catalog, tmp_path, [envelope(FILTER + bad), envelope(FILTER + "result = evidence['value'].iloc[0]")])
    assert response["status"] == "ANSWERED", response
    assert len(response["attempts"]) == 2
    assert response["attempts"][0]["failure_code"]
    payload = json.loads(client.requests[1]["messages"][1]["content"])
    assert payload["previous_attempt"]["code"] == FILTER + bad
    assert payload["previous_attempt"]["failure"]["code"] == response["attempts"][0]["failure_code"]


def test_retry_limit_is_enforced(catalog, tmp_path):
    response, client = run(catalog, tmp_path, [envelope(FILTER + "result = 999")] * 4)
    assert response["status"] == "FAILED"
    assert len(response["attempts"]) == len(client.requests) == 3


@pytest.mark.parametrize("question,status", [
    ("LNST của AAA hợp nhất?", "NEEDS_CLARIFICATION"),
    ("LNST của ZZZ hợp nhất năm 2023?", "NO_DATA"),
    ("LNST của AAA và ZZZ hợp nhất năm 2023?", "NO_DATA"),
    ("LNST của AAA hợp nhất quý 1 năm 2023?", "UNSUPPORTED"),
])
def test_gate_avoids_model_calls(catalog, tmp_path, question, status):
    response, client = run(catalog, tmp_path, [], question)
    assert response["status"] == status
    assert not client.requests and not response["attempts"]


@pytest.mark.parametrize("code,unit", [
    (FILTER + "result = evidence['value'].iloc[0]\nevidence['value'] = 999", "VND"),
    (FILTER + "result = evidence['value'].iloc[0]", "PERCENT"),
    ("evidence = income_statement[income_statement['fiscal_year'] == 2022]\nresult = 125", "VND"),
    ("result = 125", "VND"),
])
def test_verifier_rejects_forged_wrong_period_or_missing_evidence(catalog, tmp_path, code, unit):
    response, _ = run(catalog, tmp_path, [envelope(code, unit)], max_retries=0)
    assert response["status"] == "FAILED", response


def test_raw_source_scale_must_reproduce_value(catalog, tmp_path):
    catalog._fixture_provenance["LNST-2023"]["raw_text"] = "124"
    response, _ = run(catalog, tmp_path, [envelope(FILTER + "result = 125")], max_retries=0)
    assert response["reason_code"] == "SOURCE_PERIOD_SCALE"


@pytest.mark.parametrize("question,code,unit,expected", [
    ("Tăng trưởng LNST của AAA hợp nhất năm 2023 so với 2022 bao nhiêu %?",
     "evidence = income_statement\ns = evidence.sort_values('fiscal_year')['value']\nresult = (s.iloc[1] - s.iloc[0]) / s.iloc[0] * 100", "PERCENT", 25),
    ("Trung bình LNST của AAA hợp nhất năm 2022 và 2023?",
     "evidence = income_statement\nresult = evidence['value'].mean()", "VND", 112.5),
    ("ROE của AAA hợp nhất năm 2023?",
     FILTER + "evidence = pd.concat([evidence, balance_sheet])\nresult = income_statement[income_statement['fiscal_year'] == 2023]['value'].iloc[0] / balance_sheet['value'].mean() * 100", "PERCENT", 12.5),
])
def test_financial_formulas(catalog, tmp_path, question, code, unit, expected):
    datasets = ["income_statement", "balance_sheet"] if "balance_sheet" in code else None
    response, _ = run(catalog, tmp_path, [envelope(code, unit, datasets)], question, max_retries=0)
    assert response["status"] == "ANSWERED", response
    assert response["result"]["value"] == expected


def test_dataframe_multi_period_result(catalog, tmp_path):
    code = "evidence = income_statement\nresult = evidence[['ticker', 'fiscal_year', 'metric', 'value']]"
    response, _ = run(catalog, tmp_path, [envelope(code, kind="dataframe")], "So sánh LNST của AAA hợp nhất năm 2022 và 2023?")
    assert response["status"] == "ANSWERED", response


def test_security_failure_does_not_retry(catalog, tmp_path):
    response, client = run(catalog, tmp_path, [envelope("import os\nresult = 1")] * 3)
    assert response["status"] == "FAILED"
    assert len(client.requests) == 1


def test_roe_requires_opening_balance(catalog):
    catalog.frames["balance_sheet"] = catalog.frames["balance_sheet"].query("fiscal_year == 2023")
    with pytest.raises(ValueError, match="REFERENCE_INPUT_UNAVAILABLE"):
        reference_answer(understand_question("ROE của AAA hợp nhất năm 2023?", catalog), catalog)


@pytest.mark.parametrize("retries", [-1, 3, True, 1.5])
def test_invalid_retry_budget(catalog, tmp_path, retries):
    with pytest.raises(ValueError):
        PandasPipeline(catalog, None, history_root=tmp_path, max_retries=retries)


def test_repeated_request_id_keeps_both_histories(catalog, tmp_path):
    pipeline = PandasPipeline(catalog, PandasCodeGenerator(ScriptedClient([envelope(FILTER + "result = evidence['value'].iloc[0]")] * 2)), history_root=tmp_path)
    first = pipeline.ask(QUESTION, request_id="../../same")
    second = pipeline.ask(QUESTION, request_id="../../same")
    assert first["run_id"] != second["run_id"]
    assert len(list(tmp_path.glob('*/response.json'))) == 2


def test_coincidentally_correct_constant_is_not_a_valid_program(catalog, tmp_path):
    response, _ = run(catalog, tmp_path, [envelope(FILTER + "result = 125")], max_retries=0)
    assert response["reason_code"] == "FORMULA_PROGRAM"
    assert response["attempts"][0]["verification"]["formula_probe"]["passed"] is False


def test_coincidentally_correct_wrong_formula_is_rejected(catalog, tmp_path):
    question = "Trung bình LNST của AAA hợp nhất năm 2022 và 2023?"
    code = "evidence = income_statement\nresult = evidence['value'].iloc[0] * 1.125"
    response, _ = run(catalog, tmp_path, [envelope(code)], question, max_retries=0)
    assert response["reason_code"] == "FORMULA_PROGRAM", response


def test_ticker_inside_another_company_name_is_not_a_second_company(catalog):
    catalog.frames["companies"] = pd.DataFrame([
        ["FPT", "CTCP FPT", "CORPORATE", 2022, 2023, "HOP_NHAT"],
        ["FOX", "CTCP Viễn thông FPT", "CORPORATE", 2022, 2023, "HOP_NHAT"],
    ], columns=COMPANY_COLUMNS)
    u = understand_question("LNST của CTCP Viễn thông FPT (FOX) năm 2023 hợp nhất?", catalog)
    assert u.tickers == ("FOX",)
    assert set(understand_question("So sánh FPT và FOX năm 2023 hợp nhất?", catalog).tickers) == {"FPT", "FOX"}


def test_large_currency_amount_does_not_hide_an_arithmetic_error(catalog, tmp_path):
    frame = catalog.frames["income_statement"]
    frame.loc[frame['fiscal_year'] == 2023, 'value'] = 1_000_000_000_000
    catalog._fixture_provenance["LNST-2023"]["raw_text"] = "1.000.000.000.000"
    response, _ = run(catalog, tmp_path, [envelope(FILTER + "result = evidence['value'].iloc[0] + 100")], max_retries=0)
    assert response["status"] == "FAILED"
    assert response["reason_code"] == "FORMULA_RESULT"


def test_unverified_extra_result_column_is_rejected(catalog, tmp_path):
    code = "evidence = income_statement\nresult = evidence[['ticker','fiscal_year','metric','value']].copy()\nresult['unverified_amount'] = 999"
    response, _ = run(catalog, tmp_path, [envelope(code, kind="dataframe")],
                      "So sánh LNST của AAA hợp nhất năm 2022 và 2023?", max_retries=0)
    assert response["status"] == "FAILED"


def test_compound_ratio_semantics_fail_closed(catalog):
    opening = catalog.frames["balance_sheet"].iloc[[0]].copy()
    opening['fiscal_year'] = 2021
    opening['fact_id'] = 'opening-equity'
    catalog.frames['balance_sheet'] = pd.concat([catalog.frames['balance_sheet'], opening], ignore_index=True)
    with pytest.raises(ValueError, match="UNSUPPORTED_VERIFICATION"):
        reference_answer(understand_question("Tăng trưởng ROE của AAA hợp nhất năm 2023 so với 2022?", catalog), catalog)
