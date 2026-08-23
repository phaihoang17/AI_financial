# PRODUCT.md — AI Financial Data Assistant

## 1. Product Summary

AI Financial Data Assistant answers Vietnamese natural-language questions over financial statements.

The product is not merely a chatbot that reads a table. It is an end-to-end financial QA system that must locate the correct source data before reasoning over it.

## 2. Problem Statement

A user may ask questions such as:

- `LNST của AAA năm 2015 là bao nhiêu?`
- `ROE của AAA năm 2015 là bao nhiêu?`
- `Doanh thu tăng bao nhiêu % từ 2014 sang 2015?`
- `Trung bình LNST 3 năm gần nhất là bao nhiêu?`

Answering these questions correctly may require:

- identifying the company,
- identifying the correct reporting period,
- distinguishing consolidated vs. standalone statements,
- retrieving one or more financial tables,
- retrieving associated narrative paragraphs,
- combining table and text evidence,
- locating the correct row/column/cell or text span,
- resolving scale and unit from headers or surrounding text,
- applying a financial formula,
- handling Vietnamese numeric conventions,
- executing a multi-step reasoning program,
- verifying that the result is grounded in the retrieved evidence.

## 3. Product Goal

Provide accurate, grounded, reproducible answers to supported financial questions over the available financial-report corpus.

The system should prefer abstention or clarification over fabricating missing entities, periods, metrics, or evidence.

## 4. Core Product Principles

### 4.1 Correct data before clever reasoning

The product must prioritize finding the correct report, table, and evidence before optimizing reasoning prompts.

### 4.2 Structured understanding

Natural language should be converted into structured semantics before execution planning.

### 4.3 Reproducible calculations

Derived answers should be reproducible from grounded evidence and executable logic.

### 4.4 Traceable evidence

The system should preserve enough metadata to explain which report/table/evidence supported the result.

### 4.5 Safe failure

If core information cannot be resolved or the evidence cannot be verified, the product should clarify or abstain.


### 4.6 Hybrid evidence over table + text

The product must support questions whose evidence is:

- table-only,
- text-only,
- distributed across both table and narrative text.

### 4.7 Scale-aware answers

For numerical QA, correctness includes both the numeric value and its scale/unit.

### 4.8 Measurable reasoning traces

For derived questions, the system should retain an executable or inspectable reasoning trace rather than only a final answer.

## 5. Target User Experience

### 5.1 Simple lookup

User:

> `LNST của AAA năm 2015?`

Expected behavior:

1. understand `AAA`, `2015`, and `LNST`,
2. retrieve the correct report/table,
3. locate the correct cell,
4. verify period, scope, and unit,
5. return the answer with grounded evidence.

### 5.2 Derived ratio

User:

> `ROE của AAA năm 2015 là bao nhiêu?`

Expected behavior:

1. understand the request as a derived ratio,
2. determine the required financial metrics and periods,
3. retrieve the required income-statement and balance-sheet evidence,
4. generate the calculation program,
5. execute it in the sandbox,
6. verify the result strictly,
7. return the final answer.

The current supervisor design uses the ROE example to require profit after tax and average equity, including an earlier period for the average-equity calculation.

### 5.3 Multi-period comparison

User:

> `Doanh thu tăng bao nhiêu % từ 2014 sang 2015?`

Expected behavior:

- explicitly resolve both periods,
- retrieve evidence for both periods,
- preserve directionality of the requested comparison,
- execute the growth formula,
- verify units and temporal grounding.

### 5.4 Ambiguous / unsupported request

User:

> `Công ty này có nên đầu tư không?`

Expected behavior under the current supported scope:

- do not convert this into a fabricated numerical QA task,
- return an unsupported/abstain outcome or request a supported factual question.

## 6. Supported Question Classes

### `LOOKUP`

Characteristics:

- usually one metric,
- one period,
- no derived arithmetic.

### `DERIVED_RATIO`

Characteristics:

- formula-based,
- often multiple metrics,
- may require multiple tables and/or periods.

### `MULTI_PERIOD`

Characteristics:

- comparison or growth across two or more periods,
- elevated risk of temporal confusion.

### `AGGREGATE`

Characteristics:

- sum, average, min/max, or other aggregate over multiple rows/periods.

## 7. NLU / Understanding Requirements

The product requires a dedicated logical NLU/Understanding layer before planning.

