import unittest
from src.evaluation.retrieval import RequiredEvidence, RetrievalEvaluationCase, RetrievalEvaluationMode, evaluate_cases, mrr, recall_at_k
from src.evaluation.run_retrieval_eval import fixture_cases
from src.retrieval.failure_taxonomy import RetrievalFailureStage

class RetrievalEvaluationTests(unittest.TestCase):
    def test_metrics_and_fixture_report_are_deterministic(self):
        cases=fixture_cases(); report=evaluate_cases(RetrievalEvaluationMode.FIXTURE,cases)
        self.assertEqual(report.case_count if hasattr(report,"case_count") else len(report.results),12)
        self.assertEqual(type(cases[0][0]).from_dict(cases[0][0].to_dict()),cases[0][0])
        self.assertEqual(report.to_dict(),evaluate_cases(RetrievalEvaluationMode.FIXTURE,cases).to_dict())
        self.assertIn("RERANKER",report.failure_counts); self.assertIn("BM25",report.failure_counts); self.assertIn("MULTI_TABLE",report.failure_counts)
    def test_table_text_hybrid_and_grounding(self):
        report=evaluate_cases(RetrievalEvaluationMode.FIXTURE,fixture_cases()); hybrid=next(x for x in report.results if x.case_id=="fixture-hybrid")
        self.assertTrue(hybrid.evidence_grounded); self.assertEqual(hybrid.table_requirement_recall,1.0); self.assertEqual(hybrid.text_requirement_recall,1.0)
        self.assertEqual(next(x.failure_stage for x in report.results if x.case_id=="fixture-provenance-failure"),RetrievalFailureStage.EVIDENCE_BUILD)
    def test_no_relevant_and_multiple_relevant(self):
        case, inputs=fixture_cases()[0]; required=case.required_evidence
        self.assertEqual(recall_at_k([],required,1),0.0); self.assertIsNone(mrr([],required)); self.assertEqual(mrr([inputs["rerank"][0],inputs["rerank"][0]],required),1.0)
if __name__ == "__main__": unittest.main()
