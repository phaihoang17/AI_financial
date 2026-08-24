# DECISIONS.md — Architecture Decision Record

This file records the major decisions that Codex and human contributors should preserve unless a new decision explicitly supersedes them.

---

## ADR-001 — Use an explicit NLU / Understanding layer before the Supervisor

**Status:** Accepted

### Decision

Separate language understanding from execution planning.

```text
Question -> NLU -> QueryUnderstanding -> Supervisor -> Plan
```

### Rationale

The provided Supervisor design already starts with NLU slot extraction before classification and planning.
Separating it as a logical layer makes failures attributable:

- NLU error: misunderstood company/period/metric/scope,
- planning error: understood request correctly but planned wrong evidence/workflow.

### Consequences

NLU must have a structured, validated contract.
The Supervisor should not independently redo all language normalization.

---

## ADR-002 — Use hierarchical Supervisor-Worker orchestration, not a free-form agent mesh

**Status:** Accepted

### Decision

Use one clear orchestration hierarchy with structured contracts between stages.

### Rationale

The Supervisor design recommends a simple layered supervisor-worker architecture and warns that coordination failures grow with orchestration complexity.

### Consequences

Do not introduce peer-to-peer agent negotiation unless a measured requirement justifies it.
Workers should exchange structured data, not open-ended chat transcripts.

---

## ADR-003 — Supervisor plans; it does not calculate

**Status:** Accepted

### Decision

The Supervisor may classify, route, plan evidence, choose verification profile, and abstain.
It must not calculate the final financial answer.

### Rationale

This preserves role separation and allows the system to audit planning independently from evidence and computation.

### Consequences

The Supervisor output is a `Plan` contract.
Numerical computation happens downstream.

---

## ADR-004 — Treat retrieval/evidence quality as the primary engineering bottleneck

**Status:** Accepted

### Decision

Prioritize report/table/evidence retrieval and grounding before sophisticated reasoning optimizations.

### Rationale

The provided benchmark material shows a large accuracy drop from oracle evidence to retrieved evidence.
The failure analysis attributes the majority of observed errors to numerical extraction and insufficient evidence rather than arithmetic/formula calculation.

### Consequences

Roadmap ordering should put retrieval/evidence evaluation before model-routing optimization.
End-to-end metrics must be decomposable into retrieval vs. reasoning failures.

---

## ADR-005 — Preserve hierarchical table structure and metadata

**Status:** Accepted

### Decision

Do not naïvely flatten financial tables if flattening destroys header paths, parent-child relationships, or merged-cell semantics.

### Rationale

The provided materials emphasize multi-level headers, merged cells, hierarchical path matching, and cross-table linking as central challenges.

### Consequences

Table normalization and indexing must retain enough metadata to reconstruct row/column/header paths.

---

## ADR-006 — Use Text-to-Pandas / program-aided reasoning for numerical computation

**Status:** Accepted

### Decision

Prefer generated Python/Pandas programs for grounded numerical reasoning where a calculation is required.

### Rationale

The provided materials position financial statements as independent CSV/DataFrames with heterogeneous schema and highlight program-aided reasoning as a way to decouple language reasoning from exact arithmetic.

### Consequences

The Programmer generates executable logic.
Python executes arithmetic.
Free-form LLM arithmetic is not the production source of truth when an executable path exists.

---

## ADR-007 — Use schema linking and numeric masking before code execution where applicable

**Status:** Accepted

### Decision

Use schema linking to bind language concepts to table structure and use symbolic numeric placeholders/deterministic binding to reduce hallucinated constants.

### Rationale

The Stage 2 material explicitly proposes numeric masking/de-lexicalization: mask literal values, generate symbolic programs, bind real values deterministically at runtime.

### Consequences

Generated code should reference grounded symbols/evidence rather than copy arbitrary numeric constants from prompts.

---

## ADR-008 — Execute model-generated code inside an OS/process sandbox

**Status:** Accepted

### Decision

Never execute generated Python directly inside the main application process.

### Rationale

The provided security material states that language-level restrictions alone are not a reliable security boundary because allowed libraries can expose dependency paths to dangerous runtime capabilities.

### Consequences

Production execution must support process/OS isolation.
The exact choice among container, gVisor-like isolation, or microVM is deferred to infrastructure constraints.

---

## ADR-009 — Verification is a required stage, not optional post-processing

**Status:** Accepted

### Decision

Every candidate result must pass the applicable verification profile before final answer generation.

### Rationale

The reference architecture ends with dual verification and the Supervisor explicitly assigns `LIGHT` or `STRICT` verification profiles.

### Consequences

A successful program execution alone is not enough.
Verification failures must route to targeted retry, escalation, or abstain.

---

## ADR-010 — Use two-tier model routing with escalation