It must produce structured fields for:

- company/ticker,
- period(s),
- period kind (`NAM`, `QUY`, `LUY_KE`),
- statement scope (`HOP_NHAT`, `RIENG`),
- metric(s),
- operation (`none`, `ratio`, `growth`, `aggregate`, `compare`),
- ambiguity/missing information,
- confidence.

The NLU layer should normalize Vietnamese synonyms into canonical financial terms.

Example:

```text
"lãi ròng" = "LNST" = "lợi nhuận sau thuế"
```

When a value is inferred rather than explicitly stated, the structured output should retain that fact where relevant.

## 8. Planning Requirements

The Supervisor / Planner consumes the structured NLU output and decides:

- question type,
- required financial metrics,
- required tables,
- required periods,
- model tier,
- verification profile,
- retry budget,
- whether to abstain early.

The Supervisor does not calculate the financial answer.

## 9. Retrieval Requirements

The system should locate supporting evidence across both tabular and textual content.

Required evidence modes:

- `TABLE`,
- `TEXT`,
- `HYBRID`.

It should be able to locate:

1. the correct financial report,
2. the correct page/section where applicable,
3. the correct table(s),
4. relevant associated paragraph(s),
5. the correct row/column/cell or text span,
6. scale/unit evidence when it is not encoded next to the value.

The system should preserve hierarchical headers and table metadata when these are necessary to disambiguate a cell.

The default retrieval stack is:

`BM25 + BGE-M3 -> RRF/Fusion -> BGE-reranker-v2-m3`.

Retrieval quality is a product-critical metric. Evaluation must separately measure table, text, and hybrid evidence retrieval.

## 10. Reasoning and Calculation Requirements

For numerical questions:

- use grounded evidence,
- prefer executable Python/Pandas reasoning where appropriate,
- do not rely on free-form arithmetic alone,
- avoid copying answer values into generated code,
- support symbolic numeric masking / deterministic binding where feasible,
- validate generated program structure before execution,
- keep intermediate step references for multi-step reasoning,
- resolve scale/unit before final answer formatting.

For complex tables, an optional bounded table-transformation mode may be used before or during program generation. Atomic transformations may include row/column selection, adding derived columns, grouping, and sorting. The operation history must be explicit and execution must be deterministic.

The default mode remains program-aided reasoning; dynamic table transformation is a targeted fallback rather than an always-on loop.

## 11. Retrieval Product Requirements

The retrieval capability is explicitly hybrid rather than a single semantic-search black box.

Required logical stages:

1. build a retrieval query from `QueryUnderstanding` + `Plan`,
2. run lexical retrieval with BM25,
3. embed the retrieval query with BGE-M3,
4. run vector search against BGE-M3-indexed report/table representations,
5. fuse lexical and dense candidates with RRF or equivalent deterministic fusion,
6. rerank candidates with BGE-reranker-v2-m3,
7. build traceable evidence from the final candidates.

The embedding and reranker models are infrastructure components of retrieval, not user-facing agents.

### Offline indexing requirement

Before online retrieval, the system must support an indexing path that:

- normalizes report/table structure,
- preserves hierarchy and metadata,
- builds searchable textual/schema representations,
- builds a BM25 index,
- generates BGE-M3 embeddings,
- stores vectors and retrieval metadata in a vector index.

## 12. Verification Requirements

Before an answer is accepted, verify applicable dimensions:

- company,
- period,
- report scope,
- table,
- row/column grounding,
- unit,
- numeric parsing,
- financial formula,
- result sanity.

Verification policy:

- `LIGHT` for simple lookup-style questions,
- `STRICT` for derived/multi-period/aggregate reasoning.

## 13. Scale / Unit Requirements

A final answer is not correct unless both value and scale/unit are correct.

The system should preserve:

- `raw_value`,
- normalized numeric value,
- `unit`,
- `scale`,
- provenance for the scale/unit decision.

Scale may come from:

- table headers,
- row/column labels,
- surrounding narrative,
- explicit question wording.

Scale/unit errors should have a dedicated evaluation and failure category.

## 14. Optional Experience Memory

Experience memory is not required for v1.

If introduced later, it should:

- store reusable strategies from successful trajectories,
- store caution/guard rules distilled from failed trajectories,
- retrieve entries by semantic relevance,
- inject memory only above a calibrated threshold,
- cap retrieval depth,
- fall back to the base pipeline when no relevant memory exists,
- be read-only during held-out evaluation.

