"""One-shot isolated worker that runs generated Pandas code (ADR-069).

The parent starts this module as a separate process (``src/sandbox/pandas_executor.py``)
with a minimal exact environment, an empty read-only working directory and no
inherited file descriptors besides stdin/stdout/stderr. Inside, the worker:

1. verifies the environment and installs the ``t2p-pandas-limits-v1`` OS limits;
2. imports pandas/numpy and builds the approved DataFrames from the request;
3. reruns the security subset of code validation and compiles the code;
4. warms pandas' lazily imported modules, then installs audit guards that deny
   every file open, socket, process, OS and FFI event for the rest of its life;
5. executes the code with restricted builtins and an import allowlist;
6. returns JSON descriptions of ``result`` / ``evidence`` or one typed failure.

A guard violation is recorded even when the generated code swallows the
exception, and is always reported as a SECURITY failure.
"""

from __future__ import annotations

import builtins
import json
import math
import os
from pathlib import Path
import sys
from time import monotonic_ns
from typing import Any, Dict, List, Optional

from src.sandbox.limits import (
    T2P_PANDAS_LIMITS_V1,
    ResourceLimitFailureCode,
    WorkerResourceViolation,
    apply_worker_resource_limits,
)
from src.sandbox.pandas_protocol import (
    MAX_ERROR_MESSAGE_CHARS,
    MAX_RESULT_ROWS,
    T2P_REQUEST_SCHEMA_VERSION,
    T2P_RESULT_SCHEMA_VERSION,
    PandasExecutionResult,
    PandasFailureStage,
    encode,
    failure_result,
)
from src.sandbox.security import (
    SecurityFailureCode,
    _open_is_write,
    WorkerSecurityViolation,
    expected_worker_environment,
    install_worker_security_guards,
)
from src.text_to_pandas.validator import SAFE_BUILTIN_NAMES, validate_pandas_code

# Single-threaded numeric libraries: no worker threads, deterministic reductions.
THREAD_ENVIRONMENT = {
    "MKL_NUM_THREADS": "1",
    "NUMEXPR_MAX_THREADS": "1",
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
}
_DENIED_EVENT_PREFIXES = (
    "os.", "shutil.", "ctypes.", "socket.", "subprocess.", "sqlite3.", "urllib.", "http.", "ftplib.",
    "poplib.", "imaplib.", "smtplib.", "telnetlib.", "webbrowser.", "glob.", "pty.", "mmap.", "fcntl.",
    "resource.", "signal.", "sys._getframe", "sys.settrace", "sys.setprofile", "code.", "pdb.",
)
_DENIED_EVENTS = frozenset({"open", "compile_command", "import_module", "cpython.run_command"})
_violations: List[str] = []


def expected_pandas_worker_environment(project_root: Path, platform_name: Optional[str] = None) -> Dict[str, str]:
    return {**expected_worker_environment(project_root, platform_name), **THREAD_ENVIRONMENT}


def _guard(event: str, args: tuple) -> None:
    if event in _DENIED_EVENTS or event.startswith(_DENIED_EVENT_PREFIXES):
        _violations.append(event)
        if event == "open":
            code = (SecurityFailureCode.FILESYSTEM_WRITE_DENIED if _open_is_write(args)
                    else SecurityFailureCode.FILESYSTEM_READ_DENIED)
        elif event.startswith("socket."):
            code = SecurityFailureCode.NETWORK_ACCESS_DENIED
        elif event.startswith(("subprocess.", "os.exec", "os.fork", "os.posix_spawn", "os.spawn", "os.system",
                               "pty.")):
            code = SecurityFailureCode.PROCESS_SPAWN_DENIED
        else:
            code = SecurityFailureCode.ISOLATION_SETUP_FAILED
        raise WorkerSecurityViolation(code, f"generated code attempted a denied operation: {event}")


