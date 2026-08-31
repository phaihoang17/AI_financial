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

Use the following failure categories in the verification aggregate contract
(named `VerificationReport` by ADR-041):

- `GROUNDING`,
- `INSUFFICIENT_EVIDENCE`,
- `NUMERIC`,
- `SCALE_UNIT`,
- `FINANCIAL_LOGIC`.

`SCALE_UNIT` remains a dedicated category. Execution and runtime failures belong
to `ExecutionResult`, not `VerificationReport`.

### Rationale

The category set covers the existing grounding, evidence, numerical, scale/unit, and financial-logic verification requirements without adding verifier behavior or duplicating execution failures.

### Consequences

- Multiple-failure prioritization and per-check typed reasons were later fixed by
  ADR-041; this ADR's category set remains unchanged.


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
- At this decision boundary TASK-071, TASK-072, TASK-074, and TASK-075 remained
  deferred; ADR-038, ADR-039, and ADR-055 later completed them.
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

- TASK-071 is complete and made TASK-067 implementable; TASK-067 was later
  completed under ADR-040.
- Process separation is not the final production sandbox boundary.
- Runtime resource enforcement remains TASK-072, abuse testing remains
  TASK-074, and final sandbox-technology selection remained TASK-075 until
  ADR-055.
- `GPU_PRODUCTION_VALIDATION_PENDING` is unchanged.

---

## ADR-039 — Fail closed under one versioned M7 runtime-limits profile

**Status:** Accepted

### Decision

Bind every TASK-071 dispatch to the exact `m7-limits-v1` values documented in
`docs/ARCHITECTURE.md`. The parent owns wall-clock, RSS, request-size, and
response-size enforcement and kills the worker process group on wall or memory
violation. The worker owns CPU, process-count, descriptor, file-size, and core-
file rlimits. Missing RSS monitoring or required Darwin sandbox support fails
closed.

Run the worker in a fresh non-writable working directory with an exact minimal
environment, then clear that environment after startup validation. Deny runtime
filesystem reads/writes, network access, and process spawning through the
trusted worker audit boundary. On Darwin, also deny network, filesystem writes,
and process forks with the host sandbox policy. Never return partial output as a
success after any limit or security violation.

TASK-074 validates these controls through fixed test-only probes and direct
protocol/policy mutations. The probes cannot be selected by the production
execution request and do not add arbitrary Python or a new DSL operation.

### Consequences

- TASK-072 and TASK-074 are complete.
- At this decision boundary TASK-075 remained pending; ADR-055 later selected
  the final production infrastructure.
- Non-Darwin native filesystem/network isolation, RSS-monitor deployment
  behavior, and production concurrency/load calibration remained residual
  TASK-075 inputs; ADR-055 later fixes the native profile and rollout gate.
- TASK-067 was implementable at this boundary and was later completed under
  ADR-040.
- `GPU_PRODUCTION_VALIDATION_PENDING` is unchanged.

---

## ADR-040 — Evaluate M6 reasoning with oracle evidence through the real M7 worker

**Status:** Accepted

### Decision

TASK-067 supplies exact grounded oracle evidence directly to the existing M5
masking and binding functions, then runs the existing M6 generator and M7
isolated executor. Retrieval is outside this evaluation boundary, so a failed
case measures reasoning/program/runtime behavior rather than retrieval quality.
No GPU or LLM participates.

Score execution, trace, and answer independently. Compare successful
`ExecutionOutput` contracts exactly, including `CanonicalDecimal` fixed-point
strings, output kind/order, scale, and unit. Compare Programs using TASK-069:
exact first, then the approved graph-ID-only normalization. For expected
failures, require the exact failure stage/code and prohibit an emitted answer.
Attribute the earliest failure to program generation, validation, binding,
conversion, arithmetic, or sandbox/infrastructure.

The versioned `m6-oracle-reasoning-eval-v1` report intentionally omits worker
elapsed time so repeated fixture and CLI runs serialize identically. Its seven
fixtures cover lookup, direct multi-period comparison, growth, average,
unsupported ratio, division by zero, and scale conversion.

### Consequences

- TASK-067 is complete and the M6 implementation milestone is complete.
- The oracle evaluation says nothing about retrieval accuracy; retrieved-
  evidence end-to-end evaluation remains a separate boundary under ADR-013.
- The evaluator introduces no new DSL form, formula, retrieval path, model
  call, or production execution behavior.
- At this decision boundary TASK-075 remained the only pending M7 deployment
  decision; ADR-055 later completed it.
- `GPU_PRODUCTION_VALIDATION_PENDING` is unchanged.

---

## ADR-041 — Verify exact grounding and hybrid support before later M8 checks

**Status:** Accepted

### Decision

M8 Batch 1 replaces the minimal aggregate verification result with
`VerificationRequest`, per-check `VerificationCheckResult`, and
`VerificationReport`. Preserve every failed check, then select the aggregate
failure by the fixed order `GROUNDING`, `INSUFFICIENT_EVIDENCE`, `NUMERIC`,
`SCALE_UNIT`, `FINANCIAL_LOGIC`.

TASK-080 checks each required TABLE requirement through the exact
`Plan -> SchemaLinkResult -> CellLocation -> EvidenceItem -> source provenance`
chain. TEXT checks require exact paragraph/report provenance and persisted link
IDs where an associated table is used. Verification never performs fuzzy or
semantic matching, widens evidence, or infers absent metadata. Actual table
class is an optional provenance field; if absent, emit
`TABLE_CLASS_UNAVAILABLE` rather than deriving it from the expected metric or
metric-table registry.

TASK-08B recomputes coverage for every required Plan retrieval requirement and
requires agreement with `EvidenceCompletenessResult`. TABLE and TEXT source
types are not interchangeable, exact metric/period requirements reject
structural/unresolved links, and missing requirement IDs remain in failed check
subjects.

If execution failed, Verification returns no report and leaves the existing
typed `ExecutionResult` failure unchanged.

### Consequences

- TASK-080 and TASK-08B are complete.
- Existing immutable candidate/report and paragraph/link provenance is carried
  into the downstream evidence/location boundary; no verifier-derived metadata
  becomes a source of truth.
- TASK-081 through TASK-089 and TASK-08A remain deferred to M8 Batch 2.
- No LLM, retry, retrieval rerun, escalation, or abstention behavior is added.
- `GPU_PRODUCTION_VALIDATION_PENDING` is unchanged.

---

## ADR-042 — Verify numeric shape, approved scale provenance, and registered financial logic independently

**Status:** Accepted

### Decision

M8 Batch 2 extends `VerificationRequest` with the exact `Program`, approved
`ScaleUnitResolution` values, and execution-only `BindingMap`. These are the
existing immutable upstream contracts needed to verify a successful execution;
Verification does not recreate them.

TASK-081 checks Program/result identity, output kind and count, ordered collect
values, `CanonicalDecimal` values, and structural scale/unit fields. It never
recomputes registered formula results, and unit incompatibility is left to the
`SCALE_UNIT` category.

TASK-08A checks the exact `EvidenceItem -> ScaleUnitResolution -> BindingMap ->
ExecutionResult` metadata chain. Approved source and requested scale/unit
metadata cannot change, absent scale cannot default to `RAW`, hint decisions
remain traceable, unsupported unit conversion is rejected, and PERCENT cannot
mix with magnitude scales. Existing deterministic conversion policy may be
applied to verify expected output metadata, but scale is never resolved again.

TASK-082 checks only Plan, Program, and registered formula definition and
implementation metadata. Successful symbolic shapes are limited to LOOKUP with
IDENTITY, formula-free multi-period comparison with COLLECT, GROWTH_RATE, and
AVERAGE. An unsupported ratio or unregistered formula cannot verify.

All three check families append to the existing report after grounding and
evidence support. Every failure is preserved and aggregate precedence remains
`GROUNDING`, `INSUFFICIENT_EVIDENCE`, `NUMERIC`, `SCALE_UNIT`, then
`FINANCIAL_LOGIC`.

### Consequences

- TASK-081, TASK-08A, and TASK-082 are complete.
- TASK-083 through TASK-089 remain deferred to M8 Batch 3.
- No LLM, retry, retrieval rerun, escalation, or abstention behavior is added.
- `GPU_PRODUCTION_VALIDATION_PENDING` is unchanged.

---

## ADR-043 — Select deterministic verification check sets without downgrade

**Status:** Accepted

### Decision

TASK-083 defines LIGHT as a narrow optimization for a formula-free DIRECT
LOOKUP with exactly one required retrieval requirement. LIGHT runs TASK-080,
TASK-08B, TASK-081, and TASK-08A only when scale resolution is required. It
does not run TASK-082.

TASK-084 defines STRICT as the complete applicable deterministic matrix:
TASK-080, TASK-08B, TASK-081, conditional TASK-08A, and TASK-082. STRICT is
required for PROGRAM reasoning, any formula, derived ratio, multi-period,
aggregate, or multiple required retrieval requirements.

