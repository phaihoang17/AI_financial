import unittest
from src.retrieval.failure_taxonomy import RetrievalFailureEvent, RetrievalFailureStage, attribute_earliest_failure, stage_for_error
from src.retrieval.schemas import RetrievalContractError

class FailureTaxonomyTests(unittest.TestCase):
    def test_all_stages_are_constructible_and_event_identity_is_stable(self):
        ids=[]
        for stage in RetrievalFailureStage:
            event=RetrievalFailureEvent("query",stage,"TEST_CODE","safe diagnostic",False,case_id="case",artifact_fingerprint="artifact",model_fingerprint="model")
            self.assertEqual(event.to_dict()["event_id"],event.event_id); ids.append(event.event_id)
        self.assertEqual(len(ids),len(set(ids)))
        self.assertEqual(RetrievalFailureEvent.from_dict(event.to_dict()),event)
    def test_known_codes_are_not_silently_unknown(self):
        self.assertEqual(stage_for_error("SIDECAR_CORPUS_MISMATCH"),RetrievalFailureStage.PROVENANCE)
        self.assertEqual(stage_for_error("ARTIFACT_VERSION_MISMATCH"),RetrievalFailureStage.ARTIFACT_COMPATIBILITY)
        with self.assertRaisesRegex(RetrievalContractError,"FAILURE_STAGE_UNMAPPED"): stage_for_error("UNMAPPED")
    def test_stack_trace_and_earliest_attribution(self):
        with self.assertRaisesRegex(RetrievalContractError,"STACK_TRACE"): RetrievalFailureEvent("q",RetrievalFailureStage.BM25,"X","Traceback (most recent call)",False)
        self.assertEqual(attribute_earliest_failure(((RetrievalFailureStage.BM25,False),(RetrievalFailureStage.RERANKER,False))),RetrievalFailureStage.BM25)
if __name__ == "__main__": unittest.main()
