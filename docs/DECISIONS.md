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

Implemented as opt-in M11 under ADR-067. The original post-v1 decision below
remains the policy basis; the mandatory v1 path still does not require memory.

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
represented. M9's `OrchestrationState` / `AttemptRecord` contracts are immutable and
strictly validated on construction (ADR-045), so most contradictions cannot
exist in a validated state — a naive detector would be dead code.

### Decision

Keep the observer pure and read-only (no state mutation, routing, retry,
repair, budget, or answer change; never raises on a validated state) and add
only detectors that key off an invariant M9's own validation does **not**
enforce, plus one minimal raw-payload entry point. Events carry only stable
diagnostic scalars — never `binding_ref`, evidence, execution output, prompts,
or secrets.

- **`CONFLICTING_WORKER_STATE`.** `OrchestrationState._validate_pass` constrains only the
  `PASS` direction, so a terminal non-`PASS` outcome whose final `AttemptRecord`
  still carries an M8 `RetryDirective` with `action == PASS` is representable and
  contradictory. A `CLARIFICATION` terminal outcome with a non-empty attempt
  history is likewise contradictory (clarification responses are built before
  any executable attempt). Either fires the event, at most once.
- **`STALE_WORKER_STATE`.** `build_failure_response` always stamps
  `FailureAttribution.attempt_index` with `attempts[-1].attempt_index` (or
  `None`), and `OrchestrationState` never re-checks that pointer against the attempt tuple.
  An attribution pointing at a superseded / out-of-range / absent attempt is a
  stale observation that never advanced with the run.
- **`MESSAGE_SCHEMA_CORRUPTION`.** The dict passed between orchestration nodes is
  the coordination message; `OrchestrationState.from_dict` is the fail-closed edge every
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
  (`src/orchestration/nodes.py`) calls `supervise` and `verify` as
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
  (4 Qwen-free `RetrievalQuery` rows) through the real `HybridRetriever`
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
  something the runner drives itself. Driving `HybridRetriever` end to end needs
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

## ADR-058 — Separate fixture regression from recorded real-corpus TASK-100 baseline

**Status:** Accepted (evaluation tooling implemented; bounded real-corpus pilot
measured on 2026-10-10).

### Context

TASK-100's old CPU-only runner set `established=true` when three fixtures passed.
That contradicted ADR-057's requirement for production retrieval validation and
independent real gold answers. Passing every case is also not a prerequisite to
measure a correctness baseline; errors are part of the measurement.

### Decision

- Version the baseline report as `m10-strong-strict-correctness-baseline-v2`.
  Fixtures report `FIXTURE_ONLY`, `CPU_FIXTURE`, and `established=false` while
  retaining a regression-only exit code and `fixture_passed` field.
- Add recorded-E2E scoring using the existing M9 checkpoint, gold validator,
  corpus reference index, and deterministic scale conversion contracts. The
  operator supplies an export with LIVE source declaration, exact build ids,
  and terminal states bound to gold ids/questions. Replay is labelled explicitly;
  it does not independently prove the origin or freshness of exported records.
- Require complete ADR-057 artifact/live gates and matching corpus/index ids.
  Do not substitute Recall@k, fixtures, or unverified answers for answer accuracy.
- Include missing answers and abstentions in the denominator. Compare value,
  scale and unit, and cited evidence grounding. Unknown gold scales block
  establishment; no metadata is invented. No trace accuracy is claimed without
  gold reasoning programs. Numeric acceptance thresholds remain TBD.

### Current operational limits

On 2026-10-09, the exact LFS gold object (`8ec4d77d…68b9b1`, 46,778 bytes) was
read back from the origin and checked against its pointer hash. It contains
37 VERIFIED LOOKUP cases with answers, all with null gold scale. At that initial
audit the checkout had no production corpus/vector/BM25 artifacts or real M9
run export. The 37-case
retrieval logs therefore do not close TASK-100. A scale-verified gold set covering
STRONG/STRICT plans and corresponding real terminal M9 runs are still needed.
No production routing or public pipeline contract changes.

On 2026-10-10 a separate, curated three-case AVERAGE/LNST gold set was frozen
from the canonical raw reports with explicit RAW/VND answers and source-cell
audit. The real M9 export routes every case STRONG/STRICT. The existing scorer,
bound to the matching historical ADR-057 corpus/vector/BM25 validation, reports
`MEASURED`, `established=true`, zero blockers and **0/3 correct answers**.
All three abstain at Evidence (`EVIDENCE_LOCATION_AMBIGUOUS`); no live Programmer,
sandbox or verification result was produced. Reports are
`scratch_logs/task100-strong-live-e2e.json` and `task100-strong-baseline.json`.
This is a bounded failure baseline, not an official ViFinQA benchmark or a
release-quality claim. Fresh Mac HTTP endpoint parity remains unmeasured;
the existing report attests the same immutable artifact build IDs. Thresholds
remain TBD and the public contracts and routing policy are unchanged.

## ADR-059 — Pin Darwin CoreFoundation startup in the exact worker environment

**Status:** Accepted

On the current macOS host, launching Python under `sandbox-exec` with the
approved six variables adds `__CF_USER_TEXT_ENCODING` with per-user settings.
The exact environment check then correctly rejects startup before calculation,
causing the sandbox/oracle failures seen on 2026-10-09.

On Darwin only, `expected_worker_environment` now supplies
`__CF_USER_TEXT_ENCODING=0x<os.getuid() in uppercase hex>:0:0`. This is generated
deterministically and never inherited from the parent. The fixed UID/encoding
form follows [Apple's own subprocess startup code](https://github.com/apple-oss-distributions/webdavfs/blob/main/WebDAVPlugin/WebDAV_Mount.c).
UTF-8 protocol I/O remains explicit. Exact environment equality, environment
clearing, audit guards, resource limits and the OS sandbox remain enforced.
Linux retains exactly the existing six variables. No M7 request/result schema,
DSL operation, production launcher, or model routing changes.

The current execution host is macOS; RTX 5090 run evidence in ADR-057 is
historical and does not describe the current deployment. This fix and its
validation require no CUDA or model serving. Full-corpus model/index validation
on Mac remains a separate measured operational task, subject to available memory.

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


## ADR-060 — Use DeepSeek Flash API for live generative M9 ports

Status: accepted by user on 2026-10-09.

NLU and Programmer use the OpenAI-compatible DeepSeek API through the existing
serving client. Deployment model ID defaults to `deepseek-v4-flash`; DeepSeek's
current documentation states this legacy alias is served by V4.1 Flash. Record
the requested model ID; do not claim an immutable V4 checkpoint. NLU uses
non-thinking mode; a routed STRONG Programmer uses thinking with high reasoning
effort. Logical roles and deterministic CHEAP/STRONG and LIGHT/STRICT routing
remain unchanged, even though API roles share one model ID and base URL.

This API deployment does not instantiate the separate local vLLM process
topology. BGE-M3, BGE reranking, evidence provenance, numeric masking, symbolic
Program validation, isolated sandbox and deterministic verification are reused.
API credentials stay in the ignored `.env` or environment, enter HTTPS bearer
headers only, and are never forwarded through redirects or exported with runs.

The live NLU uses existing resolver registries. Unknown metrics and absent scope
remain unresolved; the current business registry supports only LNST. Raw
questions enter inference without gold answers or evidence IDs. The live export
runner records terminal M9 states and abstentions without tier relabeling;
ADR-057/058 still gate TASK-100 establishment.

## ADR-061 — Make real income-statement evidence reachable for the TASK-100 pilot

Status: implemented 2026-10-10 at the user's request to fix TASK-100 retrieval and
cell matching; the design choices below await user review.

The 0/3 TASK-100 pilot failed for stacked reasons, each fixed at its owner:

- **Evidence join.** `_locations_by_evidence` joined items to locations by
  report/page/table/label paths, which are not unique when distinct cells share
  paths. M3 now gives each TABLE `EvidenceItem` its `CellLocation.location_id`
  (the same hash as before), and the join narrows label matches by that exact
  identity. It never widens; unresolved ties still stop with
  `EVIDENCE_LOCATION_AMBIGUOUS`.
- **Provenance scan.** `EvidenceProvenanceRepository.preload` finds all
  candidate records in one corpus pass and parses each report once. Output is
  byte-identical on the recorded pilot.
- **BM25 metadata filter.** BM25 ignored TASK-030 ticker/scope/report-year
  constraints that ARCHITECTURE requires for every subquery, so other companies,
  scopes and years entered RRF. The FTS query now applies
  `report_metadata_sql_constraints` before top-k via `json_extract` on the
  stored candidate JSON; the index artifact is unchanged.
- **Subquery wording.** Subquery texts held only `LNST`, the period and the
  company, so no lexical or dense stage reached the income statement (target
  absent from fused top-50 in 6/6 pilot subqueries). Subqueries now add the
  metric registry's names for the canonical metric.
- **VAS statement headers.** Income statements lead with numeric `Mã số` codes
  and `Thuyết minh` notes, and line labels carry ordinals (`18. ...`) that parse
  `MALFORMED`, so header inference recovered no paths at all. A fallback, run
  only when the base rule recovers nothing, skips columns headed exactly
  `Mã số`/`Thuyết minh` and accepts `MALFORMED` label text. The period grammar
  accepts `YYYY VND`/`YYYY VNĐ`.
- **Metric and period matching.** The registry adds `lợi nhuận sau thuế TNDN`
  and `lợi nhuận sau thuế thu nhập doanh nghiệp` for `LNST`.
  `table_label_metric` drops only a leading ordinal and a trailing
  parenthetical before exact registry resolution. `path_periods` adds the
  location's `report_year` for a `Năm nay` column. `Năm trước` is not mapped:
  TASK-030 retrieves period P only from report year P and TASK-080 grounding
  requires that year, so mapping it would only create ambiguity. The M3 locator,
  schema linker and TASK-080 grounding share both functions.

Consequences: `NORMALIZATION_VERSION` is unchanged and no artifact is rebuilt.
Query-time evidence re-derives tables from raw sources and gets the new paths.
Persisted corpus representations, embeddings and sidecar hints still reflect the
old header inference until a re-index. Persisted scale/unit hints only cover
`nghìn/triệu/tỷ/%`, so raw-VND statements with no scale word still stop at
`SCALE_RESOLUTION_NOT_RESOLVED`. That is the next TASK-100 blocker and needs its
own decision (unit-hint extraction plus sidecar/corpus rebuild).

## ADR-062 — Derive scale/unit hints at query time from raw reports, with currency units

Status: implemented 2026-10-10 at the user's request (chosen over a GPU re-index
for now); the design choices below await user review.

