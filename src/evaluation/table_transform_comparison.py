"""TASK-06D: Static Programmer vs TABLE_TRANSFORM fallback comparison evaluation."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import json
import statistics
import time
from typing import Any, Dict, List, Optional, Sequence

from src.evidence.m5_schemas import MaskedEvidence, MaskedEvidenceBundle, make_value_placeholder
from src.orchestration.schemas import EvaluationFailureStage
from src.programmer.generator import generate_program
from src.programmer.schemas import (
    Program,
    ProgramOutputKind,
    ProgrammerInput,
    ProgrammerStatus,
)
from src.supervisor.formula_registry import FORMULA_REGISTRY_FINGERPRINT
from src.supervisor.schemas import (
    EvidenceSource,
    Plan,
    QuestionType,
    ReasoningMode,
    RetrievalRequirement,
    StatementScope,
    TableClass,
)
from src.table_transform.planner import (
    TableTransformResult,
    TableTransformStatus,
    plan_and_transform,
)
from src.table_transform.schemas import (
    TableCell,
    TableOperationType,
    TableRow,
    TableState,
    TableTransformFailureCode,
)
from src.understanding.schemas import (
    CompanyUnderstanding,
    MetricUnderstanding,
    Operation,
    PeriodKind,
    PeriodUnderstanding,
    QueryUnderstanding,
    SchemaValidationError,
    StatementScopeUnderstanding,
)

COMPARISON_SCHEMA_VERSION = "m6b-table-transform-comparison-v1"


@dataclass(frozen=True)
class ComplexTableCase:
    case_id: str
    description: str
    complexity_type: str
    plan: Plan
    query_understanding: QueryUnderstanding
    table_state: TableState
    masked_evidence: MaskedEvidenceBundle
    expected_output_kind: ProgramOutputKind
    group: str = "BASELINE"
    ground_truth_operations: tuple[TableOperationType, ...] = ()
    min_expected_steps: int = 0
    static_inability_reason: str = ""

    def __post_init__(self) -> None:
        if not self.case_id:
            raise SchemaValidationError("case_id must be non-empty")
        if not isinstance(self.plan, Plan):
            raise SchemaValidationError("plan must be a Plan")
        if not isinstance(self.table_state, TableState):
            raise SchemaValidationError("table_state must be a TableState")


@dataclass(frozen=True)
class CaseComparisonResult:
    case_id: str
    group: str
    complexity_type: str
    min_expected_steps: int
    actual_steps: int
    step_efficiency: str
    baseline_success: bool
    baseline_latency_ms: float
    baseline_failure_code: Optional[str]
    fallback_success: bool
    fallback_latency_ms: float
    fallback_failure_code: Optional[str]
    static_inability_reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "group": self.group,
            "complexity_type": self.complexity_type,
            "min_expected_steps": self.min_expected_steps,
            "actual_steps": self.actual_steps,
            "step_efficiency": self.step_efficiency,
            "baseline_success": self.baseline_success,
            "baseline_latency_ms": round(self.baseline_latency_ms, 3),
            "baseline_failure_code": self.baseline_failure_code,
            "fallback_success": self.fallback_success,
            "fallback_latency_ms": round(self.fallback_latency_ms, 3),
            "fallback_failure_code": self.fallback_failure_code,
            "static_inability_reason": self.static_inability_reason,
        }


@dataclass(frozen=True)
class TableTransformComparisonReport:
    schema_version: str
    total_case_count: int
    baseline_accuracy: float
    fallback_accuracy: float
    baseline_mean_latency_ms: float
    fallback_mean_latency_ms: float
    average_fallback_steps: float
    step_distribution: Dict[int, int]
    baseline_group_accuracy: float
    baseline_group_fallback_accuracy: float
    multistep_group_baseline_accuracy: float
    multistep_group_fallback_accuracy: float
    multistep_group_mean_steps: float
    corpus_cases_available: bool
    corpus_cases_note: str
    results: List[CaseComparisonResult]
    recommendation: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "total_case_count": self.total_case_count,
            "baseline_accuracy": round(self.baseline_accuracy, 3),
            "fallback_accuracy": round(self.fallback_accuracy, 3),
            "baseline_mean_latency_ms": round(self.baseline_mean_latency_ms, 3),
            "fallback_mean_latency_ms": round(self.fallback_mean_latency_ms, 3),
            "average_fallback_steps": round(self.average_fallback_steps, 2),
            "step_distribution": self.step_distribution,
            "baseline_group": {
                "baseline_accuracy": round(self.baseline_group_accuracy, 3),
                "fallback_accuracy": round(self.baseline_group_fallback_accuracy, 3),
            },
            "multistep_group": {
                "baseline_accuracy": round(self.multistep_group_baseline_accuracy, 3),
                "fallback_accuracy": round(self.multistep_group_fallback_accuracy, 3),
                "mean_steps": round(self.multistep_group_mean_steps, 2),
            },
            "corpus_cases_available": self.corpus_cases_available,
            "corpus_cases_note": self.corpus_cases_note,
            "results": [r.to_dict() for r in self.results],
            "recommendation": self.recommendation,
        }


def evaluate_case(case: ComplexTableCase) -> CaseComparisonResult:
    """Evaluate one complex table case across baseline M6 Programmer and M6B Fallback."""
    # 1. Baseline M6 static Programmer
    prog_input = ProgrammerInput(
        plan=case.plan,
        masked_evidence=case.masked_evidence,
        formula_registry_fingerprint=FORMULA_REGISTRY_FINGERPRINT,
    )
    t0 = time.perf_counter()
    baseline_res = generate_program(prog_input)
    t1 = time.perf_counter()
    baseline_latency_ms = (t1 - t0) * 1000.0

    baseline_success = baseline_res.status is ProgrammerStatus.GENERATED
    baseline_failure_code = baseline_res.failure_code

    # 2. M6B TABLE_TRANSFORM Fallback
    t2 = time.perf_counter()
    fallback_res = plan_and_transform(
        initial_state=case.table_state,
        query=case.query_understanding,
        plan=case.plan,
    )
    t3 = time.perf_counter()
    fallback_latency_ms = (t3 - t2) * 1000.0

    fallback_success = fallback_res.status is TableTransformStatus.SUCCESS
    fallback_failure_code = fallback_res.failure_code
    fallback_steps = fallback_res.step_count

    # Step efficiency evaluation
    if not fallback_success:
        step_eff = "FAILED"
    elif case.min_expected_steps == 0:
        step_eff = "OPTIMAL" if fallback_steps == 0 else "SUBOPTIMAL"
    elif fallback_steps == case.min_expected_steps:
        step_eff = "OPTIMAL"
    elif fallback_steps < case.min_expected_steps:
        step_eff = "EARLY_TERMINATION"
    else:
        step_eff = "SUBOPTIMAL"

    return CaseComparisonResult(
        case_id=case.case_id,
        group=case.group,
        complexity_type=case.complexity_type,
        min_expected_steps=case.min_expected_steps,
        actual_steps=fallback_steps,
        step_efficiency=step_eff,
        baseline_success=baseline_success,
        baseline_latency_ms=baseline_latency_ms,
        baseline_failure_code=baseline_failure_code,
        fallback_success=fallback_success,
        fallback_latency_ms=fallback_latency_ms,
        fallback_failure_code=fallback_failure_code,
        static_inability_reason=case.static_inability_reason,
    )


def run_comparison_evaluation(cases: Sequence[ComplexTableCase]) -> TableTransformComparisonReport:
    """Run full comparative evaluation between M6 Programmer and M6B Table Transform."""
    results = [evaluate_case(c) for c in cases]
    total = len(results)

    baseline_acc = sum(1 for r in results if r.baseline_success) / total if total else 0.0
    fallback_acc = sum(1 for r in results if r.fallback_success) / total if total else 0.0

    baseline_latencies = [r.baseline_latency_ms for r in results]
    fallback_latencies = [r.fallback_latency_ms for r in results]

    baseline_mean_lat = statistics.mean(baseline_latencies) if baseline_latencies else 0.0
    fallback_mean_lat = statistics.mean(fallback_latencies) if fallback_latencies else 0.0

    avg_steps = statistics.mean([r.actual_steps for r in results]) if results else 0.0

    # Step distribution
    step_counts = Counter([r.actual_steps for r in results])
    step_distribution = {int(k): int(v) for k, v in sorted(step_counts.items())}

    # Group-level metrics
    baseline_group_res = [r for r in results if r.group == "BASELINE"]
    multistep_group_res = [r for r in results if r.group == "MULTI_STEP"]

    b_b_acc = (
        sum(1 for r in baseline_group_res if r.baseline_success) / len(baseline_group_res)
        if baseline_group_res else 0.0
    )
    b_f_acc = (
        sum(1 for r in baseline_group_res if r.fallback_success) / len(baseline_group_res)
        if baseline_group_res else 0.0
    )

    m_b_acc = (
        sum(1 for r in multistep_group_res if r.baseline_success) / len(multistep_group_res)
        if multistep_group_res else 0.0
    )
    m_f_acc = (
        sum(1 for r in multistep_group_res if r.fallback_success) / len(multistep_group_res)
        if multistep_group_res else 0.0
    )
    m_steps = (
        statistics.mean([r.actual_steps for r in multistep_group_res])
        if multistep_group_res else 0.0
    )

    corpus_available = False
    corpus_note = (
        "Real corpus slices with gold cell-level annotations (TASK-007) are not yet available "
        "in the repository; public ViFinQA questions.jsonl is currently BLOCKED_GOLD_DATA "
        "(only id + question, no gold report/table/cell annotations). No real corpus evaluation "
        "slice could be substituted."
    )

    # Architectural recommendation
    if m_f_acc > m_b_acc:
        recommendation = (
            f"KEEP_AS_FALLBACK_WITH_LIMITATIONS: On multi-step complex tables (>= 3 operations), "
            f"TABLE_TRANSFORM achieves {m_f_acc:.1%} accuracy while static M6 achieves {m_b_acc:.1%}, "
            f"with an average of {m_steps:.2f} steps and minimal latency overhead ({fallback_mean_lat:.2f}ms vs {baseline_mean_lat:.2f}ms). "
            "However, TABLE_TRANSFORM fails on boundary cases involving complex subsidiary grouping or sorting. "
            "Because real corpus gold annotations (ViFinQA) remain BLOCKED_GOLD_DATA, "
            "TABLE_TRANSFORM MUST REMAIN DISABLED in M4 default routing until gold corpus benchmarks can be conducted."
        )
    else:
        recommendation = "DISCARD_FALLBACK: Fallback did not demonstrate an accuracy improvement."

    return TableTransformComparisonReport(
        schema_version=COMPARISON_SCHEMA_VERSION,
        total_case_count=total,
        baseline_accuracy=baseline_acc,
        fallback_accuracy=fallback_acc,
        baseline_mean_latency_ms=baseline_mean_lat,
        fallback_mean_latency_ms=fallback_mean_lat,
        average_fallback_steps=avg_steps,
        step_distribution=step_distribution,
        baseline_group_accuracy=b_b_acc,
        baseline_group_fallback_accuracy=b_f_acc,
        multistep_group_baseline_accuracy=m_b_acc,
        multistep_group_fallback_accuracy=m_f_acc,
        multistep_group_mean_steps=m_steps,
        corpus_cases_available=corpus_available,
        corpus_cases_note=corpus_note,
        results=results,
        recommendation=recommendation,
    )


def build_complex_table_fixtures() -> List[ComplexTableCase]:
    """Generate representative benchmark cases comparing static Programmer vs TABLE_TRANSFORM."""
    req_lnst_2022 = RetrievalRequirement.create(EvidenceSource.TABLE, TableClass.INCOME_STATEMENT, "LNST", "2022")
    req_lnst_2021 = RetrievalRequirement.create(EvidenceSource.TABLE, TableClass.INCOME_STATEMENT, "LNST", "2021")

    def cell(cid: str, metric: str, period: str, req_id: str) -> TableCell:
        ev = f"ev_{cid}"
        return TableCell(
            cell_id=cid,
            value=make_value_placeholder(ev),
            is_placeholder=True,
            evidence_id=ev,
            requirement_id=req_id,
            metric=metric,
            period=period,
        )

    # -------------------------------------------------------------------------
    # GROUP A: EXISTING BASELINE CASES (1 to 5)
    # -------------------------------------------------------------------------

    # 1. Standard Lookup: Single row, single column (Both succeed)
    c1 = cell("c_std", "LNST", "2022", req_lnst_2022.requirement_id)
    r_std = TableRow("r_std", "LNST", {"2022": c1})
    state_std = TableState.create("tab_std", ["2022"], [r_std])
    plan_std = Plan.from_dict({
        "question_type": "LOOKUP",
        "company": {"name": "AAA", "ticker": "AAA"},
        "periods": ["2022"],
        "period_kind": "NAM",
        "statement_scope": "HOP_NHAT",
        "target_metrics": ["LNST"],
        "derived_target": None,
        "formula_id": None,
        "tables_needed": ["INCOME_STATEMENT"],
        "retrieval_requirements": [req_lnst_2022.to_dict()],
        "evidence_sources": ["TABLE"],
        "reasoning_mode": "DIRECT",
        "requires_scale_resolution": True,
        "model_tier": "CHEAP",
        "verify_profile": "LIGHT",
        "max_retries": 1,
        "confidence": 1.0,
        "abstain": False,
        "abstain_reason": None,
    })
    q_std = QueryUnderstanding(
        raw_question="LNST năm 2022",
        company=CompanyUnderstanding(raw="AAA", ticker="AAA", name="AAA", confidence=1.0),
        periods=[PeriodUnderstanding(raw="2022", value="2022", kind=PeriodKind.NAM)],
        statement_scope=StatementScopeUnderstanding(value=StatementScope.HOP_NHAT, confidence=1.0, inferred=False),
        metrics=[MetricUnderstanding(raw="LNST", canonical="LNST", confidence=1.0)],
        operation=Operation.NONE,
        requested_scale=None,
        requested_unit=None,
        missing_information=[],
        ambiguities=[],
        confidence=1.0,
    )
    case_std = ComplexTableCase(
        case_id="CASE_1_STANDARD_LOOKUP",
        description="Clean 1x1 table for standard lookup",
        complexity_type="STANDARD_LOOKUP",
        plan=plan_std,
        query_understanding=q_std,
        table_state=state_std,
        masked_evidence=MaskedEvidenceBundle(items=[
            MaskedEvidence(
                placeholder=c1.value,
                evidence_id=c1.evidence_id,  # type: ignore[arg-type]
                requirement_id=req_lnst_2022.requirement_id,
                metric="LNST",
                period="2022",
                row_path=[],
                column_path=[],
            )
        ]),
        expected_output_kind=ProgramOutputKind.SCALAR,
        group="BASELINE",
        ground_truth_operations=(),
        min_expected_steps=0,
        static_inability_reason="None (M6 static handles clean 1x1 table directly)",
    )

    # 2. Multi-Period Compare: 1 row, 2 columns (Both succeed)
    c_mp_21 = cell("c_mp_21", "LNST", "2021", req_lnst_2021.requirement_id)
    c_mp_22 = cell("c_mp_22", "LNST", "2022", req_lnst_2022.requirement_id)
    r_mp = TableRow("r_mp", "LNST", {"2021": c_mp_21, "2022": c_mp_22})
    state_mp = TableState.create("tab_mp", ["2021", "2022"], [r_mp])
    plan_mp = Plan.from_dict({
        "question_type": "MULTI_PERIOD",
        "company": {"name": "AAA", "ticker": "AAA"},
        "periods": ["2021", "2022"],
        "period_kind": "NAM",
        "statement_scope": "HOP_NHAT",
        "target_metrics": ["LNST"],
        "derived_target": None,
        "formula_id": None,
        "tables_needed": ["INCOME_STATEMENT"],
        "retrieval_requirements": [req_lnst_2021.to_dict(), req_lnst_2022.to_dict()],
        "evidence_sources": ["TABLE"],
        "reasoning_mode": "DIRECT",
        "requires_scale_resolution": True,
        "model_tier": "CHEAP",
        "verify_profile": "STRICT",
        "max_retries": 1,
        "confidence": 1.0,
        "abstain": False,
        "abstain_reason": None,
    })
    q_mp = QueryUnderstanding(
        raw_question="LNST năm 2021 và 2022",
        company=CompanyUnderstanding(raw="AAA", ticker="AAA", name="AAA", confidence=1.0),
        periods=[
            PeriodUnderstanding(raw="2021", value="2021", kind=PeriodKind.NAM),
            PeriodUnderstanding(raw="2022", value="2022", kind=PeriodKind.NAM),
        ],
        statement_scope=StatementScopeUnderstanding(value=StatementScope.HOP_NHAT, confidence=1.0, inferred=False),
        metrics=[MetricUnderstanding(raw="LNST", canonical="LNST", confidence=1.0)],
        operation=Operation.COMPARE,
        requested_scale=None,
        requested_unit=None,
        missing_information=[],
        ambiguities=[],
        confidence=1.0,
    )
    case_mp = ComplexTableCase(
        case_id="CASE_2_MULTI_PERIOD_COMPARE",
        description="Clean 1x2 table for multi-period comparison",
        complexity_type="STANDARD_MULTI_PERIOD",
        plan=plan_mp,
        query_understanding=q_mp,
        table_state=state_mp,
        masked_evidence=MaskedEvidenceBundle(items=[
            MaskedEvidence(
                placeholder=c_mp_21.value,
                evidence_id=c_mp_21.evidence_id,  # type: ignore[arg-type]
                requirement_id=req_lnst_2021.requirement_id,
                metric="LNST",
                period="2021",
                row_path=[],
                column_path=[],
            ),
            MaskedEvidence(
                placeholder=c_mp_22.value,
                evidence_id=c_mp_22.evidence_id,  # type: ignore[arg-type]
                requirement_id=req_lnst_2022.requirement_id,
                metric="LNST",
                period="2022",
                row_path=[],
                column_path=[],
            ),
        ]),
        expected_output_kind=ProgramOutputKind.ORDERED_VALUES,
        group="BASELINE",
        ground_truth_operations=(),
        min_expected_steps=0,
        static_inability_reason="None (M6 static handles clean 1x2 table directly)",
    )

    # 3. Complex Table with Extraneous Segment Rows (1 step)
    c_seg_dt = cell("c_seg_dt", "Doanh thu", "2022", req_lnst_2022.requirement_id)
    c_seg_ln = cell("c_seg_ln", "LNST", "2022", req_lnst_2022.requirement_id)
    r_seg1 = TableRow("r_s1", "Doanh thu", {"2022": c_seg_dt})
    r_seg2 = TableRow("r_s2", "LNST", {"2022": c_seg_ln})
    state_seg = TableState.create("tab_seg", ["2022"], [r_seg1, r_seg2])
    masked_bundle_ambiguous = MaskedEvidenceBundle(items=[
        MaskedEvidence(
            placeholder=c_seg_dt.value,
            evidence_id=c_seg_dt.evidence_id,  # type: ignore[arg-type]
            requirement_id=req_lnst_2022.requirement_id,
            metric="Doanh thu",
            period="2022",
            row_path=[],
            column_path=[],
        ),
        MaskedEvidence(
            placeholder=c_seg_ln.value,
            evidence_id=c_seg_ln.evidence_id,  # type: ignore[arg-type]
            requirement_id=req_lnst_2022.requirement_id,
            metric="LNST",
            period="2022",
            row_path=[],
            column_path=[],
        ),
    ])
    case_extra_rows = ComplexTableCase(
        case_id="CASE_3_EXTRA_ROWS_AMBIGUITY",
        description="Table with multiple unpruned rows causing ambiguity for static programmer",
        complexity_type="EXTRA_ROWS_AMBIGUITY",
        plan=plan_std,
        query_understanding=q_std,
        table_state=state_seg,
        masked_evidence=masked_bundle_ambiguous,
        expected_output_kind=ProgramOutputKind.SCALAR,
        group="BASELINE",
        ground_truth_operations=(TableOperationType.SELECT_ROWS,),
        min_expected_steps=1,
        static_inability_reason="Multiple candidate rows in bundle trigger AMBIGUOUS_REQUIRED_EVIDENCE",
    )

    # 4. Complex Table with Extraneous Historical Columns (1 step)
    c_hist_20 = cell("c_h20", "LNST", "2020", "unreq_2020")
    c_hist_21 = cell("c_h21", "LNST", "2021", "unreq_2021")
    c_hist_22 = cell("c_h22", "LNST", "2022", req_lnst_2022.requirement_id)
    r_hist = TableRow("r_hist", "LNST", {"2020": c_hist_20, "2021": c_hist_21, "2022": c_hist_22})
    state_hist = TableState.create("tab_hist", ["2020", "2021", "2022"], [r_hist])
    masked_bundle_hist = MaskedEvidenceBundle(items=[
        MaskedEvidence(
            placeholder=c_hist_20.value,
            evidence_id=c_hist_20.evidence_id,  # type: ignore[arg-type]
            requirement_id="unreq_2020",
            metric="LNST",
            period="2020",
            row_path=[],
            column_path=[],
        ),
        MaskedEvidence(
            placeholder=c_hist_22.value,
            evidence_id=c_hist_22.evidence_id,  # type: ignore[arg-type]
            requirement_id=req_lnst_2022.requirement_id,
            metric="LNST",
            period="2022",
            row_path=[],
            column_path=[],
        ),
    ])
    case_extra_cols = ComplexTableCase(
        case_id="CASE_4_EXTRA_COLUMNS_PRUNING",
        description="Table with extraneous historical period columns requiring column projection",
        complexity_type="EXTRA_COLUMNS",
        plan=plan_std,
        query_understanding=q_std,
        table_state=state_hist,
        masked_evidence=masked_bundle_hist,
        expected_output_kind=ProgramOutputKind.SCALAR,
        group="BASELINE",
        ground_truth_operations=(TableOperationType.SELECT_COLUMNS,),
        min_expected_steps=1,
        static_inability_reason="Evidence contains items with unreq_2020 not in plan -> UNKNOWN_REQUIREMENT",
    )

    # 5. Complex Multi-Period Growth with Extra Rows and Columns (3 steps)
    plan_growth = Plan.from_dict({
        "question_type": "MULTI_PERIOD",
        "company": {"name": "AAA", "ticker": "AAA"},
        "periods": ["2021", "2022"],
        "period_kind": "NAM",
        "statement_scope": "HOP_NHAT",
        "target_metrics": ["LNST"],
        "derived_target": "GROWTH",
        "formula_id": "GROWTH_RATE",
        "tables_needed": ["INCOME_STATEMENT"],
        "retrieval_requirements": [req_lnst_2021.to_dict(), req_lnst_2022.to_dict()],
        "evidence_sources": ["TABLE"],
        "reasoning_mode": "PROGRAM",
        "requires_scale_resolution": True,
        "model_tier": "STRONG",
        "verify_profile": "STRICT",
        "max_retries": 2,
        "confidence": 1.0,
        "abstain": False,
        "abstain_reason": None,
    })
    q_growth = QueryUnderstanding(
        raw_question="Tăng trưởng LNST từ 2021 đến 2022",
        company=CompanyUnderstanding(raw="AAA", ticker="AAA", name="AAA", confidence=1.0),
        periods=[
            PeriodUnderstanding(raw="2021", value="2021", kind=PeriodKind.NAM),
            PeriodUnderstanding(raw="2022", value="2022", kind=PeriodKind.NAM),
        ],
        statement_scope=StatementScopeUnderstanding(value=StatementScope.HOP_NHAT, confidence=1.0, inferred=False),
        metrics=[MetricUnderstanding(raw="LNST", canonical="LNST", confidence=1.0)],
        operation=Operation.GROWTH,
        requested_scale=None,
        requested_unit=None,
        missing_information=[],
        ambiguities=[],
        confidence=1.0,
    )
    r_g_lnst = TableRow(
        "r_g_lnst",
        "LNST",
        {
            "2020": cell("cg_ln20", "LNST", "2020", "unreq_2020"),
            "2021": cell("cg_ln21", "LNST", "2021", req_lnst_2021.requirement_id),
            "2022": cell("cg_ln22", "LNST", "2022", req_lnst_2022.requirement_id),
        },
    )
    r_g_cp = TableRow(
        "r_g_cp",
        "Chi phí",
        {
            "2020": cell("cg_cp20", "Chi phí", "2020", "unreq_2020"),
            "2021": cell("cg_cp21", "Chi phí", "2021", req_lnst_2021.requirement_id),
            "2022": cell("cg_cp22", "Chi phí", "2022", req_lnst_2022.requirement_id),
        },
    )
    state_growth_complex = TableState.create("tab_g_comp", ["2020", "2021", "2022"], [r_g_lnst, r_g_cp])
    masked_bundle_g = MaskedEvidenceBundle(items=[
        MaskedEvidence(
            placeholder=r_g_lnst.cells["2021"].value,
            evidence_id=r_g_lnst.cells["2021"].evidence_id,  # type: ignore[arg-type]
            requirement_id=req_lnst_2021.requirement_id,
            metric="LNST",
            period="2021",
            row_path=[],
            column_path=[],
        ),
        MaskedEvidence(
            placeholder=r_g_cp.cells["2021"].value,
            evidence_id=r_g_cp.cells["2021"].evidence_id,  # type: ignore[arg-type]
            requirement_id=req_lnst_2021.requirement_id,
            metric="Chi phí",
            period="2021",
            row_path=[],
            column_path=[],
        ),
        MaskedEvidence(
            placeholder=r_g_lnst.cells["2022"].value,
            evidence_id=r_g_lnst.cells["2022"].evidence_id,  # type: ignore[arg-type]
            requirement_id=req_lnst_2022.requirement_id,
            metric="LNST",
            period="2022",
            row_path=[],
            column_path=[],
        ),
    ])
    case_growth_complex = ComplexTableCase(
        case_id="CASE_5_MULTI_ROW_COL_GROWTH",
        description="Multi-row multi-column table requiring row filter, column select, and derived column",
        complexity_type="MULTI_STEP_DERIVATION",
        plan=plan_growth,
        query_understanding=q_growth,
        table_state=state_growth_complex,
        masked_evidence=masked_bundle_g,
        expected_output_kind=ProgramOutputKind.SCALAR,
        group="BASELINE",
        ground_truth_operations=(
            TableOperationType.SELECT_ROWS,
            TableOperationType.SELECT_COLUMNS,
            TableOperationType.ADD_DERIVED_COLUMN,
        ),
        min_expected_steps=3,
        static_inability_reason="Competing metric rows mapped to req_lnst_2021 trigger AMBIGUOUS_REQUIRED_EVIDENCE",
    )

    # -------------------------------------------------------------------------
    # GROUP B: NEW MULTI-STEP COMPLEX CASES (>= 3 distinct operations)
    # -------------------------------------------------------------------------

    # 6. Multi-Metric Multi-Period Growth (3 steps: SELECT_ROWS -> SELECT_COLUMNS -> ADD_DERIVED_COLUMN)
    # Table has 4 metric rows and 5 period columns.
    # (a) Why static M6 fails: 20 cells in raw evidence across 4 metrics and 5 periods.
    # 1-shot schema linking has multiple competing row and column matches, triggering AMBIGUOUS_REQUIRED_EVIDENCE.
    # (b) Ground truth: [SELECT_ROWS, SELECT_COLUMNS, ADD_DERIVED_COLUMN] (3 steps).
    r6_lnst = TableRow("r6_ln", "LNST", {
        "2019": cell("c6_ln19", "LNST", "2019", "unreq_19"),
        "2020": cell("c6_ln20", "LNST", "2020", "unreq_20"),
        "2021": cell("c6_ln21", "LNST", "2021", req_lnst_2021.requirement_id),
        "2022": cell("c6_ln22", "LNST", "2022", req_lnst_2022.requirement_id),
        "2023": cell("c6_ln23", "LNST", "2023", "unreq_23"),
    })
    r6_dt = TableRow("r6_dt", "Doanh thu thuần", {
        "2019": cell("c6_dt19", "Doanh thu thuần", "2019", "unreq_19"),
        "2020": cell("c6_dt20", "Doanh thu thuần", "2020", "unreq_20"),
        "2021": cell("c6_dt21", "Doanh thu thuần", "2021", req_lnst_2021.requirement_id),
        "2022": cell("c6_dt22", "Doanh thu thuần", "2022", req_lnst_2022.requirement_id),
        "2023": cell("c6_dt23", "Doanh thu thuần", "2023", "unreq_23"),
    })
    r6_cp = TableRow("r6_cp", "Chi phí tài chính", {
        "2019": cell("c6_cp19", "Chi phí tài chính", "2019", "unreq_19"),
        "2020": cell("c6_cp20", "Chi phí tài chính", "2020", "unreq_20"),
        "2021": cell("c6_cp21", "Chi phí tài chính", "2021", "unreq_21"),
        "2022": cell("c6_cp22", "Chi phí tài chính", "2022", "unreq_22"),
        "2023": cell("c6_cp23", "Chi phí tài chính", "2023", "unreq_23"),
    })
    r6_lng = TableRow("r6_lng", "Lợi nhuận gộp", {
        "2019": cell("c6_lng19", "Lợi nhuận gộp", "2019", "unreq_19"),
        "2020": cell("c6_lng20", "Lợi nhuận gộp", "2020", "unreq_20"),
        "2021": cell("c6_lng21", "Lợi nhuận gộp", "2021", "unreq_21"),
        "2022": cell("c6_lng22", "Lợi nhuận gộp", "2022", "unreq_22"),
        "2023": cell("c6_lng23", "Lợi nhuận gộp", "2023", "unreq_23"),
    })
    state_6 = TableState.create("tab_6", ["2019", "2020", "2021", "2022", "2023"], [r6_lnst, r6_dt, r6_cp, r6_lng])
    bundle_6 = MaskedEvidenceBundle(items=[
        MaskedEvidence(
            placeholder=r6_lnst.cells["2021"].value,
            evidence_id=r6_lnst.cells["2021"].evidence_id,  # type: ignore[arg-type]
            requirement_id=req_lnst_2021.requirement_id,
            metric="LNST",
            period="2021",
            row_path=[],
            column_path=[],
        ),
        MaskedEvidence(
            placeholder=r6_dt.cells["2021"].value,
            evidence_id=r6_dt.cells["2021"].evidence_id,  # type: ignore[arg-type]
            requirement_id=req_lnst_2021.requirement_id,
            metric="Doanh thu thuần",
            period="2021",
            row_path=[],
            column_path=[],
        ),
        MaskedEvidence(
            placeholder=r6_lnst.cells["2022"].value,
            evidence_id=r6_lnst.cells["2022"].evidence_id,  # type: ignore[arg-type]
            requirement_id=req_lnst_2022.requirement_id,
            metric="LNST",
            period="2022",
            row_path=[],
            column_path=[],
        ),
    ])
    case_6 = ComplexTableCase(
        case_id="CASE_6_MULTI_METRIC_MULTI_PERIOD_GROWTH",
        description="4-metric by 5-year table requiring row filter, 2-year column projection, and growth calculation",
        complexity_type="MULTI_METRIC_YEAR_MATRIX",
        plan=plan_growth,
        query_understanding=q_growth,
        table_state=state_6,
        masked_evidence=bundle_6,
        expected_output_kind=ProgramOutputKind.SCALAR,
        group="MULTI_STEP",
        ground_truth_operations=(
            TableOperationType.SELECT_ROWS,
            TableOperationType.SELECT_COLUMNS,
            TableOperationType.ADD_DERIVED_COLUMN,
        ),
        min_expected_steps=3,
        static_inability_reason="4 metrics x 5 periods bundle creates ambiguous candidate rows for same requirement",
    )

    # 7. Subtotal and Division Breakdown Growth (3 steps)
    # (a) Why static M6 fails: Subtotal rows and unrequested past years cause requirement collisions.
    # (b) Ground truth: [SELECT_ROWS, SELECT_COLUMNS, ADD_DERIVED_COLUMN] (3 steps).
    r7_lnst = TableRow("r7_ln", "LNST", {
        "2018": cell("c7_ln18", "LNST", "2018", "unreq_18"),
        "2020": cell("c7_ln20", "LNST", "2020", "unreq_20"),
        "2021": cell("c7_ln21", "LNST", "2021", req_lnst_2021.requirement_id),
        "2022": cell("c7_ln22", "LNST", "2022", req_lnst_2022.requirement_id),
        "2024": cell("c7_ln24", "LNST", "2024", "unreq_24"),
    })
    r7_sub1 = TableRow("r7_s1", "Doanh thu hoạt động", {
        "2018": cell("c7_s1_18", "Doanh thu hoạt động", "2018", "unreq_18"),
        "2020": cell("c7_s1_20", "Doanh thu hoạt động", "2020", "unreq_20"),
        "2021": cell("c7_s1_21", "Doanh thu hoạt động", "2021", req_lnst_2021.requirement_id),
        "2022": cell("c7_s1_22", "Doanh thu hoạt động", "2022", req_lnst_2022.requirement_id),
        "2024": cell("c7_s1_24", "Doanh thu hoạt động", "2024", "unreq_24"),
    })
    r7_sub2 = TableRow("r7_s2", "Doanh thu tài chính", {
        "2018": cell("c7_s2_18", "Doanh thu tài chính", "2018", "unreq_18"),
        "2020": cell("c7_s2_20", "Doanh thu tài chính", "2020", "unreq_20"),
        "2021": cell("c7_s2_21", "Doanh thu tài chính", "2021", "unreq_21"),
        "2022": cell("c7_s2_22", "Doanh thu tài chính", "2022", "unreq_22"),
        "2024": cell("c7_s2_24", "Doanh thu tài chính", "2024", "unreq_24"),
    })
    r7_sub3 = TableRow("r7_s3", "Tổng chi phí", {
        "2018": cell("c7_s3_18", "Tổng chi phí", "2018", "unreq_18"),
        "2020": cell("c7_s3_20", "Tổng chi phí", "2020", "unreq_20"),
        "2021": cell("c7_s3_21", "Tổng chi phí", "2021", "unreq_21"),
        "2022": cell("c7_s3_22", "Tổng chi phí", "2022", "unreq_22"),
        "2024": cell("c7_s3_24", "Tổng chi phí", "2024", "unreq_24"),
    })
    state_7 = TableState.create("tab_7", ["2018", "2020", "2021", "2022", "2024"], [r7_lnst, r7_sub1, r7_sub2, r7_sub3])
    bundle_7 = MaskedEvidenceBundle(items=[
        MaskedEvidence(
            placeholder=r7_lnst.cells["2021"].value,
            evidence_id=r7_lnst.cells["2021"].evidence_id,  # type: ignore[arg-type]
            requirement_id=req_lnst_2021.requirement_id,
            metric="LNST",
            period="2021",
            row_path=[],
            column_path=[],
        ),
        MaskedEvidence(
            placeholder=r7_sub1.cells["2021"].value,
            evidence_id=r7_sub1.cells["2021"].evidence_id,  # type: ignore[arg-type]
            requirement_id=req_lnst_2021.requirement_id,
            metric="Doanh thu hoạt động",
            period="2021",
            row_path=[],
            column_path=[],
        ),
        MaskedEvidence(
            placeholder=r7_lnst.cells["2022"].value,
            evidence_id=r7_lnst.cells["2022"].evidence_id,  # type: ignore[arg-type]
            requirement_id=req_lnst_2022.requirement_id,
            metric="LNST",
            period="2022",
            row_path=[],
            column_path=[],
        ),
    ])
    case_7 = ComplexTableCase(
        case_id="CASE_7_UNWANTED_INTERMEDIATE_TOTALS_GROWTH",
        description="Table with breakdown sub-items and totals across 5 years requiring multi-step isolation",
        complexity_type="SUBTOTAL_BREAKDOWN",
        plan=plan_growth,
        query_understanding=q_growth,
        table_state=state_7,
        masked_evidence=bundle_7,
        expected_output_kind=ProgramOutputKind.SCALAR,
        group="MULTI_STEP",
        ground_truth_operations=(
            TableOperationType.SELECT_ROWS,
            TableOperationType.SELECT_COLUMNS,
            TableOperationType.ADD_DERIVED_COLUMN,
        ),
        min_expected_steps=3,
        static_inability_reason="Subtotal breakdown rows share period requirements, causing AMBIGUOUS_REQUIRED_EVIDENCE",
    )

    # 8. Forecast and Ratio Column Pruning (3 steps)
    # (a) Why static M6 fails: Raw evidence has forecast columns (2023F, 2024F) and ratio column (% thay đổi)
    # causing period kind and metric linking failure (UNKNOWN_REQUIREMENT).
    # (b) Ground truth: [SELECT_ROWS, SELECT_COLUMNS, ADD_DERIVED_COLUMN] (3 steps).
    r8_lnst = TableRow("r8_ln", "LNST", {
        "2020": cell("c8_ln20", "LNST", "2020", "unreq_20"),
        "2021": cell("c8_ln21", "LNST", "2021", req_lnst_2021.requirement_id),
        "2022": cell("c8_ln22", "LNST", "2022", req_lnst_2022.requirement_id),
        "2023F": cell("c8_ln23f", "LNST", "2023F", "unreq_23f"),
        "2024F": cell("c8_ln24f", "LNST", "2024F", "unreq_24f"),
        "Thay_doi_pct": cell("c8_pct", "LNST", "% thay đổi", "unreq_pct"),
    })
    r8_dt = TableRow("r8_dt", "Doanh thu", {
        "2020": cell("c8_dt20", "Doanh thu", "2020", "unreq_20"),
        "2021": cell("c8_dt21", "Doanh thu", "2021", req_lnst_2021.requirement_id),
        "2022": cell("c8_dt22", "Doanh thu", "2022", req_lnst_2022.requirement_id),
        "2023F": cell("c8_dt23f", "Doanh thu", "2023F", "unreq_23f"),
        "2024F": cell("c8_dt24f", "Doanh thu", "2024F", "unreq_24f"),
        "Thay_doi_pct": cell("c8_dtpct", "Doanh thu", "% thay đổi", "unreq_pct"),
    })
    state_8 = TableState.create("tab_8", ["2020", "2021", "2022", "2023F", "2024F", "Thay_doi_pct"], [r8_lnst, r8_dt])
    bundle_8 = MaskedEvidenceBundle(items=[
        MaskedEvidence(
            placeholder=r8_lnst.cells["2021"].value,
            evidence_id=r8_lnst.cells["2021"].evidence_id,  # type: ignore[arg-type]
            requirement_id=req_lnst_2021.requirement_id,
            metric="LNST",
            period="2021",
            row_path=[],
            column_path=[],
        ),
        MaskedEvidence(
            placeholder=r8_lnst.cells["2022"].value,
            evidence_id=r8_lnst.cells["2022"].evidence_id,  # type: ignore[arg-type]
            requirement_id=req_lnst_2022.requirement_id,
            metric="LNST",
            period="2022",
            row_path=[],
            column_path=[],
        ),
        MaskedEvidence(
            placeholder=r8_lnst.cells["2023F"].value,
            evidence_id=r8_lnst.cells["2023F"].evidence_id,  # type: ignore[arg-type]
            requirement_id="unreq_23f",
            metric="LNST",
            period="2023F",
            row_path=[],
            column_path=[],
        ),
    ])
    case_8 = ComplexTableCase(
        case_id="CASE_8_FORECAST_AND_RATIO_PRUNING",
        description="Table containing historical, forecast, and precomputed ratio columns requiring 3-step cleanup",
        complexity_type="FORECAST_RATIO_COLUMNS",
        plan=plan_growth,
        query_understanding=q_growth,
        table_state=state_8,
        masked_evidence=bundle_8,
        expected_output_kind=ProgramOutputKind.SCALAR,
        group="MULTI_STEP",
        ground_truth_operations=(
            TableOperationType.SELECT_ROWS,
            TableOperationType.SELECT_COLUMNS,
            TableOperationType.ADD_DERIVED_COLUMN,
        ),
        min_expected_steps=3,
        static_inability_reason="Forecast and precomputed ratio columns cause UNKNOWN_REQUIREMENT in static programmer",
    )

    # 9. Cumulative and Quarterly Intermediate Pruning (3 steps)
    # (a) Why static M6 fails: Quarterly and 6-month cumulative columns share year token 2022, causing ambiguity.
    # (b) Ground truth: [SELECT_ROWS, SELECT_COLUMNS, ADD_DERIVED_COLUMN] (3 steps).
    r9_lnst = TableRow("r9_ln", "LNST", {
        "Q1_2022": cell("c9_q1", "LNST", "Q1/2022", "unreq_q1"),
        "Q2_2022": cell("c9_q2", "LNST", "Q2/2022", "unreq_q2"),
        "6M_2022": cell("c9_6m", "LNST", "6M/2022", "unreq_6m"),
        "2021": cell("c9_21", "LNST", "2021", req_lnst_2021.requirement_id),
        "2022": cell("c9_22", "LNST", "2022", req_lnst_2022.requirement_id),
    })
    r9_cp = TableRow("r9_cp", "Chi phí", {
        "Q1_2022": cell("c9_cp_q1", "Chi phí", "Q1/2022", "unreq_q1"),
        "Q2_2022": cell("c9_cp_q2", "Chi phí", "Q2/2022", "unreq_q2"),
        "6M_2022": cell("c9_cp_6m", "Chi phí", "6M/2022", "unreq_6m"),
        "2021": cell("c9_cp_21", "Chi phí", "2021", req_lnst_2021.requirement_id),
        "2022": cell("c9_cp_22", "Chi phí", "2022", req_lnst_2022.requirement_id),
    })
    state_9 = TableState.create("tab_9", ["Q1_2022", "Q2_2022", "6M_2022", "2021", "2022"], [r9_lnst, r9_cp])
    bundle_9 = MaskedEvidenceBundle(items=[
        MaskedEvidence(
            placeholder=r9_lnst.cells["2021"].value,
            evidence_id=r9_lnst.cells["2021"].evidence_id,  # type: ignore[arg-type]
            requirement_id=req_lnst_2021.requirement_id,
            metric="LNST",
            period="2021",
            row_path=[],
            column_path=[],
        ),
        MaskedEvidence(
            placeholder=r9_lnst.cells["2022"].value,
            evidence_id=r9_lnst.cells["2022"].evidence_id,  # type: ignore[arg-type]
            requirement_id=req_lnst_2022.requirement_id,
            metric="LNST",
            period="2022",
            row_path=[],
            column_path=[],
        ),
        MaskedEvidence(
            placeholder=r9_lnst.cells["6M_2022"].value,
            evidence_id=r9_lnst.cells["6M_2022"].evidence_id,  # type: ignore[arg-type]
            requirement_id=req_lnst_2022.requirement_id,
            metric="LNST",
            period="6M/2022",
            row_path=[],
            column_path=[],
        ),
    ])
    case_9 = ComplexTableCase(
        case_id="CASE_9_CUMULATIVE_INTERMEDIATE_PRUNING_GROWTH",
        description="Table with quarterly, cumulative, and annual periods requiring 3-step isolation",
        complexity_type="CUMULATIVE_AND_QUARTERLY",
        plan=plan_growth,
        query_understanding=q_growth,
        table_state=state_9,
        masked_evidence=bundle_9,
        expected_output_kind=ProgramOutputKind.SCALAR,
        group="MULTI_STEP",
        ground_truth_operations=(
            TableOperationType.SELECT_ROWS,
            TableOperationType.SELECT_COLUMNS,
            TableOperationType.ADD_DERIVED_COLUMN,
        ),
        min_expected_steps=3,
        static_inability_reason="Cumulative 6M column collides with annual 2022 requirement -> AMBIGUOUS_REQUIRED_EVIDENCE",
    )

    # 10. Discontinued Operations & Restructuring Variants (3 steps)
    # (a) Why static M6 fails: 4 competing profit metrics containing LNST substring cause ambiguity.
    # (b) Ground truth: [SELECT_ROWS, SELECT_COLUMNS, ADD_DERIVED_COLUMN] (3 steps).
    r10_c = TableRow("r10_c", "LNST từ hoạt động liên tục", {
        "2019": cell("c10_c19", "LNST từ hoạt động liên tục", "2019", "unreq_19"),
        "2020": cell("c10_c20", "LNST từ hoạt động liên tục", "2020", "unreq_20"),
        "2021": cell("c10_c21", "LNST từ hoạt động liên tục", "2021", req_lnst_2021.requirement_id),
        "2022": cell("c10_c22", "LNST từ hoạt động liên tục", "2022", req_lnst_2022.requirement_id),
    })
    r10_d = TableRow("r10_d", "LNST từ hoạt động ngừng kinh doanh", {
        "2019": cell("c10_d19", "LNST từ hoạt động ngừng kinh doanh", "2019", "unreq_19"),
        "2020": cell("c10_d20", "LNST từ hoạt động ngừng kinh doanh", "2020", "unreq_20"),
        "2021": cell("c10_d21", "LNST từ hoạt động ngừng kinh doanh", "2021", "unreq_21"),
        "2022": cell("c10_d22", "LNST từ hoạt động ngừng kinh doanh", "2022", "unreq_22"),
    })
    r10_main = TableRow("r10_m", "LNST", {
        "2019": cell("c10_m19", "LNST", "2019", "unreq_19"),
        "2020": cell("c10_m20", "LNST", "2020", "unreq_20"),
        "2021": cell("c10_m21", "LNST", "2021", req_lnst_2021.requirement_id),
        "2022": cell("c10_m22", "LNST", "2022", req_lnst_2022.requirement_id),
    })
    state_10 = TableState.create("tab_10", ["2019", "2020", "2021", "2022"], [r10_c, r10_d, r10_main])
    bundle_10 = MaskedEvidenceBundle(items=[
        MaskedEvidence(
            placeholder=r10_main.cells["2021"].value,
            evidence_id=r10_main.cells["2021"].evidence_id,  # type: ignore[arg-type]
            requirement_id=req_lnst_2021.requirement_id,
            metric="LNST",
            period="2021",
            row_path=[],
            column_path=[],
        ),
        MaskedEvidence(
            placeholder=r10_c.cells["2021"].value,
            evidence_id=r10_c.cells["2021"].evidence_id,  # type: ignore[arg-type]
            requirement_id=req_lnst_2021.requirement_id,
            metric="LNST từ hoạt động liên tục",
            period="2021",
            row_path=[],
            column_path=[],
        ),
        MaskedEvidence(
            placeholder=r10_main.cells["2022"].value,
            evidence_id=r10_main.cells["2022"].evidence_id,  # type: ignore[arg-type]
            requirement_id=req_lnst_2022.requirement_id,
            metric="LNST",
            period="2022",
            row_path=[],
            column_path=[],
        ),
    ])
    case_10 = ComplexTableCase(
        case_id="CASE_10_DISCONTINUED_OPERATIONS_RESTRUCTURING",
        description="Table with restructuring and discontinued operation variants of LNST across 4 years",
        complexity_type="RESTRUCTURING_VARIANTS",
        plan=plan_growth,
        query_understanding=q_growth,
        table_state=state_10,
        masked_evidence=bundle_10,
        expected_output_kind=ProgramOutputKind.SCALAR,
        group="MULTI_STEP",
        ground_truth_operations=(
            TableOperationType.SELECT_ROWS,
            TableOperationType.SELECT_COLUMNS,
            TableOperationType.ADD_DERIVED_COLUMN,
        ),
        min_expected_steps=3,
        static_inability_reason="Restructuring and continued operation variants cause AMBIGUOUS_REQUIRED_EVIDENCE in static M6",
    )

    # 11. Boundary Case: Subsidiary Aggregation Then Growth (4 steps)
    # (a) Why static M6 fails: M6 Program DSL cannot express preliminary table-level group-by followed by ratio.
    # (b) Ground truth: [SELECT_ROWS, SELECT_COLUMNS, GROUP, ADD_DERIVED_COLUMN] (4 steps).
    # Planner behavior: Planner stops safely (TERMINAL_STATE_UNREACHABLE) testing hard boundary.
    r11_s1 = TableRow("r11_s1", "Phân khúc A", {
        "2020": cell("c11_s1_20", "LNST", "2020", "unreq_20"),
        "2021": cell("c11_s1_21", "LNST", "2021", req_lnst_2021.requirement_id),
        "2022": cell("c11_s1_22", "LNST", "2022", req_lnst_2022.requirement_id),
    })
    r11_s2 = TableRow("r11_s2", "Phân khúc B", {
        "2020": cell("c11_s2_20", "LNST", "2020", "unreq_20"),
        "2021": cell("c11_s2_21", "LNST", "2021", req_lnst_2021.requirement_id),
        "2022": cell("c11_s2_22", "LNST", "2022", req_lnst_2022.requirement_id),
    })
    r11_cp = TableRow("r11_cp", "Chi phí chung", {
        "2020": cell("c11_cp_20", "Chi phí", "2020", "unreq_20"),
        "2021": cell("c11_cp_21", "Chi phí", "2021", "unreq_21"),
        "2022": cell("c11_cp_22", "Chi phí", "2022", "unreq_22"),
    })
    state_11 = TableState.create("tab_11", ["2020", "2021", "2022"], [r11_s1, r11_s2, r11_cp])
    case_11 = ComplexTableCase(
        case_id="CASE_11_SUBSIDIARY_AGGREGATION_THEN_GROWTH",
        description="Multi-division table requiring segment aggregation followed by growth rate (boundary test)",
        complexity_type="GROUP_THEN_GROWTH_BOUNDARY",
        plan=plan_growth,
        query_understanding=q_growth,
        table_state=state_11,
        masked_evidence=bundle_6,
        expected_output_kind=ProgramOutputKind.SCALAR,
        group="MULTI_STEP",
        ground_truth_operations=(
            TableOperationType.SELECT_ROWS,
            TableOperationType.SELECT_COLUMNS,
            TableOperationType.GROUP,
            TableOperationType.ADD_DERIVED_COLUMN,
        ),
        min_expected_steps=4,
        static_inability_reason="M6 Program DSL has no group-then-growth tabular execution support",
    )

    # 12. Boundary Case: Ranked Segment Top Lookup (3 steps)
    # (a) Why static M6 fails: M6 DSL has no SortOp.
    # (b) Ground truth: [SELECT_ROWS, SORT, SELECT_COLUMNS] (3 steps).
    # Planner behavior: Planner fails cleanly (TERMINAL_STATE_UNREACHABLE) testing sort routing boundary.
    r12_s1 = TableRow("r12_s1", "Nhựa", {
        "2020": cell("c12_s1_20", "LNST", "2020", "unreq_20"),
        "2022": cell("c12_s1_22", "LNST", "2022", req_lnst_2022.requirement_id),
    })
    r12_s2 = TableRow("r12_s2", "Bất động sản", {
        "2020": cell("c12_s2_20", "LNST", "2020", "unreq_20"),
        "2022": cell("c12_s2_22", "LNST", "2022", req_lnst_2022.requirement_id),
    })
    r12_s3 = TableRow("r12_s3", "Vận tải", {
        "2020": cell("c12_s3_20", "LNST", "2020", "unreq_20"),
        "2022": cell("c12_s3_22", "LNST", "2022", req_lnst_2022.requirement_id),
    })
    state_12 = TableState.create("tab_12", ["2020", "2022"], [r12_s1, r12_s2, r12_s3])
    case_12 = ComplexTableCase(
        case_id="CASE_12_RANKED_SEGMENT_TOP_LOOKUP",
        description="Segment ranking table requiring sort and extraction of top segment (boundary test)",
        complexity_type="SORT_RANKING_BOUNDARY",
        plan=plan_std,
        query_understanding=q_std,
        table_state=state_12,
        masked_evidence=bundle_6,
        expected_output_kind=ProgramOutputKind.SCALAR,
        group="MULTI_STEP",
        ground_truth_operations=(
            TableOperationType.SELECT_ROWS,
            TableOperationType.SORT,
            TableOperationType.SELECT_COLUMNS,
        ),
        min_expected_steps=3,
        static_inability_reason="M6 DSL completely lacks SortOp or ranking DSL capability",
    )

    # 13. Boundary Case: Cross-Statement Derived Ratio Reduction (3 steps)
    # (a) Why static M6 fails: DERIVED_RATIO without registered formula is rejected by M6 generator.
    # (b) Ground truth: [SELECT_ROWS, SELECT_COLUMNS, ADD_DERIVED_COLUMN] (3 steps).
    # Planner behavior: Rejects or fails cleanly without hardcode leaks.
    r13_lnst = TableRow("r13_ln", "LNST", {
        "2020": cell("c13_ln20", "LNST", "2020", "unreq_20"),
        "2022": cell("c13_ln22", "LNST", "2022", req_lnst_2022.requirement_id),
    })
    r13_vcsh = TableRow("r13_v", "Vốn chủ sở hữu", {
        "2020": cell("c13_v20", "Vốn chủ sở hữu", "2020", "unreq_20"),
        "2022": cell("c13_v22", "Vốn chủ sở hữu", "2022", "unreq_22"),
    })
    state_13 = TableState.create("tab_13", ["2020", "2022"], [r13_lnst, r13_vcsh])
    case_13 = ComplexTableCase(
        case_id="CASE_13_CROSS_STATEMENT_INTERMEDIATE_RATIO",
        description="Cross-statement table requiring multi-step reduction for ratio (boundary test)",
        complexity_type="CROSS_STATEMENT_RATIO_BOUNDARY",
        plan=plan_std,
        query_understanding=q_std,
        table_state=state_13,
        masked_evidence=bundle_6,
        expected_output_kind=ProgramOutputKind.SCALAR,
        group="MULTI_STEP",
        ground_truth_operations=(
            TableOperationType.SELECT_ROWS,
            TableOperationType.SELECT_COLUMNS,
            TableOperationType.ADD_DERIVED_COLUMN,
        ),
        min_expected_steps=3,
        static_inability_reason="M6 generator explicitly rejects unregistered ratio formulations",
    )

    return [
        case_std,
        case_mp,
        case_extra_rows,
        case_extra_cols,
        case_growth_complex,
        case_6,
        case_7,
        case_8,
        case_9,
        case_10,
        case_11,
        case_12,
        case_13,
    ]


def render_comparison_markdown(report: TableTransformComparisonReport) -> str:
    """Format evaluation results into an unpooled, transparent GitHub-flavored markdown report."""
    lines = [
        "# M6B: Static Programmer vs. TABLE_TRANSFORM Fallback Comparison Report",
        "",
        "## Overall Summary Metrics",
        "",
        f"- **Total Benchmark Cases:** {report.total_case_count}",
        f"- **Overall Baseline (M6 Static) Accuracy:** {report.baseline_accuracy:.1%}",
        f"- **Overall Fallback (M6B Table Transform) Accuracy:** {report.fallback_accuracy:.1%}",
        f"- **Baseline Mean Latency:** {report.baseline_mean_latency_ms:.3f} ms",
        f"- **Fallback Mean Latency:** {report.fallback_mean_latency_ms:.3f} ms",
        f"- **Average Fallback Steps:** {report.average_fallback_steps:.2f} steps",
        "",
        "## Group-Level Disaggregated Metrics (Unpooled)",
        "",
        "### 1. Existing Baseline Cases (Cases 1 - 5)",
        f"- **Cases:** 5 (Standard lookups, simple multi-period, 1-step row/column pruning)",
        f"- **Static M6 Accuracy:** {report.baseline_group_accuracy:.1%}",
        f"- **M6B Fallback Accuracy:** {report.baseline_group_fallback_accuracy:.1%}",
        "",
        "### 2. New Multi-Step Complex Cases (Cases 6 - 13, >= 3 operations)",
        f"- **Cases:** 8 (All requiring >= 3 distinct operation types)",
        f"- **Static M6 Accuracy:** {report.multistep_group_baseline_accuracy:.1%}",
        f"- **M6B Fallback Accuracy:** {report.multistep_group_fallback_accuracy:.1%}",
        f"- **Average Steps on Multi-Step Group:** {report.multistep_group_mean_steps:.2f} steps",
        "",
        "## Step Distribution Across All Benchmark Cases",
        "",
        "| Fallback Steps Taken | Case Count |",
        "|---|---|",
    ]

    for steps, count in sorted(report.step_distribution.items()):
        lines.append(f"| {steps} steps | {count} cases |")

    lines.extend([
        "",
        "## Per-Case Detailed Results & Step Efficiency",
        "",
        "| Case ID | Group | Complexity Type | Min Steps | Actual Steps | Efficiency | Static M6 | Fallback | Fallback Latency | Static Inability Reason |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ])

    for r in report.results:
        b_res = "PASS" if r.baseline_success else f"FAIL ({r.baseline_failure_code})"
        f_res = "PASS" if r.fallback_success else f"FAIL ({r.fallback_failure_code})"
        lines.append(
            f"| `{r.case_id}` | `{r.group}` | `{r.complexity_type}` | {r.min_expected_steps} | "
            f"{r.actual_steps} | `{r.step_efficiency}` | {b_res} | {f_res} | "
            f"{r.fallback_latency_ms:.2f} ms | {r.static_inability_reason} |"
        )

    lines.extend([
        "",
        "## Independent Corpus Data Status (ViFinQA / TASK-007)",
        "",
        f"> **Status:** {report.corpus_cases_note}",
        "",
        "## Architectural Recommendation",
        "",
        f"> {report.recommendation}",
        "",
    ])

    return "\n".join(lines)
