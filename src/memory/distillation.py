"""Offline TRAIN/DEV-only distillation; no answers or raw evidence enter entries."""
from hashlib import sha256

from src.memory.schemas import MemoryContext, MemoryEntry, MemoryKind, MemoryRule
from src.orchestration.schemas import FinalResponseStatus
from src.orchestration.state import OrchestrationState
from src.programmer.schemas import canonical_json
from src.understanding.schemas import SchemaValidationError

GUARD_BY_FAILURE = {
    "FORMULA_ARITY_MISMATCH": MemoryRule.SCALAR_FORMULA_INPUTS,
    "FORMULA_INPUT_ORDER_MISMATCH": MemoryRule.PREVIOUS_THEN_CURRENT,
    "PROGRAMMER_JSON_INVALID": MemoryRule.COMPLETE_JSON,
    "SERVING_RESPONSE_TRUNCATED": MemoryRule.COMPLETE_JSON,
    "REPORT_YEAR_MISMATCH": MemoryRule.PRESERVE_SOURCE_IDS,
}


def distill_trajectory(state: OrchestrationState, *, split: str, dataset_id: str,
                       held_out_ids=(), held_out_question_signatures=()):
    if split not in {"TRAIN", "DEV"}:
        raise SchemaValidationError("held-out trajectories cannot be distilled")
    state = OrchestrationState.from_dict(state.to_dict())
    signature = sha256(state.raw_question.encode()).hexdigest()
    if state.request_id in held_out_ids or signature in held_out_question_signatures:
        raise SchemaValidationError("trajectory overlaps declared held-out data")
    if not state.terminal:
        raise SchemaValidationError("distillation requires a terminal trajectory")
    plan = state.supervisor_result.plan if state.supervisor_result else None
    if plan is None:
        return ()
    context = MemoryContext.from_plan(plan)
    digest = sha256(canonical_json(state.to_dict()).encode()).hexdigest()

    def entry(kind, rules):
        return MemoryEntry(context, signature, kind, tuple(rules), state.request_id,
                           split, dataset_id, digest)

    entries = []
    if state.outcome is FinalResponseStatus.PASS:
        rules = [MemoryRule.REQUIRED_INPUTS]
        if plan.formula_id:
            rules.append(MemoryRule.SCALAR_FORMULA_INPUTS)
        if plan.formula_id == "GROWTH_RATE":
            rules.append(MemoryRule.PREVIOUS_THEN_CURRENT)
        entries.append(entry(MemoryKind.STRATEGY, rules))
    seen = set()
    for attempt in state.attempts:
        codes = []
        if attempt.programmer_result and attempt.programmer_result.failure_code:
            codes.append(attempt.programmer_result.failure_code)
        if attempt.verification_report and attempt.verification_report.failure_reason:
            codes.append(attempt.verification_report.failure_reason)
        if attempt.execution_result and attempt.execution_result.failure:
            codes.append(attempt.execution_result.failure.code)
        for code in codes:
            rule = GUARD_BY_FAILURE.get(code)
            if rule is MemoryRule.PREVIOUS_THEN_CURRENT and plan.formula_id != "GROWTH_RATE":
                continue
            if rule is MemoryRule.SCALAR_FORMULA_INPUTS and plan.formula_id is None:
                continue
            if rule and rule not in seen:
                entries.append(entry(MemoryKind.GUARD, (rule,)))
                seen.add(rule)
    return tuple(entries)
