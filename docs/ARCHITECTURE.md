# ARCHITECTURE.md — AI Financial Data Assistant

## 1. Architecture Style

The system uses a **hierarchical supervisor-worker architecture** with an explicit **NLU / Understanding layer** before the Supervisor.

The architecture intentionally avoids a free-form mesh in which many autonomous agents reinterpret the request and negotiate with each other.

Core idea:

- NLU understands the request,
- Supervisor plans and routes,
- specialized workers execute grounded stages,
- deterministic components handle validation and numerical execution whenever possible.

## 2. Offline Evidence Indexing Pipeline

```text
Financial Reports / OCR / HTML / Narrative Text
                    |
                    v
          +--------------------+
          | Document/Page Parse|
          +---------+----------+
                    |
          +---------+----------+
          |                    |
          v                    v
+-------------------+   +-------------------+
| Table Normalizer  |   | Paragraph Extract |
| hierarchy + cells |   | narrative spans   |
+---------+---------+   +---------+---------+
          |                       |
          +-----------+-----------+
                      |
                      v
            +----------------------+
            | Association / Linking|
            | table <-> paragraphs |
            +----------+-----------+
                       |
                       v
            +----------------------+
            | Representation Build |
            | source_type + schema |
            | period/scope/scale   |
            +----------+-----------+
                       |
               +-------+-------+
               |               |
               v               v
          +---------+      +---------+
          | BM25    |      | BGE-M3  |
          | Index   |      | Embedder|
          +---------+      +----+----+
                                |
                                v
                          +------------+
                          |Vector Index|
                          +------------+
```

Index metadata should include, when available:

- company/ticker,
- report identifier,
- page/section,
- period,
- statement scope,
- source type (`TABLE` / `TEXT`),
- table identifier,
- associated paragraph/table links,
- row/column/header paths,
- raw unit/scale hints.

## 3. Online Query Pipeline

```text
User Question
      |
      v
+------------------------+
| NLU / Understanding    |
| Qwen3-8B + resolvers   |
+-----------+------------+
            |
            | QueryUnderstanding
            v
+------------------------+
| Supervisor / Planner   |
| Qwen3-8B               |
+-----------+------------+
            |
            | Plan
            v
+------------------------+
| Hybrid Retrieval Query |
| Builder                |
+-----------+------------+
            |
      +-----+------+
      |            |
      v            v
   BM25         BGE-M3
      |            |
      |       Vector Search
      |            |
      +-----+------+
            |
            v
         RRF/Fusion
            |
            v
 BGE-reranker-v2-m3
            |
            v
+------------------------+
| Evidence Builder       |
| TABLE / TEXT / HYBRID  |
+-----------+------------+
            |
            v
+------------------------+
| Scale / Unit Resolver  |
+-----------+------------+
            |
            v
+------------------------+
| Schema Linker          |
+-----------+------------+
            |
       complex table?
        /          \
      no            yes
      |              |
      |      +----------------------+
      |      | Table Transform      |
      |      | Planner              |
      |      | bounded op history   |
      |      +----------+-----------+
      |                 |
      +--------+--------+
               |
               v
+------------------------+
| Numeric Mask / Bind    |
+-----------+------------+
            |
            v
+------------------------+
| Programmer / Pandas    |
| Qwen2.5-Coder-14B      |
+-----------+------------+
            |
            v
+------------------------+
| Sandboxed Python       |
+-----------+------------+
            |
            v
+------------------------+
| Verification           |
| grounding / numeric /  |
| scale-unit / financial |
+-----------+------------+
            |
      +-----+------+
      |            |
    PASS          FAIL
      |            |
      v            v
 Answer       targeted retry /
 Builder      escalation / abstain
```

### Optional Experience-Memory Sidecar

```text
Train/Dev trajectories
       |
       v
Distill successful strategies
+ failure-derived guard rules
       |
       v
Experience Memory Index
       |
 semantic relevance gate
       |
  +----+--------------------+
  |                         |
relevant                not relevant
  |                         |
  v                         v
inject bounded advice    base pipeline
into selected roles      unchanged
```

Memory is not part of the mandatory v1 path. Held-out evaluation must be read-only with respect to memory.

