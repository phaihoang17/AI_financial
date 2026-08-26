"""Shared M10 measurement/telemetry contracts (TASK-106/110/111).

These contracts are deliberately separate from the deterministic pipeline
contracts. ADR-040 keeps wall-clock time out of the M9/evaluation state so that
fixture runs serialize identically; latency, token, and load telemetry therefore
live here and never enter a checkpoint, report, or answer.

Every telemetry report carries a :class:`MeasurementSource` so a CPU fixture
measurement can never be mistaken for a live production number. No GPU/model
latency, token, or cost value is fabricated: CPU harnesses measure only the
deterministic pipeline, and model-dependent numbers stay explicitly pending.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
import time
from typing import Dict, List, Sequence

from src.understanding.schemas import SchemaValidationError


TELEMETRY_SCHEMA_VERSION = "m10-telemetry-v1"


class MeasurementSource(str, Enum):
    """Whether a measurement came from the CPU fixtures or live production."""

    CPU_FIXTURE = "CPU_FIXTURE"
    LIVE = "LIVE"


class Stopwatch:
    """Monotonic wall-clock timer returning elapsed milliseconds."""

    def __init__(self) -> None:
        self._start = time.monotonic_ns()

    def elapsed_ms(self) -> float:
        return (time.monotonic_ns() - self._start) / 1_000_000.0


def _percentile(sorted_values: Sequence[float], pct: float) -> float:
    """Nearest-rank percentile over pre-sorted values (deterministic)."""
    if not sorted_values:
        raise SchemaValidationError("percentile requires at least one sample")
    if not 0 < pct <= 100:
        raise SchemaValidationError("pct must be in (0, 100]")
    rank = math.ceil(pct / 100.0 * len(sorted_values))
    return float(sorted_values[max(1, rank) - 1])


@dataclass(frozen=True)
class Distribution:
    """Deterministic latency-style summary over a set of millisecond samples."""

    count: int
    min_ms: float
    max_ms: float
    mean_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float

    def to_dict(self) -> Dict[str, float]:
        return {
            "count": self.count,
            "min_ms": self.min_ms,
            "max_ms": self.max_ms,
            "mean_ms": self.mean_ms,
            "p50_ms": self.p50_ms,
            "p95_ms": self.p95_ms,
            "p99_ms": self.p99_ms,
        }

    @classmethod
    def from_samples(cls, samples: Sequence[float]) -> "Distribution":
        values: List[float] = [float(value) for value in samples]
        if not values:
            raise SchemaValidationError("Distribution requires at least one sample")
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise SchemaValidationError("latency samples must be finite and non-negative")
        ordered = sorted(values)
        return cls(
            count=len(ordered),
            min_ms=ordered[0],
            max_ms=ordered[-1],
            mean_ms=sum(ordered) / len(ordered),
            p50_ms=_percentile(ordered, 50),
            p95_ms=_percentile(ordered, 95),
            p99_ms=_percentile(ordered, 99),
        )
