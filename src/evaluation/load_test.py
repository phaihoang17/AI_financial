"""TASK-111 bounded concurrency / throughput load-test harness.

Runs a supplied per-request task ``total_requests`` times at a bounded
``concurrency`` and reports throughput, latency distribution, and error/timeout
rate. On CPU it exercises the deterministic pipeline with real concurrency and
real wall-clock (``CPU_FIXTURE``); accuracy/timeout degradation at production
concurrency with live models is ``GPU_PRODUCTION_VALIDATION_PENDING`` and is not
fabricated here.
"""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Callable, List, Optional

from src.evaluation.telemetry import Distribution, MeasurementSource, Stopwatch
from src.understanding.schemas import SchemaValidationError


LOAD_TEST_SCHEMA_VERSION = "m10-load-test-v1"
MAX_CONCURRENCY = 64
MAX_TOTAL_REQUESTS = 100_000


@dataclass(frozen=True)
class LoadTestResult:
    measurement_source: MeasurementSource
    total_requests: int
    concurrency: int
    succeeded: int
    failed: int
    timeouts: int
    wall_clock_ms: float
    throughput_rps: float
    latency: Optional[Distribution]

    def to_dict(self) -> dict:
        return {
            "schema_version": LOAD_TEST_SCHEMA_VERSION,
            "measurement_source": self.measurement_source.value,
            "production_load_status": "LIVE_PENDING",
            "production_load_blocker": "GPU_PRODUCTION_VALIDATION_PENDING",
            "total_requests": self.total_requests,
            "concurrency": self.concurrency,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "timeouts": self.timeouts,
            "error_rate": (self.failed / self.total_requests) if self.total_requests else 0.0,
            "wall_clock_ms": self.wall_clock_ms,
            "throughput_rps": self.throughput_rps,
            "latency": None if self.latency is None else self.latency.to_dict(),
        }


def run_load_test(
    task: Callable[[int], object],
    *,
    total_requests: int,
    concurrency: int,
    wait_timeout_s: Optional[float] = None,
    source: MeasurementSource = MeasurementSource.CPU_FIXTURE,
) -> LoadTestResult:
    """Execute ``task(i)`` for i in range(total_requests) at bounded concurrency."""
    if isinstance(total_requests, bool) or not isinstance(total_requests, int) or not (
        1 <= total_requests <= MAX_TOTAL_REQUESTS
    ):
        raise SchemaValidationError(f"total_requests must be in [1, {MAX_TOTAL_REQUESTS}]")
    if isinstance(concurrency, bool) or not isinstance(concurrency, int) or concurrency < 1:
        raise SchemaValidationError("concurrency must be a positive integer")
    if concurrency > MAX_CONCURRENCY:
        raise SchemaValidationError(f"concurrency must not exceed {MAX_CONCURRENCY}")
    if not callable(task):
        raise SchemaValidationError("task must be callable")
    if wait_timeout_s is not None and (
        isinstance(wait_timeout_s, bool)
        or not isinstance(wait_timeout_s, (int, float))
        or wait_timeout_s <= 0
    ):
        raise SchemaValidationError("wait_timeout_s must be positive or None")
    workers = min(concurrency, total_requests)

    latencies: List[float] = []
    succeeded = 0
    failed = 0
    timeouts = 0

    def _one(index: int) -> float:
        watch = Stopwatch()
        task(index)
        return watch.elapsed_ms()

    total = Stopwatch()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_one, index) for index in range(total_requests)}
        pending = set(futures)
        while pending:
            done, pending = wait(pending, timeout=wait_timeout_s, return_when=FIRST_COMPLETED)
            if not done:
                # Timed out waiting for any pending future to complete.
                for future in pending:
                    future.cancel()
                timeouts += len(pending)
                failed += len(pending)
                pending = set()
                break
            for future in done:
                try:
                    latencies.append(future.result())
                    succeeded += 1
                except Exception:
                    failed += 1
    wall_ms = total.elapsed_ms()
    throughput = (succeeded / (wall_ms / 1000.0)) if wall_ms > 0 else 0.0
    return LoadTestResult(
        measurement_source=source,
        total_requests=total_requests,
        concurrency=concurrency,
        succeeded=succeeded,
        failed=failed,
        timeouts=timeouts,
        wall_clock_ms=wall_ms,
        throughput_rps=throughput,
        latency=Distribution.from_samples(latencies) if latencies else None,
    )
