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

## ADR-020 — Use exact pre-filtered FAISS FlatIP search in M3 v1

**Status:** Accepted

### Decision

TASK-033 uses TASK-030's exact SQLite metadata constraints to select eligible
vector IDs before each FAISS search.  For each validated shard, it builds a
temporary `IndexIDMap2(IndexFlatIP(1024))` containing only those eligible
normalized vectors, then takes deterministic shard and global top-k results.

### Rationale

The persisted FlatIP index does not apply report metadata constraints natively.
Pre-filtering preserves the approved company, year, and scope semantics without
oversampling, silently returning fewer results, or admitting ineligible
vectors.

### Consequences

Filtered search is exact but has a bounded per-shard temporary-vector cost.
All shards must validate and be searched; no partial or best-effort serving is
allowed.  Production full-corpus latency validation remains
`GPU_PRODUCTION_VALIDATION_PENDING`.

---

## ADR-021 — Preserve M2A provenance in an immutable additive sidecar

**Status:** Accepted

### Decision

Keep the canonical materialized M2 corpus immutable and publish complete
`ScaleUnitHint` and `TableTextLink` objects in a separate
`m2-provenance-sidecar-v1` SQLite artifact.  Bind the sidecar to one exact M2
corpus identity and reject publication unless all representation hint IDs and
listed source links resolve.  Expose read-only indexed provenance lookup APIs.

Apply TABLE/TEXT source eligibility as a separate, non-empty pre-top-k filter
in both BM25 SQLite FTS and vector SQLite ID selection.  It is independent of
TASK-030 report metadata eligibility and defaults to the request's evidence
sources.

### Consequences

Scale/unit and table--narrative provenance remain available for later evidence
work without mutating, rebuilding, or duplicating the canonical M2 corpus.
Source-specific retrieval can safely issue TABLE and TEXT searches separately;
no backend may widen an empty or restrictive source filter.

---

## ADR-022 — Use deterministic RRF and a pinned cross-encoder before evidence building

**Status:** Accepted

### Decision

Fuse BM25 and vector ranked lists with unweighted RRF (`k=60`) only after each
backend's exact metadata and source-type filters.  Then rerank the fused list
with raw first-logit scores from the pinned `BAAI/bge-reranker-v2-m3` revision.
Reject invalid ranks, incompatible duplicate provenance, and pairs over 8,192
tokens instead of normalizing, truncating, or partially reranking.

Expose deterministic TABLE multi-subqueries and a TEXT narrative view as
retrieval outputs, not EvidenceItem objects.  Use the immutable provenance
sidecar only for persisted associations and scale/unit hints.  Preserve
conflicts and partial retrieval state for the later evidence and verification
stages.

### Consequences

Batch 3 can combine lexical and dense retrieval without introducing an LLM or
weakening grounding.  TASK-038, TASK-039, and TASK-03E remain responsible for
cell location, evidence construction, and hybrid completeness rather than
being bypassed by retrieval orchestration.

---

## ADR-023 — Preserve parsed numeric values as canonical decimal strings

**Status:** Accepted

### Decision

`EvidenceItem.normalized_value` is `CanonicalDecimal | null`: the exact
validated decimal string produced by `NumericParseResult.decimal_value`.
Evidence and retrieval never cast it to `int`, `float`, or a percentage ratio.
The later arithmetic boundary may explicitly construct `decimal.Decimal` from
that string inside the approved execution path.

### Consequences

JSON evidence remains lossless for large and fractional financial values.
Scale/unit resolution and percent-literal interpretation remain deferred; no
binary-float conversion becomes part of the canonical evidence contract.

---

## ADR-024 — Evidence is source-located before it is considered complete

**Status:** Accepted

### Decision

Locate TABLE evidence only from source cells covered by the retrieved M2 chunk,
then build EvidenceItem from the exact source-backed cell or paragraph.
Completeness checks source types and exact metric/period grounding separately;
they do not calculate values, resolve scales, or fabricate evidence.