### Context

ADR-061 left TASK-100 at `SCALE_RESOLUTION_NOT_RESOLVED`. The pilot income
statements state VND with no scale word: AAA prints `Đơn vị tính: VND` as a
paragraph above the table, FPT heads its columns `2023 VND`. The v1 grammar
(ADR-028) has no currency units. The AAA table also has a footer paragraph, so
the one-to-one adjacency rule links nothing and no TEXT hint reaches it.
Verification needs a non-null source scale (`SOURCE_SCALE_REQUIRED`), so a
unit-only hint is not enough. Corpus representations pin hint IDs
(`scale_unit_hint_ids`), so changing offline hints means rebuilding the corpus,
embeddings, vector index, BM25 and both sidecars, which needs the GPU host.

### Decision

- **Query-time derivation.** `EvidenceProvenanceRepository.scale_unit_hints`
  re-runs `_extract_report_provenance` on the raw report of each selected
  candidate, the same source the Evidence step already rebuilds cells from, and
  associates hints through the unchanged TASK-03F `ScaleUnitHintRetriever`
  rules over an in-memory sidecar view. The live M9 path
  (`src/orchestration/live.py`) uses it instead of persisted sidecar hints, so
  it does not wait for a re-index.
- **One grammar.** Offline corpus and sidecar builds use the same grammar and
  declaration links, so a re-index persists exactly what query time derives.
  Committed artifacts are immutable and bound by artifact ID; they stay as
  built until the live roots are switched.
- **Currency-unit grammar**, matched after NFKC, case folding and whitespace
  collapse. A unit is an optional scale word (`nghìn|ngàn|triệu|tỷ`) plus a
  currency:
  - declared: `đơn vị tính|đơn vị|đvt`, `:`, then a unit with currency
    `vnd|vnđ|đồng`, in cells, captions and paragraphs;
  - in a table cell or caption: `vnd|vnđ` after a scale word, or standing alone
    or after a digit, `(` or `:` (`2023 VND`, `(VND)`).

  `đồng` needs the label because it is also an ordinary word (`hợp đồng`).
  Unlisted OCR variants such as `Ngân VND` yield nothing. The hint span is the
  unit expression, `unit_candidate` is `VND`, and `scale_candidate` is the scale
  word, else `RAW`. `ScaleUnitHint` admits `RAW` only with a unit: RAW is read
  from an explicit currency, never defaulted (ADR-032 holds). v1 scale-word hints
  are unchanged; `triệu VND` yields the v1 `triệu` hint and a MILLION/VND hint,
  which agree.
- **Unit-declaration link.** A paragraph holding a declared unit that has no v1
  link and sits immediately before a table (same page, whitespace-only gap) gets
  a `TableTextLink` with relation `CAPTION` and basis `IMMEDIATE_BEFORE`. Its
  hints reach that table as `LINKED`, the lowest precedence. A declaration never
  links to the table above it.
- **RAW never outranks a magnitude.** A bare `VND` can name a currency category
  (a `VND` column beside `USD`/`AUD`) rather than the unit. So when a cell's
  considered hints hold `RAW` and any of `THOUSAND`/`MILLION`/`BILLION`, at any
  precedence, the resolver returns `AMBIGUOUS` instead of letting precedence
  pick. This narrows ADR-032 for `RAW` only; other precedence behavior,
  masking, binding and verification are unchanged. `RetrievedScaleUnitHint`
  also rejects `RAW` without a unit.

### Consequences

- Replaying the recorded real Batch3 candidates of the three TASK-100 pilot
  questions through the real Evidence node: all three resolve RAW/VND on the
  gold values (AAA via the declaration link, FPT via `2023 VND` headers) and
  reach the Programmer (`scratch_logs/task100-adr062-evidence-replay.json`).
- A full raw-corpus pass (1,973 reports, 492 s) raised no errors and emitted
  6,142 declaration links and unit hints on 46,012 tables. 355 tables hold both
  RAW and scaled currency hints; the rule above makes such conflicts abstain
  (`scratch_logs/task100-adr062-corpus-audit.json`).
- Query-time hints differ from the persisted sidecar: they carry units, take
  HEADER/CELL kinds from ADR-061 header inference, and add declaration links.
  Narrative linked-table IDs and TEXT evidence association still use persisted
  links. Cost is one extra parse per report per query (about 0.2 s on the pilot
  reports).
- Tables mixing currency and `%` columns now mark their hints `AMBIGUOUS`. The
  status is informational; resolution still goes by precedence.

### Re-index (CPU part done 2026-10-10; vectors pending GPU)

- Artifact IDs come from the source inventory, configuration and version
  constants (`_artifact_id`), not from content. Rebuilding with ADR-061/062 code
  under the v1 labels reproduced the committed corpus ID `4f39603a…` for
  different content, so that build was stopped. The re-index sets
  `NORMALIZATION_VERSION = m2a-normalization-v2` (ADR-061 header paths) and
  `REPRESENTATION_VERSION = m2a-representation-v2` (ADR-062 hints and links).
- `main` keeps v1 until the live roots switch, because the schemas reject any
  other version and every live artifact is v1. The bump is three edits, kept
  as `reindex-adr062/version-bump.patch`: the two constants;
  `_validate_input_manifest` reading those constants instead of literal v1
  strings; and a compatibility test that used `m2a-representation-v2` as its
  wrong value. With the patch, the suite passes (1067).
- Built on this CPU host from `reindex-adr062/code` (current code plus the
  patch) into the git-ignored `reindex-adr062/`:
  - corpus `7c14b5498ce3871adfc61d8a475e910ad90e2e2143f6298a3d1342c048499a66`
    (manifest sha256 `1b95d0be…`, 2,512 s, integrity PASS): source inventory
    unchanged (`9ea9e073…`), 1,737,635 representations, 1,743,967 chunks.
    There are 656 more table chunks because ADR-061 header paths lengthen the
    content;
  - provenance sidecar
    `bf12c32520752a5718bf65a524cfd6637c8c6d716f51d66167ea2a570a444c65` (657 s):
    394,995 hints (v1 295,426) and 9,112 links (v1 2,970, plus 6,142
    declaration links). Every one is referenced by a representation;
  - table-class sidecar `86d71848…` (135 s). Its ID comes from the source
    inventory and its 54,173 classifications, both unchanged, so it equals the
    v1 ID. Its manifest binds it to the v2 corpus;
  - BM25 `91071533f3d0aa3b1820844daa4851c3b729d86c04169b94c4f0f3e9bde23f45`
    (398 s, 1,743,967 candidates).
  Build logs: `scratch_logs/task100-adr062-{corpus,provenance,table-class,bm25}-build.json`.
- Persisted-path check on v2: the tables and paragraphs the recorded real
  Batch3 run returned for the three pilot questions, taken from the v2 corpus.
  Persisted sidecar hints equal the query-time hints for every candidate. The
  real Evidence node, fed persisted hints, resolves all six gold values RAW/VND
  and reaches the Programmer
  (`scratch_logs/task100-adr062-v2-evidence-replay.json`).
- Remaining GPU step: the vector index for corpus `7c14b549…`. On this M1
  8 GiB host, BGE-M3 ran at 0.2 chunks/s on MPS (batch 16 ran out of memory),
  about 2,500 h for the corpus. On the GPU host, after applying the patch:
  `python -m src.indexing.embedding_artifact_builder --input-artifact
  <v2 corpus dir> --output-root <root> --device cuda --batch-size <N>
  --max-vectors-per-shard 100000 --resume`.
- Switch: apply the patch to `main`; point the live corpus, vector, BM25 and
  sidecar roots at the v2 set; rerun ADR-057 validation and TASK-100. The live
  path may then read persisted hints again, which equal the query-time ones
  for artifacts built by the same code.

## ADR-063 — Reuse vectors of byte-identical chunk content when rebuilding the vector index

Status: implemented 2026-10-11 (`src/indexing/vector_reuse.py`); real v2 vector
build and ADR-057 validation completed on RTX 3090 on 2026-10-13.

### Context

The ADR-062 re-index needs a vector index for v2 corpus `7c14b549…`. The
streaming builder embeds every chunk. On this M1 8 GiB host that is about
2,500 h, yet 1,736,667 of 1,743,967 v2 chunks (99.6%) have content identical
to a v1 chunk. Only 7,300 table chunks are new (27.8M tokens), changed by
ADR-061 header paths.

### Decision

