"""Canonical orchestration public API; milestone-named aliases are gone."""

import importlib

import src.orchestration as orchestration
from src.orchestration import (
    ORCHESTRATION_NODE_SEQUENCE,
    OrchestrationDependencies,
    OrchestrationGraph,
    graph,
    routing,
)
from src.orchestration.nodes import OrchestrationDependencies as NodeDependencies


def test_canonical_public_api_uses_role_based_names():
    assert OrchestrationDependencies is NodeDependencies
    assert OrchestrationGraph.__module__ == "src.orchestration.graph"
    assert ORCHESTRATION_NODE_SEQUENCE == (
        "nlu",
        "supervisor",
        "retrieval",
        "evidence",
        "programmer",
        "sandbox",
        "verification",
        "answer",
    )


def test_milestone_named_aliases_are_removed():
    for module, names in (
        (orchestration, ("Batch2Dependencies", "M9Batch2Graph", "M9Batch3Graph")),
        (graph, ("M9_BATCH2_NODE_SEQUENCE", "M9_BATCH3_NODES", "M9Batch2Result", "M9Batch3Result")),
        (routing, ("Batch3Transition", "run_batch3_nlu", "run_batch3_answer")),
    ):
        for name in names:
            assert not hasattr(module, name), f"{module.__name__}.{name} should no longer exist"


def test_deprecated_batch_module_paths_are_removed():
    for module_name in (
        "src.orchestration.batch2",
        "src.orchestration.batch3",
        "src.orchestration.graph_batch3",
        "src.retrieval.batch3",
    ):
        try:
            importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue
        raise AssertionError(f"{module_name} should no longer exist")