### Consequences

Retrieval results cannot widen into arbitrary parent-table cells. Later schema
linking, scale resolution, verification, and calculation receive a traceable
evidence boundary rather than an inferred answer.

---

## ADR-025 — Evaluate retrieval boundaries and log typed earliest failures

**Status:** Accepted

### Decision

M3 evaluation records BM25, vector, RRF, reranker, multi-table, evidence
grounding, and TASK-03E hybrid-completeness results separately. Fixture cases
use deterministic retrieval-contract IDs and run without CUDA; full-corpus
vector evaluation remains `GPU_PRODUCTION_VALIDATION_PENDING`. Failures use a
typed event with deterministic identity, preserved underlying code, optional
artifact/model fingerprints, and no stack trace payload. Attribution selects
the earliest boundary that cannot recover the required evidence.

### Consequences

Aggregate metrics cannot hide a downstream reranker/evidence failure or an
upstream recall miss. The taxonomy is observational only: it adds no retry or
retrieval behavior. Thresholds remain TBD until full-corpus evaluation data is
available.

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


## ADR-029 — Use deterministic, registry-bounded M4 v1 planning

**Status:** Accepted

### Decision

M4 Batch 1 consumes `QueryUnderstanding + PlanningGate` without an LLM and
returns `SupervisorResult`: either one executable Plan or a typed abstention.
Plans carry stable ordered `RetrievalRequirement` values. The only initial
formula definitions are `GROWTH_RATE` and `AVERAGE`; the only initial metric
table mapping is `LNST -> INCOME_STATEMENT`. Requested period kinds must be
homogeneous, dates are never inferred, and ratios abstain until a
metric-specific formula is approved.

### Consequences

The Supervisor cannot fabricate missing identity, table, period, metric, or
formula information. It does not retrieve or calculate. Explicit Plan
requirements are documented for later M3 integration, but M3 behavior does not
change in this batch. `TABLE_TRANSFORM` remains disabled.

---

## ADR-030 — Route M4 v1 plans from deterministic structure only

**Status:** Accepted

### Decision

Route reasoning mode from question type and registered formula metadata, then
route model tier from reasoning mode (`DIRECT -> CHEAP`, `PROGRAM -> STRONG`).
Use `LIGHT` verification only for a formula-free direct lookup with exactly one
required retrieval requirement; use `STRICT` otherwise.

Select requirement sources by formula metadata, then explicit requirement
source types, then the approved evidence policy. The v1 numeric policy and both
registered formulas require only `TABLE`. Derive final `Plan.evidence_sources`
exactly from the ordered requirements. Keep `TABLE_TRANSFORM` disabled.

### Consequences

- Formula-free multi-period comparison is `DIRECT + CHEAP + STRICT`.
- Growth, average, and future registered ratios are `PROGRAM + STRONG + STRICT`
  unless an approved formula contract changes their reasoning mode.
- Confidence, retrieval scores/results, table size, token count, free-form
  wording, and LLM planning cannot affect these routes.
- A future formula-level model requirement may only upgrade to `STRONG`; it
  cannot downgrade a `STRONG` route.
- Batch 1 retry budgets and scale-resolution flag behavior remain unchanged.
- TASK-047 evaluates these routes independently, and retrieval behavior remains
  unchanged.

---

## ADR-031 — Evaluate Supervisor output by exact structure before retrieval

**Status:** Accepted

### Decision

Evaluate the deterministic Supervisor directly from fixture
`QueryUnderstanding + PlanningGate` inputs. Compare the 12 approved planning
fields exactly and in canonical order. Ordered lists are order-sensitive, and
retrieval requirements compare all payload fields while ignoring only their
derived `requirement_id`.

An abstention is correct only when the actual result abstains with a null plan
and the exact expected typed reason. Report exact-match rate, per-field
accuracy, and abstain precision/recall without a production threshold.

### Consequences