def _restricted_import(name, globals=None, locals=None, fromlist=(), level=0):
    root = name.split(".")[0]
    if level or root not in {"pandas", "numpy", "math"}:
        raise ImportError(f"import of {name} is not allowed")
    return sys.modules[name] if name in sys.modules else sys.modules[root]


def _safe_builtins() -> Dict[str, Any]:
    allowed = {name: getattr(builtins, name) for name in SAFE_BUILTIN_NAMES}
    allowed["__import__"] = _restricted_import
    return allowed


def _build_frames(pd: Any, datasets: Dict[str, Any]) -> Dict[str, Any]:
    frames: Dict[str, Any] = {}
    for name, payload in datasets.items():
        if not name.isidentifier() or name.startswith("_"):
            raise ValueError(f"invalid dataset name {name!r}")
        frame = pd.DataFrame(payload["data"], columns=payload["columns"])
        for column, dtype in payload["dtypes"].items():
            if dtype in ("int64", "float64"):
                frame[column] = frame[column].astype(dtype)
        frames[name] = frame
    return frames


def _warm_up(pd: Any, np: Any) -> None:
    """Load pandas' lazily imported modules before file access is denied."""
    frame = pd.DataFrame({"k": ["a", "a", "b"], "y": [2020, 2021, 2020], "v": [1.0, 2.0, 3.0]})
    frame.groupby("k")["v"].agg(["sum", "mean", "max", "min", "count"])
    frame.groupby(["k", "y"]).size()
    frame.merge(frame, on=["k", "y"], how="left")
    frame.pivot_table(index="k", columns="y", values="v", aggfunc="sum")
    frame.pivot(index="y", columns="k", values="v")
    frame.sort_values(["k", "v"], ascending=[True, False]).groupby("k")["v"].pct_change()
    frame["v"].rolling(2).mean()
    frame["v"].rank(ascending=False)
    frame.nlargest(2, "v")
    frame.nsmallest(2, "v")
    frame.assign(z=lambda d: d["v"] * 2).melt(id_vars=["k"])
    frame.set_index(["k", "y"]).unstack()
    pd.concat([frame, frame]).drop_duplicates()
    frame["k"].str.contains("a", case=False, regex=True)
    frame["k"].str.lower().str.strip()
    frame.describe()
    frame["v"].quantile(0.5)
    frame.round(2).fillna(0).dropna()
    frame["v"].diff().shift(1).cumsum()
    frame.corr(numeric_only=True)
    frame.to_dict("records")
    frame.to_string()
    pd.to_numeric(frame["v"])
    pd.cut(frame["v"], 2)
    frame.apply(lambda row: row["v"], axis=1)
    frame.itertuples()
    np.log(frame["v"]).mean()
    np.where(frame["v"] > 1, 1, 0)


