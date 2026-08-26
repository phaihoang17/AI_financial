from __future__ import annotations

from dataclasses import replace
import json

from src.evaluation.e2e import (
    PRODUCTION_TABLE_E2E_BLOCKER,
    PRODUCTION_TABLE_E2E_STATUS,
    evaluate_e2e_case,
    evaluate_e2e_cases,
)
from src.evaluation.e2e_fixtures import e2e_fixture_cases
from src.evidence.schemas import CanonicalDecimal
from src.sandbox.schemas import ExecutionDatum, ExecutionOutput


def _cases_by_id():
    return {item.case_id: item for item in e2e_fixture_cases()}


def test_task_098_fixture_matrix_and_metrics_are_complete():
    report = evaluate_e2e_cases(e2e_fixture_cases()).to_dict()

    assert report["total_cases"] == report["passed_cases"] == 12
    assert report["failed_cases"] == 0
    assert report["status_accuracy"] == {
        "PASS": 1.0,
        "CLARIFICATION": 1.0,
        "ABSTAIN": 1.0,
    }
    for metric in (
        "nlu_exactness",
        "plan_exactness",
        "evidence_completeness",
        "trace_correctness",
        "execution_correctness",
        "verification_correctness",
        "final_status_accuracy",
        "answer_correctness",
    ):
        assert report[metric] == 1.0
    assert report["retry_success_rate"] == 0.8
    assert report["retries_used_distribution"] == {"0": 6, "1": 5, "NONE": 1}
    assert report["strong_escalation_count"] == 2


def test_decimal_and_output_comparison_is_exact_without_tolerance():
    case = _cases_by_id()["lookup"]
    expected = case.expected_execution
    value = expected.output.values[0]
    changed_output = ExecutionOutput(
        expected.output.kind,
        [
            ExecutionDatum(
                CanonicalDecimal(f"{value.value}0000000000000000001"),
                value.scale,
                value.unit,
            )
        ],
    )
    changed_case = replace(
        case,
        expected_execution=replace(expected, output=changed_output),
    )

    result = evaluate_e2e_case(changed_case)

    assert not result.execution_correct
    assert not result.passed


def test_e2e_report_is_deterministic_and_contains_no_binding_values():
    first = evaluate_e2e_cases(e2e_fixture_cases()).to_dict()
    second = evaluate_e2e_cases(e2e_fixture_cases()).to_dict()
    encoded = json.dumps(first, sort_keys=True)

    assert first == second
    assert "binding_map" not in encoded.casefold()
    assert '"bindings"' not in encoded.casefold()
    assert "binding://" not in encoded.casefold()


def test_production_table_class_blocker_remains_explicit():
    report = evaluate_e2e_cases(e2e_fixture_cases()).to_dict()

    assert report["production_table_e2e_status"] == PRODUCTION_TABLE_E2E_STATUS == "BLOCKED"
    assert (
        report["production_table_e2e_blocker"]
        == PRODUCTION_TABLE_E2E_BLOCKER
        == "PRODUCTION_TABLE_CLASS_PROVENANCE_PENDING"
    )