- Supervisor quality is measured independently from M3 retrieval and later
  execution/answer stages.
- The 14-case v1 fixture is deterministic, CPU-only, and uses no backend or LLM.
- Safety tests ensure planning cannot invent company, ticker, metric, period,
  scope, table mapping, or formula.
- A successful Plan is checked at the M3 query-builder boundary; an abstaining
  result cannot cross because its plan is null.
- M4 implementation is complete. Production-scale M3 vector validation remains
  `GPU_PRODUCTION_VALIDATION_PENDING`.

---

## ADR-032 — Resolve scale and link schema only inside grounded evidence

**Status:** Accepted

### Decision

M5 Batch 1 runs deterministic scale/unit resolution before schema linking. The
scale resolver keeps document source scale/unit separate from the user's
requested output scale/unit, applies the approved direct-cell through linked
precedence, retains every considered hint ID, and performs no numeric
conversion. The schema linker consumes ordered Supervisor retrieval
requirements and structured M3 `CellLocation` paths, using only the existing
metric registry and exact canonical periods.

Both stages operate exclusively on supplied, already-retrieved M3 evidence.
They do not widen a candidate into other table cells, flatten structured paths,
mutate `EvidenceItem`, or use fuzzy/semantic/LLM matching.

### Consequences

- `CanonicalDecimal` and all M2/M3 raw provenance remain unchanged.
- Missing scale is unresolved rather than defaulted to `RAW`.
- Conflicts at the highest available scale-hint precedence are ambiguous.
- One exact metric/period location resolves; multiple are ambiguous; none is
  unresolved.
- Numeric masking and deterministic binding are specified by ADR-033 below.
  TASK-055 regression coverage is complete; ADR-034 later unblocks TASK-053
  without marking it complete.

---

## ADR-033 — Separate programmer masking from execution value binding

**Status:** Accepted

### Decision

M5 Batch 2 exposes only `MaskedEvidenceBundle` to the future Programmer. Each
placeholder is deterministically derived from `evidence_id` and the fixed
`m5-numeric-masking-v1` contract version using the full SHA-256. Masking accepts
only resolved schema links, exact M3 evidence/location chains, canonical numeric
values, and—when the Plan requires it—resolved scale/unit results.

Keep numeric values in a separate execution-only `BindingMap`. The binder maps
each exact placeholder back to the original `EvidenceItem.normalized_value`,
copies scale/unit resolution metadata, and accepts no program or code source.
It performs neither float conversion nor scale/unit conversion.

### Consequences

- Programmer-facing evidence cannot leak `raw_value` or `normalized_value`.
- `CanonicalDecimal` remains a canonical JSON string through binding.
- Missing, ambiguous, unresolved, conflicting, or non-numeric inputs fail with
  typed masking/binding errors rather than guesses.
- The original EvidenceItem and its M2/M3 provenance remain unchanged.
- TASK-055 scale/unit regression is complete. ADR-034 later unblocks TASK-053
  anti-hardcode work; this M5 decision itself introduces no M6 Programmer or
  Sandbox behavior.

---

## ADR-034 — Use a value-free symbolic M6 Program DSL and versioned formula implementations

**Status:** Accepted

### Decision

M6 Batch 1 defines `ProgrammerInput` as exactly `Plan`,
`MaskedEvidenceBundle`, and the FormulaRegistry fingerprint. `BindingMap`
remains execution-only and cannot enter this boundary. The canonical
`m6-program-v1` Program contains only symbolic inputs and the three allowlisted
operations `IDENTITY`, `COLLECT`, and `APPLY_REGISTERED_FORMULA`.

Validate exact schema, Plan consistency, registered formula use and ordered
arity, placeholder membership, required-evidence dependency coverage, unique
IDs, topological references, valid output, deterministic identity, and
canonical serialization before later execution. Reject literals and arbitrary
code/calls/imports/attributes/files/network/eval/exec with typed failures.