def _json_scalar(value: Any, np: Any, pd: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        value = float(value)
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value[:2000]
    if isinstance(value, tuple):
        return [_json_scalar(item, np, pd) for item in value]
    if value is pd.NaT or value is pd.NA:
        return None
    if isinstance(value, (pd.Timestamp, pd.Period)):
        return str(value)
    raise TypeError(f"unsupported value type {type(value).__name__}")


def _column_name(value: Any) -> str:
    if isinstance(value, tuple):
        return "|".join(str(item) for item in value)
    return str(value)


def _describe(value: Any, np: Any, pd: Any) -> Dict[str, Any]:
    if value is None:
        return {"kind": "none"}
    if isinstance(value, pd.DataFrame):
        head = value.head(MAX_RESULT_ROWS)
        return {
            "kind": "dataframe",
            "columns": [_column_name(column) for column in head.columns],
            "dtypes": [str(dtype) for dtype in head.dtypes],
            "index": [_json_scalar(item, np, pd) for item in head.index],
            "data": [[_json_scalar(item, np, pd) for item in row] for row in head.itertuples(index=False, name=None)],
            "row_count": int(len(value)),
            "truncated": bool(len(value) > MAX_RESULT_ROWS),
        }
    if isinstance(value, pd.Series):
        head = value.head(MAX_RESULT_ROWS)
        return {
            "kind": "series",
            "name": _json_scalar(value.name, np, pd) if not isinstance(value.name, (list, dict)) else None,
            "dtype": str(value.dtype),
            "index": [_json_scalar(item, np, pd) for item in head.index],
            "values": [_json_scalar(item, np, pd) for item in head.tolist()],
            "row_count": int(len(value)),
            "truncated": bool(len(value) > MAX_RESULT_ROWS),
        }
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_RESULT_ROWS:
            raise TypeError("list result is too long")
        return {"kind": "list", "values": [_json_scalar(item, np, pd) for item in value]}
    if isinstance(value, dict):
        if len(value) > MAX_RESULT_ROWS:
            raise TypeError("dict result is too long")
        return {"kind": "dict", "items": [[_json_scalar(key, np, pd), _json_scalar(item, np, pd)]
                                          for key, item in value.items()]}
    scalar = value
    non_finite = isinstance(scalar, (float, np.floating)) and not math.isfinite(float(scalar))
    is_missing = scalar is pd.NaT or scalar is pd.NA
    return {"kind": "scalar", "value": _json_scalar(scalar, np, pd), "dtype": type(scalar).__name__,
            "non_finite": bool(non_finite), "missing": bool(is_missing)}


def _generated_line(error: BaseException) -> Optional[int]:
    """Innermost line of the generated code, without reading any source file."""
    line = None
    traceback = error.__traceback__
    while traceback is not None:
        if traceback.tb_frame.f_code.co_filename == "<generated>":
            line = traceback.tb_lineno
        traceback = traceback.tb_next
    return line


def run_request(request: Any, start_ns: int) -> PandasExecutionResult:
    request_id = request.get("request_id", "unavailable") if isinstance(request, dict) else "unavailable"

    def elapsed() -> int:
        return max(0, (monotonic_ns() - start_ns) // 1_000_000)

    if (
        not isinstance(request, dict)
        or set(request) != {"schema_version", "request_id", "code", "datasets"}
        or request["schema_version"] != T2P_REQUEST_SCHEMA_VERSION
        or not isinstance(request["code"], str)
        or not isinstance(request["datasets"], dict)
    ):
        return failure_result(request_id, PandasFailureStage.INFRASTRUCTURE, "MALFORMED_WORKER_PROTOCOL",
                              "worker request does not match the protocol")
    project_root = Path(__file__).resolve().parents[2]
    if dict(os.environ) != expected_pandas_worker_environment(project_root):
        return failure_result(request_id, PandasFailureStage.SECURITY,
                              SecurityFailureCode.ENVIRONMENT_INHERITANCE_DENIED.value,
                              "worker environment differs from the approved minimal environment")
    apply_worker_resource_limits(T2P_PANDAS_LIMITS_V1)

    import numpy as np
    import pandas as pd

    try:
        frames = _build_frames(pd, request["datasets"])
    except (KeyError, TypeError, ValueError) as error:
        return failure_result(request_id, PandasFailureStage.INFRASTRUCTURE, "INVALID_DATASET_PAYLOAD",
                              str(error), execution_ms=elapsed())
    report = validate_pandas_code(
        request["code"], datasets={name: list(frame.columns) for name, frame in frames.items()},
        security_only=True,
    )
    if not report.ok:
        issue = report.issues[0]
        return failure_result(request_id, PandasFailureStage.VALIDATION, issue.code, issue.message,
                              execution_ms=elapsed(), line=issue.line)
    try:
        code = compile(request["code"], "<generated>", "exec", dont_inherit=True)
    except SyntaxError as error:
        return failure_result(request_id, PandasFailureStage.VALIDATION, "SYNTAX_ERROR", str(error.msg),
                              execution_ms=elapsed(), line=error.lineno)
    _warm_up(pd, np)
    # Clear the environment before the recording guard denies os.unsetenv; the
    # existing M7 guard then has nothing left to clear and adds its own hook.
    os.environ.clear()
    sys.addaudithook(_guard)
    install_worker_security_guards()

    namespace: Dict[str, Any] = {"__builtins__": _safe_builtins(), "pd": pd, "np": np, "math": math, **frames}
    try:
        exec(code, namespace)
    except WorkerSecurityViolation as error:
        return failure_result(request_id, PandasFailureStage.SECURITY, error.failure.code, error.failure.message,
                              execution_ms=elapsed(), line=_generated_line(error))
    except WorkerResourceViolation as error:
        return failure_result(request_id, PandasFailureStage.RESOURCE, error.failure.code, error.failure.message,
                              execution_ms=elapsed())
    except MemoryError:
        return failure_result(request_id, PandasFailureStage.RESOURCE, ResourceLimitFailureCode.MEMORY_LIMIT.value,
                              "worker exhausted its memory budget", execution_ms=elapsed())
    except Exception as error:  # generated-code runtime error, sanitized
        if _violations:
            return failure_result(request_id, PandasFailureStage.SECURITY, "DENIED_OPERATION_ATTEMPTED",
                                  f"generated code attempted a denied operation: {_violations[0]}",
                                  execution_ms=elapsed())
        message = f"{type(error).__name__}: {error}"[:MAX_ERROR_MESSAGE_CHARS]
        return failure_result(request_id, PandasFailureStage.RUNTIME, type(error).__name__, message,
                              execution_ms=elapsed(), line=_generated_line(error))
    if _violations:
        return failure_result(request_id, PandasFailureStage.SECURITY, "DENIED_OPERATION_ATTEMPTED",
                              f"generated code attempted a denied operation: {_violations[0]}",
                              execution_ms=elapsed())
    if "result" not in namespace:
        return failure_result(request_id, PandasFailureStage.RUNTIME, "MISSING_RESULT",
                              "the code did not assign `result`", execution_ms=elapsed())
    try:
        result = _describe(namespace["result"], np, pd)
        evidence = None if namespace.get("evidence") is None else _describe(namespace["evidence"], np, pd)
    except (TypeError, ValueError) as error:
        return failure_result(request_id, PandasFailureStage.SERIALIZATION, "UNSUPPORTED_RESULT_TYPE",
                              str(error), execution_ms=elapsed())
    return PandasExecutionResult(T2P_RESULT_SCHEMA_VERSION, request_id, True, result, evidence, None, elapsed())


def main() -> int:
    start_ns = monotonic_ns()
    payload = sys.stdin.buffer.read(T2P_PANDAS_LIMITS_V1.max_request_bytes + 1)
    if len(payload) > T2P_PANDAS_LIMITS_V1.max_request_bytes:
        result = failure_result("unavailable", PandasFailureStage.RESOURCE,
                                ResourceLimitFailureCode.REQUEST_SIZE_LIMIT.value, "request exceeds the size limit")
    else:
        try:
            request = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            request = None
        try:
            result = run_request(request, start_ns)
        except WorkerResourceViolation as error:
            result = failure_result("unavailable" if not isinstance(request, dict) else request.get("request_id", ""),
                                    PandasFailureStage.RESOURCE, error.failure.code, error.failure.message)
        except MemoryError:
            result = failure_result("unavailable", PandasFailureStage.RESOURCE,
                                    ResourceLimitFailureCode.MEMORY_LIMIT.value, "worker exhausted its memory budget")
    response = encode(result.to_dict())
    if len(response) > T2P_PANDAS_LIMITS_V1.max_output_bytes:
        response = encode(failure_result(result.request_id, PandasFailureStage.RESOURCE,
                                         ResourceLimitFailureCode.OUTPUT_SIZE_LIMIT.value,
                                         "result exceeds the output size limit").to_dict())
    sys.stdout.buffer.write(response)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
