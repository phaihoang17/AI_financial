"""Canonical orchestration API with deprecated milestone-name aliases."""

from src.orchestration.graph import (
    M9_BATCH2_NODE_SEQUENCE,
    M9_BATCH3_NODES,
    M9Batch2Graph,
    M9Batch2Result,
    M9Batch3Graph,
    M9Batch3Result,
    ORCHESTRATION_NODE_SEQUENCE,
    OrchestrationGraph,
    OrchestrationResult,
)
from src.orchestration.nodes import (
    OrchestrationDependencies,
    OrchestrationFailure,
    OrchestrationFailureCode,
)

# Deprecated: use OrchestrationDependencies.
Batch2Dependencies = OrchestrationDependencies

__all__ = [
    "OrchestrationDependencies",
    "OrchestrationGraph",
    "OrchestrationResult",
    "ORCHESTRATION_NODE_SEQUENCE",
    "OrchestrationFailure",
    "OrchestrationFailureCode",
    # Deprecated compatibility exports.
    "Batch2Dependencies",
    "M9_BATCH2_NODE_SEQUENCE",
    "M9Batch2Graph",
    "M9Batch2Result",
    "M9_BATCH3_NODES",
    "M9Batch3Graph",
    "M9Batch3Result",
]
