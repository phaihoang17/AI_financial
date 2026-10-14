"""TASK-100 scoring of recorded M9 runs against independently verified gold.

This replays checkpoints; it does not call serving endpoints or generate gold.
"""

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from src.evaluation.baseline import (
    BASELINE_ACCEPTANCE_THRESHOLDS,
    BASELINE_MODEL_TIER,
    BASELINE_SCHEMA_VERSION,
    BASELINE_VERIFY_PROFILE,
)
from src.evaluation.corpus_reference_index import CorpusReferenceIndex
from src.evaluation.gold_retrieval import (
    GoldAnnotationStatus,
    GoldRetrievalCase,
    validate_gold_cases,
)
from src.evaluation.retrieval_production_validation import (
    PRODUCTION_RETRIEVAL_VALIDATION_SCHEMA_VERSION,
)
from src.evaluation.telemetry import MeasurementSource
from src.evidence.masking_schemas import SchemaLinkStatus
from src.numeric.scale_conversion import ScaleConversionStatus, convert_canonical_scale
from src.orchestration.schemas import FinalResponseStatus
from src.orchestration.state import OrchestrationState
from src.programmer.schemas import ProgramOutputKind
from src.sandbox.schemas import ExecutionDatum
from src.understanding.schemas import SchemaValidationError

BASELINE_RUNS_SCHEMA_VERSION = "m10-baseline-runs-v1"


def blocked_baseline(*blockers: str) -> dict:
    return {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "baseline_config": {"model_tier": BASELINE_MODEL_TIER.value,
                            "verify_profile": BASELINE_VERIFY_PROFILE.value},
        "acceptance_thresholds": BASELINE_ACCEPTANCE_THRESHOLDS,
        "evaluation_mode": "RECORDED_E2E",
        "established": False,
        "status": "BLOCKED",
        "blockers": list(blockers),
        "answer_accuracy": None,
    }


def _validate_retrieval(validation: Mapping[str, Any], index: CorpusReferenceIndex,
                        runs: Mapping[str, Any]) -> bool:
    """Require the complete ADR-057 artifact/live gates, bound to this run."""
    if (validation.get("schema_version") != PRODUCTION_RETRIEVAL_VALIDATION_SCHEMA_VERSION
            or validation.get("clears_gpu_pending") is not True
            or any(validation.get(k) != "PASS" for k in
                   ("overall_status", "artifact_status", "live_serving_status"))):
        return False
    checks = validation.get("artifact_serving_checks", [])
    if not isinstance(checks, list) or not all(isinstance(c, dict)
            and isinstance(c.get("name"), str) and isinstance(c.get("detail"), dict)
            and c.get("category") == "ARTIFACT_SERVING" for c in checks):
        return False
    by_name = {c.get("name"): c for c in checks}
    required = {
        "corpus_index_compatibility": "ARTIFACT", "bm25_compatibility": "ARTIFACT",
        "no_table_count_substitution": "ARTIFACT", "canonical_corpus_chunk_count": "ARTIFACT",
        "vector_count_equals_canonical_chunk_count": "ARTIFACT",
        "shard_and_sqlite_integrity": "ARTIFACT", "embedding_fingerprint": "ARTIFACT",
        "live_bge_m3_embedding_parity": "LIVE", "live_bge_reranker_parity": "LIVE",
        "deterministic_retrieval_execution": "LIVE",
    }
    if (len(by_name) != len(checks) or not required.keys() <= by_name.keys()
            or any(c.get("status") != "PASS" for c in checks)
            or any(by_name[n].get("evidence") != e for n, e in required.items())):
        return False
    vector = by_name["corpus_index_compatibility"].get("detail", {})
    bm25 = by_name["bm25_compatibility"].get("detail", {})
    return (runs.get("corpus_artifact_id") == index.corpus_artifact_id
            and bool(runs.get("vector_artifact_id")) and bool(runs.get("bm25_artifact_id"))
            and vector.get("corpus_fingerprint") == index.source_inventory_sha256
            and bm25.get("corpus_fingerprint") == index.source_inventory_sha256
            and vector.get("artifact_id") == runs.get("vector_artifact_id")
            and bm25.get("artifact_id") == runs.get("bm25_artifact_id"))


