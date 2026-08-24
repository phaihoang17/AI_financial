# TASKS.md — Implementation Roadmap

## How to Use This File

Each task should be implemented independently where possible.

A task is `DONE` only when:

- implementation exists,
- relevant tests exist and pass,
- relevant evaluation exists and passes its declared criteria,
- architecture/contracts remain consistent,
- no unrelated diff remains.

Exact metric thresholds that are not specified by the provided materials are marked `TBD` rather than invented.

---

# M0 — Project Contracts and Evaluation Skeleton

Goal: establish the interfaces and evaluation boundaries before building agents.

- [ ] **TASK-001 — Define `QueryUnderstanding` schema**
  - Fields: company, periods, period kind, statement scope, metrics, operation, missing information, ambiguity, confidence.
  - Acceptance:
    - schema validation tests,
    - explicit representation of inferred vs. explicit scope,
    - missing information is representable without fabricated defaults.

- [ ] **TASK-002 — Define canonical `Plan` schema**
  - Implement the canonical fields defined in `docs/ARCHITECTURE.md` and preserve the routing decisions in `docs/DECISIONS.md`.
  - Acceptance:
    - valid examples for LOOKUP / DERIVED_RATIO / MULTI_PERIOD / AGGREGATE,
    - invalid enum/field cases rejected,
    - ROE example serializes correctly.

- [ ] **TASK-003 — Define evidence contract**
  - Preserve report/table/period/scope and hierarchical row/column metadata where available.
  - Acceptance:
    - every selected value can be traced back to source metadata,
    - scale provenance accepts `HEADER`, `CELL`, `CAPTION`, `TEXT`, and `QUESTION`,
    - caption provenance round-trips without being collapsed into `TEXT`.

- [ ] **TASK-004 — Define execution and verification result schemas**
  - Acceptance:
    - typed success/failure outcomes,
    - explicit failure category/reason.

- [ ] **TASK-005 — Build evaluation harness skeleton**
  - Separate stage-level and end-to-end evaluation.
  - Implement the canonical `EvaluationResult` contract defined in `docs/ARCHITECTURE.md`.
  - Modes:
    - NLU,
    - Supervisor,
    - Retrieval,
    - Oracle Evidence,
    - Retrieved Evidence E2E.
  - Thresholds, aggregate metrics/reporting, per-case metadata beyond TASK-007 slices, and aggregation are deferred.

---


- [ ] **TASK-006 — Add reasoning-trace evaluation contracts**
  - Implement the canonical `ReasoningEvaluationResult` and `TraceEquivalenceHook` contracts defined in `docs/ARCHITECTURE.md`.
  - execution accuracy,
  - program/trace accuracy,
  - answer accuracy,
  - normalized equivalence hooks for semantically equivalent programs.
  - Do not define Program or Answer schemas, thresholds, or aggregate metrics.

- [ ] **TASK-007 — Add evaluation slices**
  - Implement the canonical `EvaluationSlice` and `SlicedEvaluationResult` contracts defined in `docs/ARCHITECTURE.md`.
  - `TABLE`,
  - `TEXT`,
  - `HYBRID`,
  - reasoning depth `1 / 2 / 3+`,
  - scale/unit-sensitive cases.
  - `HYBRID` is an evaluation-only classification.

`TASK-008` and `TASK-009` are intentionally unused/reserved. The roadmap continues from `TASK-007` to `TASK-010`.

# M1 — NLU / Understanding

Goal: turn Vietnamese questions into validated structured semantics.

- [ ] **TASK-010 — Company/ticker resolver**
  - Support canonical resolution from aliases/tickers when uniquely known.
  - Implement the canonical company resolver contracts and normalization v1 defined in `docs/ARCHITECTURE.md`.
  - Do not use fuzzy matching, typo correction, substring guessing, or an LLM.
  - The authoritative alias dataset and storage mechanism remain TBD.
  - Acceptance:
    - deterministic tests for aliases,
    - ambiguous alias does not silently select a ticker.

- [ ] **TASK-011 — Temporal parser**
  - Parse year, quarter, and cumulative-period expressions.
  - Implement the canonical deterministic v1 contracts and normalized values defined in `docs/ARCHITECTURE.md`.
  - Preserve exact matched text; incomplete/invalid expressions remain unresolved without nearby-text inference or an LLM.
  - Acceptance:
    - examples such as `năm 2015`, `quý 3/2015`, `lũy kế 9 tháng`.

