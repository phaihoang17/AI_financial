"""Single canonical mapping from supported metrics to financial table classes."""
from __future__ import annotations

from typing import Optional, Sequence

from src.supervisor.schemas import MetricTableMapping, TableClass


METRIC_TABLE_REGISTRY: tuple[MetricTableMapping, ...] = (
    MetricTableMapping(metric="LNST", table_class=TableClass.INCOME_STATEMENT),
)


def table_for_metric(
    metric: str,
    registry: Sequence[MetricTableMapping] = METRIC_TABLE_REGISTRY,
) -> Optional[TableClass]:
    return next(
        (mapping.table_class for mapping in registry if mapping.metric == metric),
        None,
    )
