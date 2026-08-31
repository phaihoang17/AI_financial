# `m10-gold-retrieval-v1` — gold case schema

One JSON object per line in `*.jsonl`. Canonical (de)serialisation:
`src/evaluation/gold_retrieval.py` `GoldRetrievalCase`.

| field | type | required | notes |
|---|---|---|---|
| `question_id` | string | yes | unique across the file |
| `question` | string | yes | verbatim question text |
| `company` | object | yes | `{ "ticker": "AAA", "name": "CTCP ..." }` — `ticker` required |
| `period` | string \| null | no | e.g. `"2015"`; digit strings are cross-checked against report year |
| `period_kind` | string \| null | no | e.g. `"NAM"`, `"QUY"` |
| `statement_scope` | string \| null | no | `"HOP_NHAT"` \| `"RIENG"` — cross-checked against report |
| `metric` | string \| null | no | resolved metric label if known |
| `operation` | string \| null | no | e.g. `"LOOKUP"`, `"RATIO"`, `"GROWTH"` |
| `gold_report_id` | string \| null | no | must exist in canonical corpus |
| `gold_table_id` | string \| null | no | must exist; must belong to `gold_report_id` if both set |
| `gold_paragraph_id` | string \| null | no | must exist in canonical corpus |
| `gold_chunk_ids` | string[] | yes (`[]` allowed) | M2 `EmbeddingChunk.chunk_id`s; each must exist |
| `gold_source_cell_ids` | string[] | yes (`[]` allowed) | each must exist in canonical corpus |
| `gold_answer` | object \| null | no | `{ "value": <str\|number>, "unit"?, "scale"? }` — scored only when `VERIFIED` |
| `provenance` | object | yes | `{ "source": <str>, "method": "AUTO_DERIVED"\|"MANUAL"\|"BOOTSTRAP_UNVERIFIED", "notes"? }` |
| `annotation_status` | string | yes | `AUTO_DERIVABLE` \| `NEEDS_MANUAL_ANNOTATION` \| `UNSUPPORTED` \| `VERIFIED` |

## Validation (enforced by `validate_gold_cases`)

**ERROR** (fails the run):
- duplicate `question_id`
- `annotation_status` in {`AUTO_DERIVABLE`, `VERIFIED`} with no evidence pointer
- `provenance.method == BOOTSTRAP_UNVERIFIED` with a gold-bearing status
- any referenced report / table / paragraph / chunk / source-cell id absent from
  the canonical corpus
- `gold_table_id` whose owning report differs from `gold_report_id`

**WARNING** (reported, does not fail):
- duplicate evidence signature across two questions
- `gold_answer` present while `annotation_status != VERIFIED`
- case `ticker` / `statement_scope` / `period` disagree with the referenced report
- no corpus reference index supplied (referential checks skipped)

## Metrics

`compute_gold_retrieval_metrics(cases, retrieve_fn, k_values=(1,5,10))` scores
**only** gold-bearing cases (`is_gold_bearing()` — a gold-bearing status *and* at
least one evidence pointer). It reuses `src/evaluation/retrieval.py`
`recall_at_k` / `mrr`; `retrieve_fn` must return the final reranked
`RetrievalCandidate` list from the real stack.