- [ ] **TASK-012 — Statement-scope resolver**
  - Resolve consolidated vs. standalone/company-parent scope.
  - Implement the canonical deterministic v1 contract and indicators defined in `docs/ARCHITECTURE.md`.
  - Do not default or infer scope for aggregated/unlabeled reports and do not use an LLM.
  - Acceptance:
    - explicit and inferred scope are distinguishable.

- [ ] **TASK-013 — Financial metric normalization**
  - Canonicalize Vietnamese synonyms.
  - Implement the canonical deterministic v1 metric contracts and exact normalization rules defined in `docs/ARCHITECTURE.md`.
  - Do not expand the initial registry beyond documented examples or use fuzzy/substring/LLM matching.
  - Example: `lãi ròng` / `LNST` / `lợi nhuận sau thuế`.

- [ ] **TASK-014 — Operation detector**
  - Output: `none | ratio | growth | aggregate | compare | unknown`.
  - Implement the canonical deterministic v1 contracts, minimal indicator
    vocabulary, conflict handling, and documented growth override in
    `docs/ARCHITECTURE.md`.
  - Do not use fuzzy matching, typo correction, substring guessing, or an LLM.

- [ ] **TASK-015 — Ambiguity and missing-information detector**
  - Implement the canonical `FindingName` and `PlanningGate` contracts in
    `docs/ARCHITECTURE.md` exactly.
  - Company, requested periods, and requested metrics are hard-required;
    statement-scope eligibility remains deferred.
  - Acceptance:
    - unresolved company/period/metric is surfaced,
    - no downstream plan when hard-required identity is missing.

- [ ] **TASK-016 — NLU evaluation set**
  - Implement the canonical case/result/field-comparison contracts in
    `docs/ARCHITECTURE.md`.
  - Use exact structural comparison for the seven approved semantic fields.
  - Keep `raw_question`, overall confidence, requested scale, and requested unit
    outside v1 scoring.
  - Use a small approved fixture set only; no evaluator normalization or LLM judge.
  - Thresholds and aggregate scoring: `TBD`.

---

- [ ] **TASK-017 — Requested scale/unit parser**
  - Implement the canonical deterministic `RequestedScaleUnit` contract and v1
    mappings defined in `docs/ARCHITECTURE.md`.
  - Parse only explicit `nghìn`, `ngàn`, `triệu`, `tỷ`, `%`, and `phần trăm`
    expressions into the existing `QueryUnderstanding` fields.
  - Keep `requested_unit` null; currency/unit vocabulary remains TBD.
  - Do not perform evidence-scale resolution or change `EvidenceItem.scale`.
  - Do not use fuzzy matching, typo correction, substring guessing, or an LLM.

# M2 — Financial Evidence Normalization and Hierarchy

Goal: make OCR-derived financial tables retrievable without losing structural meaning.

- [x] **TASK-020 — Parse/extract table structures**
  - Implement the canonical M2A source and normalized contracts in `docs/ARCHITECTURE.md` before parser behavior.
- [x] **TASK-021 — Normalize whitespace and table cells**
- [x] **TASK-022 — Expand/represent merged cells safely**
- [x] **TASK-023 — Normalize headers while preserving hierarchy**
- [x] **TASK-024 — Normalize numeric strings without losing raw value**
- [x] **TASK-025 — Attach row/column/header-path metadata**
- [x] **TASK-026 — Table normalization regression tests**

- [x] **TASK-027 — Build retrieval representation builder**
  - Include company, period, statement scope, table identity, schema/header hierarchy.
  - Use structured `row_paths` and `column_paths` containing `HeaderPathEntry`; do not flatten paths to strings.

- [x] **TASK-028 — Build BGE-M3 embedding indexer**
  - [x] **TASK-028A — Derive lossless embedding chunks**
    - Fragment only oversized individual source cells at the embedding boundary.
    - Preserve exact source-cell text and provenance; never truncate or modify M2A.
  - [x] **TASK-028B — Generate document embeddings**
    - Use the pinned BGE-M3 model/tokenizer configuration over every emitted chunk.

- [x] **TASK-029 — Build vector index + metadata persistence**
  - Persist complete BGE-M3 vectors with traceable retrieval metadata in immutable
    FAISS shards plus SQLite and a committed manifest.

- [x] **TASK-02A — Extract narrative paragraphs**
  - Preserve report/page/section identity.

- [x] **TASK-02B — Link associated text to tables**
  - Store table <-> paragraph relations when recoverable.

- [x] **TASK-02C — Extract scale/unit hints**
  - Preserve offline scale/unit clues from headers, captions, and paragraphs.
  - Offline M2A emits `HEADER`, `CELL`, `CAPTION`, or `TEXT` hints only and does not resolve final scale.
  - Question-derived scale remains TASK-017/TASK-054.

