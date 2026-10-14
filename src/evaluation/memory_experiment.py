"""Synthetic held-out M11 ablation and paired served CHEAP/STRONG measurement."""
from collections import Counter
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path

from src.evaluation.memory_fixtures import episode, held_out_episodes
from src.evaluation.program_trace import compare_program_traces, normalized_program_equivalence
from src.evaluation.reasoning import TraceComparison
from src.evaluation.telemetry import Distribution, Stopwatch
from src.memory.index import MemoryIndex
from src.memory.schemas import MemoryContext
from src.orchestration.api_stages import APIProgrammer
from src.orchestration.graph import OrchestrationGraph
from src.orchestration.live import LiveSandbox
from src.programmer.generator import generate_program
from src.programmer.schemas import ProgrammerInput
from src.serving.client import OpenAICompatibleClient
from src.supervisor import cheap_routing


class FixtureProgrammerClient:
    """Deterministic transport substitute; reports no invented token/cost counts."""
    def generate(self, messages, **params):
        payload = json.loads(messages[1]["content"])
        programmer_input = ProgrammerInput.from_dict({key: payload[key] for key in
                             ("plan", "masked_evidence", "formula_registry_fingerprint")})
        result = generate_program(programmer_input)
        program = result.program.to_dict()
        return json.dumps({key: program[key] for key in ("inputs", "steps", "output_ref", "output_kind")})


def _run_arm(name, client, *, memory=None, tools=True, cheap_enabled=True):
    records, samples, invocations = [], [], []
    if isinstance(client, OpenAICompatibleClient):
        client = replace(client, observer=invocations.append)
    previous = cheap_routing.CHEAP_ROUTING_ACTIVATED
    cheap_routing.CHEAP_ROUTING_ACTIVATED = cheap_enabled
    try:
        for case_id, operation, periods, values in held_out_episodes():
            understanding, fixture = episode(case_id, operation, periods, values)
            oracle = fixture.run(request_id=case_id, raw_question=understanding.raw_question).state
            expected = oracle.final_response.answer.output.to_dict()
            graph = OrchestrationGraph(replace(fixture.dependencies,
                      programmer=APIProgrammer(client, memory=memory, formula_metadata=tools), sandbox=LiveSandbox()))
            watch = Stopwatch()
            invocation_start = len(invocations)
            state = graph.run(request_id=case_id, raw_question=understanding.raw_question).state
            samples.append(watch.elapsed_ms())
            output = state.final_response.answer.output.to_dict() if state.final_response.answer else None
            context = MemoryContext.from_plan(state.supervisor_result.plan)
            retrieved = memory.retrieve(context) if memory else ()
            complete = state.attempts[-1].evidence_completeness.complete
            programmer_result = state.attempts[-1].programmer_result
            actual_program = programmer_result.program if programmer_result else None
            trace = compare_program_traces(
                oracle.attempts[-1].programmer_result.program, actual_program,
                normalized_program_equivalence,
            ) if actual_program is not None else TraceComparison.DIFFERENT
            case_invocations = invocations[invocation_start:]
            token_measured = bool(case_invocations) and all(
                isinstance(i[key], int) and not isinstance(i[key], bool) and i[key] >= 0
                for i in case_invocations for key in ("prompt_tokens", "completion_tokens")
            )
            records.append({
                "case_id": case_id, "routed_tier": state.supervisor_result.plan.model_tier.value,
                "verify_profile": state.supervisor_result.plan.verify_profile.value,
                "answer_correct": output == expected, "status": state.outcome.value,
                "failure_code": state.final_response.reason_code,
                "attempts": len(state.attempts), "required_evidence_complete": complete,
                "latency_ms": samples[-1],
                "prompt_tokens": sum(i["prompt_tokens"] for i in case_invocations) if token_measured else None,
                "completion_tokens": sum(i["completion_tokens"] for i in case_invocations) if token_measured else None,
                "attempt_history": [{
                    "index": a.attempt_index, "entry_stage": a.entry_stage.value,
                    "programmer_failure": a.programmer_result.failure_code if a.programmer_result else None,
                    "execution_failure": a.execution_result.failure.code if a.execution_result and a.execution_result.failure else None,
                    "verification_failure": a.verification_report.failure_reason if a.verification_report else None,
                } for a in state.attempts],
                "memory_entry_ids": [entry.entry_id for entry in retrieved],
                "program_validated": actual_program is not None,
                "program_trace_comparison": trace.value,
            })
    finally:
        cheap_routing.CHEAP_ROUTING_ACTIVATED = previous
    measured_tokens = bool(invocations) and all(r["prompt_tokens"] is not None
                      and r["completion_tokens"] is not None for r in records)
    return {"arm": name, "case_count": len(records),
            "answer_accuracy": sum(r["answer_correct"] for r in records) / len(records),
            "program_trace_match_rate": sum(r["program_trace_comparison"] != TraceComparison.DIFFERENT.value
                                            for r in records) / len(records),
            "retrieval_quality_metric": "SYNTHETIC_REQUIRED_EVIDENCE_COMPLETENESS",
            "retrieval_quality": sum(r["required_evidence_complete"] for r in records) / len(records),
            "production_retrieval_recall_at_k": None, "production_retrieval_mrr": None,
            "latency": Distribution.from_samples(samples).to_dict(),
            "prompt_tokens": sum(i["prompt_tokens"] for i in invocations) if measured_tokens else None,
            "completion_tokens": sum(i["completion_tokens"] for i in invocations) if measured_tokens else None,
            "monetary_cost": None, "pricing_status": "NOT_SUPPLIED",
            "failure_distribution": dict(Counter(code for r in records for a in r["attempt_history"]
                                        for field, code in a.items() if field.endswith("failure") and code)),
            "model_invocations": invocations, "cases": records}


