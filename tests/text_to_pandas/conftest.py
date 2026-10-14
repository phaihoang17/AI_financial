"""Shared fixtures for the Text-to-Pandas tests."""

import pandas as pd
import pytest

from src.text_to_pandas.catalog import DATASET_NAMES, DatasetCatalog
from src.text_to_pandas.catalog_builder import COMPANY_COLUMNS, FACT_COLUMNS


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
