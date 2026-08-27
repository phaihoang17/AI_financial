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
  --input-artifact <exact committed m2 corpus artifact directory> \
  --output-root <output-root> \
  --device cuda \
  --batch-size <N> \
  --max-vectors-per-shard 100000 \
  --resume
```

`--input-artifact` must be the **exact committed corpus artifact directory**
(the one whose `manifest.json` has `artifact_status: COMMITTED` and the
committed `artifact_id`). A family root is accepted only if it contains a
`CURRENT` file that resolves to that committed id (`_resolve_input_artifact`);
do not point it at a generic artifact-family path that has no `CURRENT`
pointer. The published output root is `m2-vector-index-v1`. On success the
builder prints `validate_published_artifact(...)` with the `artifact_id`; the
published `manifest.json` `vector_count` MUST be **1,743,311** (one vector per
canonical `EmbeddingChunk`), never 146,246 (source tables).

It has been validated only with small CPU integration artifacts. Do not run
the full-corpus command on the 8 GiB development host. After the build, run
`python -m src.evaluation.run_retrieval_production_validation` (ADR-057) to
validate corpus/index/BM25 compatibility, the exact vector count, shard/SQLite
integrity, and the LIVE BGE-M3 / BGE-reranker serving parity.

The deterministic full-corpus `m2-corpus-artifact-v1` containing all emitted
TABLE/TEXT `EmbeddingChunk` records is materialized separately from production
embedding/index deployment. It contains no vectors, FAISS index, BM25 index, or
retrieval behavior.

---

# M3 — Retrieval and Evidence

Goal: address the primary failure bottleneck with an explicit hybrid retrieval stack.

Batch 2 implementation is complete for persisted lexical retrieval, compatible
dense query embedding, and exact metadata-filtered FAISS search. Production-scale
vector validation remains `GPU_PRODUCTION_VALIDATION_PENDING`.

Batch 2.5 is complete as an additive provenance and retrieval-eligibility
increment. It leaves the canonical M2 corpus unchanged while materializing an
exactly bound `m2-provenance-sidecar-v1` for full scale/unit hints and
table--text links. It also applies non-empty TABLE/TEXT source eligibility in
BM25 and vector pre-top-k filtering.

- [x] **Batch 2.5 — M2 provenance sidecar + source-type eligibility**
  - Persist and validate complete `ScaleUnitHint` and `TableTextLink` records.
  - Require every materialized M2 hint/link reference to resolve.
  - Keep TABLE/TEXT retrieval eligibility independent from report metadata.

Batch 2.6 is complete as a second additive increment (ADR-053). It materializes
an exactly bound, immutable `m2-table-class-sidecar-v1` of conservative
source-backed `TableClassHint` records (same-page preceding context, NFKC,
literal case-insensitive statement code/title matching, no fuzzy/semantic/LLM,
no metric-mapping inference; ambiguous or unmatched tables get no hint). It
leaves the canonical M2 corpus unchanged. `EvidenceProvenanceRepository` /
`locate_cells` now fill `CellLocation.table_class` from an exact
`(report_id, page_id, table_id)` sidecar lookup, failing closed on a corrupt or
incompatible sidecar and staying `None` on a missing hint.
`PRODUCTION_TABLE_CLASS_PROVENANCE_PENDING` was cleared once the real full-corpus
source-backed integration audit passed (see Batch 2.7 below).

- [x] **Batch 2.6 — Additive source-backed TableClass provenance sidecar**
  - Build/validate `m2-table-class-sidecar-v1` bound to one M2 corpus + registry.
  - Wire exact `CellLocation.table_class` lookup; no heuristic/metric fallback.

Batch 2.7 adds the deterministic production audit from ADR-054. The tooling,
fixture tests, and the operational full-corpus run are all complete;
`PRODUCTION_TABLE_CLASS_PROVENANCE_PENDING` is cleared.

- [x] **Batch 2.7 — TableClass real-corpus provenance audit tooling**
  - Validate exact corpus/sidecar/source compatibility and integrity.
  - Report classification coverage, per-class counts, unmatched/conflicting
    tables, orphan/duplicate mappings, source-span failures, and exact source
    replay differences.
  - Exercise the real `EvidenceProvenanceRepository -> locate_cells` path.
- [x] **Run Batch 2.7 on the approved canonical full M2 production corpus**
  - `PRODUCTION_TABLE_CLASS_PROVENANCE_PENDING` cleared: the audit CLI below
    returned exit code zero and JSON `status: PASS` with `blockers: []` on the
    approved canonical inputs.

```bash
python -m src.indexing.table_class_provenance_audit \
  --corpus-artifact <canonical-full-m2-corpus-artifact> \
  --table-class-sidecar <matching-table-class-sidecar-artifact> \
  --raw-corpus-root <exact-raw-corpus-root>