Verification emits typed profile-policy checks in the existing
`FINANCIAL_LOGIC` Plan/Program consistency category. Request/Plan profile
mismatch is `VERIFY_PROFILE_MISMATCH`; invalid LIGHT dimensions use typed
`LIGHT_*` reasons. The effective check set is STRICT whenever either declared
profile is STRICT or the Plan/Program shape requires STRICT. Therefore corrupt
or stale profile metadata can never silently reduce verification.

Failed `ExecutionResult` values bypass before profile policy. Verification does
not mutate Plan, Program, Evidence, or ExecutionResult, and repeated calls over
the same request produce identical reports.

### Consequences

- TASK-083 and TASK-084 are complete.
- Existing `VerificationReport`, failure categories, and precedence are
  unchanged.
- TASK-085 through TASK-089 remain deferred to M8 Batch 4.
- No LLM, retry, retrieval rerun, escalation, or abstention behavior is added.
- `GPU_PRODUCTION_VALIDATION_PENDING` is unchanged.

---

## ADR-044 — Emit bounded reason-specific retry directives without rerunning stages

**Status:** Accepted

### Decision

M8 Batch 4 adds immutable `RetryState` and `RetryDirective` contracts and a
pure deterministic directive engine. The engine maps every existing verifier
`(failure_category, reason_code)` pair explicitly to retrieval retry,
Programmer retry, or permanent abstention. It does not fall back to category-
only routing for an unregistered code.

`Plan.max_retries` is the total rerun allowance after the initial attempt.
Retrieval retry, Programmer retry, and CHEAP-to-STRONG escalation each consume
one retry; PASS and ABSTAIN consume none. STRONG escalation is allowed once,
only from CHEAP, only for explicitly Programmer-repairable failures, and only
with remaining budget. No path can downgrade or emit a retry from a terminal
state.

Retrieval directives retain the original Plan, metadata filters, and evidence
requirements. Programmer directives retain Plan and Evidence, do not expose
`BindingMap` values, and keep M6 validation mandatory. M8 performs no rerun or
model invocation; M9 owns execution of directives.

### Consequences

- TASK-085 through TASK-089 are complete, so the M8 implementation milestone is
  complete.
- Missing/ambiguous evidence clues retry retrieval; permanent provenance/schema
  failures abstain.
- Repairable Program/output-shape failures retry the Programmer and may trigger
  the single CHEAP-to-STRONG transition; unsupported formulas, conversions,
  and Plan/profile policy failures abstain.
- Failed `ExecutionResult` values remain typed M7 failures and are never
  reclassified as Verification failures.
- M9 must persist `RetryState`, perform the selected rerun, carry immutable
  contracts forward, and terminate on PASS/ABSTAIN.
- `GPU_PRODUCTION_VALIDATION_PENDING` is unchanged.

---

## ADR-045 — Persist immutable M9 orchestration contracts and keep graph nodes thin

**Status:** Accepted

### Decision

M9 Batch 1 introduces the versioned `m9-orchestration-state-v1` contract,
immutable attempt history, deterministic Plan and retrieval-policy
fingerprints, terminal response schemas, typed execution-failure directives,
and inert stage ports. It implements TASK-090 contracts only and performs no
orchestration graph or stage execution.

`SupervisorResult.plan` is the sole Plan source of truth. The retrieval-policy
fingerprint binds its exact requirements and filters, eligible source types,
`top_k`, and artifact versions. Previous attempts cannot be changed, the last
attempt is current, and the attempt index equals shared retries used. Terminal
state cannot transition.

`BindingMap` stays outside checkpoint state. M9 persists only an opaque
process-local `binding_ref`; Programmer cannot resolve it, while Sandbox and
Verification integration may. A resume that loses the handle must rebuild it
from the deterministic EVIDENCE stage. Numeric bindings are never serialized
into graph state.

Execution failures preserve their original M7 stage/code and use a separate
`ExecutionDirective`. They never produce a `VerificationReport`, consume the
same `Plan.max_retries` budget for every rerun, do not cause automatic model
escalation, and reject unknown classifications. Batch 1 validates directive
shape and transitions but does not choose or run a route.

M9 graph execution will use LangGraph for control flow and checkpoint
coordination only. The locally inspected installation is `langgraph==1.2.10`,
which is pinned in `requirements-orchestration.txt`. Batch 1 has no LangGraph
runtime import. Financial formulas, grounding, retrieval policy, validation,
execution policy, and verification remain existing deterministic functions;
they do not move into graph nodes.

Real `CellLocation.table_class` provenance is currently unavailable from the
source-backed location path. M9 must not infer it from Plan or metric mappings.
Providing source-backed provenance remains an upstream requirement.

### Consequences

- TASK-090 is complete; TASK-091 through TASK-099 remain pending.
- Batch 1 is CPU-testable and requires no graph run, model call, retrieval
  backend, or production GPU artifact.
- TASK-075 remained deferred at this decision boundary and was later completed
  by ADR-055.
- `GPU_PRODUCTION_VALIDATION_PENDING` is unchanged.

---

## ADR-046 — Execute M9 Batch 2 as a no-retry injected straight-through graph

**Status:** Accepted

### Decision

Implement TASK-091 through TASK-094 with `langgraph==1.2.10` using the fixed
node sequence `nlu -> supervisor -> retrieval -> evidence -> programmer ->
sandbox -> verification`. Graph nodes only deserialize/project state, invoke a
stage transition, and select continue/stop. All stage dependencies that can
perform external work are injected.

The graph creates exactly one attempt at index zero. It freezes Plan and
retrieval-policy fingerprints before retrieval, rejects unsupported M3
requirement shapes without widening, and preserves the native typed artifact
at each stop. The evidence stage requires source-backed `table_class`, stores
`BindingMap` only in `BindingStorePort`, and checkpoints only `binding_ref`.
Program validation, sandbox execution contracts, and M8 verification are
reused unchanged.

### Consequences

- TASK-091 through TASK-094 are complete.
- Batch 2 has no retry loop, directive execution, final response, answer
  construction, abstain routing, or E2E evaluator.
- Failed execution never creates a VerificationReport; failed verification
  never creates a retry directive.
- Fixture adapters may supply real source-backed `table_class`; the production
  source path still cannot, so that path remains blocked upstream.
- TASK-075 remained deferred at this decision boundary and was later completed
  by ADR-055. `GPU_PRODUCTION_VALIDATION_PENDING` is unchanged.

---

## ADR-047 — Execute bounded typed retries and build answers only from verified output

**Status:** Accepted

### Decision

M9 Batch 3 executes existing M8 `RetryDirective` values and deterministic M9
`ExecutionDirective` values in the LangGraph control layer. Both use the same
`RetryState` and `Plan.max_retries` allowance. Every retrieval, Programmer,
Sandbox, and strong-tier rerun consumes exactly one retry; PASS and ABSTAIN
consume none. Terminal or exhausted state cannot emit another rerun.

Project new attempts from immutable completed attempts according to the entry
stage. Retrieval retries keep only the frozen retrieval query. Programmer
retries and strong escalation keep retrieval/evidence/M5 artifacts and the
opaque `binding_ref`. Sandbox retries additionally keep `ProgrammerResult` and
therefore never regenerate Program. Strong escalation is one-way and occurs at
most once.

Classify each failed `ExecutionResult` from its exact native M7 stage/code.
Unknown pairs fail closed without a fallback directive. Security and permanent
contract/binding/conversion/arithmetic/protocol failures abstain immediately;
only explicitly repairable Program/shape, source-scale, resource, and worker
failures rerun their targeted stage. The original execution failure remains
unchanged and never creates verification artifacts.

PlanningGate clarification uses only existing missing/ambiguity findings.
Other early and final failures produce ABSTAIN with the typed originating stage,
reason code, terminal retry state, and no answer. Full aggregated TASK-099
failure reporting is not part of this decision.

Build PASS only after successful execution, passed verification, and the M8
PASS directive. Copy `ExecutionOutput` exactly and cite only evidence reachable
from the Program output, ordered by Plan requirement and evidence ID. The
Answer Builder cannot access `BindingMap` and performs no arithmetic,
conversion, or formula recomputation.

### Consequences

- TASK-095, TASK-096, and TASK-097 are complete.
- Retry loops are bounded by construction and terminal state rejects later
  transitions.
- Checkpoints still contain only `binding_ref`, never bound values.
- TASK-098 and TASK-099 remain pending.
- Production source-backed `table_class` remains unavailable and is never
  inferred by M9.
- TASK-075 remained deferred at this decision boundary and was later completed
  by ADR-055. `GPU_PRODUCTION_VALIDATION_PENDING` is unchanged.

---

## ADR-048 — Evaluate the injected M9 terminal path and attribute only unrecovered failures

**Status:** Accepted

### Decision

