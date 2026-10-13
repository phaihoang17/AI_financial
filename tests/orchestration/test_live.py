from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.orchestration.live import BindingStore, LiveEvidenceStages
from src.retrieval.batch3 import Batch3Retriever, Batch3RetrievalError
from src.retrieval.schemas import RetrievalContractError
from src.supervisor.schemas import EvidenceSource


def test_all_hybrid_sources_are_searched_even_after_table_failure():
    retriever = Mock()
    retriever.retrieve_multi_table.return_value = SimpleNamespace(subquery_results=[
        SimpleNamespace(failure_code="BROKEN_TABLE", candidates=[]),
    ])
    retriever.retrieve_narrative.return_value = SimpleNamespace(candidates=[], linked_table_ids_by_candidate={})
    request = SimpleNamespace(assert_immutable=Mock(), top_k=10,
                              query=SimpleNamespace(evidence_sources=[EvidenceSource.TABLE, EvidenceSource.TEXT]))
    with pytest.raises(RetrievalContractError):
        LiveEvidenceStages(retriever, None).retrieve(request)
    retriever.retrieve_narrative.assert_called_once()
    retriever.retrieve_scale_unit_hints.assert_not_called()
    request.assert_immutable.assert_called_once()


def test_binding_capability_is_opaque_and_discarded():
    store = BindingStore()
    value = object()
    first, second = store.put(value), store.put(value)
    assert first != second
    assert store.resolve(first) is value
    store.discard(first)
    assert not store.contains(first)
    with pytest.raises(KeyError):
        store.resolve(first)


@pytest.mark.parametrize("batch_size", [0, -1, True, 1.5])
def test_invalid_batch_size_fails_before_backends(batch_size):
    with pytest.raises(Batch3RetrievalError):
        Batch3Retriever(None, None, lambda _: [], reranker_batch_size=batch_size)