The product must measure memory contribution through ablation before enabling it by default.

## 15. Failure Behavior

### Early clarification / abstain

Use when:

- company cannot be uniquely resolved,
- required period cannot be resolved safely,
- metric cannot be grounded,
- operation is outside the supported set.

### Post-execution abstain

Use when:

- evidence remains insufficient after allowed retries,
- result cannot be reproduced or verified,
- safe execution fails repeatedly.

The final failure should include an explicit reason rather than returning a guessed numeric answer.

## 16. Non-Goals for the Current Scope

Based on the provided materials, the current design does not establish requirements for:

- investment advice,
- stock-price prediction,
- portfolio recommendation,
- autonomous trading,
- open-ended company valuation opinions.

Do not silently expand the system into these domains without a product decision.

## 17. Product Success Measures

Exact production thresholds remain `TBD` until measured on the project dataset.

Track at least:

### Understanding / planning

- NLU field accuracy,
- question-type classification,
- evidence-source planning accuracy,
- early-abstain correctness,
- model-routing correctness.

### Retrieval

- report/page recall,
- table Recall@K,
- text-span/paragraph Recall@K,
- hybrid evidence coverage,
- reranker quality,
- wrong-period / wrong-scope rate.

### Reasoning

- program execution accuracy,
- program/trace accuracy,
- answer accuracy,
- performance by reasoning depth (`1`, `2`, `3+` steps),
- performance by source type (`TABLE`, `TEXT`, `HYBRID`),
- performance on complex-table fallback cases.

### Numerical correctness

- scale accuracy,
- unit accuracy,
- numeric parsing accuracy,
- formula correctness,
- grounding correctness.

### Production

- p50 / p95 / p99 latency,
- cost per query,
- token usage,
- cache hit rate,
- retry rate,
- model-escalation rate,
- coordination-failure rate,
- throughput at target load.

Program execution accuracy and program accuracy should be reported together: execution alone may accept accidental correct answers, while strict program matching may reject semantically equivalent programs.

## 18. Model Strategy for v1

Recommended model/engine allocation:

- NLU / Understanding: Qwen3-8B or Qwen2.5-7B-Instruct plus deterministic resolvers.
- Supervisor / Planner: Qwen3-8B.
- Embedding: BGE-M3.
- Sparse retrieval: BM25.
- Fusion: RRF or equivalent deterministic scoring.
- Reranking: BGE-reranker-v2-m3.
- Schema linking: deterministic fuzzy/schema matching; optional Qwen2.5-7B-Instruct only for ambiguous cases.
- Text-to-Pandas: Qwen2.5-Coder-14B-Instruct.
- Verification: deterministic checks first; optional Qwen3-8B for semantic edge cases.
- Narrative: template-based in v1; optional Qwen2.5-7B or Gemma 3 12B later.

NLU and Supervisor are separate logical product stages even if they reuse the same Qwen3-8B serving instance.

## 19. Source Basis

Additional research sources incorporated in v3:

- **TAT-QA (2105.07624):** hybrid table/text evidence, symbolic aggregation, explicit scale prediction.
- **FinQA (2109.00122):** retrieve supporting facts then generate executable reasoning programs; program and execution metrics.
- **PAL (2211.10435):** offload execution to Python.
- **Chain-of-Table (2401.04398):** dynamic bounded table operations and intermediate-table state.
- **Multi-Agent Financial Document Processing benchmark (2603.22651):** hierarchical orchestration tradeoffs, routing, caching, retry, failure/latency analysis.
- **FinAcumen (2606.17642):** selective experience memory and deterministic financial tools.

The 2603.22651 study benchmarks financial document extraction rather than this exact QA task; its results are used as production-orchestration guidance, not as direct QA accuracy evidence.



This product definition is grounded in the provided project materials, especially:

- end-to-end Financial QA flow: question -> report -> table -> evidence -> reasoning/calculation -> answer,
- supervisor NLU, classification, planning, routing, and early-abstain design,
- hierarchical-table and cross-table reasoning requirements,
- retrieval vs. oracle-evidence benchmark gap,
- failure analysis emphasizing extraction/evidence errors,
- program-aided reasoning, numeric masking, sandbox, and verification guidance.