Complete TASK-098 with a deterministic CPU fixture evaluator that runs the
retrieved-evidence M9 graph through injected stage adapters. Score NLU, Plan,
evidence completeness, exact Program trace, exact Decimal execution output,
verification, terminal status, final answer, retries, and strong escalation as
independent fields. Aggregate conditional terminal-status accuracies, retry
success and distribution, escalation count, and terminal stage/code counts.

Complete TASK-099 by deriving terminal attribution only when bounded routing
stops with CLARIFICATION or ABSTAIN. Earlier attempt failures that successfully
route to a later attempt are recovered history, not terminal attribution.
Preserve the exact typed code and terminal attempt index together with relevant
requirement, evidence, and Program IDs available in the terminal attempt.
Unknown native codes remain native codes rather than being replaced by generic
classification text. PASS has no attribution.

Advance the M9 checkpoint contract to `m9-orchestration-state-v2` because the
serialized `FailureAttribution` gains identifier lists. Keep the evaluation
fixture-only and explicitly report production TABLE E2E as
`BLOCKED / PRODUCTION_TABLE_CLASS_PROVENANCE_PENDING`. Fixtures may supply real
source-backed `table_class`; no Plan-derived inference is permitted.

### Consequences

- TASK-098 and TASK-099 are complete; M9 implementation tasks are complete.
- The fixture CLI requires no network, LLM, GPU, or production vector artifact.
- Production retrieved-evidence TABLE validation remains blocked upstream.
- Orchestration naming cleanup may now be planned as a separate compatibility
  change, but is not part of Batch 4.
- TASK-075 remained deferred at this decision boundary and was later completed
  by ADR-055. `GPU_PRODUCTION_VALIDATION_PENDING` and
  `PRODUCTION_TABLE_CLASS_PROVENANCE_PENDING` remain active.

---

## ADR-049 — Serve models as separate vLLM processes behind one OpenAI-compatible client boundary

**Status:** Accepted (design only; no models deployed)

### Context

M10 optimization needs a production serving architecture for four model roles:

- **Qwen3-8B (CHEAP tier)** — shared by NLU, Supervisor, and the optional
  semantic Verifier. One set of weights, three prompts/contracts (ADR-016).
- **Qwen2.5-Coder-14B-Instruct (STRONG tier)** — the Programmer.
- **BGE-M3** — online query embedding (must stay byte-compatible with the
  offline index, ADR-015/019, TASK-03C).
- **BGE-reranker-v2-m3** — cross-encoder reranking (ADR-014/022).

Generative decoder LLMs and encoder (pooling / scoring) models have opposite
runtime shapes: autoregressive decode with a growing KV cache vs. a single
forward pass with no KV cache. Serving choice must respect that, keep retrieval
model versions pinned to the index, and preserve failure attribution.

### Decision

Use **vLLM** as the primary production serving engine, running **one process
per model**, each behind vLLM's OpenAI-compatible HTTP API. The application
talks to every model through **one thin internal serving-client contract**
(base URL + model id + task), so provider/engine stays a deployment choice and
never leaks into business logic (per the AGENTS.md routing rules).

Processes:

1. `qwen3-8b` (vLLM, generate) — CHEAP tier; **shared** by NLU, Supervisor, and
   optional Verifier. Roles are separated only at the prompt/schema layer, never
   by duplicating weights (ADR-016). Guided/structured decoding enforces the
   JSON schemas.
2. `qwen-coder-14b` (vLLM, generate) — STRONG tier; Programmer. Guided decoding
   constrains output toward the `m6-program-v1` DSL shape (validation in
   TASK-068 remains the authority; guided decoding is only a prefilter).
3. `bge-m3` (encoder / embedding) — online query embedding, config-pinned to the
   offline BGE-M3 fingerprint.
4. `bge-reranker-v2-m3` (encoder / scoring) — fused-candidate reranking.

Separate processes are the default because they give **independent batching,
independent scaling, per-model version pinning, and hard failure isolation**:
a Programmer OOM cannot take down retrieval embedding, and Qwen can be upgraded
without touching index-compatible BGE-M3.

CHEAP vs. STRONG routing stays exactly as `Plan.model_tier` (deterministic,
ADR-010/030): CHEAP requests hit the `qwen3-8b` endpoint, STRONG requests hit
`qwen-coder-14b`. Serving adds no routing logic.

### Rejected alternatives

- **SGLang (primary).** Technically strong; RadixAttention prefix reuse is
  attractive for the shared, system-prompt-heavy NLU/Supervisor traffic.
  Rejected as *primary* only for operational maturity: vLLM has wider
  deployment familiarity, first-class Prometheus metrics, and native
  embedding + scoring endpoints, so one engine covers generate/embed/rerank.
  Retained as the documented alternative to revisit **if measured** prefix-cache
  reuse becomes the dominant latency lever.
- **llama.cpp (production GPU).** Rejected for production GPU throughput —
  weaker continuous batching and multi-request concurrency than vLLM/SGLang.
  Retained for CPU-only local development / CI (matches the repo's current
  CPU-only posture) and as a low-VRAM GGUF-quantized emergency fallback. Its
  GBNF grammar is a nice-to-have; vLLM/SGLang guided decoding already covers
  constrained output.
- **One shared process for all four models.** Rejected: couples batching,
  memory (KV-cache vs. no-cache), version lifecycles, and failure domains, and
  breaks retrieval index-version isolation.
- **Separate process per Qwen *role* (NLU proc + Supervisor proc).** Rejected:
  duplicates ~16 GB of Qwen3-8B weights in GPU memory for no benefit; ADR-016
  already mandates shared weights with separate prompts.

### Evaluated dimensions (summary)

- **GPU memory (bf16 est.):** Qwen3-8B ≈ 16 GB weights + KV cache;
  Qwen2.5-Coder-14B ≈ 28 GB (needs a ≥40 GB GPU, or ≈8–10 GB with 4-bit
  AWQ/GPTQ to fit 24 GB); BGE-M3 ≈ 1–2 GB; BGE-reranker-v2-m3 ≈ 1–2 GB. Encoders
  are small enough to co-reside on spare CHEAP-GPU memory.
- **Batching:** vLLM continuous batching + PagedAttention for the generative
  models; dynamic request batching for the encoder pooling/scoring servers.
- **Concurrency:** per-process request queues; CHEAP and STRONG scale
  independently to their own QPS profiles.
- **Latency:** structured/guided decoding on Qwen; encoders are single-pass and
  low-latency. Per-stage latency is already tracked by the M10 metrics list.
- **Throughput:** generation throughput is the constraint; encoders are cheap.
  STRONG (14B) is the throughput floor and scales first under load.
- **Model sharing:** exactly one shared point — Qwen3-8B weights across
  NLU/Supervisor/Verifier. Everything else is isolated.
- **Observability:** each server exposes Prometheus metrics (queue time, batch
  size, tokens/s, TTFT, KV-cache utilization); the app records the
  `docs/ARCHITECTURE.md` §9 execution trace plus model/index version
  fingerprints (TASK-03C).
- **Deployment complexity:** one engine (vLLM) for all four roles keeps the
  operational surface small; four processes are supervised independently.
- **Failure isolation:** process-per-model gives independent restart/OOM
  domains; a crash in one role degrades only that role's stage.

### Expected GPU requirements & topology

- **Minimum viable (single node):** 1× 80 GB GPU (A100/H100) hosting all four
  processes with capped `gpu_memory_utilization` per process.
- **Recommended production (two GPUs):** GPU-A (≥40 GB) for STRONG
  `qwen-coder-14b`; GPU-B (24 GB, e.g. L4/A10/RTX 4090) for CHEAP `qwen3-8b`
  with BGE-M3 + reranker co-resident in its spare memory. Encoders may fall back
  to CPU for dev, but GPU is preferred for online latency.

### Consequences

- TASK-107 is complete as a decision. **No model is deployed** by this ADR.
- The concrete serving client and per-model servers are **TASK-108 (shared
  Qwen3-8B role serving)** and **TASK-109 (retrieval model serving)** — not
  started here.
- The thin OpenAI-compatible serving-client contract is specified but not
  implemented; provider/model names remain configurable, never hardwired.
- TASK-075 remained deferred at this decision boundary and was later completed
  by ADR-055; `PRODUCTION_TABLE_CLASS_PROVENANCE_PENDING` and
  `GPU_PRODUCTION_VALIDATION_PENDING` remain active — this ADR runs no GPU and
  validates no production latency/throughput.

---

## ADR-050 — Implement the ADR-049 serving boundary as an injected, contract-reusing client

**Status:** Accepted (no models deployed)

### Decision

M10 Batch 4 implements ADR-049 in a new `src/serving/` package (TASK-108,
TASK-109). It adds serving *integration* only; it introduces no new routing,
arithmetic, ranking, or model weights.

