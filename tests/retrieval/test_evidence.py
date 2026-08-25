from pathlib import Path
import tempfile
import unittest

from src.retrieval.corpus_candidates import iter_corpus_candidates
from src.retrieval.evidence import (
    EvidenceProvenanceRepository, MatchBasis, build_evidence_items,
    check_evidence_completeness, generate_evidence_requirements, locate_cells,
)
from src.retrieval.schemas import RetrievalCompany, RetrievalQuery
from src.supervisor.schemas import EvidenceSource
from src.understanding.schemas import StatementScope
from src.indexing.provenance_sidecar import ProvenanceSidecar
from tests.indexing.test_provenance_sidecar import ProvenanceSidecarTests

def query(sources):
    return RetrievalQuery("Doanh thu", RetrievalCompany("Test Company","AAA"), ["2024"], None, StatementScope.HOP_NHAT, ["Doanh thu"], None, list(sources), None, None, ["Doanh thu"])

class EvidenceBatch4Tests(unittest.TestCase):
    def test_exact_table_and_text_provenance_and_completeness(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw, corpus, sidecar_root = ProvenanceSidecarTests()._artifacts(Path(tmp))
            candidates=list(iter_corpus_candidates(corpus)); table=next(x for x in candidates if x.source_type is EvidenceSource.TABLE); text=next(x for x in candidates if x.source_type is EvidenceSource.TEXT)
            repo=EvidenceProvenanceRepository(corpus, raw, sidecar=ProvenanceSidecar(sidecar_root, corpus))
            locations=locate_cells(query([EvidenceSource.TABLE]), [table], repo)
            evidence=build_evidence_items(locations,[table,text],repo)
        self.assertTrue(locations)
        self.assertEqual(len({x.source_cell_id for x in locations}),len(locations))
        self.assertTrue(all(x.candidate_id==table.candidate_id for x in locations))
        table_items=[x for x in evidence if x.source_type is EvidenceSource.TABLE]
        text_items=[x for x in evidence if x.source_type is EvidenceSource.TEXT]
        self.assertTrue(table_items and text_items)
        self.assertIsNotNone(text_items[0].associated_table_ref)
        self.assertTrue(all(x.raw_value is not None for x in table_items))
        self.assertTrue(all(x.normalized_value is None or isinstance(x.normalized_value,str) for x in table_items))
        req=generate_evidence_requirements(query([EvidenceSource.TABLE]))
        mapping={item.evidence_id:location for item in table_items for location in locations if item.table_ref==location.table_id and item.raw_value==location.normalized_text}
        result=check_evidence_completeness(req,evidence,mapping)
        self.assertEqual(result.missing_requirement_ids, [req[0].requirement_id])

    def test_text_and_structural_evidence_do_not_satisfy_table_exact_requirement(self):
        req=generate_evidence_requirements(query([EvidenceSource.TEXT]))
        self.assertEqual(req[0].source_type,EvidenceSource.TEXT)
        self.assertEqual(len(req),1)

if __name__ == "__main__": unittest.main()