def run_memory_ablation(client, *, memory: MemoryIndex, index_path):
    path = Path(index_path)
    before = sha256(path.read_bytes()).hexdigest()
    held = held_out_episodes()
    held_ids = {row[0] for row in held}
    held_hashes = {sha256(episode(*row)[0].raw_question.encode()).hexdigest() for row in held}
    if any(e.source_id in held_ids or e.question_signature in held_hashes for e in memory.entries):
        raise ValueError("memory overlaps held-out episodes")
    arms = [_run_arm("BASE", client, tools=False),
            _run_arm("TOOLS_ONLY", client),
            _run_arm("TOOLS_MEMORY", client, memory=memory)]
    after = sha256(path.read_bytes()).hexdigest()
    if before != after:
        raise ValueError("held-out evaluation modified the memory index")
    tools_by_id = {case["case_id"]: case for case in arms[1]["cases"]}
    gated = [case for case in arms[2]["cases"] if case["memory_entry_ids"]]
    gated_delta = (sum(case["answer_correct"] - tools_by_id[case["case_id"]]["answer_correct"]
                       for case in gated) / len(gated)) if gated else None
    control_changes = [case["case_id"] for case in arms[2]["cases"]
                       if not case["memory_entry_ids"] and case["answer_correct"] != tools_by_id[case["case_id"]]["answer_correct"]]
    return {"schema_version": "m11-memory-ablation-v1", "input_origin": "SYNTHETIC_HELD_OUT",
            "generative_measurement": "LIVE" if isinstance(client, OpenAICompatibleClient) else "CPU_FIXTURE",
            "production_quality_measured": False, "index_unchanged": True,
            "index_sha256_before": before, "index_sha256_after": after,
            "memory_artifact_id": memory.artifact_id, "arms": arms,
            "memory_accuracy_delta": arms[2]["answer_accuracy"] - arms[1]["answer_accuracy"],
            "gated_case_accuracy_delta": gated_delta,
            "unadvised_control_outcome_changes": control_changes,
            "interpretation": "One synthetic run; observed deltas are not causal or production-quality evidence."}


def run_cheap_routing_experiment(client):
    cheap = _run_arm("ROUTED_CHEAP", client)
    strong = _run_arm("FORCED_STRONG", client, cheap_enabled=False)
    eligible = {r["case_id"] for r in cheap["cases"] if r["routed_tier"] == "CHEAP"}
    cheap_accuracy = sum(r["answer_correct"] for r in cheap["cases"] if r["case_id"] in eligible) / len(eligible)
    strong_accuracy = sum(r["answer_correct"] for r in strong["cases"] if r["case_id"] in eligible) / len(eligible)
    def eligible_metrics(arm):
        selected = [case for case in arm["cases"] if case["case_id"] in eligible]
        tokens_known = all(case["completion_tokens"] is not None and case["prompt_tokens"] is not None for case in selected)
        return {"latency": Distribution.from_samples([case["latency_ms"] for case in selected]).to_dict(),
                "prompt_tokens": sum(case["prompt_tokens"] for case in selected) if tokens_known else None,
                "completion_tokens": sum(case["completion_tokens"] for case in selected) if tokens_known else None,
                "monetary_cost": None}
    return {"schema_version": "m10-cheap-routing-experiment-v1", "input_origin": "SYNTHETIC_HELD_OUT",
            "generative_measurement": "LIVE" if isinstance(client, OpenAICompatibleClient) else "CPU_FIXTURE",
            "production_quality_measured": False, "eligible_case_count": len(eligible),
            "cheap_accuracy": cheap_accuracy, "strong_accuracy": strong_accuracy,
            "accuracy_delta": cheap_accuracy - strong_accuracy,
            "eligible_cheap_metrics": eligible_metrics(cheap),
            "eligible_strong_metrics": eligible_metrics(strong),
            "cheap": cheap, "strong": strong,
            "note": "Single-endpoint runs measure CHEAP/STRONG inference settings; distinct model endpoints remain deployment choices."}