## 4. Component Responsibilities

## 4.1 NLU / Understanding Layer

### Responsibility

Answer: **What is the user asking?**

### Inputs

```yaml
question: string
context: optional
```

### Proposed Output Contract

```yaml
QueryUnderstanding:
  raw_question: string

  company:
    raw: string | null
    name: string | null
    ticker: string | null
    confidence: float

  periods:
    - value: string
      kind: NAM | QUY | LUY_KE
      raw: string | null

  statement_scope:
    value: HOP_NHAT | RIENG | null
    inferred: boolean
    confidence: float

  metrics:
    - raw: string
      canonical: string
      confidence: float

  operation: none | ratio | growth | aggregate | compare | unknown

  requested_scale: string | null
  requested_unit: string | null

  missing_information: [string]
  ambiguities: [string]
  confidence: float
```

This schema is a proposed separation of the NLU responsibilities already present inside the provided Supervisor design.
The source materials define the fields/semantics but do not prescribe this exact object name or schema.

### Deterministic-first Rules

Where stable dictionaries/parsers can resolve a field, prefer deterministic normalization.

Examples:

- aliases -> ticker,
- Vietnamese financial synonyms -> canonical metric,
- common year/quarter/cumulative expressions -> temporal structure.

LLM assistance can be used for linguistic ambiguity, but the structured result must still pass deterministic validation.

### Exit Conditions

If required identity cannot be safely resolved:

```text
NLU -> clarification/early abstain
```

Do not continue into expensive retrieval/programming when core identity is missing.

## 4.2 Supervisor / Planner

### Responsibility

Answer: **What must the system do to answer the understood request?**

The Supervisor is the routing brain.
It does not calculate or directly read numeric evidence to produce the final result.

### Canonical Plan Contract

```yaml
Plan:
  question_type: LOOKUP | DERIVED_RATIO | MULTI_PERIOD | AGGREGATE
  company: { name: string, ticker: string }
  periods: [string]
  period_kind: NAM | QUY | LUY_KE
  statement_scope: HOP_NHAT | RIENG
  target_metrics: [string]
  derived_target: string | null
  formula_id: string | null
  tables_needed: [string]
  evidence_sources: [TABLE | TEXT]
  reasoning_mode: DIRECT | PROGRAM | TABLE_TRANSFORM
  requires_scale_resolution: boolean
  model_tier: CHEAP | STRONG
  verify_profile: LIGHT | STRICT
  max_retries: integer
  confidence: float
  abstain: boolean
  abstain_reason: string | null
```

### Question Classification

```text
LOOKUP
DERIVED_RATIO
MULTI_PERIOD
AGGREGATE
```

### Planning Dimensions

The Supervisor plans at least:

- number/type of tables,
- number/type of periods,
- statement scope,
- target metrics,
- formula target where applicable,
- model tier,
- verification strictness.

### Example: ROE

```yaml
question_type: DERIVED_RATIO
company: AAA
periods: [2014, 2015]
target_metrics:
  - Lợi nhuận sau thuế
  - Vốn chủ sở hữu
derived_target: ROE
tables_needed:
  - KQKD
  - CDKT
model_tier: STRONG
verify_profile: STRICT
```

The earlier period is needed for average equity in the example design.

## 4.3 Retrieval Layer

### Responsibility

Retrieve supporting evidence required by the `Plan` from both tables and narrative text.

Logical subcomponents:

```text
Retrieval Query Builder
        |
   +----+----+
   |         |
 BM25      BGE-M3
   |         |
   |     Vector Search
   |         |
   +----+----+
        |
      RRF
        |
BGE-reranker-v2-m3
        |
 TABLE/TEXT candidates
```

### Hybrid evidence policy

The plan may request:

- `TABLE`,
- `TEXT`,
- both (`HYBRID`).

For hybrid questions, do not force all facts into one representation. Preserve source boundaries and links between table evidence and associated narrative.

### Retrieval priorities

Guard against:

- wrong company/report,
- wrong period,
- wrong consolidated/standalone scope,
- wrong table,
- wrong paragraph/text span,
- wrong hierarchical row/header path,
- insufficient cross-source evidence,
- omitted scale/unit context.