- **`ServingEndpoint` / `ServingTopology` (TASK-108).** One immutable endpoint
  per served model, validated for task and for distinct base URLs (one process
  per model = failure isolation). `endpoint_for_role` resolves NLU, Supervisor,
  and the optional Verifier to the **single shared Qwen3-8B** endpoint and the
  Programmer to the separate STRONG endpoint (ADR-016). `endpoint_for_tier`
  maps an already-decided `Plan.model_tier` (CHEAP→shared, STRONG→strong) to its
  endpoint — it consumes the deterministic M4 route (ADR-010/030) and never
  re-classifies a plan. Model ids are configurable defaults; base URLs come from
  deployment config/env, never hardwired.
- **`OpenAICompatibleClient` (thin boundary).** One client bound to one
  endpoint over an injected `Transport` (`generate`, `embed`, `score`,
  `count_tokens`, `count_pair_tokens`). Task mismatch, protocol, and transport
  failures are typed `ServingError`s. The stdlib `HttpTransport` is the only
  production-network code and is exercised only against a live server.
- **`ServingQueryEncoder` (TASK-109).** Satisfies the existing `QueryEncoder`
  protocol and feeds `embed_query_texts` unchanged, so L2 normalization, the
  1024-dim/float32 checks, and the `APPROVED_EMBEDDING_FINGERPRINT` gate remain
  the compatibility authority for index-safe query vectors.
- **`ServingBGEReranker` (TASK-109).** Subclasses the pinned `BGEReranker` and
  replaces only inference/pair-token-counting with remote `/score` and
  `/tokenize` calls. The `RERANKER_CONFIG_FINGERPRINT`, the
  `RERANKER_MAX_PAIR_TOKENS` guard, and the deterministic ranking/tie-break/
  `top_k` logic in `rerank_candidates` are reused, not duplicated.

### Consequences

- TASK-108 and TASK-109 are complete as CPU-only, network-free integrations
  tested through an injected transport (no torch, no GPU, no live server).
- Deterministic `ModelTier` routing is unchanged and remains the single source
  of truth; serving only *consumes* it.
- Retrieval serving stays byte-compatible with the offline index via the
  existing embedding/reranker contracts; the serving layer adds no new
  normalization or scoring math.
- Live-GPU throughput/latency/index-parity validation and the `HttpTransport`
  path remain `GPU_PRODUCTION_VALIDATION_PENDING`; no model is deployed.
- TASK-075 remained deferred at this decision boundary and was later completed
  by ADR-055. `PRODUCTION_TABLE_CLASS_PROVENANCE_PENDING` remained active. The
  next M10 batch owns cost/token dashboards, throughput/load
  tests, and coordination-failure logging (TASK-110–112).

---

## ADR-051 — Measure retry gain, parallelize retrieval identically, and cache only verified answers

**Status:** Accepted (CPU structure; production measurement pending)

### Decision

M10 Batch 5 implements TASK-102, TASK-103, and TASK-105 without adding a second
routing source of truth, weakening grounding, or fabricating measurements.

- **TASK-102 — retry/escalation measurement.** `retry_metrics.py` observes the
  terminal M9 `RetrievedEvidenceE2ECaseResult` values and reports retry rate,
  escalation rate, and quality recovered by retry/escalation (PASS rate among
  retried/escalated cases), plus an optional `Plan.confidence` bucketing for
  future gate calibration. Retry and CHEAP→STRONG escalation routing remain
  solely in the deterministic M8 `build_retry_directive` (ADR-030/ADR-044);
  confidence is observational and never gates a directive.
- **TASK-103 — parallel retrieval.** `retrieval/parallel.py` executes independent
  TABLE subqueries (and independent TABLE/TEXT source runs) concurrently while
  reusing the exact Batch 3 decomposition, per-subquery request construction,
  backend→RRF→reranker sequence, and partial-state merge. Results are always
  reassembled in deterministic subquery order, so requirements, filters, ranking
  semantics, provenance, and merge behavior are byte-identical to the sequential
  path (ADR-020/ADR-022 preserved).
- **TASK-105 — semantic cache experiment.** `orchestration/semantic_cache.py`
  is an optional, default-disabled, versioned, fingerprint-invalidatable cache
  that stores **only verified PASS answers**, so it can neither hold nor return
  an unverified answer and never bypasses verification (ADR-009/ADR-024). Key
  policies are NONE / QUERY / FIELD / ADAPTIVE; ADAPTIVE caches only stable
  standardized inputs. `evaluation/cache_experiment.py` compares the policies and
  monitors the correctness delta against the real fixtures (zero regressions).

### Consequences

- TASK-102, TASK-103, and TASK-105 are complete as deterministic CPU-only work
  with focused tests; `python -m pytest` stays green and `git diff --check` is
  clean.
- Real retry-rate/quality-gain over production models and traffic, and
  embedding-similarity ("semantic") cache hit-rate/quality, remain
  `GPU_PRODUCTION_VALIDATION_PENDING`; no such numbers are asserted here.
- New CLIs `run_retry_metrics_eval` and `run_cache_experiment` join the existing
  deterministic fixture evaluations.
- TASK-104 (parallel verifier checks) remains open; the next M10 batch owns
  TASK-104, TASK-106, and the TASK-110–112 dashboards/load/coordination work.
- TASK-075 remained deferred at this decision boundary and was later completed
  by ADR-055; `PRODUCTION_TABLE_CLASS_PROVENANCE_PENDING` and
  `GPU_PRODUCTION_VALIDATION_PENDING` remain active.

---

## ADR-052 — Measure latency, cost/tokens, and load as source-labeled telemetry outside the deterministic contracts

**Status:** Accepted (CPU structure; production measurement pending)

### Decision

M10 Batch 6 implements TASK-106, TASK-110, and TASK-111 as a telemetry layer that
is deliberately separate from the deterministic M9/evaluation contracts. ADR-040
keeps wall-clock out of checkpoints/reports so fixtures serialize identically;
therefore latency/token/load telemetry lives in `src/evaluation/` and never
enters a checkpoint, report, or answer. Every telemetry report carries a
`MeasurementSource` (`CPU_FIXTURE` vs. `LIVE`) so a CPU number can never be read
as a live production number.

- **TASK-106 — latency.** `telemetry.py` provides a deterministic nearest-rank
  `Distribution` (p50/p95/p99/min/max/mean), a monotonic `Stopwatch`, and the
  source enum. `latency.py` adds `profile_stages` (per-stage + total) and
  `measure_e2e_latency` (real CPU end-to-end over fixtures). Per-stage *model*
  latency needs live serving and is reported `LIVE_PENDING`.
- **TASK-110 — cost/tokens.** `cost_dashboard.py` defines versioned
  `ModelInvocation` and `TokenPrice` contracts and `build_cost_dashboard`
  aggregating tokens/query, cost/query, real priced escalation cost, per-model
  breakdown, and cache hit rate. Prices are caller-supplied (ADR-049); with no
  live invocation, token/cost are `LIVE_PENDING` — never fabricated.
- **TASK-111 — load.** `load_test.py` runs a bounded (`MAX_CONCURRENCY`,
  `MAX_TOTAL_REQUESTS`) concurrent harness measuring throughput, latency
  distribution, and error/timeout counts with real CPU concurrency. Production
  accuracy/timeout degradation with live models is `LIVE_PENDING`.

### Consequences

- TASK-106, TASK-110, and TASK-111 are complete as deterministic CPU-only work
  with focused tests; the suite stays green and `git diff --check` is clean.
- New CLIs `run_latency_eval`, `run_cost_dashboard`, `run_load_test` join the
  existing fixture evaluations; all CPU-only.
- No GPU/model latency, token, or cost value is asserted; every model-dependent
  number stays `LIVE_PENDING` behind `GPU_PRODUCTION_VALIDATION_PENDING`.
- At this decision boundary TASK-104 (parallel verifier checks) was the only
  remaining M10 implementation task; it was later completed CPU-only
  (`src/verification/verifier.py` `parallel=True`, M10 Batch 7). The remaining
  M10 work is otherwise the live GPU production-validation run (including the
  TASK-100 production correctness baseline and TASK-101 CHEAP-vs-STRONG quality
  delta), which is not started here.
- TASK-075 remained deferred at this decision boundary and was later completed
  by ADR-055; `PRODUCTION_TABLE_CLASS_PROVENANCE_PENDING` and
  `GPU_PRODUCTION_VALIDATION_PENDING` remain active.

---

## ADR-053 — Source back `CellLocation.table_class` from an additive TableClass sidecar

**Status:** Accepted (CPU structure; production real-corpus audit pending)

### Context

`CellLocation.table_class` existed in the contract but `locate_cells` always
emitted `None`, so every real TABLE plan failed M8/M9 with
`TABLE_CLASS_UNAVAILABLE` and production TABLE E2E stayed blocked by
`PRODUCTION_TABLE_CLASS_PROVENANCE_PENDING`. ADR-041/ADR-045/ADR-046 already
forbid deriving actual table class from the expected metric or the
`MetricTableMapping` registry: that registry is Supervisor expected-table
routing, not evidence provenance.

### Decision

Add an immutable, additive sibling artifact `m2-table-class-sidecar-v1`
(`src/indexing/table_class_sidecar.py`). It re-runs only the deterministic M2A
document parse over the approved raw reports and records at most one
`TableClassHint` per parsed table:

