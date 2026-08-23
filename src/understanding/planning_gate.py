"""Deterministic v1 gate from resolver findings to planning eligibility."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List

from src.understanding.company_resolver import (
    CompanyResolution,
    CompanyResolutionStatus,
)
from src.understanding.metric_normalizer import (
    MetricResolution,
    MetricResolutionStatus,
)
from src.understanding.schemas import (
    Operation,
    SchemaValidationError,
    _parse_enum,
    _require_bool,
    _require_enum,
    _require_exact_keys,
    _require_mapping,
)
from src.understanding.temporal_parser import TemporalParseResult


class FindingName(str, Enum):
    COMPANY = "COMPANY"
    PERIOD = "PERIOD"
    METRIC = "METRIC"


_FINDING_ORDER = {
    FindingName.COMPANY: 0,
    FindingName.PERIOD: 1,
    FindingName.METRIC: 2,
}


def _validate_findings(value: Any, path: str) -> List[FindingName]:
    if not isinstance(value, list):
        raise SchemaValidationError(f"{path} must be a list")
    findings = [
        _require_enum(item, FindingName, f"{path}[{index}]")
        for index, item in enumerate(value)
    ]
    if len(findings) != len(set(findings)):
        raise SchemaValidationError(f"{path} must be deduplicated")
    if findings != sorted(findings, key=_FINDING_ORDER.__getitem__):
        raise SchemaValidationError(
            f"{path} must use COMPANY, PERIOD, METRIC order"
        )
    return findings


@dataclass
class PlanningGate:
    allowed: bool
    missing_information: List[FindingName]
    ambiguities: List[FindingName]

    def __post_init__(self) -> None:
        self.allowed = _require_bool(self.allowed, "allowed")
        self.missing_information = _validate_findings(
            self.missing_information, "missing_information"
        )
        self.ambiguities = _validate_findings(self.ambiguities, "ambiguities")
        if self.allowed and (self.missing_information or self.ambiguities):
            raise SchemaValidationError(
                "allowed planning cannot contain missing information or ambiguities"
            )

    @classmethod
    def from_dict(cls, value: Any) -> PlanningGate:
        data = _require_mapping(value, "PlanningGate")
        _require_exact_keys(
            data,
            {"allowed", "missing_information", "ambiguities"},
            "PlanningGate",
        )
        missing = data["missing_information"]
        ambiguities = data["ambiguities"]
        if not isinstance(missing, list):
            raise SchemaValidationError("missing_information must be a list")
        if not isinstance(ambiguities, list):
            raise SchemaValidationError("ambiguities must be a list")
        return cls(
            allowed=data["allowed"],
            missing_information=[
                _parse_enum(
                    finding,
                    FindingName,
                    f"missing_information[{index}]",
                )
                for index, finding in enumerate(missing)
            ],
            ambiguities=[
                _parse_enum(
                    finding,
                    FindingName,
                    f"ambiguities[{index}]",
                )
                for index, finding in enumerate(ambiguities)
            ],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "missing_information": [
                finding.value for finding in self.missing_information
            ],
            "ambiguities": [finding.value for finding in self.ambiguities],
        }


def _append_once(findings: List[FindingName], finding: FindingName) -> None:
    if finding not in findings:
        findings.append(finding)


def evaluate_planning_gate(
    company_resolution: CompanyResolution,
    period_result: TemporalParseResult,
    metric_resolutions: List[MetricResolution],
    operation: Operation,
) -> PlanningGate:
    """Evaluate hard-required v1 identity findings without mutating inputs."""
    if not isinstance(company_resolution, CompanyResolution):
        raise SchemaValidationError(
            "company_resolution must be a CompanyResolution"
        )
    if not isinstance(period_result, TemporalParseResult):
        raise SchemaValidationError("period_result must be a TemporalParseResult")
    if not isinstance(metric_resolutions, list) or not all(
        isinstance(resolution, MetricResolution)
        for resolution in metric_resolutions
    ):
        raise SchemaValidationError(
            "metric_resolutions must be a list of MetricResolution values"
        )
    operation = _require_enum(operation, Operation, "operation")

    missing: List[FindingName] = []
    ambiguities: List[FindingName] = []

    if company_resolution.status is CompanyResolutionStatus.UNRESOLVED:
        _append_once(missing, FindingName.COMPANY)
    elif company_resolution.status is CompanyResolutionStatus.AMBIGUOUS:
        _append_once(ambiguities, FindingName.COMPANY)

    if not period_result.resolutions or any(
        resolution.period is None for resolution in period_result.resolutions
    ):
        _append_once(missing, FindingName.PERIOD)

    if not metric_resolutions:
        _append_once(missing, FindingName.METRIC)
    for resolution in metric_resolutions:
        if resolution.status is MetricResolutionStatus.UNRESOLVED:
            _append_once(missing, FindingName.METRIC)
        elif resolution.status is MetricResolutionStatus.AMBIGUOUS:
            _append_once(ambiguities, FindingName.METRIC)

    allowed = (
        operation is not Operation.UNKNOWN and not missing and not ambiguities
    )
    return PlanningGate(
        allowed=allowed,
        missing_information=missing,
        ambiguities=ambiguities,
    )