**Status:** Accepted

### Decision

Use a cheap tier for simple lookup-style requests and a strong tier for derived, multi-period, aggregate, or cross-table work.
Escalate after a failed cheap-tier verification before final abstain when allowed.

### Rationale

This policy is specified in the Supervisor routing document.

### Consequences

Model routing is based on structured question complexity, not ad hoc prompt decisions.
Provider/model names must remain configurable.

---

## ADR-011 — Prefer deterministic workers over autonomous agents when no LLM reasoning is needed

**Status:** Accepted

### Decision

Treat `agent` as a role specialization concept, not a requirement that every stage be an autonomous LLM.

Likely LLM-heavy roles:

- Understanding (partially),
- Supervisor/Planner,
- Programmer,
- optional Critic.

Likely deterministic/tool-heavy roles:

- entity dictionaries/parsers,
- retrieval engines/rerankers,
- evidence builder,
- numeric binding,
- sandbox runtime,
- most validation checks.

### Rationale

The source materials explicitly say different roles need different model capabilities and do not require one large model for every agent.
A simpler hierarchy also reduces coordination cost and latency.

### Consequences

Do not add an LLM call to a deterministic stage without measured benefit.

---

## ADR-012 — Targeted retry instead of unbounded reflection loops

**Status:** Accepted

### Decision

Retry based on failure type.

```text
missing evidence -> retrieval
program error -> programmer
cheap-tier verify failure -> strong tier
unsupported/unresolved -> abstain/clarify
```

### Rationale

The Supervisor design defines retry budgets and escalation behavior, while the latency material warns about cumulative sequential-agent latency.

### Consequences

Every retry path must have a bounded budget and a reason code.

---

## ADR-013 — Separate oracle-evidence reasoning evaluation from retrieved-evidence E2E evaluation

**Status:** Accepted

### Decision

Maintain at least two reasoning evaluation modes:

1. oracle/gold evidence supplied,
2. evidence produced by the retrieval pipeline.

### Rationale

The provided benchmark shows materially different accuracy between these settings.
Without separation, retrieval failure can be misdiagnosed as reasoning failure.

### Consequences

Evaluation tooling should support failure attribution by stage.

---


---

## ADR-014 — Use hybrid retrieval: BM25 + BGE-M3 + RRF + BGE reranker

**Status:** Accepted

### Decision

Use:

```text
Query
  |
  +--> BM25
  |
  +--> BGE-M3 -> Vector Search
          |
          v
       Fusion/RRF
          |
          v
BGE-reranker-v2-m3
          |
          v
      Evidence
```

### Rationale

Financial retrieval benefits from both exact lexical matching and semantic similarity.
The Supervisor material explicitly proposes BGE-M3 and BGE-reranker-v2-m3 for retrieval.

### Consequences

Maintain sparse and dense indexes, deterministic fusion, reranking, and stage-level retrieval metrics.

---

## ADR-015 — Separate offline indexing from online query serving

**Status:** Accepted

### Decision

Create an offline path for table normalization, hierarchy-aware representation building, BM25 indexing, BGE-M3 embedding generation, and vector indexing.

### Rationale

The embedding model must index documents/tables before online query embeddings can be searched.

### Consequences

Index/model version compatibility becomes part of deployment and reproducibility.

---

## ADR-016 — NLU and Supervisor may share Qwen3-8B weights but remain separate roles

**Status:** Accepted

### Decision

Use separate prompts and contracts for NLU and Supervisor while allowing both to reuse the same Qwen3-8B serving instance.

### Rationale

This preserves failure attribution while avoiding duplicate model memory.

### Consequences

Shared weights must not blur responsibility boundaries.

---

## ADR-018 — Keep embedding chunking outside M2A

**Status:** Accepted

### Decision

Preserve the M2A `RetrievalRepresentation` contract. Derive M2B
`EmbeddingChunk` values only at the embedding boundary. Tables are chunked
deterministically by logical rows and consecutive anchor-column groups. If an
individual source cell still exceeds the 7,168-token target, fragment only that
cell with the pinned BGE-M3 tokenizer. `CellFragment` retains the source cell ID,
ordered fragment indexes, exact text, and token count.

Fragments are lossless: concatenating them reproduces the complete normalized
cell text. No truncation, semantic splitting, LLM, or modification of M2A
`NormalizedCell`/`RetrievalRepresentation` is permitted.

### Consequences

The complete corpus can be embedded without omitting oversized cells. Chunk IDs
include the fragmentation algorithm version and chunking configuration
fingerprint, separately from the embedding model fingerprint.

---

## ADR-019 — Complete M2B artifacts use immutable FAISS shards and SQLite

**Status:** Accepted

### Decision

