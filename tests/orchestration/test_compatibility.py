"""Canonical orchestration imports and temporary compatibility aliases.

Deprecated Batch 2 / Batch 3 module paths (``src.orchestration.batch2``,
``src.orchestration.batch3``, ``src.orchestration.graph_batch3``) have been
removed; deprecated symbol-level aliases now live directly in the canonical
modules (``graph.py``, ``routing.py``) that replaced them.
"""

from src.orchestration import (
    Batch2Dependencies,
    M9_BATCH3_NODES,
    M9Batch3Graph,
    M9Batch3Result,
    ORCHESTRATION_NODE_SEQUENCE,
    OrchestrationDependencies,
    OrchestrationGraph,
    OrchestrationResult,
)
from src.orchestration.graph import (
    M9Batch2Graph,
    StraightThroughOrchestrationGraph,
)
from src.orchestration.nodes import OrchestrationDependencies as NodeDependencies
from src.orchestration.routing import (
    Batch3Transition,
    RoutingTransition,
    route_nlu,
    run_batch3_nlu,
)
from src.orchestration.state import M9State
from tests.orchestration.test_graph import make_graph
from tests.orchestration.test_straight_through_graph import (
    fixture_pairs,
    query_understanding,
)


def test_canonical_public_api_uses_role_based_names():
    assert OrchestrationDependencies is NodeDependencies
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


def test_legacy_public_imports_are_identity_aliases():
    assert Batch2Dependencies is OrchestrationDependencies
    assert M9Batch3Graph is OrchestrationGraph
    assert M9Batch3Result is OrchestrationResult
    assert M9_BATCH3_NODES is ORCHESTRATION_NODE_SEQUENCE
    assert M9Batch2Graph is StraightThroughOrchestrationGraph
    assert Batch3Transition is RoutingTransition
    assert run_batch3_nlu is route_nlu


def test_canonical_and_legacy_final_graph_names_have_identical_behavior():
    current = query_understanding()
    pairs = fixture_pairs(("2015",), ("12.5",))
    canonical, _ = make_graph(current, [pairs])
    _, legacy_dependencies = make_graph(current, [pairs])
    legacy = M9Batch3Graph(legacy_dependencies)

    canonical_result = canonical.run(
        request_id="compatibility-behavior",
        raw_question=current.raw_question,
    )
    legacy_result = legacy.run(
        request_id="compatibility-behavior",
        raw_question=current.raw_question,
    )

    assert isinstance(canonical_result, OrchestrationResult)
    assert isinstance(legacy_result, OrchestrationResult)
    assert canonical_result.state.to_dict() == legacy_result.state.to_dict()
    assert M9State.from_dict(canonical_result.state.to_dict()).to_dict() == (
        legacy_result.state.to_dict()
    )


def test_final_graph_has_one_canonical_class_implementation():
    assert M9Batch3Graph.__module__ == "src.orchestration.graph"
    assert OrchestrationGraph.__module__ == "src.orchestration.graph"


def test_deprecated_batch_module_paths_are_removed():
    import importlib

    for module_name in (
        "src.orchestration.batch2",
        "src.orchestration.batch3",
        "src.orchestration.graph_batch3",
    ):
        try:
            importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue
        raise AssertionError(f"{module_name} should no longer exist")
