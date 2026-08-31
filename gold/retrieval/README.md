# Gold retrieval / answer-correctness set

Real per-question annotations for **TASK-100** (strong-tier / strict-verification
correctness baseline) and **RETRIEVAL_QUALITY** (Recall@k / MRR). Kept strictly
separate from fixtures (`src/evaluation/*_fixtures.py`) and smoke queries
(`deploy/retrieval/smoke-queries.jsonl`) — neither of those may ever be used as
gold.

- Schema: `m10-gold-retrieval-v1` — see `SCHEMA.md` and
  `src/evaluation/gold_retrieval.py` (`GoldRetrievalCase`).
- Tooling: `python -m src.evaluation.run_gold_retrieval_validation`.
- Files: `pilot.jsonl` (one JSON object per line; `//` lines are comments).

## Current state (2026-08-31)

`pilot.jsonl` is **empty**. The canonical corpus is present
(`artifacts/m2-corpus-v1/4f39603a89e213129de6c57e1234456e88086832c8b61bd9821770d76a6dc78e`
— 1973 reports, 146 246 tables, 1 743 311 chunks) but the **ViFinQA source
question/answer pairs are not available on this instance**, so there is nothing
to annotate. No cases are invented.

## Populating the pilot

1. Make the real ViFinQA `questions.jsonl` readable.
2. Classify it:
   ```
   python -m src.evaluation.run_gold_retrieval_validation \
       --corpus-artifact artifacts/m2-corpus-v1/<artifact_id> \
       --questions-source <questions.jsonl>
   ```
   Buckets:
   - **AUTO_DERIVABLE** — the source already carries a corpus-resolvable
     evidence pointer (report + table, or paragraph).
   - **NEEDS_MANUAL_ANNOTATION** — company/report resolve but a human must
     supply the evidence pointer.
   - **UNSUPPORTED** — no resolvable ticker or no matching report in the corpus.
3. Hand-annotate 20–50 high-quality cases into `pilot.jsonl`
   (`annotation_status: VERIFIED` once a human confirms evidence).
4. Validate:
   ```
   python -m src.evaluation.run_gold_retrieval_validation \
       --corpus-artifact artifacts/m2-corpus-v1/<artifact_id> \
       --gold gold/retrieval/pilot.jsonl
   ```
   Exit 0 == zero ERROR issues.
5. Compute metrics on the gold-bearing cases only via
   `src.evaluation.gold_retrieval.compute_gold_retrieval_metrics` with a
   `retrieve_fn` backed by the real `Batch3Retriever`.

## Rules

- A `BOOTSTRAP_UNVERIFIED` provenance can never back an `AUTO_DERIVABLE` or
  `VERIFIED` status — bootstrap guesses are not gold until verified.
- `gold_answer` is only scored when `annotation_status == VERIFIED`.
- Every referenced report / table / paragraph / chunk / source-cell id must
  exist in the canonical corpus (validator enforces this).