Persist all successful M2B embeddings as normalized float32 `IndexFlatIP` FAISS
shards, capped at 100,000 vectors per shard, with SQLite mapping from embedding
ID to chunk ID to the original M2A representation and provenance. Publish only
after every chunk and shard passes validation and SHA-256 checks. Staging is
resumable; published artifacts are immutable. A missing or incompatible shard,
metadata database, model fingerprint, or schema version rejects loading.

The implementation uses a streaming builder: M2 JSONL is validated and hashed
before embedding, batches are committed to SQLite independently, and a
checkpoint is committed for each completed FAISS shard. A resumed build may
reuse only checkpointed shards whose FAISS bytes, IDs, counts, SQLite rows, and
fingerprints validate; an incomplete shard is replayed and its uncheckpointed
SQLite rows are removed first. The current pointer is updated only after the
fully validated immutable build directory is published.

### Consequences

The 8 GiB development Mac is limited to small integration artifacts. Full
corpus builds require an approved higher-memory build machine; M3 owns search.

---

## ADR-017 — Narrative model is optional for v1

**Status:** Accepted

### Decision

Use deterministic answer templates in the initial critical path.
Add Qwen2.5-7B or Gemma 3 12B only when richer narrative is required.

### Rationale

A narrative LLM adds latency/cost but does not fix evidence correctness.

### Consequences

The verified numerical result remains the source of truth.



---

## ADR-018 — Support hybrid table + text evidence

**Status:** Accepted

### Decision

Retrieval and evidence contracts must support `TABLE`, `TEXT`, and `HYBRID` evidence.

### Rationale

TAT-QA and FinQA both include financial questions that rely on table cells, narrative spans, or both. A table-only architecture can therefore miss required evidence even when the correct report has been retrieved.

### Consequences

- index narrative paragraphs as well as tables,
- preserve table-text association metadata,
- evaluate table/text/hybrid retrieval separately.

---

## ADR-019 — Treat scale and unit as first-class grounded data

**Status:** Accepted

### Decision

Resolve and preserve scale/unit before calculation and verify them independently.

### Rationale

TAT-QA identifies answer scale as a distinct finance-specific challenge; scale may be present in table headers or associated text rather than adjacent to the numeric value.

### Consequences

Evidence and verification schemas gain scale/unit provenance fields and a dedicated failure category.

---

## ADR-020 — Evaluate both program correctness and execution correctness

**Status:** Accepted

### Decision

For program-aided numerical reasoning, report both:

- program/trace correctness,
- execution/final-answer correctness.

### Rationale

FinQA explicitly evaluates both. Execution accuracy may be accidentally correct, while strict program matching may reject equivalent programs.

### Consequences

The evaluation harness must retain generated reasoning traces and support normalized/equivalence-aware program checks where practical.

---

## ADR-021 — Use grammar/schema constraints for generated reasoning programs

**Status:** Accepted

### Decision

Validate generated DSL/Python/Pandas structure before sandbox execution.

### Rationale

FinQA constrains generation with a program DSL and grammar masks, while PAL motivates program execution through an external interpreter.

### Consequences

Program generation must have a machine-validated contract rather than arbitrary free-form code.

---

## ADR-022 — Add bounded Chain-of-Table-style reasoning only as a complex-table fallback

**Status:** Accepted

### Decision

Allow a `TABLE_TRANSFORM` reasoning mode with a bounded atomic operation pool and explicit operation history.

### Rationale

Chain-of-Table shows that dynamically transforming a table according to the question can solve cases where static-table program generation struggles.

### Consequences

- not enabled for every query,
- deterministic operation execution,
- hard iteration/retry limits,
- separately evaluated against the simpler baseline.

---

## ADR-023 — Keep hierarchical orchestration as the production default; avoid global reflexive loops

**Status:** Accepted

### Decision

Retain supervisor-worker orchestration as the default and use targeted retry/escalation instead of a global reflexive architecture.

### Rationale

The uploaded 2026 financial-document-processing benchmark reports hierarchical orchestration near the favorable cost/accuracy frontier, while reflexive architectures improve raw accuracy at substantially higher cost/latency and higher coordination complexity.

This study concerns financial document extraction rather than this exact QA task, so it is used as operational guidance rather than direct QA evidence.

### Consequences

Production evaluation must include latency, cost, token efficiency, retry rate, and coordination failures—not accuracy alone.

---

## ADR-024 — Semantic caching is an optimization, not a correctness mechanism

**Status:** Accepted

### Decision

Introduce semantic caching only after a measured correctness baseline, and prefer adaptive caching for stable/standardized inputs.

### Rationale

The financial multi-agent benchmark reports meaningful cost/latency savings with modest quality impact, especially when caching is disabled for non-standard cases.

### Consequences

Cache use requires similarity thresholds, cache-versioning, invalidation rules, and quality monitoring.

