"""Thread-safe service metrics, Prometheus exposition and SLO accounting (ADR-072).

Only the standard library is used. Counters and histograms are cumulative,
like Prometheus client metrics, so the alert rules in ``deploy/service/alerts.yml``
read them with ``rate()``/``increase()``; ``src/service/alerting.py`` evaluates
the same rules over sampled snapshots so they are tested without Prometheus.

A request is a *bad* event for availability when the service could not give a
typed answer: HTTP 5xx (including 503 overload) or a ``FAILED`` response whose
reason is an infrastructure, transport or sandbox-resource failure. Typed
refusals (``NO_DATA``, ``UNSUPPORTED``, ``NEEDS_CLARIFICATION``) are good
events: refusing correctly is the service working.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import threading
import time
import tomllib
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

DEFAULT_SLO_FILE = Path(__file__).resolve().parents[2] / "deploy" / "service" / "slo.toml"
LATENCY_BUCKETS_S = (0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 15.0, 30.0, 60.0, 120.0)
# Reasons that are the service's fault, not the question's.
INFRASTRUCTURE_REASONS = frozenset({
    "SERVING_TRANSPORT_FAILED", "SERVING_PROTOCOL_INVALID", "SERVING_AUTH_INVALID", "MISSING_CONFIG",
    "SERVING_REFUSED", "WORKER_START_FAILED", "WORKER_CRASHED", "INVALID_WORKER_RESULT",
    "MALFORMED_WORKER_PROTOCOL", "CAPACITY_EXCEEDED", "ISOLATION_SETUP_FAILED", "MEMORY_MONITOR_UNAVAILABLE",
    "LIMIT_SETUP_FAILED", "INTERNAL_ERROR", "OVERLOADED", "TIMEOUT",
})
# Generated code tried a denied capability: contained, but always investigated.
SECURITY_REASONS = frozenset({"FILESYSTEM_READ_DENIED", "FILESYSTEM_WRITE_DENIED", "NETWORK_ACCESS_DENIED",
                              "PROCESS_SPAWN_DENIED", "ENVIRONMENT_INHERITANCE_DENIED"})


def is_bad_event(http_status: int, status: Optional[str], reason: Optional[str]) -> bool:
    if http_status >= 500:
        return True
    return status == "FAILED" and reason in INFRASTRUCTURE_REASONS


@dataclass(frozen=True)
class SLO:
    availability_target: float
    latency_threshold_s: float
    latency_target: float
    window_days: int
    page_burn_rate: float
    ticket_burn_rate: float

    @property
    def error_budget(self) -> float:
        return 1 - self.availability_target


def load_slo(path: str | Path = DEFAULT_SLO_FILE) -> SLO:
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    availability, latency, burn = data["availability"], data["latency"], data["burn_rate"]
    slo = SLO(float(availability["target"]), float(latency["threshold_seconds"]), float(latency["target"]),
              int(data["window_days"]), float(burn["page"]), float(burn["ticket"]))
    if not (0.9 <= slo.availability_target < 1 and 0.5 <= slo.latency_target < 1 and slo.latency_threshold_s > 0):
        raise ValueError("SLO targets are out of range")
    return slo


class ServiceMetrics:
    """Cumulative counters and a latency histogram for the ask endpoint."""

    def __init__(self, clock=time.monotonic) -> None:
        self._lock = threading.Lock()
        self._clock = clock
        self.requests: Dict[Tuple[int, str, str], int] = defaultdict(int)  # (http, status, reason)
        self.bad_events = 0
        self.total_events = 0
        self.latency_buckets = [0] * (len(LATENCY_BUCKETS_S) + 1)
        self.latency_sum = 0.0
        self.latency_count = 0
        self.slow_answers = 0  # answered requests slower than the latency SLO threshold
        self.answered = 0
        self.in_flight = 0
        self.rejected_overload = 0
        self.security_events = 0
        self.release = "unknown"
        self.ready = False
        self.reloads = 0

    def started(self) -> None:
        with self._lock:
            self.in_flight += 1

    def finished(self, http_status: int, status: Optional[str], reason: Optional[str], seconds: float,
                 *, latency_threshold_s: float) -> None:
        with self._lock:
            self.in_flight -= 1
            self.requests[(http_status, status or "-", reason or "-")] += 1
            self.total_events += 1
            self.bad_events += is_bad_event(http_status, status, reason)
            if http_status == 503 and reason == "OVERLOADED":
                self.rejected_overload += 1
            self.security_events += reason in SECURITY_REASONS
            index = next((i for i, bound in enumerate(LATENCY_BUCKETS_S) if seconds <= bound), len(LATENCY_BUCKETS_S))
            self.latency_buckets[index] += 1
            self.latency_sum += seconds
            self.latency_count += 1
            if status == "ANSWERED":
                self.answered += 1
                self.slow_answers += seconds > latency_threshold_s

    def snapshot(self) -> Dict[str, float]:
        with self._lock:
            return {"t": self._clock(), "total": self.total_events, "bad": self.bad_events,
                    "answered": self.answered, "slow_answers": self.slow_answers, "in_flight": self.in_flight,
                    "rejected_overload": self.rejected_overload, "security_events": self.security_events,
                    "ready": float(self.ready)}

    def render(self) -> str:
        """Prometheus text exposition format 0.0.4."""
        with self._lock:
            lines: List[str] = [
                "# HELP aifin_requests_total Ask requests by HTTP status, answer status and reason.",
                "# TYPE aifin_requests_total counter",
            ]
            for (http_status, status, reason), count in sorted(self.requests.items()):
                lines.append(f'aifin_requests_total{{code="{http_status}",status="{status}",reason="{reason}"}} {count}')
            lines += ["# HELP aifin_slo_events_total Availability SLO events.", "# TYPE aifin_slo_events_total counter",
                      f'aifin_slo_events_total{{kind="total"}} {self.total_events}',
                      f'aifin_slo_events_total{{kind="bad"}} {self.bad_events}',
                      "# HELP aifin_answer_slow_total Answered requests slower than the latency SLO threshold.",
                      "# TYPE aifin_answer_slow_total counter",
                      f"aifin_answer_slow_total {self.slow_answers}",
                      "# HELP aifin_answered_total Answered requests.", "# TYPE aifin_answered_total counter",
                      f"aifin_answered_total {self.answered}",
                      "# HELP aifin_request_duration_seconds Ask latency.",
                      "# TYPE aifin_request_duration_seconds histogram"]
            cumulative = 0
            for bound, count in zip(LATENCY_BUCKETS_S, self.latency_buckets):
                cumulative += count
                lines.append(f'aifin_request_duration_seconds_bucket{{le="{bound}"}} {cumulative}')
            lines += [f'aifin_request_duration_seconds_bucket{{le="+Inf"}} {self.latency_count}',
                      f"aifin_request_duration_seconds_sum {self.latency_sum:.6f}",
                      f"aifin_request_duration_seconds_count {self.latency_count}",
                      "# HELP aifin_in_flight Ask requests in progress.", "# TYPE aifin_in_flight gauge",
                      f"aifin_in_flight {self.in_flight}",
                      "# HELP aifin_overload_rejections_total Requests refused by admission control.",
                      "# TYPE aifin_overload_rejections_total counter",
                      f"aifin_overload_rejections_total {self.rejected_overload}",
                      "# HELP aifin_sandbox_security_events_total Generated code that attempted a denied capability.",
                      "# TYPE aifin_sandbox_security_events_total counter",
                      f"aifin_sandbox_security_events_total {self.security_events}",
                      "# HELP aifin_ready Readiness of the serving release.", "# TYPE aifin_ready gauge",
                      f"aifin_ready {int(self.ready)}",
                      "# HELP aifin_release_info Serving release.", "# TYPE aifin_release_info gauge",
                      f'aifin_release_info{{release="{self.release}"}} 1',
                      "# HELP aifin_reloads_total Release reloads.", "# TYPE aifin_reloads_total counter",
                      f"aifin_reloads_total {self.reloads}"]
            return "\n".join(lines) + "\n"


def slo_report(events: Iterable[Mapping[str, object]], slo: SLO) -> Dict[str, object]:
    """Availability, latency attainment and error budget spent over recorded request events."""
    rows = list(events)
    total = len(rows)
    bad = sum(is_bad_event(int(r["http_status"]), r.get("status"), r.get("reason")) for r in rows)  # type: ignore[arg-type]
    answered = [r for r in rows if r.get("status") == "ANSWERED"]
    fast = sum(float(r["seconds"]) <= slo.latency_threshold_s for r in answered)  # type: ignore[arg-type]
    availability = None if not total else 1 - bad / total
    latency = None if not answered else fast / len(answered)
    return {"requests": total, "bad_events": bad, "availability": availability,
            "availability_target": slo.availability_target,
            "error_budget_spent": None if not total else (bad / total) / slo.error_budget,
            "latency_attainment": latency, "latency_target": slo.latency_target,
            "latency_threshold_s": slo.latency_threshold_s,
            "met": availability is not None and availability >= slo.availability_target
            and (latency is None or latency >= slo.latency_target)}