### Evidence budget

More context is not automatically better. FinQA-style retriever-generator results motivate retrieving compact supporting facts rather than passing the whole document to the program generator.

The exact `top_k` is an evaluated configuration, not a hardcoded constant.

## 4.4 Evidence Builder

### Responsibility

Turn retrieval candidates into the smallest sufficient, traceable evidence bundle for downstream reasoning.

### Proposed Evidence Contract

```yaml
EvidenceItem:
  evidence_id: string
  source_type: TABLE | TEXT

  report_ref: string
  page_ref: string | null
  table_ref: string | null
  associated_table_ref: string | null

  statement_scope: HOP_NHAT | RIENG | null
  period: string | null
  metric: string | null

  row_label: string | null
  column_label: string | null
  row_path: [string]
  column_path: [string]

  text_span: string | null

  raw_value: string | number | null
  normalized_value: number | null

  unit: string | null
  scale: RAW | THOUSAND | MILLION | BILLION | PERCENT | OTHER | null
  scale_source: HEADER | CELL | TEXT | QUESTION | null

  retrieval_score: float | null
  rerank_score: float | null
```

### Constraint

Do not pass a large unstructured top-k dump to the Programmer when a smaller grounded bundle can be built first.

A hybrid bundle may contain both a table cell/row and a narrative span when the calculation requires both.

## 4.5 Scale / Unit Resolver

### Responsibility

Resolve numeric magnitude and units before program execution.

Scale/unit may be stated in:

- a table header,
- a row/column label,
- an associated paragraph,
- the user question.

The resolved value must retain provenance so Verification can audit the decision.

## 4.6 Schema Linking

Map natural-language concepts to actual table structure.

Examples:

```text
"lãi ròng"
     -> canonical metric
     -> actual row/header path
```

Schema linking should preserve hierarchical context when a label alone is ambiguous.

## 4.7 Numeric Masking / De-lexicalization

### Goal

Reduce numeric hallucination and copied constants during code generation.

### Flow

```text
real values
   -> symbolic placeholders
   -> program generation
   -> deterministic value binding
   -> execution
```

Example concept:

```text
[NUM_0], [NUM_1]
```

The LLM generates the symbolic relationship rather than memorizing the literal values.

## 4.8 Complex Table Reasoning Fallback

### Responsibility

Handle cases where one-shot reasoning over a static complex table is unreliable.

Inspired by Chain-of-Table, use a bounded operation pool such as:

- `select_row`,
- `select_column`,
- `add_column`,
- `group_by`,
- `sort_by`.

At each step:

1. inspect the latest intermediate table,
2. select one permitted operation,
3. generate/validate its arguments,
4. execute the operation deterministically,
5. append `(operation, args)` to history,
6. stop at an explicit terminal state or retry budget.

This is an optional targeted mode selected by `Plan.reasoning_mode = TABLE_TRANSFORM`; it is not the default path and is not an unbounded reflection loop.

## 4.9 Programmer / Pandas Coder

### Responsibility

Generate constrained executable logic from:

```text
Plan + Evidence + Schema
```

The Programmer should not independently perform open-ended retrieval.

### Program Contract

The exact code contract must be finalized during implementation.

Required properties:

- use Python/Pandas or a constrained financial reasoning DSL that compiles to Python/Pandas,
- operate only over provided grounded evidence,
- preserve row/column/evidence identifiers,
- avoid intermediate rounding where not required,
- expose intermediate references for multi-step reasoning,
- assign the final numerical result to a known output variable,
- validate grammar/schema before sandbox execution.

FinQA-style evaluation suggests reporting both program/trace correctness and final execution correctness.

### Failure Types

- syntax error,
- missing key/column,
- wrong evidence reference,
- invalid numeric parse,
- unsupported operation.

These failures should be classified for targeted retry.

## 4.10 Sandboxed Python Executor

### Responsibility

Execute generated programs safely and deterministically.

### Security Boundary

Language-level restrictions are a first filter, not the primary isolation boundary.

Production execution should support process/OS isolation such as:

- container isolation,
- stronger syscall isolation (e.g. gVisor-style),
- microVM isolation for higher security requirements.

The final deployment choice is an infrastructure decision.

### Proposed Execution Result

```yaml
ExecutionResult:
  success: boolean
  result: number | string | null
  error_type: string | null
  error_message: string | null
  execution_ms: integer
```

## 4.11 Verification Layer

### Responsibility

Decide whether the grounded evidence and computed result are acceptable.

Logical checks:

### Grounding verification

- company,
- report,
- statement scope,
- period,
- table,
- row/column/header path.

### Numeric / scale / unit verification

- Vietnamese number parsing,
- sign conventions,
- scale resolution,
- unit conversion,
- scale/unit provenance,
- missing/NaN handling,
- divide-by-zero,
- magnitude sanity.

### Financial-logic verification

- correct formula family,
- required periods present,
- accounting consistency checks when applicable.

Independent verification checks may be parallelized.

## 4.12 Answer Builder

### Responsibility

Build the user-facing answer from verified output.

It should not silently change the computed value or invent missing evidence.

The exact answer schema/citation format is not specified in the provided sources and must be defined by the implementation/product layer.

## 4.13 Optional Experience Memory

Experience memory is deferred until the non-memory baseline is stable.

If implemented:

```yaml
MemoryEntry:
  source_question: string
  strategy_findings: [string]
  caution_rules: [string]
  embedding_ref: string
  provenance: string
```

Inference policy:

1. embed the current question + relevant context,
2. retrieve candidate memory entries,
3. discard entries below a calibrated similarity threshold,
4. deduplicate,
5. cap retrieval depth,
6. inject only the remaining strategies/guards,
7. otherwise fall back to the base policy.

Memory cannot override current evidence or deterministic verification.

Held-out evaluation/test must not write new memory entries.

## 5. Routing Policy

Current policy from the Supervisor design:

| Question type | Model tier | Verify profile | Retry budget |
|---|---|---|---|
| `LOOKUP` | `CHEAP` | `LIGHT` | 1 |
| `DERIVED_RATIO` | `STRONG` | `STRICT` | 2 |
| `MULTI_PERIOD` | `STRONG` | `STRICT` | 2 |
| `AGGREGATE` | `STRONG` | `STRICT` | 2 |

A cheap-tier failure should escalate to the strong tier before final abstain where the routing policy allows it.

## 6. Retry / Failure Routing

```text
Verification failure
        |
        v
  classify failure
        |
  +-----+----------------+----------------+----------------+
  |                      |                |                |
missing evidence     wrong program     cheap model     unsupported/
  |                      |                |            unresolved
  v                      v                v                v
Retrieval retry     Programmer retry    STRONG         Abstain/
                                      escalation       Clarify
```

No unbounded retry loops.

## 7. Parallelization

The source materials explicitly distinguish real data dependencies from steps that can run independently.

### Can be parallelized

- retrieval of independent tables in a multi-hop question,
- independent verifier checks.

### Must remain sequential

- Programmer waits for evidence,
- Sandbox waits for generated program.

## 8. Model Specialization

Logical roles have different model requirements:

- NLU / Supervisor: language understanding + planning,
- Retriever: retrieval/ranking models plus optional small LLM,
- Programmer: code-generation capability,
- Critic/Verifier: small/fast model only where deterministic checks are insufficient.

Do not assume every component requires a large LLM.

## 9. Observability

Every request should eventually carry a traceable execution record containing at least:

- NLU output,
- Plan,
- retrieval candidates/scores,
- selected Evidence,
- generated program/version,
- execution result,
- verification outcomes,
- retry/escalation decisions,
- final status.

This observability contract is proposed for debugging/evaluation; exact logging/storage is not specified in the sources.

## 10. Evaluation Boundaries

Evaluate stages separately so failures are attributable.

### NLU / Planning

- field-level semantic accuracy,
- question type,
- required evidence source (`TABLE/TEXT/HYBRID`),
- required periods/tables,
- abstain/clarification,
- routing/confidence calibration.

### Retrieval

- table Recall@K,
- paragraph/text Recall@K,
- hybrid evidence coverage,
- reranker quality,
- scale/unit evidence recall.