Keep one existing FormulaRegistry and add matching versioned
`m6-formula-implementation-v1` metadata. `GROWTH_RATE` is ordered
`previous,current` with percent-growth semantics and approved constant `100`;
`AVERAGE` is a variadic arithmetic mean with at least two values. No ratio
formula is added.

### Consequences

- Program IDs depend only on symbolic Program content and are independent of
  execution binding values.
- Numeric constants cannot enter Program; approved constants exist only in a
  registered `FormulaImplementation`.
- `CanonicalDecimal` remains a string; M6 performs no arithmetic, parsing,
  conversion, model inference, or execution.
- At the Batch 1 boundary, TASK-053 was unblocked but not completed; ADR-035
  records its later Batch 2 completion.
- At the Batch 1 boundary, TASK-061 through TASK-067 and TASK-069 remained
  deferred; ADR-035 completes only TASK-061 through TASK-064.

---

## ADR-035 — Generate only the four approved M6 v1 symbolic Program shapes

**Status:** Accepted

### Decision

M6 Batch 2 uses one deterministic Programmer function whose only input is
`ProgrammerInput`. Revalidate the nested Plan and MaskedEvidenceBundle, map one
masked item to every required retrieval requirement, and construct Program
inputs in Plan requirement order.

Generate only:

- `LOOKUP -> IDENTITY`,
- formula-free `MULTI_PERIOD -> COLLECT`,
- `GROWTH_RATE -> APPLY_REGISTERED_FORMULA`,
- `AVERAGE -> APPLY_REGISTERED_FORMULA`.

Reject `DERIVED_RATIO` until a ratio formula is registered; never infer or
author a formula. Reject missing/ambiguous required evidence, unknown
requirements, invalid placeholders, and unsupported formulas with typed
Programmer failures. Pass every constructed Program through ADR-034/TASK-068
validation before returning `GENERATED`, and propagate validator failure codes
through `ProgrammerResult.REJECTED`.

### Consequences

- Masked bundle arrival order cannot change operand order or Program identity.
- BindingMap, raw/normalized values, numeric literals, arbitrary code, and
  model inference are absent from generation.
- TASK-053 is complete against real generated Programs, including value,
  placeholder, evidence-coverage, formula-registry, and constant mutations.
- TASK-065, TASK-066, TASK-067, and TASK-069 remain pending and no execution is
  introduced.

---

## ADR-036 — Reuse M2 parsing and evaluate M6 structure without execution

**Status:** Accepted

### Decision

M6 Batch 3 adds no numeric grammar. Adapt the existing M2
`NumericParseResult` into `CanonicalDecimal` and preserve every unsuccessful M2
status as the typed boundary failure. Convert only approved magnitude scales
with `decimal.Decimal`, serialize back to `CanonicalDecimal`, treat percent as
a separate identity-only semantic scale, and reject unresolved scales or
different units rather than guessing.

Evaluate Programs by exact equality first and an optional deterministic
`TraceEquivalenceHook` second. The approved normalized hook ignores only
Program and graph-node IDs while preserving ordered grounded inputs,
operations, formulas, references, and output structure. Package trace results
with the existing `ReasoningEvaluationResult`; execution and answer scores
remain independently supplied and are not computed in this batch.

### Consequences

- M2 remains the single Vietnamese numeric grammar and parser.
- Binary float, rounding, FX conversion, formula execution, sandbox execution,
  and model inference remain outside Batch 3.
- Scale conversion is deterministic shared infrastructure and does not mutate
  `EvidenceItem`, `ValueBinding`, or `BindingMap`.
- TASK-065, TASK-066, and TASK-069 are complete.
- TASK-067 remains `BLOCKED_BY_M7_EXECUTION_BOUNDARY`; no oracle execution
  scoring is invented before M7.

---

## ADR-037 — Admit only validated DSL and use Decimal-safe M7 execution contracts

**Status:** Accepted

### Decision

