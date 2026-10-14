"""Concurrent-load calibration of the isolated sandbox workers (ADR-055 rollout gate).

Starts fresh one-shot workers through the production executors
(``execute_sandboxed`` for the M7 DSL, ``execute_pandas_sandboxed`` for
Text-to-Pandas) at increasing concurrency. Every benign request carries
distinct values and is accepted only when the worker returns that request's own
expected result, so cross-talk or a partial result counts as wrong. Abuse
requests (CPU loop, memory pressure, file/network/process/introspection
attempts) are interleaved with the Text-to-Pandas load and must fail closed
with a typed failure while benign requests keep passing. After each level the
parent checks for leaked child processes, descriptors, threads and worker temp
directories.

The report records the isolation mode in effect; on a host where
``linux-nsjail-v1`` cannot be set up it measures the local process boundary
only. Exits non-zero on any wrong, escaped or leaked result, or on a benign
failure at or below ``--target-concurrency``.
"""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import glob
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
import threading
from time import monotonic_ns
from typing import Any, Callable, Dict, List, Optional, Sequence

import pandas as pd
import psutil

from src.evaluation.e2e_fixtures import _oracle_artifacts, fixture_pairs, fixture_understanding
from src.evaluation.oracle_reasoning import outputs_equal
from src.evaluation.wide_eval import percentile
from src.sandbox.executor import execute_sandboxed
from src.sandbox.pandas_executor import execute_pandas_sandboxed
from src.sandbox.production import resolve_isolation_mode
from src.text_to_pandas.catalog import frame_payload
from src.text_to_pandas.catalog_builder import FACT_COLUMNS

SCHEMA_VERSION = "sandbox-load-eval-v1"
WORKER_TEMP_PREFIXES = ("m7-worker-", "t2p-worker-")
# Executors join their RSS-monitor thread for at most 0.2 s; under heavy load one
# psutil /proc scan can outlast that, and the daemon thread exits moments later.
# Resources must return to baseline within this window to count as not leaked.
LEAK_SETTLE_SECONDS = 5.0
BENIGN_CODE = (
    "evidence = income_statement[(income_statement['ticker'] == 'AAA') "
    "& (income_statement['fiscal_year'] == 2010)]\n"
    "result = evidence['value'].sum()\n"
)
# Each abuse program must end in one of the listed failure stages, never success.
ABUSE_PROGRAMS: Dict[str, tuple] = {
    "cpu_loop": ("while True:\n    pass\nresult = 1\n", {"RESOURCE"}),
    "memory": ("big = np.ones(400_000_000)\nresult = float(big.sum())\n", {"RESOURCE"}),
    "file_read": ("result = open('/etc/passwd').read()\n", {"VALIDATION", "SECURITY"}),
    "network": ("result = pd.read_csv('http://127.0.0.1:9/x.csv')\n", {"VALIDATION", "SECURITY"}),
    "process": ("result = pd.io.common.os.system('id')\n", {"VALIDATION", "SECURITY"}),
    "introspection": ("result = income_statement.__class__.__init__.__globals__\n", {"VALIDATION", "SECURITY"}),
}


@dataclass(frozen=True)
class LoadCase:
    worker: str
    kind: str  # benign | abuse name
    run: Callable[[], Any]
    expected: Any = None
    allowed_stages: frozenset = frozenset()


def m7_cases(count: int, offset: int) -> List[LoadCase]:
    cases = []
    for index in range(offset, offset + count):
        value = f"{1000 + index}.{index % 97:02d}"  # distinct per request
        oracle = _oracle_artifacts(fixture_understanding(f"load-{index}"), fixture_pairs(("2015",), (value,)))
        request = oracle.request
        cases.append(LoadCase("m7", "benign", lambda request=request: execute_sandboxed(request),
                              oracle.execution.output))
    return cases


def _t2p_payload(index: int, rows: int) -> tuple:
    data = [[f"f{index}-{row}", "AAA" if row % 2 == 0 else "BBB", "Load Co", 2000 + row % 25, "HOP_NHAT",
             "60", "LNST", "LNST", (index + 1) * 1_000_003 + row, "VND", "RAW"] for row in range(rows)]
    expected = sum(row[8] for row in data if row[1] == "AAA" and row[3] == 2010)
    return {"income_statement": frame_payload(pd.DataFrame(data, columns=FACT_COLUMNS))}, expected