Acceptance for M2:

- hierarchical paths remain recoverable,
- period/header identity remains explicit,
- raw and normalized numeric values can be audited.

M2 implementation is complete. TASK-028A passed the full-corpus chunk audit
(zero omitted representations/cells and zero over-budget chunks); TASK-028B and
TASK-029 implementations are complete. The production full-corpus embedding /
index artifact has not been built on the 8 GiB development host. Building that
artifact on the approved production machine is an operational/deployment run,
not missing indexing functionality.

The streaming TASK-028B/TASK-029 CLI is:

```bash
python -m src.indexing.embedding_artifact_builder \
  --input-artifact <m2-corpus-artifact> \
  --output-root <output-root> \
  --device <cpu|cuda|mps> \
  --batch-size <N> \
  --max-vectors-per-shard 100000 \
  --resume
```

It has been validated only with small CPU integration artifacts. Do not run
the full-corpus command on the 8 GiB development host.

The deterministic full-corpus `m2-corpus-artifact-v1` containing all emitted
TABLE/TEXT `EmbeddingChunk` records is materialized separately from production
embedding/index deployment. It contains no vectors, FAISS index, BM25 index, or
retrieval behavior.

---

# M3 — Retrieval and Evidence

Goal: address the primary failure bottleneck with an explicit hybrid retrieval stack.

- [ ] **TASK-030 — Report-level metadata filter / retriever**
  - Filter by company, period, and statement scope.

- [ ] **TASK-031 — BM25 index + search**
  - Cover ticker, report labels, financial metrics, and table terminology.

- [ ] **TASK-032 — BGE-M3 query embedder**
  - Use the same embedding configuration as offline indexing.

- [ ] **TASK-033 — Vector search**
  - Search the BGE-M3 vector index and preserve candidate metadata.

- [ ] **TASK-034 — Hybrid fusion with RRF**
  - Merge BM25 and dense candidate lists deterministically.

- [ ] **TASK-035 — BGE-reranker-v2-m3 integration**
  - Rerank fused candidates before evidence selection.

- [ ] **TASK-036 — Hybrid Retrieval Query Builder**
  - Build table and/or text retrieval queries from `QueryUnderstanding + Plan`.
  - Preserve requested evidence source type.

- [ ] **TASK-037 — Multi-table retrieval**
  - Support independent/parallel retrieval for plans requiring multiple tables.

- [ ] **TASK-038 — Evidence/cell locator**
  - Ground target metrics to row/column/header path.

- [ ] **TASK-039 — Evidence Builder**
  - Convert selected retrieval results to canonical evidence objects.

- [ ] **TASK-03A — Retrieval evaluation**
  - Evaluate BM25, dense retrieval, fusion, reranker, and final evidence selection separately.
  - Thresholds: `TBD`.

- [ ] **TASK-03B — Retrieval failure taxonomy logging**
  - wrong company,
  - wrong period,
  - wrong scope,
  - wrong table,
  - wrong row/column,
  - insufficient evidence.

- [ ] **TASK-03C — Index/model version checks**
  - Detect incompatible BM25/vector/embedding/reranker versions before serving.

- [ ] **TASK-03D — Narrative-text retrieval**
  - Retrieve associated paragraphs/text spans, not only tables.

- [ ] **TASK-03E — Hybrid evidence completeness**
  - Verify that required table + text evidence is present before reasoning.

- [ ] **TASK-03F — Scale/unit evidence retrieval**
  - Retrieve header/paragraph clues needed to interpret numeric magnitude.

# M4 — Supervisor / Planner

Goal: transform validated `QueryUnderstanding` into a deterministic execution `Plan`.

- [ ] **TASK-040 — Question-type classifier**
  - `LOOKUP | DERIVED_RATIO | MULTI_PERIOD | AGGREGATE`.

- [ ] **TASK-041 — Required-table planner**
  - Produce `tables_needed` and `target_metrics`.

- [ ] **TASK-042 — Required-period planner**
  - Include implicit periods required by formulas (e.g. average-equity pattern).

- [ ] **TASK-043 — Formula/derived-target planner**

- [ ] **TASK-044 — Early-abstain policy**
  - unresolved company,
  - unsafe missing period/scope,
  - ungroundable metric,
  - unsupported operation.

- [ ] **TASK-045 — Model-tier routing**
  - `CHEAP` vs. `STRONG`.

- [ ] **TASK-046 — Verification-profile routing**
  - `LIGHT` vs. `STRICT`.