M7 Batch 1 replaces the old float-permitting `ExecutionResult` with versioned
`SandboxExecutionRequest`, `ExecutionDatum`, `ExecutionOutput`,
`ExecutionFailure`, and `ExecutionResult` contracts. Execution values are
`CanonicalDecimal` strings with scale/unit metadata; binary float is forbidden.
Success/failure and scalar/ordered-output shapes are exact invariants.

Before future dispatch, TASK-070 reruns TASK-068 against the supplied
`ProgrammerInput`, accepts only the three `m6-program-v1` operations, enforces
bounded structural size/depth, and requires an exact bijection across Program
inputs, masked evidence, and execution-only `BindingMap`. Existing M6 failure
codes are retained under typed execution failure stages.

Use a fresh Decimal context with precision 50, `ROUND_HALF_EVEN`, trapped
`DivisionByZero`, `InvalidOperation`, and `Overflow`, and allowed
`Inexact`/`Rounded`. Serialize finite results in fixed-point form without
unnecessary fractional zeroes or negative zero. Never convert through float or
quantize currency in M7.

TASK-071 owns the trusted DSL interpreter and isolated worker. It must lower
validated Programs to internal instructions only; it may not compile or run
model-generated Python source. Formula and scale/unit runtime semantics are
fixed in `docs/ARCHITECTURE.md`, but no formula execution is implemented in
Batch 1.

### Consequences

- TASK-070 and TASK-073 are complete.
- TASK-071, TASK-072, TASK-074, and TASK-075 remain deferred.
- Static policy remains a pre-filter and is not the production isolation
  boundary.
- TASK-067 remains `BLOCKED_BY_M7_EXECUTION_BOUNDARY` until the TASK-071
  interpreter/process boundary exists.
- `GPU_PRODUCTION_VALIDATION_PENDING` is unchanged.

---

## ADR-038 — Interpret the M6 DSL in one fixed isolated worker process

**Status:** Accepted

### Decision

M7 Batch 2 executes a `SandboxExecutionRequest` only after parent-side TASK-070
validation. The parent sends bounded canonical JSON to one fixed Python module
without a shell and with a minimal environment. The worker independently reruns
TASK-070/TASK-068, binds the execution-only `BindingMap`, and interprets Program
steps in order using Decimal arithmetic.

The trusted interpreter implements only `IDENTITY`, `COLLECT`, and
`APPLY_REGISTERED_FORMULA`. Formula dispatch is limited to the registered
`GROWTH_RATE` and `AVERAGE` implementation kinds. It does not compile or execute
Program text, arbitrary Python, imports, callables, filesystem/network actions,
subprocesses, or model output.

Parent and worker exchange canonical UTF-8 JSON bounded to 1,048,576 bytes in
each direction; pickle is not permitted. Worker crashes, malformed protocol,
and invalid or mismatched results become typed `INFRASTRUCTURE` failures without
including binding values or worker stderr.

### Consequences

- TASK-071 is complete and TASK-067 is now implementable, but TASK-067 remains
  unimplemented.
- Process separation is not the final production sandbox boundary.
- Runtime resource enforcement remains TASK-072, abuse testing remains
  TASK-074, and final sandbox-technology selection remains TASK-075.
- `GPU_PRODUCTION_VALIDATION_PENDING` is unchanged.

---

## Deferred Decisions

The provided source materials do not fully specify the following. Do not silently treat them as final:

- exact application framework,
- exact repository package structure,
- exact database/vector-store choice,
- exact model provider,
- exact answer/citation JSON schema,
- authoritative company-alias dataset and storage mechanism,
- authoritative financial-metric vocabulary, synonym source, and storage mechanism,
- requested currency/unit vocabulary beyond TASK-017 v1 scale expressions,
- exact production accuracy/latency/cost thresholds,
- exact sandbox technology,
- exact deployment topology,
- handling of `aggregated`, generic, explanatory, and unlabeled reports through statement-scope mapping or abstention behavior.

Resolve each through a future ADR when implementation constraints are known.