`embed_chunks` embeds only `chunk.content`. So under the same approved
embedding fingerprint, an earlier vector of byte-identical content is the
vector the model would produce again. `ContentReuseEncoder` wraps the pinned
encoder and passes it to the unchanged `build_streaming_embedding_artifact`
(`--reuse-vectors-from <committed vector artifact dir> --reuse-corpus <its
corpus dir>`). The artifact layout, IDs, fingerprint and validation stay as
they are.

- The source must be `COMMITTED` with `APPROVED_EMBEDDING_FINGERPRINT`. Its
  corpus manifest sha256 must equal the source's `input_manifest_sha256`.
  The source metadata, every FAISS shard and every corpus shard must match
  their recorded sha256. The vector order must equal the corpus record order,
  and the counts must agree. Any failure raises `REUSE_SOURCE_INVALID` before
  anything is built.
- Content is matched by its exact UTF-8 sha256. A hit returns the stored
  float32 vector. A miss goes to the pinned encoder, one text at a time,
  because padded batches of long table chunks exhaust 8 GiB.
- Reused vectors may come from a different device (the v1 index was built on
  an RTX 5090); same model, revision and fp32 config, so differences are
  floating-point rounding only.

### Consequences

- The v2 vector index needs about 27.8M new tokens: about 25 h on this host's
  CPU (314 tokens/s measured on a 6-chunk sample), or a short run on a GPU
  host with `--device cuda`.
- Builder quirk found: `--resume` on a first run fails with
  `CHECKPOINT_INVALID`, because staging is created before the checkpoint check.
  Use `--resume` only to continue an interrupted run.

### Real v2 build and validation (2026-10-13)

- Resumed the compatible 100,000-vector checkpoint on RTX 3090 24 GiB, using
  the pinned CUDA encoder and committed v1 vectors for identical content.
  Published vector artifact
  `f9cee82be944e3eaef01294c3e9d26d7399b33fdb986f8920e4fd8af8f0cb707`:
  `COMMITTED`, 1,743,967 vectors, 18 shards, 1,743,967 SQLite rows, integrity
  PASS (`scratch_logs/task100-adr063-vector-build-cuda.json`).
- Applied `version-bump.patch` to the main source tree. Validation and live M9
  invocations use the matching v2 corpus/vector/BM25/provenance/table-class
  roots recorded in `scratch_logs/adr063-live-artifacts.json`. No v1 artifact
  was overwritten.
- Fresh ADR-057 artifact and LIVE embedding/reranker parity checks PASS;
  `clears_gpu_pending=true`. The existing smoke function used existing CUDA
  HTTP adapters, top-k 10 and the unchanged 30-second timeout: 4 queries,
  byte-identical repeated results, zero crashes/timeouts. Evidence:
  `scratch_logs/adr063-production-validation.json` and
  `scratch_logs/adr063-retrieval-served-smoke.json`. Recall@k/MRR was not measured.
- Recreated the missing three-case AVERAGE/LNST gold under user authorization.
  Raw annual row 60, headers, scope, VND, exact source cells and the 2023
  comparative values were audited independently of inference; Decimal
  computes the answers. All 1,973 downloaded raw reports match canonical
  hashes. Gold validation has zero errors/warnings. The gold is agent-audited,
  **not human-reviewed or an official benchmark**; provenance records this in
  `gold/retrieval/strong-strict-source-audit.json`. The singleton `gold.period`
  is null for these two-period questions; the audit preserves both years.
- TASK-100 fresh live runs, scored against matching ADR-057 evidence, are
  `MEASURED`, `established=true`, no blockers, accuracy **1/3 (33.3%)**.
  AAA RIENG passes execution, STRICT verification and grounding, returning
  `99031536285 RAW/VND`. AAA HOP_NHAT stops at Programmer with
  `FORMULA_ARITY_MISMATCH: AVERAGE received 1 refs`; FPT HOP_NHAT exhausts
  bounded attempts with malformed Programmer JSON (`Unterminated string`).
  Evidence: `scratch_logs/task100-adr063-live-e2e.json` and
  `scratch_logs/task100-adr063-baseline.json`. Acceptance thresholds remain
  unset; this measurement does not establish release quality.
- Regression: 2,138 tests pass, 6 skip, 2 fail because optional on-disk real
  500-vector fixtures still declare v1 and are rejected by the v2 contract.
  No validation was relaxed. Supervisor fixtures 14/14, oracle 7/7,
  E2E fixtures 12/12 and baseline fixtures 3/3 pass.


## ADR-064 — Preserve Programmer failure codes and retry at the Programmer stage

**Status:** Implemented; validation results recorded in TASKS.md.

APIProgrammer returns rejected results for malformed JSON, serving failures,
and invalid proposals instead of losing their codes as PROGRAMMER_STAGE_FAILED.
The serving client checks finish_reason before returning content: length is
SERVING_RESPONSE_TRUNCATED, distinct from PROGRAMMER_JSON_INVALID. It records
token usage and finish reason without logging prompt or answer content.

The existing registry supplies formula input order and arity to the prompt.
AVERAGE receives scalar references directly. The validator remains authoritative;
no missing input is invented and no malformed proposal is repaired.

Repairable Programmer rejections use the existing stage-specific attempt
projection and shared Plan.max_retries budget. Evidence, bindings and Plan stay
fixed. Unknown and permanent contract failures abstain. No new LLM call outside
the existing bounded attempt mechanism, contract field, validator bypass, or
sandbox change is introduced. The 4096-token budget remains unchanged pending
actual truncation evidence.


## ADR-065 — Recover OCR-merged profit labels and prefer the requested report year

**Status:** Implemented; validation recorded in TASKS.md.

### Cause

FPT's raw consolidated 2022 income-statement row 60 labels the total as
`Lợi nhuận sau thuế TNDN (60 = 50 - 51 - 52) Phân bổ cho:`. The allocation
heading is merged into the total row. Exact metric matching misses it, while
the 2023 report's comparative column matches LNST/2022. TASK-080 correctly
rejects that later report with REPORT_YEAR_MISMATCH. All three previous FPT
attempts show this same failure. The original 2022 value agrees with the 2023
comparative value and the independently frozen gold source audit.

### Decision

The shared statement-label normalizer removes only an exact terminal allocation
heading before applying its existing ordinal/parenthetical rules. It does not
strip allocation text after the heading, broaden company-mother profit into
total profit, change raw source labels, or flatten header paths.

Within already-retrieved exact schema matches, prefer copied report_year equal
to the requirement period's year. Multiple same-year matches remain ambiguous.
When none exists, retain previous linking behavior so mandatory grounding
verification produces the original failure and its targeted retrieval retry.
An early hard rejection would break that existing recovery mechanism.

No Plan, evidence, numeric binding, Program, sandbox, verifier, retry budget,
retrieval filter, model call, artifact, or registry synonym contract changes.
Report identity and period identity remain separate, and the current same-year
TASK-030/TASK-080 policy remains enforced. No FPT-specific ID or answer is
embedded in production logic.


## ADR-066 — Wide real-data LNST evaluation and v1 acceptance thresholds

**Status:** Accepted 2026-10-13 under the user's goal "đánh giá diện rộng trên
dữ liệu tài chính thật; chốt ngưỡng nghiệm thu chất lượng, độ trễ, chi phí".
Thresholds were fixed in `src/evaluation/wide_eval.py` before the first wide run.

### Gold

`src/evaluation/wide_gold.py` builds `gold/wide/` deterministically (seed
20261013) from the raw ViFinQA annual statements, never from pipeline output:

- value = the LNST total row: TT200 `Mã số 60`, or the bank template's Roman
  numeral `Lợi nhuận sau thuế` line, read from raw HTML;
- current/prior columns oriented by year or `Năm nay`/`Năm trước` headers;
- unit from the table header or nearest `Đơn vị` note (RAW / THOUSAND / MILLION),
  gold stored in RAW VND (GROWTH as PERCENT);
- report-year value accepted only when the next report's prior-year column
  agrees (restated values are skipped, not guessed); restatement tables and
  partial-period tables are skipped; conflicting duplicate rows are skipped;
- the three-case STRONG/STRICT pilot is held out.

The v1 registry supports only LNST with LOOKUP / GROWTH_RATE / AVERAGE, so the
in-scope set is LNST-only: 140 cases (60 LOOKUP, 40 GROWTH_RATE, 40 AVERAGE)
over 75 tickers, including 30 bank-template and 41 MILLION/THOUSAND source
values. A separate 17-question expected-non-answer set covers unsupported
metrics, missing period, missing scope (only where both scopes exist),
unknown companies and periods outside the corpus. The set is agent-built and
cross-checked, not human-reviewed; `wide-lnst-audit.json` records every raw
source row and the confirming next-report value.

### Acceptance thresholds (v1 release gate)

| Group | Metric | Threshold |
|---|---|---|
| Quality | answer accuracy (value + unit + grounding + verification, all in-scope cases) | ≥ 0.85 |
| Quality | lowest per-operation accuracy | ≥ 0.75 |
| Quality | answered precision (correct / PASS answers) | ≥ 0.98 |
| Quality | wrong-answer rate (answered but wrong / in-scope cases) | ≤ 0.02 |
| Quality | expected-non-answer questions answered | 0 |
| Quality | runner errors | 0 |
| Latency | end-to-end p50 / p95 / max per question (sequential) | ≤ 20 s / 45 s / 120 s |
| Cost | mean / p95 LLM cost per question at DeepSeek Flash peak prices | ≤ $0.01 / $0.02 |
| Cost | mean LLM calls per question | ≤ 4 |