- [ ] **TASK-047 — Supervisor evaluation**
  - question class,
  - required tables,
  - required periods,
  - routing,
  - abstain correctness.
  - Target thresholds: `TBD`.

---

- [ ] **TASK-048 — Evidence-source planner**
  - output `TABLE`, `TEXT`, or both.

- [ ] **TASK-049 — Reasoning-mode and complexity router**
  - `DIRECT | PROGRAM | TABLE_TRANSFORM`.
  - `TABLE_TRANSFORM` must be opt-in based on measured complexity/failure criteria.

# M5 — Schema Linking, Scale/Unit, and Numeric Masking

Goal: bind language to table structure and remove literal-number dependence from program generation.

- [ ] **TASK-050 — Schema linker**
  - Map canonical metrics to actual row/header paths.

- [ ] **TASK-051 — Numeric masking**
  - Replace grounded literal values with symbolic placeholders.

- [ ] **TASK-052 — Deterministic value binder**
  - Bind placeholders back to grounded values only at execution time.

- [ ] **TASK-053 — Anti-hardcode tests**
  - Ensure generated programs do not depend on copied gold answer values.

---

- [ ] **TASK-054 — Scale/unit resolver**
  - Resolve scale from header/cell/text/question and retain provenance.

- [ ] **TASK-055 — Scale/unit regression tests**
  - million vs. billion,
  - raw vs. thousand,
  - percent,
  - mixed-source scale clues.

# M6 — Programmer / Text-to-Pandas

Goal: generate reproducible Python/Pandas programs from `Plan + Evidence`.

- [ ] **TASK-060 — Programmer input/output contract**

- [ ] **TASK-061 — LOOKUP program generation**

- [ ] **TASK-062 — DERIVED_RATIO program generation**

- [ ] **TASK-063 — MULTI_PERIOD program generation**

- [ ] **TASK-064 — AGGREGATE program generation**

- [ ] **TASK-065 — Vietnamese numeric parsing utilities**

- [ ] **TASK-066 — Unit conversion utilities**

- [ ] **TASK-067 — Oracle-evidence reasoning evaluation**
  - Run Programmer with gold/oracle evidence so reasoning quality is measured independently from retrieval.
  - Target thresholds: `TBD`.

---

- [ ] **TASK-068 — Program grammar/schema validator**
  - Reject structurally invalid programs before sandbox execution.

- [ ] **TASK-069 — Program/trace evaluation**
  - Compare reasoning trace separately from execution result.

# M6B — Complex Table Reasoning Fallback

Goal: evaluate a bounded Chain-of-Table-inspired path for difficult table structures without replacing the baseline.

- [ ] **TASK-06A — Define atomic table operation pool**
  - select rows,
  - select columns,
  - add derived column,
  - group,
  - sort.

- [ ] **TASK-06B — Implement deterministic table-operation executor**

- [ ] **TASK-06C — Implement bounded table-transform planner**
  - latest table state + question + operation history,
  - explicit terminal state,
  - hard maximum steps.

- [ ] **TASK-06D — Compare static Programmer vs. TABLE_TRANSFORM fallback**
  - only keep fallback if it improves complex-table cases without unacceptable latency/cost.

# M7 — Sandboxed Execution

Goal: execute generated code safely outside the host application process.

- [ ] **TASK-070 — Static program policy / allowlist**
  - Treat as pre-filter, not the only security boundary.

- [ ] **TASK-071 — Isolated execution process**

- [ ] **TASK-072 — Resource limits**
  - time,
  - memory,
  - filesystem/network policy as appropriate.

- [ ] **TASK-073 — ExecutionResult contract implementation**

- [ ] **TASK-074 — Sandbox abuse/security tests**

- [ ] **TASK-075 — Infrastructure ADR for final sandbox technology**
  - Container / gVisor-like / MicroVM decision based on deployment constraints.

---

# M8 — Verification and Targeted Retry

Goal: reject wrong-but-executable answers and route failures to the correct stage.

- [ ] **TASK-080 — Grounding verifier**
  - company/report/scope/period/table/row/column.

- [ ] **TASK-081 — Numeric and unit verifier**

- [ ] **TASK-082 — Financial-logic verifier**

- [ ] **TASK-083 — LIGHT verification profile**

- [ ] **TASK-084 — STRICT verification profile**

- [ ] **TASK-085 — Failure classifier**

- [ ] **TASK-086 — Retrieval retry path**

- [ ] **TASK-087 — Programmer retry path**

- [ ] **TASK-088 — CHEAP -> STRONG escalation**

- [ ] **TASK-089 — Retry budget / termination tests**
  - no unbounded loops.

---

