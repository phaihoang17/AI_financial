"""Soak test of the sandbox workers with resource-trend analysis (ADR-072 gate).

The stress matrix (``run_sandbox_load_eval``) proves each concurrency level
returns to baseline. This soak keeps a mixed workload running for a fixed
duration — M7 DSL requests, Text-to-Pandas requests with distinct expected
results, and interleaved abuse programs — and samples the parent process at a
fixed interval: RSS, descriptors (handles on Windows), threads, live worker
processes and leftover worker temp directories, plus per-window throughput and
p95 latency.

Stability is judged by trend, not by one reading. Under load, descriptor,
thread and worker counts move with the requests in flight, so every
``checkpoint_s`` the soak stops submitting, lets in-flight requests drain and
the process settle, and takes a quiescent snapshot; those exact counts must not
grow from checkpoint to checkpoint (descriptors, threads, temp dirs, live
workers). RSS, which does not depend on in-flight work, gets an ordinary
least-squares slope over every sample after a warm-up, and its fitted growth
must stay within a small budget. Latency drift is judged
per worker kind on pooled latencies: the p95 of the last 20% of measured windows
must stay within 1.5x of the first 20%'s. Any wrong benign
result, escaped abuse, benign failure or harness exception fails the soak.
"""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import json
import os
from pathlib import Path
import platform
import sys
import threading
from time import monotonic, monotonic_ns
from typing import Any, Dict, List, Optional, Sequence

import psutil

from src.evaluation.run_sandbox_load_eval import _judge, _snapshot, m7_cases, t2p_cases
from src.evaluation.wide_eval import percentile
from src.sandbox.production import resolve_isolation_mode

SCHEMA_VERSION = "sandbox-soak-eval-v1"
RSS_GROWTH_BUDGET_MB = 32.0
RSS_GROWTH_BUDGET_FRACTION = 0.05
LATENCY_DRIFT_MAX = 1.5
SETTLE_SECONDS = 5.0


def slope(points: Sequence[tuple]) -> Optional[float]:
    """Least-squares slope of (x, y) points; None for fewer than three points."""
    if len(points) < 3:
        return None
    n = len(points)
    mean_x = sum(x for x, _ in points) / n
    mean_y = sum(y for _, y in points) / n
    variance = sum((x - mean_x) ** 2 for x, _ in points)
    if variance == 0:
        return 0.0
    return sum((x - mean_x) * (y - mean_y) for x, y in points) / variance


def _case_stream(rows: int, abuse_every: int):
    offset = 0
    while True:
        batch = m7_cases(4, offset) + t2p_cases(4, offset, rows, abuse_every)
        offset += 4
        for case in batch:
            yield case


def trend_analysis(samples: List[Dict[str, Any]], warmup_fraction: float) -> Dict[str, Any]:
    start = samples[0]["t_s"]
    measured = [sample for sample in samples if sample["t_s"] - start >= warmup_fraction * (samples[-1]["t_s"] - start)]
    span_h = (measured[-1]["t_s"] - measured[0]["t_s"]) / 3600 if len(measured) > 1 else 0.0
    trends: Dict[str, Any] = {}
    for key in ("rss_mb", "fds", "threads", "temp_dirs"):
        fitted = slope([(sample["t_s"] / 3600, sample[key]) for sample in measured])
        trends[key] = {"slope_per_hour": fitted, "fitted_growth": None if fitted is None else fitted * span_h,
                       "first": measured[0][key], "last": measured[-1][key], "max": max(s[key] for s in measured)}
    rss = trends["rss_mb"]
    budget = max(RSS_GROWTH_BUDGET_MB, RSS_GROWTH_BUDGET_FRACTION * rss["first"])
    checks = {
        "rss_growth_within_budget": rss["fitted_growth"] is not None and rss["fitted_growth"] <= budget,
        "descriptors_flat": trends["fds"]["fitted_growth"] is not None and trends["fds"]["fitted_growth"] < 2,
        "threads_flat": trends["threads"]["fitted_growth"] is not None and trends["threads"]["fitted_growth"] < 2,
        "temp_dirs_flat": trends["temp_dirs"]["fitted_growth"] is not None and trends["temp_dirs"]["fitted_growth"] < 2,
    }
    return {"warmup_fraction": warmup_fraction, "measured_samples": len(measured), "span_hours": round(span_h, 4),
            "rss_budget_mb": round(budget, 1), "series": trends, "checks": checks}