---

## ADR-025 — Experience memory must be selective and relevance-gated

**Status:** Deferred for post-v1, design accepted

### Decision

If cross-episode memory is introduced, store:

- successful reusable strategies,
- failure-derived caution/guard rules,

and inject them only when semantic similarity exceeds a calibrated threshold.

### Rationale

FinAcumen reports that irrelevant memory can hurt reasoning and uses selective retrieval plus deterministic fallback.

### Consequences

- memory is optional,
- capped retrieval depth,
- deduplication,
- fallback to base reasoning,
- held-out evaluation is read-only,
- memory never overrides current evidence or deterministic validation.

---

## ADR-026 — Evaluate difficulty by evidence source and reasoning depth

**Status:** Accepted

### Decision

Break down evaluation by:

- table-only,
- text-only,
- hybrid table-text,
- one-step,
- two-step,
- three-or-more-step reasoning,
- scale/unit-sensitive questions.

### Rationale

FinQA reports materially different performance across evidence sources and increasing difficulty with more reasoning steps.

### Consequences

Aggregate accuracy alone is insufficient for release decisions.


## ADR-027 — Use a minimal verification-result failure taxonomy

**Status:** Accepted

### Decision

Use the following failure categories in `VerificationResult`:

- `GROUNDING`,
- `INSUFFICIENT_EVIDENCE`,
- `NUMERIC`,
- `SCALE_UNIT`,
- `FINANCIAL_LOGIC`.

`SCALE_UNIT` remains a dedicated category. Execution and runtime failures belong to `ExecutionResult`, not `VerificationResult`.

### Rationale

The category set covers the existing grounding, evidence, numerical, scale/unit, and financial-logic verification requirements without adding verifier behavior or duplicating execution failures.

### Consequences

- Multiple-failure prioritization is deferred.
- A controlled vocabulary of detailed reason codes is deferred; `failure_reason` remains a required diagnostic string when verification fails.


## ADR-028 — Preserve raw OCR identity and structured hierarchy through M2A

**Status:** Accepted

### Decision

Use the canonical M2A contracts in `docs/ARCHITECTURE.md`. Preserve immutable
raw OCR/HTML, source spans, merged-cell anchors, and structured header paths.
Treat `CAPTION` as scale/unit provenance distinct from general narrative `TEXT`.

For the bundled ViFinQA corpus, ticker and report year are required because all
1,973 report paths provide validated `TICKER/YEAR` segments. These values must be
copied only from path metadata and never inferred from OCR text.

### Consequences

- Every normalized value remains traceable to report/page/table/source cell.
- Ambiguous numeric, hierarchy, and table-text relationships are not guessed.
- M2A representations remain model- and storage-independent.
- `ScaleSource` is `HEADER | CELL | CAPTION | TEXT | QUESTION`; offline M2A never
  emits `QUESTION`.
- Inline HTML captions remain fields of `SourceTable`; `TableTextLink` connects
  only real `SourceTable` and `Paragraph` records and never synthesizes caption
  paragraphs.
- Header inference v1 uses only deterministic top-band, left-prefix, numeric
  data-column, and merged-span structure. Non-unique structures remain
  `UNKNOWN`/`AMBIGUOUS` with empty paths; no financial vocabulary or LLM is
  permitted.
- Offline period labels use only the approved exact standalone grammar and
  normalize to TASK-011 `YYYY`/`YYYY-Qn` values. Context never supplies a
  missing period component.
- Table-text linking v1 is one-to-one, same-page, whitespace-only immediate
  adjacency and emits only `ADJACENT_CONTEXT`. Semantic caption, note, and
  explicit-reference recognition are deferred.
- Scale hint extraction uses only the approved six expressions with exact
  source spans and canonical source-reference mappings. Units remain null;
  conflicting table-associated scales remain separate `AMBIGUOUS` hints.
- Retrieval representation content is fixed-order compact JSON without IDs.
  Structured header paths retain IDs and labels, use row-major first-occurrence
  deduplication, and remain model- and storage-independent.


## Deferred Decisions

The provided source materials do not fully specify the following. Do not silently treat them as final:

- exact application framework,
- exact repository package structure,
- exact database/vector-store choice,
- exact model provider,
- exact generated Program schema,
- exact answer/citation JSON schema,
- authoritative company-alias dataset and storage mechanism,
- authoritative financial-metric vocabulary, synonym source, and storage mechanism,
- requested currency/unit vocabulary beyond TASK-017 v1 scale expressions,
- exact production accuracy/latency/cost thresholds,
- exact sandbox technology,
- exact deployment topology,
- handling of `aggregated`, generic, explanatory, and unlabeled reports through statement-scope mapping or abstention behavior.

Resolve each through a future ADR when implementation constraints are known.