Rationale: a financial assistant must prefer abstention to a wrong number, so
precision and wrong-answer rate are the hardest gates; accuracy measures useful
coverage. Latency targets an interactive analyst tool and excludes the one-time
graph/artifact start-up. Cost uses peak prices (cache hits counted at their
price) so off-peak can only be cheaper. Accuracy is reported with a Wilson 95%
interval; the gate uses the point estimate. The verdict is ACCEPTED only when
every threshold holds. Thresholds change only through a new ADR, never to fit
a run.


## ADR-067 — Wire served tier routing and bounded optional experience memory

**Status:** Implemented under the user's TASK-101/120–126 request; synthetic
mechanism validation complete. Production memory quality remains unmeasured.

APIProgrammer consumes the existing activated_model_tier decision for eligible
initial CHEAP calls and preserves explicit STRONG escalation. Separate serving
clients may be supplied per tier; the existing single-client deployment remains
supported using its CHEAP/STRONG inference settings. Plan and verification
profile do not change. Paired experiments measure answer/program validity,
latency, token usage and failure distribution; unpriced monetary costs stay null.

Implement M11 as offline TRAIN/DEV-only distillation of allowlisted strategy and
guard rules plus an immutable, DEV-calibrated BGE-M3 vector index. Source-question
and trajectory hashes preserve provenance without retaining answers or raw
numeric evidence. Only Programmer receives advice, within its existing call.
No advice overrides structured contracts or deterministic execution controls.

The user explicitly chose separate synthetic train/dev data for mechanism
validation. Held-out inputs never write memory and remain disjoint by IDs and
question hashes. Synthetic accuracy and relevance calibration are not production
quality evidence. BASE/tools/tools+memory ablation retains mandatory validation,
OS execution and verification in every arm. No free-form arithmetic baseline or
unbounded self-reflection loop is introduced. Memory remains opt-in.

The published synthetic-v2 index contains four entries from three TRAIN
trajectories and uses four disjoint DEV examples. BGE-M3 calibration separates
min-positive cosine 0.9892331299507086 from max-negative 0.9818967904639953,
with threshold 0.985564960207352. A two-period AVERAGE strategy transfers to
three-period DEV/held-out contexts without changing the registry or bindings.

Latest live held-out ablation reports BASE 4/4, TOOLS_ONLY 4/4, TOOLS_MEMORY 4/4;
memory accuracy delta is zero, and observed mean latency is higher with memory.
All final program traces match the independently generated oracle after graph-ID
normalization. The preceding v2 run had BASE 3/4 and an unadvised comparison
control that varied in retry behavior. Both reports are retained. These four
cases provide no causal benefit claim or production retrieval measurement.
Paired tier measurements use one DeepSeek endpoint with different thinking
settings, not two separately deployed models. Token counts and latency are
observed; monetary costs remain null because pricing was not supplied.

TASK-101 additionally checks three separately source-audited real-report
LOOKUP cases without memory. Both serving modes answer 3/3 and produce oracle-
equivalent final programs; quality delta is zero. Completion tokens are 600
versus 1,731, including NLU. CHEAP-first sequential timing includes storage and
retrieval warmup and is not a causal latency comparison. Report:
`scratch_logs/task101-real-lookup-routing-scored.json`. Broad production quality
remains unmeasured; the original TASK-100 AVERAGE gold is unchanged.

Validation and task-by-task evidence: `docs/TASKS.md`, M11 completion evidence;
raw report: `scratch_logs/m11-memory-live-trace-eval.json`. Optional v1 retrieval
artifact failures remain visible in the full regression; no checks were weakened.


## ADR-068 — Make real-statement layouts reachable: OCR-tolerant table class, VAS header variants, positional record reads

**Status:** Implemented 2026-10-13 after the first ADR-066 wide-run cases failed
at the evidence stage; validation recorded in TASKS.md.

### Cause

The first wide-run questions all abstained although NLU, planning and the
retrieval filter were correct:

- `TABLE_CLASS_UNAVAILABLE` (GAS 2017, BSR 2019): the right LNST cell was found
  but its income statement had no table class. The sidecar matched statement
  titles literally and OCR corrupts tone marks ("KẾT QUÀ", "KÊT", "HOAT ĐÔNG"),
  writes form codes with spaces ("B 02 – DN/HN"), and bank (Thông tư 49) forms
  ("B03/TCTD", "Báo cáo kết quả hoạt động riêng/hợp nhất") were not registered.
  34 of 209 wide-gold tables had no class.
- `EVIDENCE_INCOMPLETE` (PRT 2023): the cell was found but its "Năm 2023 / VND"
  column did not name a period; only "2023", "2023 VND" and "Năm nay" did.
- `EVIDENCE_INCOMPLETE` (EIB 2023): header inference recovered no structure for
  the bank layout (a leading STT column mixing "1" and "XIII"), so the chunk is a
  flat cell list. 21 of 209 wide-gold tables were flat for related reasons:
  OCR-joined "Mãsố"/"Thuyếtminh"/"2020VND", "Mã"/"TM"/"STT" or merged
  "Mã số Thuyết minh" headers, "18.1" ordinals, and one misaligned EPS row that
  made the whole table unrecoverable.
- Latency: `EvidenceProvenanceRepository.preload` and `CorpusChunkLookup` scanned
  and re-validated the full 1.74M-record corpus per question (52–198 s/question).

### Decision

- Table-class registry v2: titles and codes match case- and diacritic-insensitively
  (one-for-one folding, so spans still index raw text); codes are written
  "B 02-DN" so the zero-width gap also accepts spaced forms; TT49 codes
  B02–B05/TCTD and the bank income-statement titles are registered. The
  conflicting-classes-means-no-hint rule is unchanged. On a 150-report sample no
  hint changed class; hints lost were mostly notes tables previously mislabelled
  through OCR-corrupted notes headers. Rebuilt artifact
  `cfde11f8…3ca3aa3` (71,768 hints, was 54,173); wide-gold coverage 209/209.
- Query-time `path_periods` also reads "Năm YYYY", "Năm tài chính YYYY" and
  "YYYY Triệu/nghìn đồng" year headers. The indexing period grammar accepts the
  VND-family "YYYY Triệu đồng" / "YYYY nghìn VND" / OCR-joined "2020VND"; "Năm 2024"
  and other currencies stay rejected there.
- Header inference label-column rule: label-column headers compare without
  whitespace and include "Mã", "TM", "STT" and the merged "Mã số Thuyết minh"; a
  first column of only line ordinals ("1", "18.1", "XIII") is a label column; rows
  off a dominant (≥ 90 %) row-header width are left unstructured instead of
  discarding the table. When a table has label columns, this rule replaces the
  generic one if the generic rule recovers nothing consistent or fewer rows (it
  drops lines whose labels carry formulas such as "(60=50-51-52)" — exactly the
  LNST line — and reads VAS codes as data).
- The statement-label normalizer also removes OCR-merged "Trong đó:" headings
  (as ADR-065 does for "Phân bổ cho:"), page carry-over notes ("(mang/chuyển sang
  trang sau)", "(mang sang từ trang trước)"), OCR math delimiters "\(" "\)",
  nested trailing parentheticals ("(60=50-(51+52))"), dot-less ordinals ("18 ")
  and a "Tổng" prefix on the "lợi nhuận sau thuế" line. The metric registry and
  its synonyms are unchanged.
- Query-time year headers also read "Năm nay VND" and "(Cho) năm (tài chính) kết
  thúc ngày 31 tháng 12 năm YYYY". Table unit hints accept a unit glued to its
  year ("Năm 2022VND", "2016Triệu VND") or after "Năm nay"/"Năm trước"; unit
  declarations accept OCR "Đơn vi tính".
- Measured on every wide-gold source cell with query-time evidence: metric,
  period and unit hint all resolve for 201 of 209 tables (176 after only the
  first round of these changes). The remaining 8: a September fiscal year end (HSG, 4), units glued to
  following words (2), and two OCR-broken headers.
- Schema linking (narrows ADR-065): several valid same-year matches that come
  from one report and carry the identical parsed value are one reported figure
  (a statement continued across pages repeats its LNST line, a note restates it),
  so the income-statement cell, then the fullest table (most located cells — the
  statement itself), then the lowest evidence ID, is chosen. Matches with
  different values, or from different reports, stay AMBIGUOUS.
- Table-class gate: only TABLE evidence whose source-backed class is one the Plan
  requires may satisfy a requirement and flow on to scale resolution, linking and
  masking. A same-value note table without a class no longer vetoes the classified
  statement cell; when no eligible evidence remains, the original
  TABLE_CLASS_UNAVAILABLE / TABLE_CLASS_MISMATCH failure is raised as before.
- Sandbox: the requested output scale/unit applies to the answer. An intermediate
  IDENTITY/COLLECT step feeding a formula keeps its source scale (AVERAGE and
  GROWTH_RATE honour the request themselves), so "tăng trưởng bao nhiêu %" no
  longer tries to convert VND inputs to PERCENT.
- A currency followed by "/" ("VND/cổ phiếu" on the EPS line) is a rate, not the
  table's amount unit, and yields no scale hint (it made every bank statement with
  "Triệu VND" columns AMBIGUOUS). A declared unit glued to the next year header
  ("Đơn vị tính: VNDNăm 2020") is read.
- The temporal parser no longer reads the adjective "quý" in fixed noun phrases
  ("Vàng bạc Đá quý Phú Nhuận", "kim loại quý", "quý khách/cổ đông/công ty",
  "quý giá/báu") as an unnumbered quarter; a bare "quý" still requires
  clarification.