def evaluate_production_baseline(
    cases: Sequence[GoldRetrievalCase], index: CorpusReferenceIndex,
    runs: Mapping[str, Any], retrieval_validation: Mapping[str, Any],
) -> dict:
    """Score every supplied VERIFIED scalar case; omissions count as failures.

    shortcut: export origin is operator-attested, capture live runs in-process
    when a production M9 adapter exists; this report labels evaluation as replay.
    """
    report = blocked_baseline()
    if runs.get("schema_version") != BASELINE_RUNS_SCHEMA_VERSION:
        raise SchemaValidationError("unsupported baseline run export schema")
    if runs.get("measurement_source") != MeasurementSource.LIVE.value:
        return blocked_baseline("LIVE_E2E_RUNS_REQUIRED")
    if not _validate_retrieval(retrieval_validation, index, runs):
        return blocked_baseline("RETRIEVAL_PRODUCTION_VALIDATION_REQUIRED")
    validation = validate_gold_cases(cases, index)
    report["gold_validation"] = validation.to_dict()
    # Grounding mismatches are warnings in the general gold tool, but block this baseline.
    if not validation.ok or any(i.code in {"TICKER_MISMATCH", "SCOPE_MISMATCH",
                                          "PERIOD_MISMATCH"} for i in validation.issues):
        report["blockers"] = ["GOLD_VALIDATION_FAILED"]
        return report
    selected = [c for c in cases if c.annotation_status is GoldAnnotationStatus.VERIFIED
                and c.gold_answer is not None]
    if not selected:
        report["blockers"] = ["BLOCKED_GOLD_DATA"]
        return report
    raw_states = runs.get("states")
    if not isinstance(raw_states, list):
        raise SchemaValidationError("baseline runs.states must be a list")
    states = {}
    for raw in raw_states:
        state = OrchestrationState.from_dict(raw)
        if not state.terminal or state.request_id in states:
            raise SchemaValidationError("baseline runs require unique terminal request_id values")
        states[state.request_id] = state
    if set(states) - {c.question_id for c in selected}:
        raise SchemaValidationError("baseline export contains requests outside the scored gold set")
    results = []
    blockers = set()
    for case in selected:
        gold = case.gold_answer
        try:
            gold_datum = ExecutionDatum.from_dict(gold.to_dict())
            gold_value, gold_scale = gold_datum.value, gold_datum.scale
        except (ValueError, SchemaValidationError) as error:
            raise SchemaValidationError(f"invalid gold numeric answer for {case.question_id}") from error
        if gold_scale is None:
            blockers.add("GOLD_SCALE_UNRESOLVED")
        state = states.get(case.question_id)
        result = {"question_id": case.question_id, "answer_correct": False,
                  "value_correct": False, "unit_correct": False, "grounding_correct": False,
                  "execution_success": False, "verification_passed": False,
                  "failure_stage": None, "failure_code": None}
        if state is None:
            blockers.add("E2E_RUNS_INCOMPLETE")
            result["failure_code"] = "MISSING_E2E_RUN"
            results.append(result)
            continue
        if state.raw_question != case.question:
            raise SchemaValidationError(f"question mismatch for {case.question_id}")
        plan = state.supervisor_result.plan if state.supervisor_result else None
        if plan is not None and (plan.model_tier is not BASELINE_MODEL_TIER
                or plan.verify_profile is not BASELINE_VERIFY_PROFILE
                or any(a.model_tier is not BASELINE_MODEL_TIER for a in state.attempts)):
            blockers.add("STRONG_STRICT_CONFIGURATION_REQUIRED")
        attempt = state.attempts[-1] if state.attempts else None
        result["execution_success"] = bool(attempt and attempt.execution_result
                                           and attempt.execution_result.success)
        result["verification_passed"] = bool(attempt and attempt.verification_report
                                             and attempt.verification_report.passed)
        if state.failure_attribution:
            result["failure_stage"] = state.failure_attribution.stage.value
            result["failure_code"] = state.failure_attribution.code
        answer = state.final_response.answer
        if state.outcome is FinalResponseStatus.PASS and answer is not None:
            if answer.output.kind is not ProgramOutputKind.SCALAR:
                raise SchemaValidationError("scalar gold requires a scalar E2E answer")
            datum = answer.output.values[0]
            converted = convert_canonical_scale(datum.value, source_scale=datum.scale,
                source_unit=datum.unit, requested_output_scale=gold_scale)
            result["value_correct"] = (gold_scale is not None
                and converted.status is ScaleConversionStatus.SUCCESS
                and Decimal(converted.canonical_value) == Decimal(gold_value))
            result["unit_correct"] = datum.unit == gold.unit
            refs = answer.evidence
            used_ids = {r.evidence_id for r in refs}
            used_cells = {link.matched_source_cell_id for link in attempt.schema_links
                          if link.evidence_id in used_ids and link.status is SchemaLinkStatus.RESOLVED}
            used_paragraphs = {r.paragraph_ref for r in refs if r.paragraph_ref}
            used_chunks = {location.chunk_id for location in attempt.cell_locations
                           if location.source_cell_id in used_cells}
            used_chunks.update(c.chunk_id for c in attempt.retrieval_candidates
                               if c.paragraph_id in used_paragraphs)
            result["grounding_correct"] = bool(plan and refs
                and plan.company.ticker == case.company_ticker
                and (case.statement_scope is None or plan.statement_scope.value == case.statement_scope)
                and (case.period is None or case.period in plan.periods)
                and (case.period_kind is None or plan.period_kind.value == case.period_kind)
                and (not case.gold_source_cell_ids or used_cells.intersection(case.gold_source_cell_ids))
                and (not case.gold_chunk_ids or used_chunks.intersection(case.gold_chunk_ids))
                and (case.gold_report_id is None or case.gold_report_id in {r.report_ref for r in refs})
                and (case.gold_table_id is None or case.gold_table_id in {r.table_ref for r in refs})
                and (case.gold_paragraph_id is None or case.gold_paragraph_id in {r.paragraph_ref for r in refs}))
            result["answer_correct"] = all(result[k] for k in
                ("value_correct", "unit_correct", "grounding_correct", "verification_passed"))
        results.append(result)
    if not any(s.supervisor_result and s.supervisor_result.plan
               and s.supervisor_result.plan.model_tier is BASELINE_MODEL_TIER
               and s.supervisor_result.plan.verify_profile is BASELINE_VERIFY_PROFILE
               for s in states.values()):
        blockers.add("STRONG_STRICT_CONFIGURATION_REQUIRED")
    report.update({
        "measurement_source": MeasurementSource.LIVE.value,
        "corpus_artifact_id": index.corpus_artifact_id,
        "vector_artifact_id": runs["vector_artifact_id"],
        "bm25_artifact_id": runs["bm25_artifact_id"],
        "scored_case_count": len(results), "skipped_case_count": len(cases) - len(selected),
        "correct_count": sum(r["answer_correct"] for r in results),
        "answer_accuracy": (None if "GOLD_SCALE_UNRESOLVED" in blockers else
                            sum(r["answer_correct"] for r in results) / len(results)),
        "execution_success_rate": sum(r["execution_success"] for r in results) / len(results),
        "verification_pass_rate": sum(r["verification_passed"] for r in results) / len(results),
        "trace_accuracy": None,  # GoldRetrievalCase contains no gold program.
        "blockers": sorted(blockers), "established": not blockers,
        "status": "BLOCKED" if blockers else "MEASURED",
        "case_results": results,
    })
    return report
