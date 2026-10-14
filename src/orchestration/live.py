"""Assemble existing M9 ports with real artifacts and API generative stages."""

import json
from pathlib import Path
from uuid import uuid4

from src.indexing.provenance_sidecar import ProvenanceSidecar
from src.indexing.table_class_sidecar import TableClassSidecar
from src.orchestration.api_stages import APINLU, APIProgrammer
from src.orchestration.graph import OrchestrationGraph
from src.orchestration.nodes import OrchestrationDependencies
from src.orchestration.ports import RetrievalArtifacts
from src.retrieval.batch3 import Batch3Retriever, CorpusChunkLookup
from src.retrieval.bm25 import BM25Index
from src.retrieval.evidence import EvidenceProvenanceRepository, build_evidence_items, locate_cells
from src.retrieval.query_embedding import embed_query_texts
from src.retrieval.vector_search import VectorSearcher
from src.sandbox.executor import execute_sandboxed
from src.supervisor.schemas import EvidenceSource
from src.understanding.company_resolver import CompanyAlias


class BindingStore:
    def __init__(self):
        self._values = {}

    def put(self, binding_map):
        reference = f"binding://live/{uuid4().hex}"
        self._values[reference] = binding_map
        return reference

    def resolve(self, binding_ref):
        return self._values[binding_ref]

    def contains(self, binding_ref):
        return binding_ref in self._values

    def discard(self, binding_ref):
        self._values.pop(binding_ref, None)


class LiveEvidenceStages:
    def __init__(self, retriever, repository):
        self.retriever = retriever
        self.repository = repository
        self.linked_tables = {}

    def retrieve(self, request):
        request.assert_immutable()
        candidates = {}
        failures = []
        self.linked_tables = {}
        if EvidenceSource.TABLE in request.query.evidence_sources:
            try:
                tables = self.retriever.retrieve_multi_table(request.query, top_k=request.top_k)
                for result in tables.subquery_results:
                    if result.failure_code is not None:
                        from src.retrieval.schemas import RetrievalContractError
                        failures.append(RetrievalContractError(result.failure_code, "TABLE subquery failed"))
                    for candidate in result.candidates:
                        candidates.setdefault(candidate.candidate_id, candidate)
            except Exception as error:
                failures.append(error)
        if EvidenceSource.TEXT in request.query.evidence_sources:
            try:
                narrative = self.retriever.retrieve_narrative(request.query, top_k=request.top_k)
                self.linked_tables = narrative.linked_table_ids_by_candidate
                for candidate in narrative.candidates:
                    candidates.setdefault(candidate.candidate_id, candidate)
            except Exception as error:
                failures.append(error)
        if failures:
            raise failures[0]
        selected = list(candidates.values())
        # ADR-062: hints come from the raw reports the evidence cells are rebuilt from.
        hints = self.repository.scale_unit_hints(selected)
        return RetrievalArtifacts(request.query, tuple(selected), hints)

    def locate(self, query, candidates):
        return locate_cells(query, candidates, self.repository)

    def build(self, locations, candidates):
        return build_evidence_items(locations, candidates, self.repository,
                                    linked_table_ids_by_candidate=self.linked_tables)


class LiveSandbox:
    def execute(self, request):
        return execute_sandboxed(request)


def build_live_graph(client, *, corpus_root, vector_root, bm25_root, raw_root,
                     provenance_root, table_class_root, top_k=10,
                     strong_client=None, memory=None, query_encoder=None, reranker=None):
    bm25 = BM25Index(bm25_root, corpus_root)
    vector = VectorSearcher(vector_root, corpus_root)
    sidecar = ProvenanceSidecar(provenance_root, corpus_root)
    table_classes = TableClassSidecar(table_class_root, corpus_root)
    lookup = CorpusChunkLookup(corpus_root, vector_metadata_path=vector.metadata_path)
    if query_encoder is None and reranker is None:
        # Keep only one BGE model resident on the 8 GiB Mac.
        retriever = Batch3Retriever(
            bm25, vector, lambda query: embed_query_texts(query, batch_size=1, device="cpu"),
            sidecar=sidecar, chunk_lookup=lookup, reranker_batch_size=1,
        )
    else:
        # ADR-049 served BGE endpoints: the pinned models stay resident in their own processes.
        retriever = Batch3Retriever(
            bm25, vector, lambda query: embed_query_texts(query, encoder=query_encoder),
            reranker=reranker, sidecar=sidecar, chunk_lookup=lookup,
        )
    repository = EvidenceProvenanceRepository(
        corpus_root, raw_root, sidecar=sidecar, table_class_sidecar=table_classes,
        vector_metadata_path=vector.metadata_path,
    )
    stages = LiveEvidenceStages(retriever, repository)
    roots = {"corpus": corpus_root, "vector": vector_root, "bm25": bm25_root,
             "provenance": provenance_root, "table_class": table_class_root}
    manifests = {name: json.loads((Path(root) / "manifest.json").read_text())
                 for name, root in roots.items()}
    companies = sorted({(report["ticker"], report["company_name"])
                        for report in manifests["corpus"]["reports"]
                        if report.get("ticker") and report.get("company_name")})
    aliases = [CompanyAlias(name, name, ticker) for ticker, name in companies]
    dependencies = OrchestrationDependencies(
        nlu=APINLU(client, aliases), retrieval=stages, cell_locator=stages,
        evidence_builder=stages, programmer=APIProgrammer(client, strong_client=strong_client, memory=memory),
        sandbox=LiveSandbox(), binding_store=BindingStore(), top_k=top_k,
        artifact_versions={**{name: manifest["artifact_id"] for name, manifest in manifests.items()},
                           "generative_model": client.endpoint.model_id,
                           **({"strong_generative_model": strong_client.endpoint.model_id} if strong_client else {}),
                           **({"experience_memory": memory.artifact_id} if memory else {})},
    )
    return OrchestrationGraph(dependencies)