- `hint_id`, `report_id`, `page_id`, `table_id`, `table_class`,
  `registry_entry_id`, `matched_source_span`, `matched_text`.

Classification is conservative and deterministic:

- same-page evidence only — the context window is the fixed
  `PRECEDING_CONTEXT_WINDOW_CHARS` before the table, clamped to the page content
  span and to the end of the previous table on that page;
- Unicode NFKC normalization; spans are taken only from an offset-preserving
  normalization, else from the raw window, and every recorded span must
  round-trip and re-match after NFKC + case folding;
- literal, case-insensitive matching against the pinned
  `m2-table-class-registry-v1` (statement-form codes `B01-DN` / `B01-DN/HN`,
  `B02-DN`, `B03-DN`, `B09-DN` and the four canonical Vietnamese statement
  titles), with a small bounded run of non-alphanumeric formatting slack
  between literal tokens and word-boundary guards;
- no fuzzy matching, no semantic/LLM classification, and no use of metric
  mappings;
- a window that matches more than one distinct `TableClass`, or matches
  nothing, yields **no** hint.

The canonical M2 corpus is never rebuilt or mutated. The sidecar binds to one
exact corpus identity and to the registry fingerprint; `TableClassSidecar`
validates on construction and a corrupt or incompatible sidecar raises
`TableClassSidecarError` (fail closed) rather than degrading to a heuristic.

`EvidenceProvenanceRepository` gains an optional `table_class_sidecar`.
`locate_cells` sets `CellLocation.table_class` from an exact
`(report_id, page_id, table_id)` sidecar lookup: a missing hint stays `None`,
and there is no metric-based or heuristic fallback. M8 grounding and M9 evidence
logic are unchanged.

### Consequences

- Real source-backed `table_class` can now flow into M8/M9 for reports whose
  statement code or title is recognised in the same-page preceding context.
- Behaviour is unchanged when no sidecar is supplied or no hint exists
  (`table_class` stays `None`).
- `PRODUCTION_TABLE_CLASS_PROVENANCE_PENDING` is **not** cleared: it requires a
  real full-corpus source-backed integration audit, which the CPU-only
  development host cannot run.
- TASK-075 remained deferred at this decision boundary and was later completed
  by ADR-055. `GPU_PRODUCTION_VALIDATION_PENDING` is unchanged.

---

## ADR-054 — Clear TableClass production provenance only through an exact real-corpus audit

**Status:** Accepted (audit tooling complete; production run PASSED —
`PRODUCTION_TABLE_CLASS_PROVENANCE_PENDING` cleared)

### Decision

Add `src.indexing.table_class_provenance_audit` as the sole deterministic audit
for `PRODUCTION_TABLE_CLASS_PROVENANCE_PENDING`. It validates the exact canonical
M2 corpus and TableClass sidecar compatibility before reading audit data, then
replays ADR-053's existing source-only classifier over the exact raw reports.
The raw corpus root is required because the immutable M2 chunk artifact stores
source identity and hashes, not complete raw report text.

The audit compares the parsed source-table inventory, canonical M2 TABLE
inventory, and persisted sidecar mappings exactly. It reports coverage and all
required integrity diagnostics, validates source spans by exact raw-text slice,
and exercises the production `EvidenceProvenanceRepository -> locate_cells`
path for every `TableClass` represented by the sidecar. When an unmatched table
exists, it also proves that the production path preserves `None`.

PASS requires exact compatibility and zero artifact, mapping, orphan, span,
round-trip, source-replay, or evidence-path failures, plus at least one genuine
source-backed classification. It deliberately defines no minimum coverage and
does not make unmatched or source-ambiguous tables blocking. No alternate
metric/content/fuzzy/LLM classifier is introduced.

### Consequences

- Fixture tests can prove audit behavior but cannot clear the production flag.
- A compatibility mismatch or corruption returns `status: BLOCKED` and a
  non-zero exit; the audit never degrades to a partial or heuristic result.
- TASK-075 was later completed by ADR-055.
  `GPU_PRODUCTION_VALIDATION_PENDING` is unchanged.

### Production run — flag cleared

The audit CLI was run on the approved canonical inputs and returned exit code 0
with `status: PASS`, `blockers: []`, so
`PRODUCTION_TABLE_CLASS_PROVENANCE_PENDING` is **cleared**.

- corpus artifact id
  `4f39603a89e213129de6c57e1234456e88086832c8b61bd9821770d76a6dc78e`
  (`corpus_id` `ViFinQA`, `artifact_status` `COMMITTED`); corpus fingerprint
  `9ea9e073a8aff69e48ae9b258a2bd657cd5194a52de9742e89f5bdb8d17f61c9`; corpus
  manifest sha256
  `68de4553a91296fb9fe5bea82a5672b1013febea2769824ee8a71329f0448c97`
- table-class sidecar artifact id
  `86d718489f1c5aa455876cbb0bc28c72169ae610f753442f51adab714ad57de8`
  (`m2-table-class-sidecar-v1`; registry `m2-table-class-registry-v1`; registry
  fingerprint
  `ea871c01ce8d0cf82d723217027afb369824515f42d68fbc1b63526a50f4a7c0`), exactly
  bound to the corpus artifact above
- raw corpus root `~/Documents/data/ViFinQA/financial_statements`
- 1973 reports; 146246 canonical tables; 0 unparseable; 54173 source-backed
  classifications (coverage 0.3704 — BALANCE_SHEET 4789, INCOME_STATEMENT 2982,
  CASH_FLOW_STATEMENT 2132, NOTES 44270); 0 orphan / 0 duplicate / 0 conflicting
  mappings; 0 invalid source spans; 0 source-span round-trip failures; 0
  source-supported hint mismatches; 0 corpus/source table mismatches
- production `EvidenceProvenanceRepository -> locate_cells` evidence path: PASS
  for all four `TableClass` values and for the unclassified-stays-`None` check
- the 92073 unmatched or ambiguous/conflicting tables remain valid `None`
  cases; ADR-054 defines no minimum coverage and this run does not change that

---

## ADR-055 — Use the existing one-shot DSL worker with NsJail on Linux production

**Status:** Accepted (TASK-075 complete)

### Context

M7 executes a closed deterministic DSL, not arbitrary generated Python or
user-supplied code. The only operations are `IDENTITY`, `COLLECT`, and
registered `GROWTH_RATE` / `AVERAGE` formula dispatch. Parent and worker both
validate the request, and M7 already has exact Decimal, protocol, timeout,
resource, and failure contracts.

M7 Batch 3 still left one production decision open. Darwin had an additive host
sandbox, while Linux needed a fail-closed native namespace/seccomp boundary.
The selected technology must preserve the current one-worker-per-request
lifecycle and the `SandboxExecutionRequest -> ExecutionResult` contract.

### Decision

Use the existing one-shot OS process with **NsJail** as the v1 Linux production
isolation technology. NsJail supplies user/mount/PID/network/IPC/UTS/cgroup
namespaces, seccomp-bpf, capability drop, `no_new_privs`, read-only mount
construction, and cgroup v2 limits around the already-trusted worker. The
existing parent RSS/wall monitor, worker rlimits, cleared environment, audit
guard, Decimal policy, and 1 MiB canonical JSON protocol remain mandatory.

Production is explicitly enabled with
`M7_SANDBOX_ISOLATION=linux-nsjail-v1`. The launcher is Linux-only and never
falls back. It requires the fixed `/opt/ai-financial` application layout,
root-owned non-writable launcher/config/policy files, and exact approved config
and seccomp-policy fingerprints. The canonical files are
`deploy/sandbox/m7-nsjail.cfg` and
`deploy/sandbox/m7-seccomp.policy`.

The production jail exposes only read-only Python runtime and application-code
mounts. It exposes no host root, report corpus, credentials, writable mount,
`/proc`, network interface, or runtime socket. Trusted Python startup reads the
runtime/code mounts; after worker preload and revalidation, the existing audit
guard denies all later filesystem reads/writes, sockets, and process spawning.

The exact `m7-limits-v1` contract does not change: 1,500 ms wall, CPU 1/2
seconds soft/hard, RSS 256 MiB, one jailed process, 32 descriptors, zero-byte
file creation, and 1 MiB request/response limits. NsJail/cgroup limits are
additive outer stops and cannot relax those values.

### Alternatives

- **Standard container as the per-request sandbox:** rejected for v1 because it
  adds container startup or a new pooled/service lifecycle. A hardened
  container remains approved as packaging around the NsJail parent.
- **gVisor:** stronger host-kernel isolation and OCI integration, but adds a new
  runtime class and syscall/filesystem overhead intended for untrusted-code or
  stronger multi-tenant boundaries. Deferred until the documented triggers.
- **MicroVM / Firecracker:** strongest tenant boundary of the considered
  options, but requires KVM, guest images/kernel patching, a jailer, transport,
  pooling/capacity, and additional observability. It is disproportionate for a
  closed DSL with no arbitrary-code surface.