def t2p_cases(count: int, offset: int, rows: int, abuse_every: int) -> List[LoadCase]:
    cases = []
    abuse_names = sorted(ABUSE_PROGRAMS)
    for index in range(offset, offset + count):
        datasets, expected = _t2p_payload(index, rows)
        if abuse_every and (index - offset) % abuse_every == abuse_every - 1:
            name = abuse_names[((index - offset) // abuse_every) % len(abuse_names)]
            code, stages = ABUSE_PROGRAMS[name]
            cases.append(LoadCase("t2p", name, lambda code=code, datasets=datasets: execute_pandas_sandboxed(
                code, datasets), allowed_stages=frozenset(stages)))
        else:
            cases.append(LoadCase("t2p", "benign", lambda datasets=datasets: execute_pandas_sandboxed(
                BENIGN_CODE, datasets), expected))
    return cases


def _judge(case: LoadCase, result: Any) -> Dict[str, Any]:
    failure = None if result.failure is None else {"stage": result.failure.stage.value, "code": result.failure.code}
    if case.kind != "benign":
        escaped = bool(result.success)
        contained = not escaped and failure is not None and failure["stage"] in case.allowed_stages
        return {"outcome": "ABUSE_CONTAINED" if contained else "ABUSE_ESCAPED" if escaped else "ABUSE_MISCLASSIFIED",
                "failure": failure}
    if not result.success:
        return {"outcome": "BENIGN_FAILED", "failure": failure}
    if case.worker == "m7":
        correct = outputs_equal(case.expected, result.output)
    else:
        correct = result.result.get("kind") == "scalar" and result.result.get("value") == case.expected
    return {"outcome": "BENIGN_CORRECT" if correct else "BENIGN_WRONG", "failure": None}


def _worker_temp_dirs() -> set:
    root = tempfile.gettempdir()
    return {path for prefix in WORKER_TEMP_PREFIXES for path in glob.glob(os.path.join(root, prefix + "*"))}


def _snapshot() -> Dict[str, Any]:
    me = psutil.Process()
    # Windows has no descriptor count; its handle count plays the same role.
    descriptors = me.num_fds() if hasattr(me, "num_fds") else me.num_handles()
    return {"fds": descriptors, "threads": threading.active_count(), "children": len(me.children(recursive=True)),
            "temp_dirs": _worker_temp_dirs(), "rss_mb": me.memory_info().rss / 2**20}


def run_level(cases: Sequence[LoadCase], concurrency: int) -> Dict[str, Any]:
    before = _snapshot()
    peak = {"children": 0}
    stop = threading.Event()

    def sample() -> None:
        me = psutil.Process()
        while not stop.wait(0.05):
            try:
                peak["children"] = max(peak["children"], len(me.children(recursive=True)))
            except psutil.Error:
                continue

    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()

    def one(case: LoadCase) -> Dict[str, Any]:
        start = monotonic_ns()
        try:
            verdict = _judge(case, case.run())
        except Exception as error:  # an executor must return a typed result, never raise
            verdict = {"outcome": "HARNESS_EXCEPTION", "failure": {"stage": "EXCEPTION", "code": type(error).__name__}}
        return {"worker": case.worker, "kind": case.kind, "latency_ms": (monotonic_ns() - start) / 1e6, **verdict}

    start = monotonic_ns()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        rows = list(pool.map(one, cases))
    wall_s = (monotonic_ns() - start) / 1e9
    stop.set()
    sampler.join()

    def leaks_of(after: Dict[str, Any]) -> Dict[str, int]:
        return {
            "children_alive": after["children"],
            "fd_delta": after["fds"] - before["fds"],
            "thread_delta": after["threads"] - before["threads"],
            "temp_dirs_left": len(after["temp_dirs"] - before["temp_dirs"]),
        }

    settle_start = monotonic_ns()
    after = _snapshot()
    immediate = leaks_of(after)
    leaks = immediate
    while any(value > 0 for value in leaks.values()) and \
            monotonic_ns() - settle_start < LEAK_SETTLE_SECONDS * 1e9:
        stop.wait(0.05)
        after = _snapshot()
        leaks = leaks_of(after)
    settle_ms = round((monotonic_ns() - settle_start) / 1e6, 1)
    benign = [row["latency_ms"] for row in rows if row["kind"] == "benign"]
    outcomes = Counter(row["outcome"] for row in rows)
    return {
        "concurrency": concurrency, "requests": len(rows), "wall_s": round(wall_s, 3),
        "throughput_rps": round(len(rows) / wall_s, 2) if wall_s else None,
        "benign_latency_ms": {"p50": percentile(benign, 0.5), "p95": percentile(benign, 0.95),
                              "p99": percentile(benign, 0.99), "max": max(benign, default=None)},
        "outcomes": dict(outcomes),
        "failure_codes": dict(Counter(f"{row['kind']}:{row['failure']['stage']}/{row['failure']['code']}"
                                      for row in rows if row["failure"])),
        "peak_concurrent_worker_processes": peak["children"],
        "parent_rss_mb": round(after["rss_mb"], 1),
        "leaks": leaks,
        "leaks_at_level_end": immediate,
        "leak_settle_ms": settle_ms,
        "leak_free": all(value <= 0 for value in leaks.values()),
    }


def evaluate(workers: Sequence[str], levels: Sequence[int], *, requests: int, rows: int, abuse_every: int,
             target: int) -> Dict[str, Any]:
    results = []
    offset = 0
    for worker in workers:
        for level in levels:
            count = max(requests, 2 * level)
            cases = m7_cases(count, offset) if worker == "m7" else t2p_cases(count, offset, rows, abuse_every)
            offset += count
            report = run_level(cases, level)
            outcomes = report["outcomes"]
            report["worker"] = worker
            report["safe"] = (not outcomes.get("BENIGN_WRONG") and not outcomes.get("ABUSE_ESCAPED")
                              and not outcomes.get("ABUSE_MISCLASSIFIED") and not outcomes.get("HARNESS_EXCEPTION")
                              and report["leak_free"])
            report["benign_all_passed"] = not outcomes.get("BENIGN_FAILED")
            report["passed"] = report["safe"] and (report["benign_all_passed"] or level > target)
            results.append(report)
            print(json.dumps({key: report[key] for key in ("worker", "concurrency", "outcomes", "benign_latency_ms",
                                                            "throughput_rps", "leaks", "passed")}),
                  file=sys.stderr, flush=True)
    import numpy

    return {
        "schema_version": SCHEMA_VERSION,
        "isolation_mode": resolve_isolation_mode().value,
        "host": {"system": platform.system(), "release": platform.release(), "cpu_count": os.cpu_count(),
                 "uid": os.getuid() if hasattr(os, "getuid") else None, "python": platform.python_version(), "executable": sys.executable,
                 "pandas": pd.__version__, "numpy": numpy.__version__, "psutil": psutil.__version__},
        "target_concurrency": target, "rows_per_t2p_request": rows, "abuse_every": abuse_every,
        "abuse_programs": sorted(ABUSE_PROGRAMS),
        "levels": results,
        "passed": all(report["passed"] for report in results),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", default="m7,t2p")
    parser.add_argument("--concurrency", default="1,4,16,32")
    parser.add_argument("--requests", type=int, default=64, help="minimum requests per level (at least 2x level)")
    parser.add_argument("--rows", type=int, default=400, help="rows in each Text-to-Pandas data slice")
    parser.add_argument("--abuse-every", type=int, default=4, help="every Nth Text-to-Pandas request is abuse; 0 = none")
    parser.add_argument("--target-concurrency", type=int, default=16)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    report = evaluate([item for item in args.workers.split(",") if item],
                      [int(item) for item in args.concurrency.split(",")], requests=args.requests, rows=args.rows,
                      abuse_every=args.abuse_every, target=args.target_concurrency)
    text = json.dumps(report, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "isolation_mode": report["isolation_mode"],
                      "levels": [{key: level[key] for key in ("worker", "concurrency", "passed")}
                                 for level in report["levels"]]}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