- GROWTH_RATE periods are planned in chronological order. The formula consumes
  (previous, current) and the validator requires Plan requirement order, so the
  natural wording "năm 2024 so với năm 2023" made every growth question fail with
  FORMULA_INPUT_ORDER_MISMATCH after exhausting Programmer retries. Plan fields
  are unchanged; AVERAGE keeps question order.
- `CorpusRecordLocator` reads selected records by the shard/line position the
  committed M2B vector metadata stores, re-validating each line exactly as a
  full pass does (`parse_input_record`, now shared with `_iter_input_records`)
  and requiring equal chunk and representation IDs. The live graph uses it for
  both lookups; without vector metadata the full pass remains.

### Consequences

- The corpus, vector and BM25 artifacts are not rebuilt: their chunks for the
  previously flat tables stay flat, so retrieval of those tables relies on their
  flat text (the EIB 2023 bank statement is not retrieved). A future re-index
  with these header rules is the fix; ADR-063 vector reuse keeps it cheap.
- No Plan, evidence, verification, retry or model contract changes.

## ADR-069 — Opt-in Text-to-Pandas pipeline with independent verification

**Status:** Implemented for the user-requested Text-to-Pandas work. Default
orchestration remains M9 until the new path passes measured acceptance gates.

The new path uses the existing catalog builder, deterministic question discovery,
`PandasCodeGenerator`, AST validator and isolated Pandas worker. `PandasPipeline.ask`
connects these components into a Python API. `src.text_to_pandas.run` exposes the
same API as a CLI for real-model demos and resumable benchmarks. No new HTTP
service, dependency, model call for verification or symbolic DSL is added.

Generated Python is limited to the approved DataFrame namespace, validated
before execution and again inside a fresh worker. The parent never executes
generated code. The existing M7 process/environment/resource boundary is reused;
production Linux uses the separate pinned `t2p-nsjail.cfg`. Local process tests
do not establish NsJail production deployment readiness.

Verification independently selects the requested facts from the catalog. It
checks exact, unmodified evidence rows, report/cell provenance, source path year,
raw numeric parsing and scale, output unit/type and Decimal reference arithmetic.
ROE/ROA require prior-year opening balances. Growth and average use existing
FormulaRegistry semantics; ratios use the shared `src.formulas.ratios` definitions.
A second isolated execution with perturbed source values checks the generated
program against the reference again, catching constants and coincidental correct
answers. This adds one sandbox invocation, no LLM call, to successful attempts.
One perturbation is a regression guard, not a proof for arbitrary Python.

Repair is bounded to two retries (three generations total). Validation, ordinary
runtime and verification feedback can repair code; security/resource/infrastructure
failures do not cause open-ended recovery. An independent missing input or zero
denominator yields NO_DATA. Unknown semantics fail closed. Explicit report scope
is required. Supported verification includes named metrics, one ratio, two-period
growth, mean/sum, keyed comparisons/time series and single-year rankings. Mixed
units, threshold screening, unrecognised line items and difference semantics are
not promoted to verified answers.

Financial comparisons allow four floating-point ULPs, with an absolute floor
of 1e-12, rather than a relative amount tolerance that could conceal a large
VND error. DataFrame result columns must contain only the verified key/value
schema; additional financial columns and compound ratio operations fail closed.

Each request gets a unique run directory. The request, every completed attempt
and terminal response are written with exclusive creation. Attempts retain code,
typed failures, validation/execution/verification reports and token telemetry.
Repeated caller IDs cannot overwrite prior history or create caller-selected
paths. The CLI atomically saves progress after each question; resume requires
matching catalog/model/code fingerprint, retry budget and question set.

The benchmark retains all 140 frozen wide LNST positive cases and ADR-066's
thresholds. The six legacy UNSUPPORTED_METRIC controls are now in scope for the
larger catalog; they remain unchanged in the old benchmark. The separate
`t2p-negative.jsonl` retains the other 11 controls and adds six market-data and
subannual-data controls. No gold answers or expected source IDs enter generation,
discovery, catalog building or verification. Scoring separately measures numeric,
unit and exact gold-cell correctness, complete-run evidence, negative false
answers, latency, tokens and configured-price cost. A partial/fixture run or
missing telemetry cannot receive ACCEPTED. No default changes are automatic.

## ADR-070 — NsJail profile compatibility, broad gold and measured load gates

**Status:** Profile fixes, concurrent runner and measurements implemented.
Full production NsJail conformance and the broad accuracy gate remain open.

### Decision

Keep the existing sandbox limits and fail-closed production selection. Fix
three defects demonstrated with NsJail 3.4: incompatible simultaneous memsw
and swap ceilings, seccomp syntax unsupported by bundled kafel, and an empty
outside UID/GID mapping that mapped a root launcher to host root. Retain
`cgroup_mem_swap_max: 0`, use kafel `//` comments/plain fork names/numeric
syscalls where needed, and explicitly map UID/GID 65534 inside and outside.
The policy is x86_64-specific; another architecture needs its own reviewed pin.

The root-owned deployment files and `src/sandbox/production.py` agree on:

| File | SHA-256 |
|---|---|
| `deploy/sandbox/m7-nsjail.cfg` | `b9a0f9a6b1e1fede6200f5f75cba13b65df183815267a394e27e7b5fecd22623` |
| `deploy/sandbox/t2p-nsjail.cfg` | `17032b290270864f106d6f859027792573118cfb366d9456fe2d7a9e9f0691f0` |
| `deploy/sandbox/m7-seccomp.policy` | `528709ce4db5ff3d87c14bd298784c422de0a3f4d1dc1b74945080d6a95604f3` |

Add opt-in live-runner concurrency, default 1, with request-local history and
telemetry, locked atomic progress writes and wall-time reporting. Resume must
retain concurrency and all existing identity fields. A final source check
catches changes during the last in-flight requests. No NLU, financial formula,
verification or default routing change is part of this work.

### Independent broad measurement

Broad gold v1 uses raw statement line codes and whole-word labels, explicit
column orientation and unit notes, independently cross-checked in the next
report. It rejects subsidiary-note rows whose amounts merely equal a code;
the invalid NAB v0 case and original run are archived. Seed 20261014 yields
140 positives across 55 tickers and ten statement metrics/six ratios, plus
18 negative controls. Agent-built gold is not human-reviewed.

The sequential live run scores **105/140 (75.0%)**, Wilson95 0.672–0.814;
106 answered, one wrong gold-cell citation, zero negative false answers and
zero runner errors. The concurrency-8 run scores **106/140 (75.7143%)**,
Wilson95 0.6799–0.8207; 107 answered, the same grounding mismatch, zero negative
false answers and zero runner errors. Both are **NOT_ACCEPTED**: accuracy is
below 0.85 and ratio accuracy is 24/36, below 0.75. NLU/catalog gaps remain
visible; fixes require a new held-out seed before a quality claim.

All 158 concurrent request/history identities and source companies match.
Status agreement is 156/158; exact result agreement is 157/158. All 106
jointly answered questions have identical results. One former equity-average
failure answers correctly; one current-liabilities average changes from FAILED
to UNSUPPORTED. No evidence indicates a cross-request mix-up.

Concurrent wall time is **64.277 s**, throughput **2.458 requests/s**. The old
runner did not record wall time: its first-start/last-end span estimates
480.264 s and 0.329 requests/s, an estimated 7.472x speedup. Concurrent p50/p95
request latency is 3.043/5.733 s. Cached-input shares and repairs differ, so the
lower measured API cost is not attributed to concurrency.

### Sandbox load evidence

Run four matrices sequentially at 1,8,16,32,64, through the actual M7/T2P
executors, with 64 requests per level and 128 at level 64. There are 3,072
requests across the matrices, including 384 contained abuse requests. No
BENIGN_WRONG, ABUSE_ESCAPED, ABUSE_MISCLASSIFIED or harness exceptions occur.
Every level at or below 16 has zero benign failures and no leaks.

| Boundary/user | Highest safe level with zero benign failures, M7/T2P | M7 p95/p99 at 16 (ms) | M7 p95/p99 at 64 (ms) | Matrix gate |
|---|---|---|---|---|
| Local/root | 64/64 | 237.8/251.2 | 1047.7/1105.8 | PASS |
| Local/sbxload | 64/64 | 253.9/313.4 | 1106.2/1182.2 | PASS |
| Namespace-free NsJail/root | 32/64 | 495.6/526.6 | 1724.7/1847.2 | FAIL |
| Namespace-free NsJail/sbxload | 64/64 | 406.9/505.6 | 1590.0/1811.5 | PASS |

The root wrapper has eight M7 WALL_CLOCK_TIMEOUT failures and thread_delta=1
at 64. Leak failure is retained, not excused by the above-target allowance.
Three focused root repeats have 4,0,3 timeouts and no remaining thread after
either the immediate snapshot or one second. The original thread was not
identified; the 0.2-second RSS-monitor join is a plausible cleanup race, not
a proved cause. A diagnostic launched from the repository instead of the
deployment root was rejected by the environment guard; that invalid-cwd
artifact is excluded from the matrix conclusions.

These percentiles measure complete executor calls, including preparation and
cleanup outside the 1,500 ms M7 communicate timeout. The wrapped 64 level
has insufficient latency headroom even where every request returns correctly.
No limit is relaxed. The non-root matrices exercise RLIMIT_NPROC=1 per UID.

### Fresh pinned environment and remaining deployment gate