NsJail is a shared-kernel boundary, so it is not equivalent to gVisor or a VM.
That residual risk is accepted only for the current closed, validated,
non-callable DSL.

### Failure and telemetry policy

Setup/profile/fingerprint failures map to
`SECURITY/ISOLATION_SETUP_FAILED`; existing worker audit denials remain typed
`SECURITY`; exact resource violations remain `RESOURCE`; and unattributable
abnormal jail/worker exits remain fail-closed `INFRASTRUCTURE`. No failure may
carry partial output.

Operational telemetry records isolation mode, policy fingerprints, startup and
execution latency, exit/failure stage/code, and wall/RSS kills. It must not log
bindings, request values, protocol payloads, worker stderr, corpus data, or
secrets, and it does not enter deterministic contracts/checkpoints.

Every production Linux image/host must pass the actual M7 abuse probes through
the installed NsJail profile and a cold-start/target-concurrency latency run
before rollout. This host conformance gate cannot weaken the 1,500 ms wall
limit.

### Migration triggers

Re-evaluate gVisor first and MicroVM/Firecracker for a required VM-grade tenant
boundary if arbitrary/user Python, third-party binaries, native extensions, or
a broader callable DSL are introduced; mutually untrusted tenants share a
host; compliance requires host-kernel separation; the seccomp/mount surface
must broaden materially; a real escape/data exposure invalidates the threat
model; or measured stronger-runtime cost/latency fits the product SLO.

### Consequences

- TASK-075 is complete; TASK-070 through TASK-075 now form the complete M7 v1
  implementation/production-boundary decision.
- `SandboxExecutionRequest`, `ExecutionResult`, M9 `SandboxStagePort`, DSL,
  Decimal semantics, M8/M9 behavior, and all exact limits are unchanged.
- Local Darwin behavior remains the existing `sandbox-exec` development/test
  boundary. Local Linux without the explicit production mode remains
  development-only and is not production-approved.
- Linux host/image conformance and target-load measurements remain rollout
  evidence; they are not GPU retrieval/model validation and do not clear
  `GPU_PRODUCTION_VALIDATION_PENDING`.

