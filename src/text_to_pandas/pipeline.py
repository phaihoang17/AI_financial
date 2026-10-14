"""Question API with bounded code repair and append-only per-attempt history."""

import ast
from datetime import datetime, timezone
import json
from pathlib import Path
from time import monotonic_ns
from uuid import uuid4

from src.sandbox.pandas_executor import execute_pandas_sandboxed
from src.sandbox.pandas_protocol import PandasFailureStage
from src.text_to_pandas.metrics import METRICS_BY_ID
from src.text_to_pandas.catalog import DatasetCatalog
from src.text_to_pandas.schemas import AttemptRecord, AttemptStage, ProgramStatus, ResponseStatus
from src.text_to_pandas.understanding import GateDecision, discover, understand_question
from src.text_to_pandas.validator import validate_pandas_code
from src.text_to_pandas.verifier import reference_answer, result_matches, verify_answer


class PandasPipeline:
    """Use existing catalog, generator and isolated executor through one API.

    One run uses at most three generations. A unique directory retains each
    completed attempt immediately, followed by the terminal response. No prior
    run is overwritten, even when the caller repeats a request ID.
    """

    def __init__(self, catalog, generator, *, history_root, max_retries=2):
        if type(max_retries) is not int or not 0 <= max_retries <= 2:
            raise ValueError("max_retries must be an integer from 0 to 2")
        self.catalog, self.generator = catalog, generator
        self.history_root, self.max_retries = Path(history_root), max_retries

    def ask(self, question, *, request_id=None):
        if not isinstance(question, str) or not question.strip() or len(question) > 8000:
            raise ValueError("question must be nonempty text of at most 8000 characters")
        if request_id is not None and (not isinstance(request_id, str) or not request_id.strip()):
            raise ValueError("request_id must be nonempty text")
        start = monotonic_ns()
        run_id = uuid4().hex
        directory = self.history_root / run_id
        directory.mkdir(parents=True, exist_ok=False)
        attempts = []
        understanding = understand_question(question, self.catalog)
        discovery = discover(understanding, self.catalog)
        response = {"schema_version": "t2p-response-v1", "run_id": run_id,
                    "request_id": request_id or run_id, "question": question,
                    "catalog_id": self.catalog.catalog_id,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "understanding": understanding.to_dict(), "discovery": discovery.to_dict()}

        def write(name, value):
            # Exclusive creation prevents accidental rewriting of attempt history.
            with (directory / name).open("x", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, allow_nan=False, indent=2)
                handle.write("\n")

        write("request.json", response)

        def finish(status, reason=None, message=None, result=None, unit=None, sources=None):
            response.update(status=status.value, reason_code=reason, message=message,
                            result=result, unit=unit, scale="PERCENT" if unit == "PERCENT" else "RAW" if unit in {"VND", "VND/share"} else None,
                            sources=sources or [], attempts=attempts,
                            elapsed_ms=(monotonic_ns() - start) // 1_000_000)
            write("response.json", response)
            return response

        if discovery.decision is not GateDecision.PROCEED:
            return finish(ResponseStatus(discovery.decision.value), discovery.reason, discovery.message)
        previous, feedback = None, None
        payload = discovery.data.payload()
        for index in range(self.max_retries + 1):
            attempt = AttemptRecord(index + 1)
            generated = self.generator.generate(understanding, discovery, previous=previous, feedback=feedback)
            attempt.generation_ms = generated.elapsed_ms
            attempt.llm_usage = generated.usage
            program = generated.program
            retry = True
            if program is None:
                attempt.failure_code, attempt.failure_message = generated.error_code, generated.error_message
                # Transport/config errors need operator recovery, not code repair.
                retry = generated.error_code in {"GENERATION_JSON_INVALID", "GENERATION_ENVELOPE_INVALID", "SERVING_RESPONSE_TRUNCATED"}
            else:
                attempt.program = program.to_dict()
                if program.status is not ProgramStatus.OK:
                    attempt.outcome = AttemptStage.DECLINED
                    retry = False
                else:
                    attempt.outcome = AttemptStage.VALIDATION
                    validation = validate_pandas_code(program.code, datasets={name: frame.columns for name, frame in discovery.data.frames.items()},
                                                      known_metrics=METRICS_BY_ID)
                    attempt.validation = validation.to_dict()
                    used = {node.id for node in ast.walk(ast.parse(program.code)) if isinstance(node, ast.Name)
                            and isinstance(node.ctx, ast.Load) and node.id in payload} if validation.ok else set()
                    if not validation.ok:
                        issue = validation.issues[0]
                        attempt.failure_code, attempt.failure_message = issue.code, issue.message
                        retry = not validation.security_violation
                    elif used != set(program.selected_datasets):
                        attempt.failure_code = "DATASET_DECLARATION_MISMATCH"
                        attempt.failure_message = "selected_datasets must name exactly the DataFrames read by code"
                    else:
                        attempt.outcome = AttemptStage.EXECUTION
                        execution = execute_pandas_sandboxed(program.code, payload, request_id=f"{run_id}-{index + 1}")
                        attempt.execution = execution.to_dict()
                        attempt.execution_ms = execution.execution_ms
                        if not execution.success:
                            attempt.failure_code, attempt.failure_message = execution.failure.code, execution.failure.message
                            retry = execution.failure.stage in {PandasFailureStage.RUNTIME, PandasFailureStage.VALIDATION, PandasFailureStage.SERIALIZATION}
                        else:
                            attempt.outcome = AttemptStage.VERIFICATION
                            report = verify_answer(understanding, program, execution, self.catalog, discovery)
                            if report["passed"]:
                                # One deterministic perturbation detects constants and coincidental
                                # formula matches without another model call or any gold data.
                                frames = {name: frame.copy() for name, frame in discovery.data.frames.items()}
                                for frame in frames.values():
                                    if "value" in frame:
                                        offsets = frame.index.to_series() % 7
                                        frame["value"] = frame["value"] * (1.1 + offsets / 10) + offsets + 17
                                probe_catalog = DatasetCatalog.from_frames(frames)
                                probe = execute_pandas_sandboxed(program.code, probe_catalog.slice(
                                    tickers=understanding.tickers, metrics=understanding.required_metrics).payload(),
                                    request_id=f"{run_id}-{index + 1}-formula-probe")
                                try:
                                    expected, _, _ = reference_answer(understanding, probe_catalog)
                                    probe_passed = probe.success and result_matches(understanding, probe.result, expected)
                                except ValueError:
                                    probe_passed = False
                                report["formula_probe"] = {"passed": bool(probe_passed), "execution_ms": probe.execution_ms,
                                                           "failure": None if probe.failure is None else probe.failure.to_dict()}
                                report["checks"].append({"code": "FORMULA_PROGRAM", "passed": bool(probe_passed),
                                                         "message": "code must also match the reference after source-value perturbation"})
                                report["passed"] = bool(probe_passed)
                            attempt.verification = report
                            if report["passed"]:
                                attempt.outcome = AttemptStage.PASSED
                            else:
                                failed = next(check for check in report["checks"] if not check["passed"])
                                attempt.failure_code, attempt.failure_message = failed["code"], failed["message"]
                                retry = failed["code"] not in {"REFERENCE_INPUT_UNAVAILABLE", "ZERO_DENOMINATOR", "UNSUPPORTED_VERIFICATION"}
                                probe_failure = (report.get("formula_probe") or {}).get("failure")
                                if probe_failure and probe_failure["stage"] in {"SECURITY", "RESOURCE", "INFRASTRUCTURE"}:
                                    retry = False
            record = attempt.to_dict()
            attempts.append(record)
            write(f"attempt-{index + 1}.json", record)
            if attempt.outcome is AttemptStage.PASSED:
                return finish(ResponseStatus.ANSWERED, result=attempt.execution["result"], unit=program.result_unit,
                              sources=attempt.verification["sources"])
            if attempt.outcome is AttemptStage.DECLINED:
                return finish(ResponseStatus(program.status.value), program.status.value, program.explanation)
            if not retry:
                break
            previous = attempt.program or {"code": ""}
            feedback = {"stage": attempt.outcome.value, "code": attempt.failure_code, "message": attempt.failure_message}
        return finish(ResponseStatus.NO_DATA if attempt.failure_code in {"REFERENCE_INPUT_UNAVAILABLE", "ZERO_DENOMINATOR"}
                      else ResponseStatus.FAILED, attempt.failure_code, attempt.failure_message)