### Reasoning

Run both:

- oracle/gold-evidence reasoning,
- retrieved-evidence reasoning.

Report:

- execution accuracy,
- program/trace accuracy,
- final answer accuracy,
- breakdown by reasoning steps (`1`, `2`, `3+`),
- breakdown by evidence source,
- complex-table fallback performance.

### Numerical correctness

- scale accuracy,
- unit accuracy,
- formula correctness,
- grounding correctness.

### Production

- p50 / p95 / p99 latency,
- cost/query,
- tokens/query,
- cache hit rate,
- retry/escalation rate,
- coordination-failure rate,
- throughput under target load.

Execution accuracy and program accuracy are complementary: execution can be accidentally correct; exact program matching can reject semantically equivalent programs.

## 11. Proposed Code Layout

```text
src/
├── indexing/
│   ├── document_parser.py
│   ├── table_normalizer.py
│   ├── paragraph_extractor.py
│   ├── table_text_linker.py
│   ├── representation_builder.py
│   ├── bm25_indexer.py
│   ├── embedding_indexer.py
│   └── metadata_indexer.py
├── understanding/
│   ├── pipeline.py
│   ├── company_resolver.py
│   ├── temporal_parser.py
│   ├── metric_resolver.py
│   ├── ambiguity.py
│   └── schemas.py
├── supervisor/
│   ├── classifier.py
│   ├── planner.py
│   ├── router.py
│   └── schemas.py
├── retrieval/
│   ├── query_builder.py
│   ├── sparse/
│   │   └── bm25.py
│   ├── dense/
│   │   ├── embedder.py
│   │   └── vector_store.py
│   ├── fusion/
│   │   └── rrf.py
│   ├── reranker/
│   │   └── bge_reranker.py
│   ├── report_retriever.py
│   ├── table_retriever.py
│   ├── evidence_locator.py
│   └── schemas.py
├── evidence/
│   ├── builder.py
│   ├── scale_unit_resolver.py
│   ├── schema_linker.py
│   ├── numeric_masking.py
│   └── schemas.py
├── table_reasoning/
│   ├── planner.py
│   ├── operations.py
│   ├── executor.py
│   └── schemas.py
├── programmer/
│   ├── agent.py
│   ├── prompt.py
│   └── schemas.py
├── sandbox/
│   ├── executor.py
│   ├── policy.py
│   └── schemas.py
├── verification/
│   ├── grounding.py
│   ├── numerical.py
│   ├── scale_unit.py
│   ├── financial.py
│   └── schemas.py
├── memory/                 # optional post-v1
│   ├── bank.py
│   ├── retriever.py
│   ├── distiller.py
│   └── schemas.py
├── answer/
│   └── builder.py
└── orchestration/
    ├── graph.py
    └── state.py
```

This folder structure is a proposed implementation mapping, not a requirement from the source materials.

## 12. Source Basis

Architecture is grounded in:

- Supervisor NLU -> classification -> retrieval planning -> model/verify routing,
- deterministic Plan contract,
- TableRAG + hierarchical index,
- numeric masking + schema linking,
- program-aided/code-based reasoning,
- sandboxed Python execution,
- dual/multi-layer verification,
- role-specialized multi-model architecture,
- latency guidance to parallelize only truly independent work,
- retrieval-heavy benchmark failure analysis.


### Additional v3 source basis

- TAT-QA (arXiv:2105.07624): hybrid table/text evidence and scale prediction.
- FinQA (arXiv:2109.00122): retriever-generator, supporting facts, executable reasoning programs, program/execution metrics.
- PAL (arXiv:2211.10435): delegate execution to Python.
- Chain-of-Table (arXiv:2401.04398): dynamic bounded table-operation chains.
- Multi-Agent Financial Document Processing benchmark (arXiv:2603.22651): hierarchical orchestration and production cost/latency/retry/caching tradeoffs.
- FinAcumen (arXiv:2606.17642): selective experience memory and deterministic financial tools.

The 2603.22651 benchmark is a financial-document extraction study rather than the exact QA task, so its quantitative results are treated as orchestration guidance.
