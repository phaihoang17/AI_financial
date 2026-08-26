"""TASK-106 end-to-end and per-stage latency distribution.

Two honest instruments:

- :func:`profile_stages` times an ordered set of stage callables plus their total,
  producing a per-stage + E2E :class:`Distribution`. It is the reusable
  measurement primitive and is source-agnostic.
- :func:`measure_e2e_latency` runs the real deterministic M9 fixture graph and
  records **end-to-end** CPU wall-clock. Because the CPU fixtures run no model,
  per-stage *model* latency is not meaningful here and is reported as pending;
  true per-stage serving latency requires live serving (TASK-108/109) and is
  ``GPU_PRODUCTION_VALIDATION_PENDING``.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Dict, Mapping, Optional, Sequence

from src.evaluation.e2e import RetrievedEvidenceE2ECase
from src.evaluation.e2e_fixtures import e2e_fixture_cases
from src.evaluation.telemetry import (
    Distribution,
    MeasurementSource,
    Stopwatch,
    TELEMETRY_SCHEMA_VERSION,
)
from src.understanding.schemas import SchemaValidationError


LATENCY_REPORT_SCHEMA_VERSION = "m10-latency-distribution-v1"
STAGE_LATENCY_LIVE_BLOCKER = "GPU_PRODUCTION_VALIDATION_PENDING"


@dataclass(frozen=True)
class StageProfile:
    e2e: Distribution
    stages: Mapping[str, Distribution]

    def to_dict(self) -> dict:
        return {
            "e2e": self.e2e.to_dict(),
            "stages": {name: dist.to_dict() for name, dist in self.stages.items()},
        }


def profile_stages(
    stage_callables: "OrderedDict[str, Callable[[], object]]",
    *,
    repeats: int = 1,
) -> StageProfile:
    """Time each stage callable and the whole sequence over ``repeats`` runs."""
    if not isinstance(stage_callables, OrderedDict) or not stage_callables:
        raise SchemaValidationError("stage_callables must be a non-empty OrderedDict")
    if isinstance(repeats, bool) or not isinstance(repeats, int) or repeats < 1:
        raise SchemaValidationError("repeats must be a positive integer")
    per_stage: Dict[str, list] = {name: [] for name in stage_callables}
    e2e_samples: list = []
    for _ in range(repeats):
        total = Stopwatch()
        for name, call in stage_callables.items():
            watch = Stopwatch()
            call()
            per_stage[name].append(watch.elapsed_ms())
        e2e_samples.append(total.elapsed_ms())
    return StageProfile(
        e2e=Distribution.from_samples(e2e_samples),
        stages=OrderedDict(
            (name, Distribution.from_samples(samples)) for name, samples in per_stage.items()
        ),
    )


@dataclass(frozen=True)
class E2ELatencyReport:
    measurement_source: MeasurementSource
    e2e: Distribution
    repeats: int
    case_count: int

    def to_dict(self) -> dict:
        return {
            "schema_version": LATENCY_REPORT_SCHEMA_VERSION,
            "telemetry_schema_version": TELEMETRY_SCHEMA_VERSION,
            "measurement_source": self.measurement_source.value,
            "repeats": self.repeats,
            "case_count": self.case_count,
            "e2e": self.e2e.to_dict(),
            "stage_latency_status": "LIVE_PENDING",
            "stage_latency_blocker": STAGE_LATENCY_LIVE_BLOCKER,
        }


def measure_e2e_latency(
    cases: Optional[Sequence[RetrievedEvidenceE2ECase]] = None,
    *,
    repeats: int = 1,
) -> E2ELatencyReport:
    """Measure real CPU end-to-end wall-clock over the deterministic fixtures."""
    selected = list(e2e_fixture_cases() if cases is None else cases)
    if not selected:
        raise SchemaValidationError("at least one case is required")
    if isinstance(repeats, bool) or not isinstance(repeats, int) or repeats < 1:
        raise SchemaValidationError("repeats must be a positive integer")
    samples: list = []
    for _ in range(repeats):
        for case in selected:
            watch = Stopwatch()
            case.graph_factory().run(
                request_id=f"lat-{case.case_id}", raw_question=case.raw_question
            )
            samples.append(watch.elapsed_ms())
    return E2ELatencyReport(
        measurement_source=MeasurementSource.CPU_FIXTURE,
        e2e=Distribution.from_samples(samples),
        repeats=repeats,
        case_count=len(selected),
    )
