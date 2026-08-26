from __future__ import annotations

import json

from src.evaluation.baseline import (
    BASELINE_ACCEPTANCE_THRESHOLDS,
    BASELINE_MODEL_TIER,
    BASELINE_SCHEMA_VERSION,
    BASELINE_VERIFY_PROFILE,
    evaluate_strong_strict_baseline,
    strong_strict_baseline_cases,
)
from src.evaluation.e2e import (
    PRODUCTION_TABLE_E2E_BLOCKER,
    PRODUCTION_TABLE_E2E_STATUS,
)
from src.evaluation.e2e_fixtures import e2e_fixture_cases
from src.evaluation.run_baseline_eval import main
from src.supervisor.schemas import ModelTier, VerifyProfile
from src.understanding.schemas import SchemaValidationError


def test_baseline_selects_only_strong_strict_planned_cases():
    selected = strong_strict_baseline_cases()

    assert {case.case_id for case in selected} == {
        "growth-rate",
        "average",
        "programmer-retry",
    }
    for case in selected:
        assert case.expected_plan is not None
        assert case.expected_plan.model_tier is ModelTier.STRONG
        assert case.expected_plan.verify_profile is VerifyProfile.STRICT


def test_baseline_subset_is_a_strict_subset_of_all_cases():
    all_ids = {case.case_id for case in e2e_fixture_cases()}
    selected_ids = {case.case_id for case in strong_strict_baseline_cases()}

    assert selected_ids < all_ids


def test_baseline_requires_at_least_one_case():
    try:
        strong_strict_baseline_cases(())
    except SchemaValidationError:
        pass
    else:  # pragma: no cover - explicit failure path
        raise AssertionError("empty baseline must raise SchemaValidationError")


def test_baseline_report_establishes_correctness_reference():
    report = evaluate_strong_strict_baseline()

    assert report.total_cases == 3
    assert report.failed_cases == 0
    assert report.established is True


def test_baseline_report_dict_carries_config_and_blockers():
    payload = evaluate_strong_strict_baseline().to_dict()

    assert payload["schema_version"] == BASELINE_SCHEMA_VERSION
    assert payload["baseline_config"] == {
        "model_tier": BASELINE_MODEL_TIER.value,
        "verify_profile": BASELINE_VERIFY_PROFILE.value,
    }
    # Thresholds remain TBD per docs/TASKS.md; none are invented.
    assert payload["acceptance_thresholds"] is BASELINE_ACCEPTANCE_THRESHOLDS is None
    assert payload["established"] is True
    assert payload["production_table_e2e_status"] == PRODUCTION_TABLE_E2E_STATUS
    assert payload["production_table_e2e_blocker"] == PRODUCTION_TABLE_E2E_BLOCKER


def test_baseline_report_embeds_the_existing_e2e_report():
    payload = evaluate_strong_strict_baseline().to_dict()
    inner = payload["e2e_report"]

    assert inner["schema_version"] == "m9-retrieved-evidence-e2e-v1"
    assert inner["total_cases"] == inner["passed_cases"] == 3
    assert inner["failed_cases"] == 0
    # The baseline exercises only the PASS status of the strong/strict path.
    assert inner["status_accuracy"]["PASS"] == 1.0
    assert {item["case_id"] for item in inner["case_results"]} == {
        "growth-rate",
        "average",
        "programmer-retry",
    }


def test_baseline_report_is_json_serializable():
    payload = evaluate_strong_strict_baseline().to_dict()

    assert json.loads(json.dumps(payload, sort_keys=True)) == payload


def test_baseline_cli_passes_when_baseline_holds():
    assert main([]) == 0
    assert main(["--mode", "strong-strict"]) == 0