New Python 3.12.3 and 3.11.16 venvs, each with an initially empty uv cache,
install `requirements-test.txt` and verify all six direct pins: NumPy 2.4.6,
Pandas 2.3.3, faiss-cpu 1.15.1, psutil 7.2.2, pytest 9.1.1, langgraph 1.2.10.
Both final-source suites pass **1,243 tests, 373 subtests, two skips**.
Supervisor 14/14, oracle reasoning 7/7, E2E 12/12, baseline 3/3 and retrieval
8/12 outputs are byte-identical between the venvs. Retrieval's four fixture
failures are deliberate attribution controls, not production retrieval scores.

This unprivileged container cannot create the production namespaces or write
cgroup v2. Reduced NsJail profiles exercise seccomp, rlimits, dropped caps and
no_new_privs; they do not prove full production isolation. Run the real pinned
profiles, focused abuse probes and matrix on a capable Linux host/VM before
rollout. The jail's `/usr` Python must contain Pandas/NumPy; `/venv` is not
mounted. Preserve fail-closed behavior and the unresolved root leak gate.

Evidence: `scratch_logs/sandbox-load-matrix-summary.json`, the four
`sandbox-load-*.json` matrices, `sandbox-load-nsjail-root-thread-diagnostic.json`,
`t2p-broad-live-c8.json`, `t2p-broad-benchmark-c8.json`,
`t2p-broad-concurrency-comparison.json`, and `fresh-pinned-env/`.

### VSF 2022 provenance investigation

Replaying extraction of the hash-verified VSF 2022 consolidated report reproduces
the grounding mismatch. Page 3 is a management explanation letter with a
three-row year-comparison table. A prose mention of the income-statement title
classifies that summary as INCOME_STATEMENT; the code-less whole-label fallback
assigns its LNST row. Page 14 contains the actual B02-DN/HN line 60 with the same
21,108,412,384 VND. `_resolve_metric_duplicates` retains the first equal-value
occurrence and clears the later row's metric, so the summary cell is canonical.
The verifier checks catalog identity/raw value/scale/year and arithmetic but
does not independently prefer primary-statement provenance. Numeric correctness
passes; frozen gold-cell grounding fails. This predates concurrency.

Evidence: `scratch_logs/t2p-vsf-2022-grounding-investigation.json` retains both
tables, source spans, classification matches, layouts, pre/post-dedup facts,
catalog provenance and live/gold checks. Investigation changes no source,
catalog or gold. A follow-up should prefer coded primary-statement provenance
for equal-value duplicates, preserve bank layouts/conflict rejection, add
independent synthetic regressions, build a new catalog and measure new held-out
gold. Do not repair this by admitting the observed summary cell into frozen gold.

---

## ADR-071 — Broad-set phrasing fixes, coded-row catalog provenance, held-out re-measurement

**Status:** Implemented and measured. Default M9 routing is unchanged.

### Context

ADR-070's broad set scored 105/140 (NOT_ACCEPTED). Every miss except one was a
safe abstention, clustered in understanding and catalog provenance:
"dòng tiền thuần từ hoạt động …" was not a CFO/CFI phrase; "nợ phải trả ngắn
hạn" resolved to LIABILITIES; "bình quân" inside the ROE/ROA names was read as
an averaging operation; long ratio names ("tỷ suất lợi nhuận gộp/sau thuế trên
doanh thu thuần", "hệ số nợ phải trả trên vốn chủ sở hữu") were not ratios; and
the VSF 2022 LNST fact cited a code-less management-letter summary instead of
the line-60 row.

### Decision

- Add the missing phrasings as synonyms: CFO/CFI/CFF "dòng tiền thuần từ hoạt
  động …", "nợ phải trả ngắn/dài hạn" in `src/text_to_pandas/metrics.py`, and
  the long ratio names in `src/formulas/ratios.py`, which stays the single
  source of ratio definitions; no formula changes. Longest-match detection
  makes the specific phrase win over the shorter metric inside it.
- Mask detected ratio-name spans before operation-cue detection, so words in a
  ratio's own name are not operations. "ROE bình quân … 2022 và 2023" (an
  average of a ratio) remains a compound operation that fails closed.
- In `_resolve_metric_duplicates`, an agreeing repeat keeps the first row with a
  line code, else the first row. Different-value conflicts still drop all rows;
  code-less bank layouts are unchanged. Build version `t2p-catalog-build-v2`;
  `artifacts/t2p-catalog-v2` (catalog id `c0e872e6…`) differs from v1 only in
  five VSF LNST facts now citing line 60, with zero value/row/coverage changes.
- The load harness waits up to 5 s for threads, children, descriptors and temp
  dirs to return to baseline and reports both end-of-level and settled values.
  The executor's 0.2 s RSS-monitor join can end before one `/proc` scan under
  heavy load; a resource still present after the window is a leak.
- `broad_gold --exclude-gold` keeps a new sample disjoint from earlier gold.

None of the 314 wide/T2P LNST questions changes its understanding, discovery
or prompt context (deterministic before/after comparison), so the ADR-069
acceptance is unaffected; only the 27 broad questions in the clusters change.

### Measurement

The fixes were derived from `gold/broad`, so they are measured on
`gold/broad-h2`: seed 20261015, 140 positives over 58 tickers and 18 negatives,
no (metric, ticker, scope, year) unit or question shared with `gold/broad`, same
templates. Sequential live DeepSeek run with catalog v2: **137/140 (97.86%,
Wilson95 0.939–0.993), ACCEPTED**, all thresholds pass, 137 answered with zero
wrong answers, zero negative false answers, zero runner errors; p50/p95/max
latency 2.90/3.45/4.00 s; $0.00213/query; 0.89 LLM calls/query. The three misses
are catalog gaps (SSI 2018 EQUITY, VIF 2023 CFI) and one model UNSUPPORTED on
HHV current liabilities. Reference only: `gold/broad` at concurrency 8 with
catalog v2 scores 133/140 with zero wrong answers; its seven misses are catalog
gaps (SSI, HHV, VIF, PNJ).

A root namespace-free NsJail matrix rerun at 1,8,16,32,64 passes with zero
residual resources at every level end. Wrapped M7 at 64 has 7/128
WALL_CLOCK_TIMEOUT (p95 1,737 ms), so a host should run at most 32 concurrent
M7 workers under NsJail. Full suites pass in the fresh pinned 3.11/3.12 venvs.

### Limits

h2 shares the builder's question templates, so it measures the fixes on unseen
companies, years and values, not unseen phrasings. Gold is agent-built, not
human-reviewed. Catalog extraction gaps remain. Full production NsJail
conformance still requires a capable Linux host or VM.

Evidence: `scratch_logs/t2p-broad-h2-live.json`, `t2p-broad-h2-benchmark.json`,
`t2p-broad-v2cat-c8-live.json`, `t2p-broad-v2cat-c8-benchmark.json`,
`t2p-catalog-v2-build.json`, `sandbox-load-nsjail-root-settle.json`,
`fresh-pinned-env/adr071-py3*-pytest.txt`.

## ADR-072 — Production gates: catalog v4, production gold and holdouts, platforms, providers, service

**Status:** Implemented and measured on this Linux host. Default M9 routing is
unchanged. Eight of nine gates pass on holdout3; not production-ready because no
gold case has been reviewed by a person. Full NsJail and the macOS/Windows runtime
still need other hosts.

### Context

The production thresholds: 200–300 human-reviewed gold cases with ≥100 independent
holdout; ≥95% correct answers overall and ≥90% in every critical group; ≥99% code
execution success on valid questions with data; 100% provenance on the audited
sources; no fabricated number and every must-refuse case refused; zero sandbox
escapes; no resource growth under stress and soak; an API with SLO, monitoring,
alerting and a tested rollback; ≤32 concurrent M7 workers per host under NsJail
(ADR-071: 7/128 timeouts at 64); the catalog gaps (SSI, HHV, VIF, PNJ); one sandbox
configuration for macOS, Linux and Windows; configuration for several LLM providers.

### Decision

**Catalog v4** (`artifacts/t2p-catalog-v4`, build `t2p-catalog-build-v4`, catalog id
`ddf9bae2236a7b9ab35b0ff5c4f3592aeb6a798731d5dad7958f096017b5f910`; v3 `436a61ce…`
is the same minus the last two fixes). Every gap was root-caused on the raw reports:

