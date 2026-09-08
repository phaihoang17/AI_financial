"""Unit tests for TASK-06D comparison evaluation between static Programmer and TABLE_TRANSFORM."""

import pytest

from src.evaluation.table_transform_comparison import (
    COMPARISON_SCHEMA_VERSION,
    build_complex_table_fixtures,
    evaluate_case,
    render_comparison_markdown,
    run_comparison_evaluation,
)


class TestTableTransformComparison:
    def test_fixtures_generation(self):
        fixtures = build_complex_table_fixtures()
        assert len(fixtures) == 13
        case_ids = [f.case_id for f in fixtures]
        assert len(case_ids) == len(set(case_ids))

        # Check multi-step group requirements (>= 8 cases, all requiring >= 3 min_expected_steps)
        multistep_cases = [f for f in fixtures if f.group == "MULTI_STEP"]
        assert len(multistep_cases) >= 8
        for c in multistep_cases:
            assert c.min_expected_steps >= 3
            assert c.static_inability_reason != ""

    def test_evaluate_single_case(self):
        fixtures = build_complex_table_fixtures()
        case1 = fixtures[0]  # Standard lookup
        res = evaluate_case(case1)
        assert res.case_id == "CASE_1_STANDARD_LOOKUP"
        assert res.baseline_success is True
        assert res.fallback_success is True
        assert res.step_efficiency == "OPTIMAL"

    def test_run_comparison_evaluation(self):
        fixtures = build_complex_table_fixtures()
        report = run_comparison_evaluation(fixtures)

        assert report.schema_version == COMPARISON_SCHEMA_VERSION
        assert report.total_case_count == 13
        assert report.baseline_group_accuracy == 0.4
        assert report.baseline_group_fallback_accuracy == 1.0
        assert report.multistep_group_baseline_accuracy == 0.0
        assert report.multistep_group_fallback_accuracy > 0.5
        assert report.multistep_group_mean_steps >= 2.0
        assert len(report.step_distribution) > 1
        assert "KEEP_AS_FALLBACK" in report.recommendation

        report_dict = report.to_dict()
        assert report_dict["schema_version"] == COMPARISON_SCHEMA_VERSION
        assert len(report_dict["results"]) == 13
        assert "step_distribution" in report_dict
        assert "baseline_group" in report_dict
        assert "multistep_group" in report_dict

    def test_render_comparison_markdown(self):
        fixtures = build_complex_table_fixtures()
        report = run_comparison_evaluation(fixtures)
        md = render_comparison_markdown(report)

        assert "# M6B: Static Programmer vs. TABLE_TRANSFORM Fallback Comparison Report" in md
        assert "Group-Level Disaggregated Metrics (Unpooled)" in md
        assert "Step Distribution Across All Benchmark Cases" in md
        assert "Per-Case Detailed Results & Step Efficiency" in md
        assert "Independent Corpus Data Status" in md
        assert "KEEP_AS_FALLBACK" in md