- [ ] **TASK-08A — Scale/unit verifier**
  - verify value magnitude and scale provenance.

- [ ] **TASK-08B — Hybrid evidence support verifier**
  - reject answers whose planned text/table support is missing.

# M9 — End-to-End Orchestration

Goal: connect all stages using structured state, without stage-to-stage free-form reinterpretation.

- [ ] **TASK-090 — Orchestration state model**

- [ ] **TASK-091 — NLU -> Supervisor flow**

- [ ] **TASK-092 — Supervisor -> Retrieval flow**

- [ ] **TASK-093 — Evidence -> Programmer flow**

- [ ] **TASK-094 — Programmer -> Sandbox -> Verification flow**

- [ ] **TASK-095 — Clarification / early-abstain response path**

- [ ] **TASK-096 — Final abstain response path**

- [ ] **TASK-097 — Answer Builder**
  - final answer format and evidence format require explicit product decision if not already defined by external requirements.

- [ ] **TASK-098 — Retrieved-evidence E2E evaluation**

- [ ] **TASK-099 — Failure attribution report**
  - distinguish NLU, planning, retrieval, code, execution, verification failures.

---

# M10 — Cost, Latency, and Serving Optimization

Do this only after the correctness baseline is measurable.

- [ ] **TASK-100 — Establish strong-tier/strict-verification correctness baseline**

- [ ] **TASK-101 — Activate CHEAP routing for eligible simple cases**

- [ ] **TASK-102 — Confidence-gated retry and model escalation**
  - measure retry rate and quality gain.

- [ ] **TASK-103 — Parallelize independent multi-table / multi-source retrieval**

- [ ] **TASK-104 — Parallelize independent verifier checks**

- [ ] **TASK-105 — Semantic cache experiment**
  - cache only sufficiently similar, stable inputs,
  - compare no-cache vs. field/query-level vs. adaptive policy,
  - monitor correctness delta.

- [ ] **TASK-106 — End-to-end latency distribution**
  - p50,
  - p95,
  - p99,
  - per-stage latency.

- [ ] **TASK-107 — Model serving ADR**
  - exact serving engine/provider depends on deployment constraints.

- [ ] **TASK-108 — Shared Qwen3-8B role serving**
  - separate NLU/Supervisor prompts and contracts on one service where feasible.

- [ ] **TASK-109 — Retrieval model serving**
  - BGE-M3 batching,
  - BGE-reranker-v2-m3 serving,
  - model/index version tracking.

- [ ] **TASK-110 — Cost/token dashboard**
  - cost/query,
  - tokens/query,
  - escalation cost,
  - cache hit rate.

- [ ] **TASK-111 — Throughput/load test**
  - measure accuracy/timeout degradation at target concurrency and document volume.

- [ ] **TASK-112 — Coordination-failure logging**
  - conflicting worker state,
  - stale state,
  - retry loops,
  - message/schema corruption.

# M11 — Selective Experience Memory (Optional, Post-v1)

Goal: test whether prior successful/failure trajectories improve tool routing and reasoning without contaminating grounded inference.

- [ ] **TASK-120 — Define memory-entry schema**
  - source question/context signature,
  - successful strategy findings,
  - failure-derived caution rules,
  - provenance.

- [ ] **TASK-121 — Build offline memory distillation from train/dev trajectories**

- [ ] **TASK-122 — Build memory embedding/index**

- [ ] **TASK-123 — Similarity-gated memory retrieval**
  - threshold,
  - top-k cap,
  - deduplication,
  - base-policy fallback.

- [ ] **TASK-124 — Memory injection policy**
  - selected roles only,
  - memory cannot override evidence or validators.

- [ ] **TASK-125 — Held-out evaluation isolation**
  - memory read-only,
  - no test/gold writes.

- [ ] **TASK-126 — Memory ablation**
  - base,
  - tools only,
  - tools + memory,
  - measure accuracy, retrieval quality, latency, and failure modes.

# Recommended Implementation Order

```text
M0  Contracts / Evaluation
        |
M1  NLU
        |
M2  Table + Text Evidence Normalization
        |
M3  Hybrid Retrieval + Evidence
        |
M4  Supervisor / Planner
        |
M5  Schema + Scale/Unit + Numeric Masking
        |
M6  Programmer
        |
M6B Complex-Table Fallback (experimental)
        |
M7  Sandbox
        |
M8  Verification
        |
M9  End-to-End
        |
M10 Production Optimization
        |
M11 Selective Experience Memory (optional)
```

The ordering deliberately keeps memory, semantic caching, and complex reflexive behavior out of the initial correctness path.