Reference basis: the official
[NsJail documentation](https://github.com/google/nsjail),
[gVisor architecture and production guidance](https://gvisor.dev/docs/),
[Firecracker production host guidance](https://github.com/firecracker-microvm/firecracker/blob/main/docs/prod-host-setup.md),
and [Docker seccomp guidance](https://docs.docker.com/engine/security/seccomp/).

---

## ADR-056 — Observe coordination failures read-only over invariants M9 does not already enforce

**Status:** Accepted (TASK-112 complete, CPU-only)

### Context

TASK-112 asks for logging of conflicting worker state, stale state, retry
loops, and message/schema corruption. `src/orchestration/coordination_log.py`
already emitted `ATTEMPT_RECORDED`, `MODEL_TIER_ESCALATION`,
`RETRY_BUDGET_CONSUMED`, `RETRY_BUDGET_EXHAUSTED`, and `TERMINAL_FAILURE`,
covering the retry-loop bullet. The remaining three categories were not
represented. M9's `M9State` / `AttemptRecord` contracts are immutable and
strictly validated on construction (ADR-045), so most contradictions cannot
exist in a validated state — a naive detector would be dead code.

### Decision

Keep the observer pure and read-only (no state mutation, routing, retry,
repair, budget, or answer change; never raises on a validated state) and add
only detectors that key off an invariant M9's own validation does **not**
enforce, plus one minimal raw-payload entry point. Events carry only stable
diagnostic scalars — never `binding_ref`, evidence, execution output, prompts,
or secrets.

- **`CONFLICTING_WORKER_STATE`.** `M9State._validate_pass` constrains only the
  `PASS` direction, so a terminal non-`PASS` outcome whose final `AttemptRecord`
  still carries an M8 `RetryDirective` with `action == PASS` is representable and
  contradictory. A `CLARIFICATION` terminal outcome with a non-empty attempt
  history is likewise contradictory (clarification responses are built before
  any executable attempt). Either fires the event, at most once.
- **`STALE_WORKER_STATE`.** `build_failure_response` always stamps
  `FailureAttribution.attempt_index` with `attempts[-1].attempt_index` (or
  `None`), and `M9State` never re-checks that pointer against the attempt tuple.
  An attribution pointing at a superseded / out-of-range / absent attempt is a
  stale observation that never advanced with the run.
- **`MESSAGE_SCHEMA_CORRUPTION`.** The dict passed between orchestration nodes is
  the coordination message; `M9State.from_dict` is the fail-closed edge every
  node already crosses. New `scan_coordination_payload(payload)` runs that edge
  and, on `SchemaValidationError`, records one event with only the boundary name
  and the exception class name — no payload body, no message. A payload that
  parses is delegated to `build_coordination_log`. This function is not wired
  into the graph; it is the smallest added observation contract and changes no
  M9 behavior.

Detection is deterministic and fail-closed: a valid state emits none of the
three, each category appears at most once, and anything not provable from
available state is not emitted.

### Consequences

- TASK-112 is complete; every acceptance bullet is covered by
  `tests/orchestration/test_coordination_log.py` (conflicting/stale/corruption
  detected, valid state emits none, no duplicates, deterministic ordering,
  redaction, existing retry/terminal events unchanged).
- No orchestration graph, contract, schema version, or routing decision
  changes. The full suite stays green and `git diff --check` is clean.
- Production coordination-failure *rate* over live traffic is still an
  operational telemetry concern; this ADR only defines the deterministic
  event vocabulary and detection rules.
- `GPU_PRODUCTION_VALIDATION_PENDING` is unchanged;
  `PRODUCTION_TABLE_CLASS_PROVENANCE_PENDING` was already cleared (ADR-054).

---

## ADR-057 — Validate production retrieval as separated artifact/serving vs. gold-gated quality; no LLM in the v1 path

**Status:** Accepted (runner + preflight + calibration implemented CPU-only;
**production run PASSED on 1× RTX 5090 — retrieval portion of
`GPU_PRODUCTION_VALIDATION_PENDING` CLEARED**, see "Production run evidence"
below; supersedes the stale "requires live Qwen serving" language in earlier M10
batch notes and ADR-049/050 consequences)

### Context

The GPU validation runbook carried production-blocking inaccuracies: it implied
the vector acceptance count was the 146,246 source-table count (it is the
**1,743,311** `EmbeddingChunk` count — a different quantity), it treated
`Qwen3-8B` as part of the active online *retrieval* path (only `BAAI/bge-m3` and
`BAAI/bge-reranker-v2-m3` are), it let the injected-transport `tests/serving`
unit tests read as live-serving evidence, and
`run_retrieval_eval --mode full-corpus` printed a pass-looking
`GPU_PRODUCTION_VALIDATION_PENDING` line while being a stub. There was no
executable full-corpus retrieval production validation.

### Decision

Add `src/evaluation/retrieval_production_validation.py` +
`run_retrieval_production_validation` and `src/serving/live_probe.py`, and split
production retrieval validation into two categories that never mix:

1. **ARTIFACT_SERVING** — each check is `ARTIFACT` evidence (static file
   inspection) or `LIVE` evidence (a real served-model call):
   corpus/index/BM25 compatibility; **exact `vector_count` == canonical
   `chunk_count` == 1,743,311** with an explicit
   `TABLE_COUNT_SUBSTITUTED_FOR_VECTOR_COUNT` guard against the 146,246
   source-table count and the representation count; embedding fingerprint; FAISS
   shard sha256 + shard-count sum; SQLite `integrity_check`; a **LIVE** BGE-M3
   probe (dim 1024, finite floats, cosine ≥ 0.999 vs. the pinned CPU encoder);
   a **LIVE** BGE-reranker probe (shape/order, induced-ranking parity vs. the
   pinned CPU reranker); and a **LIVE** deterministic retrieval-execution smoke
   over the existing Backend→RRF→reranker stack (twice, byte-identical, no
   crash/timeout). The runner changes no ranking semantics — it only *calls* the
   existing stack. Tolerance rationale: production bf16/fp16 GPU inference vs.
   fp32 CPU reference cannot be bit-exact, so directional agreement (cosine for
   the index metric) and ranking parity (all `rerank_candidates` consumes) are
   the gates.
2. **RETRIEVAL_QUALITY** — Recall@k / MRR only when real per-question gold
   retrieval-evidence annotations exist. The public ViFinQA `questions.jsonl`
   has only `id` + `question`, so this is `BLOCKED_GOLD_DATA`. Fixture retrieval
   metrics are never substituted.

`run_retrieval_eval --mode full-corpus` now exits non-zero with
`status: NOT_RUN_HERE` and points at the runner; fixture mode is labelled CPU
regression only.

Add `src/indexing/embedding_build_preflight.py` (`preflight_vector_build`) — a
read-only, no-model check that computes the deterministic `build_id` and returns
`FRESH` / `RESUME` / `ALREADY_PUBLISHED` for a given `--output-root` and
`--max-vectors-per-shard` — and `src/indexing/embedding_batch_calibration.py` —
a bounded-sample (≤ 20,000 chunks) BGE-M3 throughput/VRAM/OOM sweep over batch
sizes 8/16/32/64 that recommends the largest stable batch beating the prior by
`--min-speedup`× and never touches an output root.

### Qwen is not in the v1 active path (verified in code)

- No active v1 query path calls `Qwen3-8B` or `Qwen2.5-Coder-14B`. The M9 graph
  (`src/orchestration/nodes.py`) calls `supervise_batch1` and `verify` as
  deterministic functions; NLU / Programmer / Sandbox are injected ports whose
  only implementations (`src/evaluation/e2e_fixtures.py`) are deterministic;
  `generate_program` is a pure symbolic-DSL builder and takes no model tier.
- `Plan.model_tier`, `RetryState.current_model_tier`, and the `ESCALATE_STRONG`
  directive only set recorded enum values. Nothing consumes them to pick or call
  a model. `src/serving/` (Qwen topology + `OpenAICompatibleClient`) is imported
  by no active-path module. `torch` / `transformers` occur only in BGE-M3 /
  BGE-reranker code.
- `GPU_PRODUCTION_VALIDATION_PENDING` is canonically a retrieval-model /
  production-vector-artifact blocker (ADR-020, ADR-025, ADR-031). The
  LLM-serving language attached to it in ADR-049/050 and later batch notes
  concerns `src/serving/` code that is not wired in; no canonical document
  requires an LLM on the v1 critical path (AGENTS.md: "sole third-party runtime
  dependency is langgraph").

### What clears what

- **TASK-109** clears when `run_retrieval_production_validation` returns
  `overall_status: PASS` (`clears_gpu_pending: true`) on the real committed
  corpus/vector/BM25 artifacts with both LIVE probes and the smoke passing on
  GPU hardware. No LLM is involved.
- **`GPU_PRODUCTION_VALIDATION_PENDING`** clears on the same condition. It has no
  LLM-serving component in the current architecture. `RETRIEVAL_QUALITY` stays
  independently `BLOCKED_GOLD_DATA` and does not gate the flag.
- **TASK-100** is **retrieval-gated, not Qwen-gated**: it needs the production
  vector artifact built + ADR-057 PASS, plus real per-question gold answers to
  score real-corpus correctness (`BLOCKED_GOLD_DATA`). "STRONG tier" in v1 is a
  deterministic routing label, not a model.
- **TASK-101** is **not GPU-gated in the current v1 architecture**: with no LLM,
  CHEAP and STRONG resolve to the same deterministic path, so there is no
  quality/cost delta to measure. It is blocked on model-backed ports being built
  and wired into M9 at all — unscheduled, out-of-v1-scope work.
- No numeric threshold is invented for TASK-100 or TASK-101.

### Production run evidence (M10 Batch 8, 1× RTX 5090)

Executed end to end on an RTX 5090 (driver CUDA 13.0, torch cu128, `faiss-cpu`,
`transformers 4.46.3`):

- **Vector artifact** — family root
  `artifacts/m2-vector-index-v1/full-corpus-rtx5090`, `CURRENT` →
  `build_id 6e196ee45ef08dd1c85a2da647abdd36c9d4613b4de59cb2a381c9fd852a8069`.
  `embedding_artifact_builder.validate_published_artifact` (existing validator,
  `input_artifact` = the committed M2 corpus) = `integrity: PASS`,
  `vector_count 1743311`, `chunk_count 1743311`, `shard_count 18`,
  `sqlite_row_count 1743311`; `artifact_status COMMITTED`;
  `embedding_fingerprint
  ebf2adc2a61d75a65db3829163a003e729095310360b9b162b570b34579585b3`;
  every FAISS shard sha256 + FAISS `ntotal` verified; SQLite `integrity_check ok`.
- **BM25 artifact** — built with `src.retrieval.bm25.build_bm25_index` over the
  canonical corpus
  `4f39603a89e213129de6c57e1234456e88086832c8b61bd9821770d76a6dc78e`; family
  root `artifacts/m3-bm25-index-v1/full-corpus-rtx5090`,
  `build_id 35399789d6b5cb5cfb08168922dc5cce72b2003bf19827e83736a115493b4d51`,
  `artifact_status COMMITTED`, `candidate_count 1743311`,
  `corpus_fingerprint 9ea9e073a8aff69e48ae9b258a2bd657cd5194a52de9742e89f5bdb8d17f61c9`,
  `bm25.sqlite sha256
  9fcb552a2dbbebfdc98f2dcaf1d034809a86a5f1cabb6357f674a9de21a6523e`.
  `validate_bm25_artifact_compatibility` + `BM25Index` load (SQLite
  `integrity_check ok`, FTS rows 1743311) = PASS.
- **LIVE serving** — pinned BGE-M3 (`5617a9f61b028005a4858fdac845db406aefb181`)
  and BGE-reranker-v2-m3 (`953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`) loaded on
  CUDA and exposed over an OpenAI-compatible HTTP server
  (`deploy/serving/serve_retrieval_models.py`) that performs no model logic of
  its own — it calls the repository's own `load_bge_m3_encoder` /
  `load_bge_reranker`. This is the "any OpenAI-compatible server" of steps H/I.
  For the run: `python deploy/serving/serve_retrieval_models.py --host 127.0.0.1
  --embed-port 18100 --rerank-port 18101 --device cuda`, with
  `deploy/serving/retrieval-endpoints.json` `base_url`s pointed at those two
  ports for the validation only (the committed file keeps its `REPLACE_HOST`
  placeholders — the endpoint is deployment-specific).
  Probed through the production `HttpTransport` / `OpenAICompatibleClient`:
  `live_bge_m3_embedding_parity` PASS (dim 1024, finite, min cosine
  `0.9999999999990075` ≥ 0.999); `live_bge_reranker_parity` PASS (order parity
  `true`, max score abs diff `1.9e-06`).
- **Deterministic retrieval-execution smoke** —
  `run_retrieval_execution_smoke` over `deploy/retrieval/smoke-queries.jsonl`
  (4 Qwen-free `RetrievalQuery` rows) through the real `Batch3Retriever`
  (BM25 + `VectorSearcher` + pinned CPU BGE-M3 query encoder + pinned CPU
  BGE-reranker): `ran true`, `byte_identical true`, `crashes 0`, `timeouts 0`,
  `sample_size 4` (exit 0).
- **`run_retrieval_production_validation`** (`--operator-expected-vector-count
  1743311`) → `overall_status PASS`, `artifact_status PASS`,
  `live_serving_status PASS`, `clears_gpu_pending true` (exit 0); all 10
  `artifact_serving_checks` PASS; `retrieval_quality_status BLOCKED_GOLD_DATA`.
- **TASK-109 is COMPLETE**; the retrieval portion of
  `GPU_PRODUCTION_VALIDATION_PENDING` is CLEARED. TASK-100 still needs real
  per-question gold answers (`BLOCKED_GOLD_DATA`); TASK-101 stays blocked on
  model-backed ports (no LLM ⇒ no CHEAP-vs-STRONG delta) — unchanged.

### Consequences

- The runner and probes are implemented CPU-only with focused tests
  (`tests/evaluation/test_retrieval_production_validation.py`,
  `tests/serving/test_live_probe.py`); the suite stays green and
  `git diff --check` is clean.
- No retrieval ranking, embedding, index, or serving-routing behavior changes;
  no model is deployed.
- **Known limitation:** the deterministic retrieval-execution smoke is a typed
  input the runner *gates on* (`ran` + `byte_identical` + no crash/timeout), not
  something the runner drives itself. Driving `Batch3Retriever` end to end needs
  the retrieval stack wired into an online path (building a `RetrievalQuery` and
  a `query_embedder`), which is a separate task; until then the operator
  produces the smoke result from a real run over committed `RetrievalQuery`
  fixtures (Qwen-free) and passes it in. The two LIVE model probes and every
  artifact check are fully executable now.
- The vector builder CLI is unchanged; `--input-artifact` takes the exact
  committed corpus artifact directory (or a family root with a `CURRENT` pointer
  resolving to the committed id).
- `PRODUCTION_TABLE_CLASS_PROVENANCE_PENDING` was already cleared (ADR-054) and
  is unaffected.

### Published vector artifact path (resolved from the builder)

`--output-root` is used verbatim as the family root. On success the builder
`os.replace`s staging into `<output-root>/artifacts/<build_id>/` (immutable,
`artifact_status: COMMITTED`) and writes `<output-root>/manifest.json` (pointer,
carries `artifact_directory: "artifacts/<build_id>"`) and `<output-root>/CURRENT`
(the `build_id`). There is **no** `m2-vector-index-v1` subdirectory
(`OUTPUT_ARTIFACT_ROOT_NAME` is dead code).
`run_retrieval_production_validation --vector-artifact` and `VectorSearcher` take
the **`<output-root>` family root**, never the inner `artifacts/<build_id>`.

Resolve + verify after a build:

```bash
test -f "$OUT/manifest.json" && test -f "$OUT/CURRENT" || echo "NOT PUBLISHED"
BUILD_ID="$(cat "$OUT/CURRENT")"
ART="$OUT/artifacts/$BUILD_ID"
python - <<'PY'
import json, os
out = os.environ["OUT"]
top = json.load(open(f"{out}/manifest.json"))
inner = json.load(open(f"{out}/artifacts/{open(f'{out}/CURRENT').read().strip()}/manifest.json"))
assert top["artifact_directory"] == f"artifacts/{open(f'{out}/CURRENT').read().strip()}"
assert inner["artifact_status"] == "COMMITTED", inner["artifact_status"]
print("vector_count        :", inner["vector_count"])
print("embedding_fingerprint:", inner["embedding_fingerprint"])
PY
```

`vector_count` MUST print `1743311`; `embedding_fingerprint` MUST print
`ebf2adc2a61d75a65db3829163a003e729095310360b9b162b570b34579585b3`.

### FINAL 1× RTX 5090 runbook (retrieval only; no Qwen)

Machine-specific placeholders only: `$CORPUS` = path to the committed M2 corpus
artifact directory (its `manifest.json` has `artifact_status: COMMITTED`,
`artifact_id 4f39603a89e213129de6c57e1234456e88086832c8b61bd9821770d76a6dc78e`,
`source_inventory_sha256
9ea9e073a8aff69e48ae9b258a2bd657cd5194a52de9742e89f5bdb8d17f61c9`,
`chunk_count 1743311`); `$BM25` = path to the committed `m3-bm25-index-v1`
directory bound to `$CORPUS`; `$OUT` = chosen vector output root; `$GPU` = CUDA
device index; host/ports live in `deploy/serving/retrieval-endpoints.json`.

**A. Environment / dependencies**

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-orchestration.txt          # langgraph==1.2.10
pip install "faiss-cpu>=1.8" torch --index-url <cuda wheel index>   # + faiss-gpu if used
pip install "transformers>=4.44" huggingface_hub
export HF_HOME="$HOME/.cache/huggingface"
python -c "import faiss, torch, transformers; print('cuda', torch.cuda.is_available())"
```

**B. Artifact restore** — place `$CORPUS` and `$BM25` on local disk (they are the
immutable committed artifacts; do not rebuild). Pre-cache the pinned weights:

```bash
python -c "from src.indexing.embedding_indexer import load_bge_m3_encoder as L; L(device='cpu')"
python -c "from src.retrieval.reranker import load_bge_reranker as L; L(device='cpu')"
```

BGE-M3 pin: `BAAI/bge-m3` @ `5617a9f61b028005a4858fdac845db406aefb181`.
Reranker pin: `BAAI/bge-reranker-v2-m3`.

**C. Preflight (read-only, no model):**

```bash
python -m src.indexing.embedding_build_preflight \
  --input-artifact "$CORPUS" --output-root "$OUT" --max-vectors-per-shard 100000
```

`mode: FRESH` (no `staging_conflict`) → E · `mode: RESUME` → F ·
`mode: ALREADY_PUBLISHED` → G · `staging_conflict: true` → delete the named
`$OUT/.staging/<build_id>` deliberately, re-run C.

**D. Batch calibration (never publishes; run before E/F):**

```bash
CUDA_VISIBLE_DEVICES=$GPU python -m src.indexing.embedding_batch_calibration \
  --input-artifact "$CORPUS" --sample-size 2000 \
  --batch-sizes 8,16,32,64 --device cuda --min-speedup 1.05
```

Take `recommendation.recommended_batch_size` → `$BS`.

**E. FRESH build (only on preflight `FRESH`, no conflict — no `--resume`):**

```bash
CUDA_VISIBLE_DEVICES=$GPU python -m src.indexing.embedding_artifact_builder \
  --input-artifact "$CORPUS" --output-root "$OUT" \
  --device cuda --batch-size "$BS" --max-vectors-per-shard 100000
```

**F. RESUME build (only on preflight `RESUME`):**

```bash
CUDA_VISIBLE_DEVICES=$GPU python -m src.indexing.embedding_artifact_builder \
  --input-artifact "$CORPUS" --output-root "$OUT" \
  --device cuda --batch-size "$BS" --max-vectors-per-shard 100000 --resume
```

**G. Resolve exact committed vector artifact path** — run the "Resolve + verify
after a build" block above with `OUT="$OUT"`. `--vector-artifact` for L is
`"$OUT"` (the family root).

**H. Start BGE-M3 endpoint** (example vLLM; any OpenAI-compatible `/v1/embeddings`
server works):

```bash
CUDA_VISIBLE_DEVICES=$GPU python -m vllm.entrypoints.openai.api_server \
  --model BAAI/bge-m3 --revision 5617a9f61b028005a4858fdac845db406aefb181 \
  --task embed --port <EMBED_PORT>
```

**I. Start BGE-reranker endpoint** (`/score`):

```bash
CUDA_VISIBLE_DEVICES=$GPU python -m vllm.entrypoints.openai.api_server \
  --model BAAI/bge-reranker-v2-m3 --task score --port <RERANK_PORT>
```

Then edit `deploy/serving/retrieval-endpoints.json`: set `embedding.base_url`
and `reranker.base_url` to `http://<host>:<EMBED_PORT>` / `:<RERANK_PORT>`.

**J. Live health checks:**

```bash
curl -fsS "http://<host>:<EMBED_PORT>/v1/models"
curl -fsS "http://<host>:<RERANK_PORT>/v1/models"
```

**K. Deterministic retrieval smoke:**

```bash
CUDA_VISIBLE_DEVICES="" python -m src.evaluation.run_retrieval_execution_smoke \
  --corpus-artifact "$CORPUS" --vector-artifact "$OUT" --bm25-artifact "$BM25" \
  --queries deploy/retrieval/smoke-queries.jsonl --top-k 10 \
  > smoke-result.json
echo "smoke exit=$?"
```

Exit 0 ⇔ `ran && byte_identical && crashes==0 && timeouts==0`.

**L. Retrieval production validation:**

```bash
python -m src.evaluation.run_retrieval_production_validation \
  --corpus-artifact "$CORPUS" --vector-artifact "$OUT" --bm25-artifact "$BM25" \
  --serving-config deploy/serving/retrieval-endpoints.json \
  --retrieval-smoke smoke-result.json \
  --operator-expected-vector-count 1743311
echo "validation exit=$?"
```

**M. Exact PASS conditions** — L prints `overall_status: PASS`,
`clears_gpu_pending: true`, `live_serving_status: PASS`, and every
`artifact_serving_checks[*].status == PASS`:

- `corpus_index_compatibility` PASS · `bm25_compatibility` PASS
- `no_table_count_substitution` PASS · `canonical_corpus_chunk_count` PASS
  (`chunk_count == 1743311`) · `vector_count_equals_canonical_chunk_count` PASS
- `shard_and_sqlite_integrity` PASS (every shard sha256 ok, `sum(shard
  vector_count) == 1743311`, SQLite `integrity_check` ok)
- `embedding_fingerprint` PASS
  (`== ebf2adc2a61d75a65db3829163a003e729095310360b9b162b570b34579585b3`)
- `live_bge_m3_embedding_parity` PASS (dim 1024, finite, min cosine ≥ 0.999)
- `live_bge_reranker_parity` PASS (shape + order parity)
- `deterministic_retrieval_execution` PASS (from K: `byte_identical`, 0 crashes,
  0 timeouts)

On that result: **TASK-109 clears** and the retrieval portion of
`GPU_PRODUCTION_VALIDATION_PENDING` clears. `RETRIEVAL_QUALITY` stays
`BLOCKED_GOLD_DATA` and does not gate the flag. There is no LLM-serving sub-gate.
**TASK-100** additionally needs real per-question gold answers (still
`BLOCKED_GOLD_DATA`); **TASK-101** is not GPU-gated in current v1 (no LLM ⇒ no
CHEAP-vs-STRONG delta) and stays blocked on model-backed ports being built.

---

## Deferred Decisions

The provided source materials do not fully specify the following. Do not silently treat them as final:

- exact application frameworks outside the accepted M9 LangGraph boundary,
- exact repository package structure,
- exact database/vector-store choice,
- exact model provider,
- authoritative company-alias dataset and storage mechanism,
- authoritative financial-metric vocabulary, synonym source, and storage mechanism,
- requested currency/unit vocabulary beyond TASK-017 v1 scale expressions,
- exact production accuracy/latency/cost thresholds,
- exact deployment topology,
- handling of `aggregated`, generic, explanatory, and unlabeled reports through statement-scope mapping or abstention behavior.

Resolve each through a future ADR when implementation constraints are known.