def latency_drift(windows: List[Dict[str, Any]], warmup_fraction: float, share: float = 0.2) -> Dict[str, Any]:
    """Per worker kind: pooled p95 of the last ``share`` of measured windows over the first."""
    measured = windows[int(len(windows) * warmup_fraction):]
    size = max(1, int(len(measured) * share))
    result = {}
    for kind in ("m7", "t2p"):
        first = [value for w in measured[:size] for value in w["latencies"][kind]]
        last = [value for w in measured[-size:] for value in w["latencies"][kind]]
        first_p95, last_p95 = percentile(first, 0.95), percentile(last, 0.95)
        p95_series = [(w["t_s"] / 3600, w[f"{kind}_p95_ms"]) for w in measured if w[f"{kind}_p95_ms"] is not None]
        result[kind] = {"first_p95_ms": first_p95, "last_p95_ms": last_p95,
                        "ratio": None if not first_p95 or last_p95 is None else last_p95 / first_p95,
                        "p95_slope_ms_per_hour": slope(p95_series), "windows_pooled": size}
    return result


def quiescent_trend(checkpoints: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Exact counts at drained checkpoints: none may rise above the first checkpoint's."""
    series = {}
    for key in ("fds", "threads", "temp_dirs", "workers"):
        values = [point[key] for point in checkpoints]
        fitted = slope([(point["t_s"] / 3600, point[key]) for point in checkpoints])
        series[key] = {"values": values, "slope_per_hour": fitted,
                       "growth": None if not values else max(values) - values[0]}
    checks = {f"quiescent_{key}_flat": bool(item["values"]) and len(item["values"]) >= 3 and item["growth"] <= 0
              for key, item in series.items()}
    return {"checkpoints": len(checkpoints), "series": series, "checks": checks}


def soak(duration_s: float, concurrency: int, *, sample_s: float, rows: int, abuse_every: int,
         warmup_fraction: float = 0.1, checkpoint_s: float = 300.0) -> Dict[str, Any]:
    baseline = _snapshot()
    me = psutil.Process()
    samples: List[Dict[str, Any]] = []
    window: List[Dict[str, Any]] = []
    windows: List[Dict[str, Any]] = []
    lock = threading.Lock()
    outcomes: Counter = Counter()
    started = monotonic()

    def one(case) -> None:
        begin = monotonic_ns()
        try:
            verdict = _judge(case, case.run())
        except Exception as error:  # executors must return typed results
            verdict = {"outcome": "HARNESS_EXCEPTION", "failure": {"stage": "EXCEPTION", "code": type(error).__name__}}
        with lock:
            outcomes[verdict["outcome"]] += 1
            window.append({"kind": case.kind, "worker": case.worker, "latency_ms": (monotonic_ns() - begin) / 1e6})

    def take_sample() -> None:
        snapshot = _snapshot()
        try:
            workers = len(me.children(recursive=True))
        except psutil.Error:
            workers = -1
        with lock:
            done = list(window)
            window.clear()
        latencies = {kind: [row["latency_ms"] for row in done if row["kind"] == "benign" and row["worker"] == kind]
                     for kind in ("m7", "t2p")}
        windows.append({"t_s": round(monotonic() - started, 3), "requests": len(done), "latencies": latencies,
                        **{f"{kind}_p95_ms": percentile(values, 0.95) for kind, values in latencies.items()}})
        samples.append({"t_s": round(monotonic() - started, 3), "rss_mb": round(snapshot["rss_mb"], 2),
                        "fds": snapshot["fds"], "threads": snapshot["threads"], "workers": workers,
                        "temp_dirs": len(snapshot["temp_dirs"] - baseline["temp_dirs"]), "requests": len(done)})

    checkpoints: List[Dict[str, Any]] = []

    def settle() -> Dict[str, Any]:
        # Drained: no worker alive, no worker temp dir left, then one exact reading.
        settle_start = monotonic()
        after = _snapshot()
        while (len(after["temp_dirs"] - baseline["temp_dirs"]) or me.children(recursive=True)) \
                and monotonic() - settle_start < SETTLE_SECONDS:
            threading.Event().wait(0.1)
            after = _snapshot()
        threading.Event().wait(0.5)  # RSS-monitor threads end within 0.2 s of their worker
        after = _snapshot()
        point = {"t_s": round(monotonic() - started, 3), "rss_mb": round(after["rss_mb"], 2), "fds": after["fds"],
                 "threads": after["threads"], "temp_dirs": len(after["temp_dirs"] - baseline["temp_dirs"]),
                 "workers": len(me.children(recursive=True))}
        checkpoints.append(point)
        return point

    stream = _case_stream(rows, abuse_every)
    next_sample = monotonic()
    next_checkpoint = monotonic() + checkpoint_s
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        running = set()
        while monotonic() - started < duration_s:
            while len(running) < concurrency:
                running.add(pool.submit(one, next(stream)))
            done, running = wait(running, timeout=0.05, return_when=FIRST_COMPLETED)
            if monotonic() >= next_sample:
                take_sample()
                next_sample += sample_s
            if monotonic() >= next_checkpoint:
                wait(running)
                running = set()
                settle()
                next_checkpoint = monotonic() + checkpoint_s
        wait(running)
    after = settle()
    take_sample()
    trend = trend_analysis(samples[:-1] if len(samples) > 3 else samples, warmup_fraction)
    trend["checks"] = {"rss_growth_within_budget": trend["checks"]["rss_growth_within_budget"]}
    quiescent = quiescent_trend(checkpoints)
    drift = latency_drift(windows[:-1], warmup_fraction)
    end_state = {"workers_alive": after["workers"], "temp_dirs_left": after["temp_dirs"],
                 "thread_delta": after["threads"] - baseline["threads"], "fd_delta": after["fds"] - baseline["fds"]}
    safety = {key: outcomes.get(key, 0) for key in ("BENIGN_WRONG", "BENIGN_FAILED", "ABUSE_ESCAPED",
                                                    "ABUSE_MISCLASSIFIED", "HARNESS_EXCEPTION")}
    checks = {**trend["checks"], **quiescent["checks"],
              "latency_drift_ok": all(item["ratio"] is not None and item["ratio"] <= LATENCY_DRIFT_MAX
                                      for item in drift.values()),
              "drained_to_baseline": all(value <= 0 for value in end_state.values()),
              "no_wrong_failed_or_escaped": not any(safety.values())}
    return {"duration_s": duration_s, "concurrency": concurrency, "sample_s": sample_s,
            "requests": sum(outcomes.values()), "outcomes": dict(outcomes),
            "throughput_rps": round(sum(outcomes.values()) / (monotonic() - started), 2),
            "latency_drift": drift, "end_state": end_state, "trend": trend, "quiescent": quiescent,
            "checks": checks,
            "passed": all(checks.values()), "samples": samples,
            "windows": [{key: value for key, value in w.items() if key != "latencies"} for w in windows]}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--duration-s", type=float, default=1800)
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--sample-s", type=float, default=15)
    parser.add_argument("--checkpoint-s", type=float, default=300, help="drained checkpoint interval")
    parser.add_argument("--rows", type=int, default=400)
    parser.add_argument("--abuse-every", type=int, default=4)
    parser.add_argument("--stress", help="run_sandbox_load_eval report to fold into the stability verdict")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    result = soak(args.duration_s, args.concurrency, sample_s=args.sample_s, rows=args.rows,
                  abuse_every=args.abuse_every, checkpoint_s=args.checkpoint_s)
    stress = json.loads(Path(args.stress).read_text()) if args.stress else None
    report = {
        "schema_version": SCHEMA_VERSION, "isolation_mode": resolve_isolation_mode().value,
        "host": {"system": platform.system(), "release": platform.release(), "cpu_count": os.cpu_count(),
                 "python": platform.python_version()},
        "soak": result,
        "stress": None if stress is None else {
            "levels_passed": [(level["worker"], level["concurrency"], level["passed"]) for level in stress["levels"]],
            "passed": stress["passed"]},
        "trend": {"rss": result["trend"], "quiescent": result["quiescent"]}, "checks": result["checks"],
        "passed": result["passed"] and (stress is None or stress["passed"]),
    }
    report["summary"] = {"soak_passed": result["passed"], "stress_passed": None if stress is None else stress["passed"],
                         "requests": result["requests"], "duration_s": args.duration_s}
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "checks": report["checks"], "outcomes": result["outcomes"]}),
          file=sys.stderr)
    return 0 if report["passed"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