```

Production run evidence:

- corpus artifact id
  `4f39603a89e213129de6c57e1234456e88086832c8b61bd9821770d76a6dc78e`
  (`corpus_id` `ViFinQA`, `artifact_status` `COMMITTED`), corpus fingerprint
  `9ea9e073a8aff69e48ae9b258a2bd657cd5194a52de9742e89f5bdb8d17f61c9`,
  corpus manifest sha256
  `68de4553a91296fb9fe5bea82a5672b1013febea2769824ee8a71329f0448c97`
- table-class sidecar artifact id
  `86d718489f1c5aa455876cbb0bc28c72169ae610f753442f51adab714ad57de8`
  (`m2-table-class-sidecar-v1`, registry `m2-table-class-registry-v1`,
  registry fingerprint
  `ea871c01ce8d0cf82d723217027afb369824515f42d68fbc1b63526a50f4a7c0`),
  exactly bound to the corpus artifact above
- raw corpus root `~/Documents/data/ViFinQA/financial_statements` (approved
  canonical raw source)
- result: `status: PASS`, `blockers: []`, exit code 0; 1973 reports,
  146246 canonical tables, 0 unparseable; 54173 source-backed classifications
  (coverage 0.3704) — BALANCE_SHEET 4789, INCOME_STATEMENT 2982,
  CASH_FLOW_STATEMENT 2132, NOTES 44270
- zero integrity/mapping failures: 0 orphan, 0 duplicate, 0 conflicting
  mappings; 0 invalid source spans; 0 source-span round-trip failures;
  0 source-supported hint mismatches; 0 corpus/source table mismatches
- 92073 unmatched tables (90775 source-no-match + 1298 ambiguous/conflicting)
  remain valid `None` cases; no minimum coverage threshold applies
- production `locate_cells` evidence path: PASS for all four `TableClass`
  values and for the unclassified-stays-`None` check

- [x] **TASK-030 — Report-level metadata filter / retriever**
  - Filter by company, period, and statement scope.

- [x] **TASK-031 — BM25 index + search**
  - Cover ticker, report labels, financial metrics, and table terminology.

- [x] **TASK-032 — BGE-M3 query embedder**
  - Use the same embedding configuration as offline indexing.

- [x] **TASK-033 — Vector search**
  - Search the BGE-M3 vector index and preserve candidate metadata.

- [x] **TASK-034 — Hybrid fusion with RRF**
  - Merge BM25 and dense candidate lists deterministically.

- [x] **TASK-035 — BGE-reranker-v2-m3 integration**
  - Rerank fused candidates before evidence selection.

- [x] **TASK-036 — Hybrid Retrieval Query Builder**
  - Build table and/or text retrieval queries from `QueryUnderstanding + Plan`.
  - Preserve requested evidence source type.

- [x] **TASK-037 — Multi-table retrieval**
  - Support independent/parallel retrieval for plans requiring multiple tables.

- [x] **TASK-038 — Evidence/cell locator**
  - Ground target metrics to row/column/header path.

- [x] **TASK-039 — Evidence Builder**
  - Convert selected retrieval results to canonical evidence objects.

- [x] **TASK-03A — Retrieval evaluation**
  - Evaluate BM25, dense retrieval, fusion, reranker, and final evidence selection separately.
  - Thresholds: `TBD`.

- [x] **TASK-03B — Retrieval failure taxonomy logging**
  - wrong company,
  - wrong period,
  - wrong scope,
  - wrong table,
  - wrong row/column,
  - insufficient evidence.

- [x] **TASK-03C — Index/model version checks**
  - Detect incompatible BM25/vector/embedding/reranker versions before serving.

- [x] **TASK-03D — Narrative-text retrieval**
  - Retrieve associated paragraphs/text spans, not only tables.

- [x] **TASK-03E — Hybrid evidence completeness**
  - Verify that required table + text evidence is present before reasoning.

- [x] **TASK-03F — Scale/unit evidence retrieval**
  - Retrieve header/paragraph clues needed to interpret numeric magnitude.

# M4 — Supervisor / Planner

Goal: transform validated `QueryUnderstanding` into a deterministic execution `Plan`.

- [x] **TASK-040 — Question-type classifier**
  - `LOOKUP | DERIVED_RATIO | MULTI_PERIOD | AGGREGATE`.

- [x] **TASK-041 — Required-table planner**
  - Produce `tables_needed` and `target_metrics`.

- [x] **TASK-042 — Required-period planner**
  - Include implicit periods required by formulas (e.g. average-equity pattern).

- [x] **TASK-043 — Formula/derived-target planner**

- [x] **TASK-044 — Early-abstain policy**
  - unresolved company,
  - unsafe missing period/scope,
  - ungroundable metric,
  - unsupported operation.

- [x] **TASK-045 — Model-tier routing**
  - `CHEAP` vs. `STRONG`.

- [x] **TASK-046 — Verification-profile routing**
  - `LIGHT` vs. `STRICT`.

- [x] **TASK-047 — Supervisor evaluation**
  - question class,
  - required tables,
  - required periods,
  - routing,
  - abstain correctness.
  - Target thresholds: `TBD`.

---

- [x] **TASK-048 — Evidence-source planner**
  - output `TABLE`, `TEXT`, or both.

- [x] **TASK-049 — Reasoning-mode and complexity router**
  - `DIRECT | PROGRAM | TABLE_TRANSFORM`.
  - `TABLE_TRANSFORM` is disabled in M4 v1; future opt-in requires measured
    complexity/failure criteria.

**M4_IMPLEMENTATION_COMPLETE**

The remaining `GPU_PRODUCTION_VALIDATION_PENDING` status belongs to the
production-scale M3 vector artifact and is not missing M4 implementation.

# M5 — Schema Linking, Scale/Unit, and Numeric Masking

Goal: bind language to table structure and remove literal-number dependence from program generation.

- [x] **TASK-050 — Schema linker**
  - Map canonical metrics to actual row/header paths.

- [x] **TASK-051 — Numeric masking**
  - Replace grounded literal values with symbolic placeholders.

- [x] **TASK-052 — Deterministic value binder**
  - Bind placeholders back to grounded values only at execution time.

- [x] **TASK-053 — Anti-hardcode tests**
  - Ensure generated programs do not depend on copied gold answer values.
  - Complete against real M6 generated Programs: BindingMap-value mutation,
    financial-value leakage, placeholder mutation, required-evidence coverage,
    formula-registry enforcement, and generated-constant rejection.

---

- [x] **TASK-054 — Scale/unit resolver**
  - Resolve document scale from header/cell/text hints, keep the question's
    requested output scale separate, and retain provenance.

M5 Batch 1 is deterministic and operates only on already-grounded M3 evidence:
`Plan + Evidence -> ScaleUnitResolution -> SchemaLinkResult`. It does not
change `EvidenceItem`, convert `CanonicalDecimal`, widen retrieval, or use an
LLM.

M5 Batch 2 extends that boundary with value-free `MaskedEvidenceBundle` for the
future Programmer and a separate execution-only `BindingMap`. It preserves
`CanonicalDecimal` strings and performs no scale/unit conversion. M6 Batch 1
now provides the Program contract, and M6 Batch 2 completes TASK-053 against
real generated Programs without execution.

- [x] **TASK-055 — Scale/unit regression tests**
  - million vs. billion,
  - raw vs. thousand,
  - percent,
  - mixed-source scale clues.

TASK-055 covers every canonical source scale, direct and linked hint sources,
the complete precedence order, conflicts/unresolved results, percent literals,
source/request separation, numeric immutability, provenance, determinism, and
the required-scale masking gate.

# M6 — Programmer / Text-to-Pandas

Goal: generate reproducible Python/Pandas programs from `Plan + Evidence`.

- [x] **TASK-060 — Programmer input/output contract**

- [x] **TASK-061 — LOOKUP program generation**

- [x] **TASK-062 — DERIVED_RATIO handling**
  - Deterministically reject because no ratio formula is registered; do not
    invent a formula.

- [x] **TASK-063 — MULTI_PERIOD program generation**

- [x] **TASK-064 — AGGREGATE program generation**

- [x] **TASK-065 — Vietnamese numeric parsing utilities**
  - Reuse the M2 parser through a typed CanonicalDecimal adapter; add no grammar.

- [x] **TASK-066 — Unit conversion utilities**
  - Decimal-only magnitude conversion with typed scale/unit failures.

- [x] **TASK-067 — Oracle-evidence reasoning evaluation**
  - Run Programmer with gold/oracle evidence so reasoning quality is measured independently from retrieval.
  - Deterministic fixture acceptance: all canonical fixtures must pass exact
    execution, trace, answer, and failure-attribution checks.

---

- [x] **TASK-068 — Program grammar/schema validator**
  - Reject structurally invalid programs before sandbox execution.

- [x] **TASK-069 — Program/trace evaluation**
  - Compare reasoning trace separately from execution result.

M6 Batch 1 defines the
value-free `m6-program-v1` symbolic DSL, deterministic Program identity and
serialization, versioned FormulaImplementation metadata for `GROWTH_RATE` and
`AVERAGE`, and typed pre-execution validation. It performs no model inference,
numeric parsing/conversion, formula execution, or sandbox execution.

M6 Batch 2 completes TASK-061 through TASK-064 and TASK-053. The deterministic
Programmer emits only IDENTITY lookup, COLLECT comparison, registered
GROWTH_RATE, and registered AVERAGE shapes; rejects ratios; preserves Plan
requirement order; and validates every Program through TASK-068. It still
performs no model inference, BindingMap access, numeric parsing/conversion,
formula execution, or sandbox execution.

M6 Batch 3 completes TASK-065, TASK-066, and TASK-069. It reuses the M2 parser,
adds exact Decimal-only scale conversion that returns CanonicalDecimal strings,
and evaluates Program structure independently of execution.

M6 Batch 4 completes TASK-067. Seven directly supplied oracle-evidence cases
run through real M5 masking/binding, M6 Program generation, and M7 isolated
execution without retrieval, GPU, or LLM use. Execution, normalized trace, and
answer correctness are scored independently; exact failures are attributed to
generation, validation, binding, conversion, arithmetic, or sandbox
infrastructure. M6 implementation is complete.

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

Goal: execute validated symbolic Programs safely outside the host application
process without arbitrary Python execution.

- [x] **TASK-070 — Static program policy / allowlist**
  - Treat as pre-filter, not the only security boundary.

- [x] **TASK-071 — Trusted DSL interpreter + isolated execution process**

- [x] **TASK-072 — Resource limits**
  - time,
  - memory,
  - filesystem/network policy as appropriate.

- [x] **TASK-073 — ExecutionResult contract implementation**

- [x] **TASK-074 — Sandbox abuse/security tests**

- [x] **TASK-075 — Infrastructure ADR for final sandbox technology**
  - Container / gVisor-like / MicroVM decision based on deployment constraints.
  - Selected v1: existing one-shot worker with fail-closed Linux NsJail
    namespaces/seccomp/cgroup hardening (ADR-055).

M7 Batch 1 completed TASK-070 and TASK-073. It defines the versioned execution
request/result boundary, typed failure stages, Decimal precision/serialization,
exact Program--MaskedEvidence--BindingMap bijection, TASK-068 revalidation, and
bounded static DSL policy. It performs no Program or formula execution, process
isolation, runtime resource limiting, abuse testing, or sandbox-technology
selection.

M7 Batch 2 completes TASK-071. The parent validates TASK-070, sends bounded
canonical JSON to a fixed separate worker, and validates the typed result. The
worker independently revalidates, binds `CanonicalDecimal` values, and
interprets only `IDENTITY`, `COLLECT`, `GROWTH_RATE`, and `AVERAGE` through the
built-in DSL/FormulaRegistry boundary. It never compiles model output to Python
source. At the Batch 2 boundary TASK-067 became implementable and TASK-072,
TASK-074, and TASK-075 remained pending; TASK-067 was later completed by M6
Batch 4.
`GPU_PRODUCTION_VALIDATION_PENDING` is unchanged.

M7 Batch 3 completes TASK-072 and TASK-074 with the exact versioned
`m7-limits-v1` profile documented in `docs/ARCHITECTURE.md`. The parent enforces
wall-clock, RSS, request, and output bounds and terminates the worker process
group on violation. The worker enforces CPU/process/descriptor/file-size limits,
an exact cleared environment, and filesystem/network/process-spawn denials.
Abuse tests cover every approved resource, protocol, policy, and capability
boundary without adding an operation to the production DSL. TASK-075 was later
completed by M7 Batch 4; this batch itself did not select final production
sandbox infrastructure.
`GPU_PRODUCTION_VALIDATION_PENDING` is unchanged.

M7 Batch 4 completes TASK-075 through ADR-055. It compares the existing
process boundary, standard containers, gVisor, and MicroVM/Firecracker across
security, startup, resources, operations, portability, observability, contract
compatibility, and migration cost. Because M7 executes only a closed validated
DSL and never arbitrary Python/user code, v1 keeps the one-worker-per-request
process and adds a fail-closed Linux NsJail boundary rather than a heavier
runtime.

Production mode `linux-nsjail-v1` requires the fixed minimal read-only image
layout plus root-owned, fingerprint-pinned NsJail config/seccomp policy. It adds
user/mount/PID/network/IPC/UTS/cgroup namespaces, capability drop,
`no_new_privs`, seccomp-bpf, and cgroup v2 outer limits while preserving every
`m7-limits-v1` value and the existing parent/worker enforcement. Missing,
tampered, unsupported, or non-Linux production setup fails as
`SECURITY/ISOLATION_SETUP_FAILED` with no local fallback.

Focused tests cover production-mode selection, fail-closed platform/config
handling, exact profile fingerprints, required native controls, and executor
command integration. `SandboxExecutionRequest`, `ExecutionResult`, the M9
`SandboxStagePort`, Decimal/DSL behavior, and M8/M9 semantics are unchanged.
Each production Linux image/host must still pass the real abuse probes and
cold-start/target-concurrency calibration before rollout; this deployment gate
does not reopen TASK-075 or clear `GPU_PRODUCTION_VALIDATION_PENDING`.

---

# M8 — Verification and Targeted Retry

Goal: reject wrong-but-executable answers and route failures to the correct stage.

- [x] **TASK-080 — Grounding verifier**
  - company/report/scope/period/table/row/column.

- [x] **TASK-081 — Numeric and unit verifier**

- [x] **TASK-082 — Financial-logic verifier**

- [x] **TASK-083 — LIGHT verification profile**

- [x] **TASK-084 — STRICT verification profile**

- [x] **TASK-085 — Failure classifier**

- [x] **TASK-086 — Retrieval retry path**

- [x] **TASK-087 — Programmer retry path**

- [x] **TASK-088 — CHEAP -> STRONG escalation**

- [x] **TASK-089 — Retry budget / termination tests**
  - no unbounded loops.

---

- [x] **TASK-08A — Scale/unit verifier**
  - verify value magnitude and scale provenance.

- [x] **TASK-08B — Hybrid evidence support verifier**
  - reject answers whose planned text/table support is missing.

M8 Batch 1 completes the core `VerificationRequest`, per-check
`VerificationCheckResult`, and aggregate `VerificationReport` contracts plus
TASK-080 and TASK-08B. Grounding follows the exact Plan requirement -> schema
link -> cell/paragraph -> evidence -> immutable provenance chain. Actual table
class is never inferred; unavailable provenance fails as
`TABLE_CLASS_UNAVAILABLE`. TABLE, TEXT, and HYBRID coverage is checked directly
against required Plan requirement IDs and preserves every missing ID.

Failed `ExecutionResult` values bypass Verification without reclassification.
This batch adds no LLM, retry, retrieval rerun, model escalation, or abstention
decision. TASK-081 through TASK-089 and TASK-08A remain Batch 2 work.
`GPU_PRODUCTION_VALIDATION_PENDING` is unchanged.

M8 Batch 2 extends `VerificationRequest` with the exact symbolic `Program`,
approved `ScaleUnitResolution` values, and execution-only `BindingMap`. TASK-081
checks output identity, kind, scalar/ordered shape, canonical decimals, and
ordered values without recomputing formulas. TASK-08A verifies immutable
scale/unit provenance and approved output conversion metadata without resolving
scale again. TASK-082 accepts only the registered LOOKUP, compare, growth, and
average symbolic shapes and rejects unsupported ratios.

All failed checks remain in the report and the existing precedence is
unchanged. This batch adds no LLM, retry, retrieval rerun, model escalation, or
abstention decision. TASK-083 through TASK-089 remain Batch 3 work.
`GPU_PRODUCTION_VALIDATION_PENDING` is unchanged.

M8 Batch 3 implements the deterministic LIGHT and STRICT check matrices. LIGHT
is accepted only for one-requirement, formula-free DIRECT LOOKUP plans;
it runs grounding, evidence support, numeric, and applicable scale/unit checks,
but not financial logic. STRICT runs every applicable deterministic verifier
and is required for formulas, PROGRAM reasoning, derived ratios, multi-period,
aggregate, and multi-requirement plans.

Plan/request profile mismatch and invalid LIGHT declarations are retained as
typed verification failures. Effective selection can strengthen to STRICT but
never downgrade. Failed execution still bypasses verification unchanged. This
batch adds no LLM, retry, retrieval rerun, model escalation, or abstention
decision. TASK-085 through TASK-089 remain Batch 4 work.
`GPU_PRODUCTION_VALIDATION_PENDING` is unchanged.

M8 Batch 4 completes TASK-085 through TASK-089 with immutable retry contracts,
an exhaustive reason-code classifier, bounded retrieval/Programmer directives,
one-time CHEAP-to-STRONG escalation, and deterministic termination. Every rerun
or escalation consumes one `Plan.max_retries` unit; PASS and ABSTAIN consume
none. Retrieval directives keep Plan filters and requirements unchanged;
Programmer directives keep Plan/Evidence value-safe and retain mandatory M6
validation. Failed `ExecutionResult` values remain outside Verification retry
classification.

M8 emits directives only. M9 remains responsible for executing them and for
end-to-end state orchestration. No retrieval, Programmer, model, or LLM call is
added by Batch 4. `GPU_PRODUCTION_VALIDATION_PENDING` is unchanged.

**M8_IMPLEMENTATION_COMPLETE**

# M9 — End-to-End Orchestration

Goal: connect all stages using structured state, without stage-to-stage free-form reinterpretation.

- [x] **TASK-090 — Orchestration state model**

- [x] **TASK-091 — NLU -> Supervisor flow**

- [x] **TASK-092 — Supervisor -> Retrieval flow**

- [x] **TASK-093 — Evidence -> Programmer flow**

- [x] **TASK-094 — Programmer -> Sandbox -> Verification flow**

- [x] **TASK-095 — Clarification / early-abstain response path**

- [x] **TASK-096 — Final abstain response path**

- [x] **TASK-097 — Answer Builder**
  - copies verified `ExecutionOutput` and cites only Program-used evidence in
    deterministic Plan-requirement order.

- [x] **TASK-098 — Retrieved-evidence E2E evaluation**

- [x] **TASK-099 — Failure attribution report**
  - distinguish NLU, planning, retrieval, code, execution, verification failures.

M9 Batch 1 completes TASK-090 contracts only: versioned checkpoint state,
immutable attempt history and Plan/retrieval-policy fingerprints, opaque
process-local binding references, terminal response schemas, execution-failure
directives, and inert typed stage ports. It adds no orchestration graph, stage
call, retry execution, answer construction, or E2E behavior. At the Batch 1
boundary, TASK-091 through TASK-099 were pending. Source-backed
`CellLocation.table_class` provenance is
an upstream requirement; current real locations do not provide it, and M9 must
not infer it from Plan or metric mappings. At this batch boundary TASK-075
remained deferred; it was later completed by M7 Batch 4, and
`GPU_PRODUCTION_VALIDATION_PENDING` is unchanged.

M9 Batch 2 completes TASK-091 through TASK-094 with the pinned LangGraph
straight-through flow and injected NLU/retrieval/evidence/programmer/sandbox
adapters. It creates only attempt zero, stops on the first native typed stage
failure, and otherwise stops after the M8 VerificationReport. It adds no retry
execution, directive classification, final response, answer construction,
abstain routing, or E2E evaluation. Fixture paths can supply source-backed
`table_class`; the production source path remains blocked and M9 does not infer
it. At this batch boundary TASK-075 remained deferred; it was later completed
by M7 Batch 4. `GPU_PRODUCTION_VALIDATION_PENDING` is
unchanged.

M9 Batch 3 completes TASK-095 through TASK-097. The graph now executes existing
M8 verification directives and explicit M9 execution-failure directives with
one shared, bounded retry budget. Retrieval, Programmer, strong-tier, and
Sandbox retries create immutable new attempts with stage-specific preservation;
Sandbox retry does not regenerate Program. Clarification uses only existing
PlanningGate findings, all terminal failures emit typed abstention without an
answer, and PASS copies verified output with only Program-used citations.
Unknown execution failure codes fail closed and failed execution never creates
a VerificationReport. TASK-098 and TASK-099 remain pending. Production
source-backed `table_class` remains blocked upstream. At this batch boundary
TASK-075 remained deferred and was later completed by M7 Batch 4,
and `GPU_PRODUCTION_VALIDATION_PENDING` is unchanged.

M9 Batch 4 completes TASK-098 and TASK-099 with a deterministic CPU-only
retrieved-evidence fixture evaluator and typed terminal attribution. The CLI
scores NLU, Plan, evidence, trace, execution, verification, status, answer,
retry count, and strong escalation independently, and reports retry/failure
distributions. Attribution records only the earliest unrecovered terminal
failure after bounded retries, preserves native codes and relevant IDs, and is
absent on PASS. The checkpoint schema is `m9-orchestration-state-v2`.
Production TABLE E2E remains explicitly blocked by
`PRODUCTION_TABLE_CLASS_PROVENANCE_PENDING`; no `table_class` inference was
added. M9 implementation tasks TASK-090 through TASK-099 are complete.
At this batch boundary TASK-075 remained deferred; it was later completed by
M7 Batch 4. `GPU_PRODUCTION_VALIDATION_PENDING` is unchanged.

---

# M10 — Cost, Latency, and Serving Optimization

Do this only after the correctness baseline is measurable.

- [ ] **TASK-100 — Establish strong-tier/strict-verification correctness baseline**

- [ ] **TASK-101 — Activate CHEAP routing for eligible simple cases**

- [x] **TASK-102 — Confidence-gated retry and model escalation**
  - measure retry rate and quality gain.

- [x] **TASK-103 — Parallelize independent multi-table / multi-source retrieval**

- [x] **TASK-104 — Parallelize independent verifier checks**
  - `src/verification/verifier.py` `verify(request, parallel=True)` runs the
    independent check groups on a thread pool and reassembles them in the fixed
    order, so the `VerificationReport` (checks, order, failure precedence) is
    byte-identical to the default sequential path
    (`tests/verification/test_parallel_verifier.py`). CPU-only; no model.

- [x] **TASK-105 — Semantic cache experiment**
  - cache only sufficiently similar, stable inputs,
  - compare no-cache vs. field/query-level vs. adaptive policy,
  - monitor correctness delta.

- [x] **TASK-106 — End-to-end latency distribution**
  - p50,
  - p95,
  - p99,
  - per-stage latency.

- [x] **TASK-107 — Model serving ADR**
  - exact serving engine/provider depends on deployment constraints.

- [x] **TASK-108 — Shared Qwen3-8B role serving**
  - separate NLU/Supervisor prompts and contracts on one service where feasible.

- [x] **TASK-109 — Retrieval model serving** (implemented; live-serving evidence
  still `GPU_PRODUCTION_VALIDATION_PENDING`)
  - BGE-M3 batching,
  - BGE-reranker-v2-m3 serving,
  - model/index version tracking.
  - `src/serving/` adapters are implemented CPU-only over an injected transport.
    Real live-serving evidence comes only from
    `src/serving/live_probe.py` +
    `python -m src.evaluation.run_retrieval_production_validation`
    (ADR-057), which calls the deployed BGE-M3 / BGE-reranker endpoints and
    checks dimension (1024), finite output, and parity against the pinned local
    implementation. `Qwen3-8B` is **not** on the active online *retrieval* path
    and is not required for this clearance.
  - The vector-index acceptance count is **1,743,311 vectors** (one per canonical
    M2 `EmbeddingChunk`). It is not 146,246 (that is the source-table count) and
    not the representation count.

- [x] **TASK-110 — Cost/token dashboard**
  - cost/query,
  - tokens/query,
  - escalation cost,
  - cache hit rate.

- [x] **TASK-111 — Throughput/load test**
  - measure accuracy/timeout degradation at target concurrency and document volume.

- [x] **TASK-112 — Coordination-failure logging**
  - conflicting worker state,
  - stale state,
  - retry loops,
  - message/schema corruption.
  - `src/orchestration/coordination_log.py` is a pure read-only observer
    (`tests/orchestration/test_coordination_log.py`, ADR-056). It keeps the
    existing `ATTEMPT_RECORDED` / `MODEL_TIER_ESCALATION` /
    `RETRY_BUDGET_CONSUMED` / `RETRY_BUDGET_EXHAUSTED` / `TERMINAL_FAILURE`
    events (retry-loop bullet) and adds three typed detectors, each only over an
    invariant M9's own contracts do not already enforce:
    - `CONFLICTING_WORKER_STATE` — a non-`PASS` terminal outcome whose final
      `AttemptRecord` still carries an M8 `RetryDirective` with `action == PASS`
      (`M9State._validate_pass` only constrains the `PASS` direction), or a
      `CLARIFICATION` terminal outcome with a non-empty attempt history.
    - `STALE_WORKER_STATE` — `FailureAttribution.attempt_index` does not equal
      the current `attempts[-1].attempt_index` (or is set with no attempts),
      i.e. attribution never advanced with the run.
    - `MESSAGE_SCHEMA_CORRUPTION` — `scan_coordination_payload` runs a raw
      checkpoint dict through the existing `M9State.from_dict` fail-closed edge
      and, on `SchemaValidationError`, records only the boundary name and the
      exception class name (no payload body, no message).
    Detection is deterministic and fail-closed (a valid state emits none of the
    three); events carry only stable diagnostic scalars — no `binding_ref`,
    evidence, output value, prompt, or secret.

M10 Batch 6 completes TASK-106, TASK-110, and TASK-111 (ADR-052) as versioned
measurement/telemetry layers held separate from the deterministic pipeline
contracts (ADR-040), each carrying a `MeasurementSource` so CPU fixture numbers
can never be mistaken for live production numbers.

TASK-106 adds `src/evaluation/telemetry.py` (shared `Distribution` p50/p95/p99,
`Stopwatch`, `MeasurementSource`) and `src/evaluation/latency.py`:
`profile_stages` times an ordered stage set + total, and `measure_e2e_latency`
records real CPU end-to-end wall-clock over the fixtures. Per-stage live-model
latency is reported `LIVE_PENDING`.

TASK-110 adds `src/evaluation/cost_dashboard.py`: versioned `ModelInvocation` /
`TokenPrice` contracts and `build_cost_dashboard` aggregating tokens/query,
cost/query, real priced escalation cost, per-model breakdown, and cache hit
rate. Prices are always caller-supplied; with no live invocation the token/cost
fields are `LIVE_PENDING` rather than fabricated.

TASK-111 adds `src/evaluation/load_test.py`: a bounded (`MAX_CONCURRENCY`,
`MAX_TOTAL_REQUESTS`) `run_load_test` measuring throughput, latency
distribution, and error/timeout counts under real concurrency on CPU. Production
accuracy/timeout degradation with live models is `LIVE_PENDING`.

New CLIs `run_latency_eval`, `run_cost_dashboard`, and `run_load_test` run
CPU-only. No GPU/model latency, token, or cost number is fabricated. TASK-104
was later completed (M10 Batch 7). At this batch boundary TASK-075 remained
deferred; it was later completed by M7 Batch 4, and
`PRODUCTION_TABLE_CLASS_PROVENANCE_PENDING` remains active.

M10 Batch 5 completes TASK-102, TASK-103, and TASK-105 (ADR-051) as CPU-only,
deterministic increments that add no second routing source of truth.

TASK-102 is measurement only: `src/evaluation/retry_metrics.py` reads the
terminal M9 case results and reports retry rate, escalation rate, and quality
recovered by retry/escalation, optionally bucketed by `Plan.confidence` for
future calibration. Retry/escalation routing stays in the deterministic M8
`build_retry_directive`; confidence never gates a directive.

TASK-103 adds `src/retrieval/parallel.py`: `retrieve_multi_table_parallel` and
`retrieve_sources_parallel` run independent TABLE subqueries / TABLE-vs-TEXT
sources concurrently but reuse the exact Batch 3 decomposition, per-subquery
requests, backend→RRF→reranker sequence, and subquery-ordered merge, so results
are byte-identical to the sequential path (proven against
`Batch3Retriever.retrieve_multi_table`).

TASK-105 adds an optional `SemanticCache`
(`src/orchestration/semantic_cache.py`) and a policy experiment
(`src/evaluation/cache_experiment.py`) comparing no-cache / query / field /
adaptive. The cache is disabled by default, versioned, fingerprint-invalidatable,
and stores only verified PASS answers — it never bypasses verification or returns
an unverified answer. The experiment monitors correctness delta on the real
fixtures (zero regressions).

Measurements are the CPU fixture structure only; retry-rate/quality-gain over
real models, and embedding-similarity ("semantic") cache hit-rate/quality, are
`GPU_PRODUCTION_VALIDATION_PENDING`. TASK-104 was later completed (M10 Batch 7).
At this batch boundary TASK-075 remained deferred and was later completed by
M7 Batch 4; `PRODUCTION_TABLE_CLASS_PROVENANCE_PENDING` remains active.

M10 Batch 7 records the current audited state of the remaining M10 items. It
adds no pipeline behavior.

TASK-104 is complete and CPU-only (see the checklist entry above); the stale
"TASK-104 remains open" lines in the Batch 5/6 notes are corrected accordingly.

TASK-112 is complete and CPU-only (ADR-056): the coordination-failure observer
now emits distinct typed detectors for conflicting worker state, stale
worker/state observation, and message/schema corruption, in addition to the
existing attempt/escalation/retry-budget/terminal events. Every acceptance
bullet is covered by `tests/orchestration/test_coordination_log.py`. It changes
no M9 orchestration behavior.

**No LLM is in the v1 active query path (verified in code, ADR-057).** The M9
graph calls `supervise_batch1` and `verify` as deterministic functions and takes
NLU / Programmer / Sandbox through injected ports whose only implementations are
deterministic (`src/evaluation/e2e_fixtures.py`). `generate_program` is a pure
symbolic-DSL builder and ignores model tier. `Plan.model_tier` /
`RetryState.current_model_tier` / `ESCALATE_STRONG` only set recorded enum
values — nothing consumes them to invoke a model. `src/serving/` (the Qwen
topology + client) is imported by nothing on the active path. `torch` /
`transformers` appear only in BGE-M3 / BGE-reranker code. So Qwen3-8B and
Qwen2.5-Coder-14B are **not** required to validate the v1 query path, and
`GPU_PRODUCTION_VALIDATION_PENDING` is canonically a **retrieval-model /
production-vector-artifact blocker only** (ADR-020, ADR-025, ADR-031).

TASK-100 and TASK-101 have CPU modules implemented and unit-tested but stay
unchecked. Classified by what their current implementation can actually measure;
no numeric threshold is invented for either:

- **TASK-100** — `src/evaluation/baseline.py` + `run_baseline_eval` measure
  STRONG-tier + STRICT-verification correctness over the deterministic
  retrieved-evidence fixtures (3 cases, all PASS, `acceptance_thresholds = None`).
  In v1 "STRONG tier" is a deterministic routing label, not a model, so this
  task is **retrieval-gated, not Qwen-gated**: closing it needs the production
  vector artifact built + retrieval production validation (ADR-057) PASS, and
  real per-question gold answers to score real-corpus correctness against
  (`BLOCKED_GOLD_DATA`, like retrieval quality). It is not a live-LLM task in the
  current architecture.
- **TASK-101** — `src/supervisor/cheap_routing.py` `activated_model_tier`
  returns exactly the M4-routed `Plan.model_tier` for every plan; the switch is
  not consumed by the graph. With no LLM, CHEAP vs STRONG resolves to the *same*
  deterministic code path, so there is **no measurable quality/cost delta today**.
  TASK-101's "activate CHEAP routing + measure the delta" only becomes meaningful
  once model-backed NLU/Supervisor/Programmer ports are actually built and wired
  into M9 — unbuilt, out-of-v1-scope work with no task. Current implementation
  can only assert the routing decision is deterministic and non-lossy (already
  proven). **Not GPU-gated in the current v1 architecture.**

M10 Batch 8 fixes the GPU validation runbook and closes the missing executable
retrieval-validation gap (ADR-057). It adds no pipeline behavior and modifies no
retrieval semantics.

- The vector-index acceptance count is corrected to **1,743,311 vectors** (one
  per canonical M2 `EmbeddingChunk`); 146,246 is the source-table count and must
  never be used as the vector count.
- Qwen serving is **removed** from the required GPU validation runbook: it is not
  on the active v1 path (see above).
- `python -m src.evaluation.run_retrieval_eval --mode full-corpus` is CPU
  regression only; it now exits non-zero with `status: NOT_RUN_HERE` and points
  to the real runner instead of printing a pass-looking line.
- New `src/evaluation/retrieval_production_validation.py` +
  `run_retrieval_production_validation` runner: category 1
  **ARTIFACT_SERVING** (corpus/index/BM25 compatibility, exact
  `vector_count == canonical chunk_count`, anti-substitution guard for the table
  count, shard + SQLite integrity, embedding fingerprint, and the two **LIVE**
  serving probes + a deterministic retrieval-execution smoke); category 2
  **RETRIEVAL_QUALITY** which is `BLOCKED_GOLD_DATA` because the public ViFinQA
  `questions.jsonl` has no gold retrieval-evidence annotations — fixture
  retrieval metrics are never substituted.
- New `src/serving/live_probe.py`: real-endpoint probes for BGE-M3 (dim 1024,
  finite floats, cosine ≥ 0.999 vs. the pinned CPU encoder) and BGE-reranker
  (response shape/order, order parity vs. the pinned CPU reranker). The injected
  `tests/serving` transports are not accepted as live evidence.
- New `src/indexing/embedding_build_preflight.py` (`preflight_vector_build`):
  read-only, no model load; resolves the committed corpus, computes the
  deterministic `build_id`, and decides `FRESH` / `RESUME` / `ALREADY_PUBLISHED`
  for a given `--output-root` + `--max-vectors-per-shard`.
- New `src/indexing/embedding_batch_calibration.py`: runs the pinned BGE-M3
  encoder over a bounded corpus sample (≤ 20,000 chunks) at batch sizes
  8/16/32/64, records chunks/sec + peak VRAM + OOM, and recommends the largest
  stable batch that is at least `--min-speedup`× faster than the prior. It never
  calls the builder and never writes to an output root.
- `GPU_PRODUCTION_VALIDATION_PENDING` stays active. It clears for retrieval only
  when `run_retrieval_production_validation` returns `overall_status: PASS`
  (`clears_gpu_pending: true`) on the real committed corpus/vector/BM25 artifacts
  with both LIVE probes passing on GPU hardware. It does **not** wait on any LLM.
M10 Batch 4 completes TASK-108 and TASK-109 (ADR-050) by implementing the
ADR-049 serving boundary in `src/serving/`. `ServingTopology` resolves the four
one-process-per-model endpoints: NLU/Supervisor/optional Verifier share the one
Qwen3-8B process, the Programmer uses the separate STRONG process, and
`endpoint_for_tier` consumes the already-decided `Plan.model_tier` without
re-deriving any route. `OpenAICompatibleClient` is a thin transport-injected
client (generate/embed/score/tokenize). `ServingQueryEncoder` plugs into the
existing `embed_query_texts` (M2B fingerprint/dimension/normalization gate
unchanged) and `ServingBGEReranker` subclasses the pinned `BGEReranker` so
`rerank_candidates` deterministic ranking is reused, not duplicated. Everything
is CPU-only and network-free through an injected transport; the production
`HttpTransport` and live-GPU serving remain
`GPU_PRODUCTION_VALIDATION_PENDING`. No model is deployed and no ModelTier
routing changed. At this batch boundary TASK-075 remained deferred and was
later completed by M7 Batch 4;
`PRODUCTION_TABLE_CLASS_PROVENANCE_PENDING` remains active.

M10 Batch 3 completes TASK-107 as a docs-only decision (ADR-049). It selects
vLLM as the primary production serving engine, one process per model, behind one
thin OpenAI-compatible serving-client contract: a shared `qwen3-8b` process for
NLU/Supervisor/optional Verifier (CHEAP), a `qwen-coder-14b` process for the
Programmer (STRONG), and version-pinned `bge-m3` embedding and
`bge-reranker-v2-m3` scoring processes. It records the GPU-memory, batching,
concurrency, latency, throughput, model-sharing, observability, deployment, and
failure-isolation tradeoffs and the expected two-GPU topology. SGLang (primary),
llama.cpp (production GPU), a single shared process, and per-role Qwen processes
are the rejected alternatives.

No model is deployed and no serving code is added; the servers and client are
TASK-108/TASK-109 (next M10 batch), which are not started. `Plan.model_tier`
routing is unchanged. At this batch boundary TASK-075 remained deferred and was
later completed by M7 Batch 4;
`PRODUCTION_TABLE_CLASS_PROVENANCE_PENDING` and
`GPU_PRODUCTION_VALIDATION_PENDING` remain active.

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