- securities companies (SSI) print B0x-CTCK forms: recognised in the catalog (the
  pinned M2 TableClass registry is untouched), TT334 totals mapped first (balance
  sheet 100/200/270/300/310/340/400/440, income 90/200/201), TT200 codes as fallback
  with agreeing labels; "Báo cáo tình hình tài chính" without a CTCK form (banks since
  TT49's amendment) keeps the TT200/bank rules;
- dotted/dashed header dates ("31.12.2022", HHV) orient columns;
- a page title contradicted by the table's coded rows (VIF page headers) yields to
  the rows when the title's statement has no agreeing row and exactly one other has two;
- an OCR "Thuyết minh" header over the label column (PNJ) is recovered;
- wording: "nợ phải trả ngắn/dài hạn", "lưu chuyển tiền thuần (sử dụng vào)/từ …", "TÀISẢN";
- side-by-side group | parent columns use the report's scope (MSN; OCR "Công tv");
- restatement tables never inherit a page layout;
- note numbers read as figures are dropped;
- statement identities (total assets = total capital, current + non-current,
  liabilities + equity against both totals, CFO + CFI + CFF = line 50) clear the
  metrics no passing identity confirms;
- an agreeing repeat prefers the statement line over a "(mang sang trang sau)" copy,
  and agreeing coded rows win over a differing code-less repeat (KBC: an OCR digit).

Against v2: 53,803 metric facts (+1,611 net) and **zero changed values**; every
component of every earlier gold case still matches (wide 220/220, broad 242/242,
broad-h2 241/242 — VIC 2016 CFO is cleared because CFO + CFI + CFF ≠ line 50).
The provenance index no longer assumes `fact_id` is the first sorted key.

**Production gold** (`src/evaluation/production_gold.py`, `gold/production-v1`).
Four splits of 127 positives (45 lookup, 24 growth, 28 average, 30 ratio; 97
corporate and 30 bank cases each) plus 20 must-refuse controls (ten kinds, adding a
missing scope for an existing year, a line banks do not report, a future period and
an injected number). The ADR-070 raw-HTML method gains a whole-label bank path; no
split reuses a report of an earlier split or any earlier gold file. `dev` is for
fixes. A holdout is frozen before any measurement and scored once; afterwards it
becomes development data and a new holdout is built and frozen before any fix:
`holdout` (own phrasings) → `holdout2` (own phrasings) → `holdout3`, whose seed
questions an LLM rewrote (`src/evaluation/paraphrase_gold.py`, auto-validated,
frozen unread) so its wording does not come from the person fixing the system.
`src/evaluation/gold_qa.py` flags gold sources taken from the other scope's column
of a combined group | parent table; such defects (1 of 508 cases) are excluded alike
in every split. Cases stay `AUTO_DERIVABLE` until a named person approves them
(`src/evaluation/gold_review.py`: packets with the printed source rows, a terminal
reviewer, a shared review page, ingestion of its decisions).

**Understanding.** Wording variants are rewritten to one canonical form before
matching (`canonical_phrasing`: lãi/lợi nhuận, kế toán, ròng, giá trị/quy mô, vốn
chủ, lưu chuyển/dòng tiền ± thuần, tỷ số/tỷ lệ/hệ số/khả năng thanh toán, biên lãi,
"nợ (phải trả)/vốn chủ sở hữu", "lợi nhuận/sinh lời trên …"); a description followed
by a ratio acronym in parentheses is that ratio's name; an upper-case token in
parentheses right after a metric or ratio name is an abbreviation, never a ticker
(a company name before "(XYZ)" still refuses). Each step changed exactly the
holdout misses it was derived from and nothing else among ~900 recorded questions,
negatives included.

**Gate** (`src/evaluation/production_gate.py`) computes every threshold from
recorded evidence; missing evidence is `BLOCKED`. Critical groups: operation,
statement (income, balance sheet, cash flow, ratio), template (corporate, bank).

**LLM providers** (`deploy/llm/providers.toml`, `src/serving/providers.py`):
`openai-chat` and `anthropic-messages` protocols; DeepSeek (default, measured),
OpenAI, Azure OpenAI, Anthropic, Gemini, Qwen/DashScope, OpenRouter, Groq, Mistral,
vLLM, Ollama. Credentials only from the environment or `.env`; per-provider JSON
mode, thinking switch, temperature, completion-limit field, headers, extra body,
bounded retries on 408/409/429/5xx/529/timeouts, price list.

**Sandbox platforms** (`deploy/sandbox/sandbox.toml`, `src/sandbox/host.py`,
`src/sandbox/windows_job.py`): local boundaries per OS (Linux rlimits, macOS
`sandbox-exec`, Windows job object), `container-v1` on any OS (digest-pinned image,
no network, read-only root, no capabilities, caps on pids/memory/CPU, unprivileged
user, `env -i`, container removed on timeout), admission caps per isolation mode
(M7: 32 under NsJail) with `CAPACITY_EXCEEDED` after the admission timeout.

**Service** (`src/service/`): stdlib HTTP API with admission control, deadline,
token, `/healthz` `/readyz` `/metrics` `/slo` and reload; SLOs in
`deploy/service/slo.toml`; alert rules defined once and rendered to
`deploy/service/alerts.yml`; releases pinning catalog, generator and code, promoted
after preflight and a canary set, rolled back automatically on a page alert during
the bake window, or by command.

### Measurement

- **Accuracy (DeepSeek, concurrency 8).** dev 127/127. holdout scored once at
  104/127 (81.9%, Wilson95 0.74–0.88); every miss was a safe abstention on unseen
  wording. After the holdout-derived fixes: holdout (now dev) 127/127; holdout2 scored
  once at 104/127 (81.9%), again all safe abstentions (18 wording, 3 "(NII)" read as
  a ticker, 2 catalog). After those fixes and catalog v4: holdout2 (now dev) 126/126
  (one gold defect excluded). **holdout3 scored once: 122/127 (96.1%, Wilson95
  0.911–0.983)**; every group ≥90% (lowest: cash flow 9/10, lookup and bank 93.3%);
  code execution 124/125 (99.2%); provenance 100%; 20/20 refusals. Its five misses
  are safe abstentions (two catalog gaps, two wordings, one parenthesised term read
  as an unknown company). A first holdout3 attempt hit HTTP 402 (insufficient
  provider balance), produced no model output and is archived as invalid. Zero wrong
  answers and zero must-refuse cases answered in every split and run; code
  execution 100% and provenance 100% wherever the question was understood.
- **Sandbox security.** 27 canary probes (secret file and parent-only environment
  disclosure, file write, process spawn, loopback network, introspection, eval/query
  injection, pickle, `/proc`, CPU/memory/recursion/output bombs) run through the
  validator and directly through the worker: 0 escapes locally and under the
  reduced NsJail profile; the focused security tests pass.
- **Capacity.** Reduced NsJail profile, 64 offered M7 requests: uncapped 2/128
  WALL_CLOCK_TIMEOUT; with the 32-worker cap 128/128 correct, p95 1,045 ms, no leaks.
- **Stability.** Stress 1, 8, 16, 32, 64 for both workers passes. Soak 30 min at
  concurrency 16 (26,611 requests, 14.7/s, mixed M7/T2P/abuse): 0 wrong or escaped;
  latency drift 0.98× (M7) and 0.99× (T2P); RSS +3.8 MB fitted (budget 32 MB);
  descriptors, threads, temp dirs and workers identical at every drained checkpoint
  (earlier soaks judged in-load counts, which move with in-flight work; the
  harness now checkpoints).
- **API.** Reliability drill 20/20: canary promotion, metrics, SLO met, sustained
  overload answered with 503 and ticketed after 5 minutes, wrong-catalog and
  wrong-answer releases rejected, a release with a production provider outage paged
  within 2 minutes and rolled back automatically, recovery, manual rollback. The
  drill found that the stdlib server's backlog of 5 reset connections in a burst;
  it is now 512.
- **Tests.** `pytest tests`: 1369 passed, 2 skipped.

### Limits

No case is human-reviewed; the review page and CLI are ready. The holdouts measure
robustness to paraphrases written by this agent (holdout and holdout2) or an LLM
(holdout3), not to questions written by users. Live measurement uses DeepSeek only;
other providers are covered by offline tests. Full NsJail (namespaces, cgroup v2)
and the macOS/Windows CI matrix need capable hosts. The service drill replays
recorded programs on loopback with a simulated clock; no Prometheus/Alertmanager is
deployed.

Evidence: `scratch_logs/goal-production/` (`PROGRESS.md`, `production-gate-holdout1.json`,
`production-gate-holdout2.json`, `holdout*-dev-r1-live.json`, `stability-local.json`,
`security-suite-v3.json`, `security-probes-nsjail.json`, `api-reliability-v3.json`,
`nsjail-m7-offered64-cap{32,64}.json`, `invalid-provider-402/`).

## ADR-073 — Browser UI served by the question API

**Status:** Implemented. Additive API change; the default public response is
unchanged except for the `statement` key on sources.

### Context

People need a chat interface over `/v1/ask` (sidebar with history, chat stream,
a detail canvas for tables, sources and code). The API answered one question at a
time and exposed no coverage, so a client could not tell users which companies and
years exist, and it never returned the program, which the planned "code" view needs.

### Decision

- `src/service/web/` holds a dependency-free page (HTML, CSS, vanilla JS, no build)
  served by the existing stdlib service at `/` and `/static/{app.js,app.css}` from a
  fixed path map, with `Content-Security-Policy: default-src 'none'; script-src 'self';
  style-src 'self'; connect-src 'self'` and `nosniff`. Server and model text is
  rendered with `textContent` only. The page needs no token; data endpoints keep it.
- `GET /v1/catalog` (bearer token like `/v1/ask`): catalog id, release, build version,
  report count, companies (`ticker`, `company_name`, `template`, `first_year`,
  `last_year`, `statement_scopes`) and metric names (`name_vi` from
  `src/text_to_pandas/metrics.py`). No statement values.
- Public sources add `statement` (already in the provenance row), so the UI names
  the statement without parsing identifiers.
- The generated program stays hidden by default. `AIFIN_EXPOSE_PROGRAM=1` (an
  analyst deployment) adds `program` = `{code, explanation, attempts}` from the
  attempt that passed verification, for `ANSWERED` responses only.
- The UI keeps chat history in browser storage and does not send it: the pipeline
  stays single-turn, and the composer says so. Clarifications are completed by the
  user (a year field for `MISSING_PERIOD`, hợp nhất/riêng buttons for
  `MISSING_SCOPE`), which re-asks a full question.
- No user file upload: answers come only from the release's catalog, which carries
  scale, unit and provenance.

### Consequences

The UI shows exactly what the API returns; display rounding is local and CSV export
keeps exact values. A separate frontend stack can replace the page later without an
API change. Cross-company ranking questions remain outside the measured gold groups.


---

## ADR-074 — Role-based file and symbol names; repository lint gate

**Status:** Implemented. Code-structure rename only: no contract value, persisted
identifier, schema version, error code or runtime behavior changes. The fixture
evaluations produce byte-identical reports before and after.

### Context

Several modules, tests and symbols were named after the milestone or batch that
introduced them (`batch3.py`, `m5_schemas.py`, `M9State`), so their names said when
they were written, not what they do. The orchestration package still exported
deprecated `M9Batch*` / `run_batch3_*` aliases that only a compatibility test used.
`ruff check src tests` had no repository config, so its result depended on the
machine, and the typing style was mixed (`List[...]` beside `list[...]`).

### Decision

- Renamed modules: `src/retrieval/batch3.py` -> `src/retrieval/hybrid_retriever.py`,
  `src/evidence/m5_schemas.py` -> `src/evidence/masking_schemas.py`,
  `src/numeric/m2_adapter.py` -> `src/numeric/parser_adapter.py`.
- Renamed symbols: `Batch3Retriever` -> `HybridRetriever`, `Batch3RetrievalError` ->
  `HybridRetrievalError`, `M9State` -> `OrchestrationState`, `M9Phase` ->
  `OrchestrationPhase`, `supervise_batch1` -> `supervise`.
- Removed the deprecated orchestration aliases (`Batch2Dependencies`, `M9Batch2Graph`,
  `M9Batch2Result`, `M9Batch3Graph`, `M9Batch3Result`, `M9_BATCH2_NODE_SEQUENCE`,
  `M9_BATCH3_NODES`, `Batch3Transition`, `run_batch3_*`);
  `tests/orchestration/test_public_api.py` asserts they stay gone.
- Test files follow the module or behavior they test (for example
  `tests/retrieval/test_hybrid_retriever.py`, `tests/evidence/helpers.py`); the two
  evidence batch integration tests are merged into `tests/evidence/test_integration.py`.
  The shared Text-to-Pandas `catalog` fixture lives in `tests/text_to_pandas/conftest.py`.
- Reference PDFs carry the paper name (`docs/references/tat-qa-2105.07624v2.pdf`, ...).
- `ruff.toml` pins the lint gate: Python 3.11, rules `E4 E7 E9 F I UP B`, imports
  sorted within sections. `B905` and `UP042` are ignored because their fixes change
  behavior (`zip(strict=)`, `StrEnum` string formatting).
- Kept on purpose: identifiers whose value is persisted or deployed — schema and
  contract versions (`m7-execution-request-v1`, `M7_LIMITS_V1`, `M2A_SCHEMA_VERSION`,
  `M9_STATE_SCHEMA_VERSION`, `M5_MASKING_CONTRACT_VERSION`), error codes, the
  `M7_*` environment variables and `deploy/sandbox/m7-*.cfg|policy`, which match the
  installed `/etc/ai-financial/` paths.

### Consequences

Imports of the old module paths and symbols fail; there is no alias period. Earlier
ADRs and task notes now cite the new names. `source_fingerprint()` hashes every
`src/**/*.py`, so release and live-run records made before this change no longer match
the current source and must be re-measured before a release preflight passes.

---

## ADR-075 — Reconstructed official statement set as a PDF

**Status:** Implemented. Additive: the question pipeline, the `Plan` contract and
catalog v4 are unchanged (a rebuild reproduces `catalog_id ddf9bae2…`); releases
gain two optional fields.

### Context

Users asked to "lập bộ báo cáo tài chính chính thức": the full B01-DN, B02-DN,
B03-DN and B09-DN of one company, year and scope, as a PDF. The question API
answers one value at a time and asks for a metric when a question names only a
report. The catalog keeps only the current-year column, drops "-" cells and
headings, and skips the notes, so it cannot lay out a statement as printed.

### Decision

- The statement set is a deterministic reconstruction of the published report,
  not a question class: no NLU, Supervisor, LLM or generated code is involved.
- The catalog's table reader is shared, not copied:
  `catalog_builder.statement_tables()` yields every B01/B02/B03 table with its
  layout and scale (skipped tables carry their reason), `_Layout` adds
  `prior_column` and `note_column`, and `_TableReader.lines()` yields every
  printed line (headings and "-" cells included) with both period cells.
  `extract_report_facts` runs on the same generator.
- Accounting identities keyed by printed line code live in
  `src/formulas/identities.py` (B01 section sums and 270 = 440; the B02 chain
  10/20/30/40/50/60 and 60 = 61 + 62; B03 50 and 70; B03 70 and 60 against B01
  110). Each is checked per period column as PASS, FAIL or MISSING (an omitted
  summand counts as zero). Expense lines are read as costs when the report prints
  them in parentheses. The tolerance is two printed units of the coarsest member.
  Checks report; they never change a figure.
- `python -m src.statements.builder` writes `reports/<report_id>.json.gz` and a
  manifest. Each report holds the lines as printed, B09 as ordered
  paragraph/table blocks, the checks and warnings.
  - B09 starts at the first line that opens the notes after the first B03 table.
    A footer that only mentions the notes does not start it.
  - Tables inside B09 are notes, never statement lines.
  - `NOTES_SCOPE_MISMATCH` flags a file whose notes name the other scope.
  - The manifest carries `statements_id`, per-identity counts and a catalog parity
    check: every catalog fact must be read back by the same `fact_id` with an equal
    absolute value.
- Only TT200/TT202 corporate forms are built. Securities (B0x-CTCK) and bank
  (TT49, no "Mã số" column) reports are recorded as `UNSUPPORTED_TEMPLATE`.
- A release may pin the artifact (`statements`, `statements_id`; preflight
  `STATEMENTS_ID`). The ADR-073 rule "answers come only from the release's
  catalog" extends to "served data comes only from artifacts the release pins".
  `GET /v1/statements?ticker=&year=&scope=` (same token) loads one report,
  checking its SHA-256 on read, and renders it with fpdf2 2.8.9 and vendored
  Noto Sans (OFL). The PDF has a cover with a reconciliation banner and a
  disclaimer that it is an automated reconstruction, not the signed original;
  B01–B03 with failed-identity lines marked; B09 verbatim; and an appendix of
  every check, warning and provenance. Refusals are typed 400/404/422/503, and at
  most two renders run at once. The browser sidebar has the download form.

### Measurement

`artifacts/statements-v1` (`statements_id d231b6e1…`), built from the corpus
`4f39603a…` in 244 s with 4 workers on the 8 GiB Mac:

- **Templates:** 1,887 reports: 1,468 TT200, 385 bank, 22 securities, 12 with no
  readable statement.
- **Reconciliation:** 553 TT200 sets reconcile fully; 915 are flagged.
- **Catalog parity:** 158,845 catalog facts read back with 0 missing and 0
  mismatched; 4,636 catalog facts came from tables inside B09.
- **Sampled failures:** mostly OCR — two figures merged into one cell, codes shifted
  into value columns, a lost line code — plus a few pre-TT200 B02 layouts.
- **Render time:** about 1 s per PDF.

### Consequences and limits

- Statements inherit the reader's classification gaps. An untitled B03 page, as in
  VNM 2024 separate, is missing from the set (and from the catalog), and the set is
  flagged.
- A file printing both the separate and the consolidated set shows the first one,
  flagged by `NOTES_SCOPE_MISMATCH`.
- Wide notes tables wrap in narrow columns on A4 portrait.
- Bank and securities templates, and moving the catalog's metric-keyed guard onto
  `src/formulas/identities.py` (in a later catalog build), are follow-ups. A test keeps the
  two identity lists aligned until then.

## ADR-076 — Resolve only source-delimited merged financial expense cells

**Status:** Implemented. Catalog build v5 and statement-set build v2 supersede
the build artifacts in ADR-075 without changing the catalog or statement schemas.

The OCR corpus sometimes combines B02 code 22, financial expenses, with its
"trong đó: chi phí lãi vay" subline in one value cell. The shared reader now
uses the first of exactly two parseable amounts only when the printed code is
`22`, the label names both items, and the amounts have an explicit whitespace or
parenthesis boundary. It retains the full raw cell text and its cell ID. It
does not split run-together digits or infer a missing code from arithmetic.
The same rule feeds the catalog and the statement-set checks.

On the same 1,887-report corpus, catalog v5 adds 171 current-year code-22 facts;
no old catalog row changes or disappears. Statement-set v2 adds 171 current and
172 prior parsed cell values, all on code 22. Of 1,468 supported corporate sets,
120 move from NOT_RECONCILED to RECONCILED, none regress: **673 reconciled, 795
still flagged**. The catalog parity check covers 159,016 facts with zero missing
or mismatched. `artifacts/statements-v2/review_queue.csv` lists each remaining
flagged set, its source file, failed checks, missing totals and warnings;
`remediation_summary.json` records the comparison with v1.

The remaining OCR text includes lost or duplicated line codes, merged digits
without a recoverable boundary, and missing statement tables. The workspace has
no original PDF or scan for independent verification. These cases remain flagged;
the builder does not back-solve or replace a published figure. No release was
promoted as part of this remediation.

## ADR-077 — Separate AI drafting mode for statement PDFs

**Status:** Implemented. The deterministic statement download in ADR-075 is
unchanged. A new `POST /v1/statements/draft` uses the same pinned statement set
and the release's configured provider client. The runtime sends
`src/statements/system_prompt.md` with role `system`; the skill reference points
to that same file. The model returns only a short preface and indexes of
existing B09 text blocks to emphasize. Code validates this response and renders
all source figures, notes and reconciliation checks without model edits. The
draft is visibly marked as AI-generated and retains the OCR disclaimer. Replay
releases refuse drafting, and invalid model output fails closed. This separate
request incurs provider latency and cost only when selected.
The release source fingerprint includes the runtime prompt file, so changing
the drafting instructions requires a new preflighted release.
