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

Current execution host (2026-10-09): macOS, Apple M1, 8 GiB RAM; no RTX 5090 is
currently used. GPU run evidence below is historical. Local CPU validation uses
the Python 3.11 environment in `README.md` / `requirements-test.txt`. ADR-059
fixes exact Darwin worker startup while preserving all sandbox controls.
At that validation boundary, real-corpus TASK-100 remained open; local
fixture/test success cannot close it. The bounded real-corpus pilot measured
on 2026-10-10 is recorded in the TASK-100 entry below.
Mac validation after the fix: 1035 tests passed, 4 real-model/artifact checks
skipped, zero failures; Supervisor 14/14, oracle reasoning 7/7, E2E fixtures
12/12 and baseline fixtures 3/3. Evidence: `scratch_logs/mac-validation.json`.

---

# M0 — Project Contracts and Evaluation Skeleton

Goal: establish the interfaces and evaluation boundaries before building agents.

- [x] **TASK-001 — Define `QueryUnderstanding` schema**
  - Fields: company, periods, period kind, statement scope, metrics, operation, missing information, ambiguity, confidence.
  - Acceptance:
    - schema validation tests,
    - explicit representation of inferred vs. explicit scope,
    - missing information is representable without fabricated defaults.

- [x] **TASK-002 — Define canonical `Plan` schema**
  - Implement the canonical fields defined in `docs/ARCHITECTURE.md` and preserve the routing decisions in `docs/DECISIONS.md`.
  - Acceptance:
    - valid examples for LOOKUP / DERIVED_RATIO / MULTI_PERIOD / AGGREGATE,
    - invalid enum/field cases rejected,
    - ROE example serializes correctly under an explicitly registered test
      formula and metric-table mappings; the production registry remains
      ratio-free and rejects unregistered ROE (current M4/M6 v1 policy).

- [x] **TASK-003 — Define evidence contract**
  - Preserve report/table/period/scope and hierarchical row/column metadata where available.
  - Acceptance:
    - every selected value can be traced back to source metadata,
    - scale provenance accepts `HEADER`, `CELL`, `CAPTION`, `TEXT`, and `QUESTION`,
    - caption provenance round-trips without being collapsed into `TEXT`.

- [x] **TASK-004 — Define execution and verification result schemas**
  - Acceptance:
    - typed success/failure outcomes,
    - explicit failure category/reason.

- [x] **TASK-005 — Build evaluation harness skeleton**
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


- [x] **TASK-006 — Add reasoning-trace evaluation contracts**
  - Implement the canonical `ReasoningEvaluationResult` and `TraceEquivalenceHook` contracts defined in `docs/ARCHITECTURE.md`.
  - execution accuracy,
  - program/trace accuracy,
  - answer accuracy,
  - normalized equivalence hooks for semantically equivalent programs.
  - Do not define Program or Answer schemas, thresholds, or aggregate metrics.

- [x] **TASK-007 — Add evaluation slices**
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

- [x] **TASK-010 — Company/ticker resolver**
  - Support canonical resolution from aliases/tickers when uniquely known.
  - Implement the canonical company resolver contracts and normalization v1 defined in `docs/ARCHITECTURE.md`.
  - Do not use fuzzy matching, typo correction, substring guessing, or an LLM.
  - The authoritative alias dataset and storage mechanism remain TBD.
  - Acceptance:
    - deterministic tests for aliases,
    - ambiguous alias does not silently select a ticker.

- [x] **TASK-011 — Temporal parser**
  - Parse year, quarter, and cumulative-period expressions.
  - Implement the canonical deterministic v1 contracts and normalized values defined in `docs/ARCHITECTURE.md`.
  - Preserve exact matched text; incomplete/invalid expressions remain unresolved without nearby-text inference or an LLM.
  - Acceptance:
    - examples such as `năm 2015`, `quý 3/2015`, `lũy kế 9 tháng`.

- [x] **TASK-012 — Statement-scope resolver**
  - Resolve consolidated vs. standalone/company-parent scope.
  - Implement the canonical deterministic v1 contract and indicators defined in `docs/ARCHITECTURE.md`.
  - Do not default or infer scope for aggregated/unlabeled reports and do not use an LLM.
  - Acceptance:
    - explicit and inferred scope are distinguishable.

- [x] **TASK-013 — Financial metric normalization**
  - Canonicalize Vietnamese synonyms.
  - Implement the canonical deterministic v1 metric contracts and exact normalization rules defined in `docs/ARCHITECTURE.md`.
  - Do not expand the initial registry beyond documented examples or use fuzzy/substring/LLM matching.
  - Example: `lãi ròng` / `LNST` / `lợi nhuận sau thuế`.

- [x] **TASK-014 — Operation detector**
  - Output: `none | ratio | growth | aggregate | compare | unknown`.
  - Implement the canonical deterministic v1 contracts, minimal indicator
    vocabulary, conflict handling, and documented growth override in
    `docs/ARCHITECTURE.md`.
  - Do not use fuzzy matching, typo correction, substring guessing, or an LLM.

- [x] **TASK-015 — Ambiguity and missing-information detector**
  - Implement the canonical `FindingName` and `PlanningGate` contracts in
    `docs/ARCHITECTURE.md` exactly.
  - Company, requested periods, and requested metrics are hard-required;
    statement-scope eligibility remains deferred.
  - Acceptance:
    - unresolved company/period/metric is surfaced,
    - no downstream plan when hard-required identity is missing.

- [x] **TASK-016 — NLU evaluation set**
  - Implement the canonical case/result/field-comparison contracts in
    `docs/ARCHITECTURE.md`.
  - Use exact structural comparison for the seven approved semantic fields.
  - Keep `raw_question`, overall confidence, requested scale, and requested unit
    outside v1 scoring.
  - Use a small approved fixture set only; no evaluator normalization or LLM judge.
  - Thresholds and aggregate scoring: `TBD`.

---

- [x] **TASK-017 — Requested scale/unit parser**
  - Implement the canonical deterministic `RequestedScaleUnit` contract and v1
    mappings defined in `docs/ARCHITECTURE.md`.
  - Parse only explicit `nghìn`, `ngàn`, `triệu`, `tỷ`, `%`, and `phần trăm`
    expressions into the existing `QueryUnderstanding` fields.
  - Keep `requested_unit` null; currency/unit vocabulary remains TBD.
  - Do not perform evidence-scale resolution or change `EvidenceItem.scale`.
  - Do not use fuzzy matching, typo correction, substring guessing, or an LLM.

## M0/M1 completion audit — 2026-10-13

All 15 M0/M1 tasks are complete against the current documented v1 contracts.
Their implementations already existed; this audit closes stale checkboxes and
adds the missing TASK-002 DERIVED_RATIO/ROE serialization proof. No production
component, registry, model invocation, or public contract is added or changed.

| Task | Existing implementation | Acceptance evidence |
|---|---|---|
| TASK-001 | `src/understanding/schemas.py` | `tests/understanding/test_schemas.py`: exact fields, nullable/missing identity, explicit/inferred scope, enum/confidence rejection, round-trip |
| TASK-002 | `src/supervisor/schemas.py` | `tests/supervisor/test_schemas.py`: LOOKUP, MULTI_PERIOD, AGGREGATE, registered DERIVED_RATIO/ROE round-trip; invalid fields/registry rejection; production registries restored unchanged |
| TASK-003 | `src/evidence/schemas.py` | `tests/evidence/test_schemas.py`: source report/table/period/scope and ordered paths; lossless numeric boundary; HEADER/CELL/CAPTION/TEXT/QUESTION provenance; CAPTION round-trip |
| TASK-004 | `src/sandbox/schemas.py`, `src/verification/schemas.py` | `tests/sandbox/test_schemas.py`, `tests/verification/test_schemas.py`: typed success/failure outcomes, stage/code/message, category/reason and deterministic precedence; current M7/M8 contracts reused |
| TASK-005 | `src/evaluation/harness.py` | `tests/evaluation/test_harness.py`: all five modes, exact failure stages, pass/fail invariants, separate stage/oracle/retrieved-E2E paths |
| TASK-006 | `src/evaluation/reasoning.py` | `tests/evaluation/test_reasoning.py`: independent execution/trace/answer outcomes; exact-first equivalence hook, typed result validation, round-trip |
| TASK-007 | `src/evaluation/slices.py` | `tests/evaluation/test_slices.py`: TABLE/TEXT/HYBRID, three reasoning depths, strict scale/unit-sensitive flag; HYBRID stays evaluation-only; nested contracts round-trip |
| TASK-010 | `src/understanding/company_resolver.py` | `tests/understanding/test_company_resolver.py`: exact alias/ticker resolution, case/trim normalization, duplicate canonical deduplication, unresolved/ambiguous outcomes, no fuzzy guessing |
| TASK-011 | `src/understanding/temporal_parser.py` | `tests/understanding/test_temporal_parser.py`: year/quarter/cumulative forms, exact raw spans/source order, longest non-overlap, incomplete/invalid values stay unresolved |
| TASK-012 | `src/understanding/statement_scope_resolver.py` | `tests/understanding/test_statement_scope_resolver.py`: explicit scope overrides structured context, inferred flag, conflicting/null scopes, no hidden default |
| TASK-013 | `src/understanding/metric_normalizer.py` | `tests/understanding/test_metric_normalizer.py`: approved exact synonyms, Unicode/case/whitespace normalization, distinct-metric ambiguity, no fuzzy/substring matching |
| TASK-014 | `src/understanding/operation_detector.py` | `tests/understanding/test_operation_detector.py`: all six operations, empty/unknown vs none, exact indicators, growth override, other conflicts remain unknown |
| TASK-015 | `src/understanding/planning_gate.py` | `tests/understanding/test_planning_gate.py`, `tests/supervisor/test_planner.py`: missing/ambiguous hard identities, canonical finding order, unknown-operation block, input immutability, blocked gate never produces a Plan |
| TASK-016 | `src/evaluation/nlu.py`, `tests/evaluation/fixtures/nlu_v1.json` | `tests/evaluation/test_nlu.py`: three approved fixtures, exact seven-field comparisons, independent field mutations, excluded raw question/confidence/requested scale/unit, no evaluator normalization or LLM judge |
| TASK-017 | `src/understanding/requested_scale_unit_parser.py` | `tests/understanding/test_requested_scale_unit_parser.py`: complete supported tokens/symbols, repeated/conflicting scales, normalized matching, requested_unit stays null, existing QueryUnderstanding fields reused |

TASK-002 exercises real Plan validation with an isolated declarative TEST_ROE
registry fixture and explicit LNST/equity mappings. Its 2015 profit and 2015/2014
equity requirements are authored test inputs, not inferred by the Supervisor.
Unregistered formulas are still rejected inside the fixture, and the unchanged
production registry rejects the ROE payload. This proves the schema acceptance
example; it does not register or execute a production ROE formula.

Validation: the focused M0/M1 audit passes **200 tests and 191 subtests**.
Supervisor fixtures 14/14, oracle reasoning 7/7, retrieved-E2E fixtures 12/12,
and baseline fixtures 3/3 pass. Logs are `scratch_logs/m0-m1-audit-tests.txt`,
`m0-m1-supervisor.json`, `m0-m1-oracle.json`, `m0-m1-e2e.json`, and
`m0-m1-baseline-fixture.json`. Full repository regression reports **1091 passed,
350 subtests passed, 2 skipped, and 2 existing failures** from the optional real
500-vector v1 artifacts, which are incompatible with the current v2 contract.
No test or compatibility check was weakened. Evidence:
`scratch_logs/m0-m1-repository-pytest.txt`.

The completion audit also round-trips all five scale-provenance sources and
checks that all 15 task entries are marked complete while the production formula
registry remains exactly GROWTH_RATE/AVERAGE. Its report and validation-log hashes
are `scratch_logs/m0-m1-completion-audit.json`. At that audit boundary, eight
roadmap tasks remained open: TASK-101 and TASK-120 through TASK-126. Their later
completion is recorded in the M11 evidence below.

Scope limits remain as documented: authoritative alias/vocabulary storage,
extra temporal/operation forms, scope eligibility in PlanningGate, currency/unit
parsing, and production NLU quality thresholds are deferred. TASK-016 fixtures
verify comparison contracts; they do not establish raw-question NLU accuracy.
No real-model or full-corpus evaluation is claimed for this contract audit.

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

- [x] **TASK-06A — Define atomic table operation pool**
  - select rows,
  - select columns,
  - add derived column,
  - group,
  - sort.

- [x] **TASK-06B — Implement deterministic table-operation executor**

- [x] **TASK-06C — Implement bounded table-transform planner**
  - latest table state + question + operation history,
  - explicit terminal state,
  - hard maximum steps.

- [x] **TASK-06D — Compare static Programmer vs. TABLE_TRANSFORM fallback**
  - only keep fallback if it improves complex-table cases without unacceptable latency/cost.

M6B completes TASK-06A through TASK-06D. It defines the value-free
`m6b-table-transform-v1` atomic operation contracts (`select_rows`,
`select_columns`, `add_derived_column`, `group`, `sort`), deterministic
pure-function executor with complete cell provenance, bounded planner with
explicit terminal state and hard maximum-step cutoff, and a comparative
evaluation harness against the static M6 Programmer. The expanded comparative
benchmark (13 total cases) separates baseline cases (5 cases: Static 40.0% vs.
Fallback 100.0%) from multi-step complex cases (8 cases requiring >= 3 operations:
Static 0.0% vs. Fallback 75.0% with 2.50 average steps). Comprehensive
anti-hardcode tests (`test_anti_hardcode.py`) verify 0 leakage of raw numbers
across all intermediate planner states and complete invariance under mutation.
Because real corpus gold annotations (ViFinQA) remain `BLOCKED_GOLD_DATA`,
`TABLE_TRANSFORM` remains strictly disabled in M4 production routing.

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

- [x] **TASK-100 — Establish strong-tier/strict-verification correctness baseline**
  - Fixture regression alone must never set `established=true` (ADR-058).
  - Validate independent VERIFIED scalar gold against the canonical corpus;
    require explicit answer scale and preserve unit.
  - Score terminal real M9 runs under STRONG/STRICT, bound to passing ADR-057
    corpus/vector/BM25 artifact/live validation. Missing runs and abstentions
    remain incorrect; report value/unit/grounding and failures separately.
  - Record achieved accuracy; acceptance thresholds remain TBD. A measured
    baseline need not score 100%, but fixtures cannot close this task.
  - Rework on 2026-10-09: fixture CLI is now explicitly regression-only;
    `--mode production` scores recorded E2E exports (ADR-058), not live calls.
    Follow-up on 2026-10-09: the exact 37-case LFS LOOKUP pilot was recovered;
    source audit resolves 21 RAW, 15 MILLION and 1 THOUSAND answers, preserving
    values/units/evidence ids (`gold/retrieval/pilot-scale-audit.json`). The complete
    1,973-report raw source matches canonical ids/hashes/inventory. M2 corpus,
    vector index and both sidecars are now present at the repository root.
    Full-corpus BM25 was rebuilt in 342.702 seconds: 1,743,311 candidates,
    artifact `35399789d6b5cb5cfb08168922dc5cce72b2003bf19827e83736a115493b4d51`
    (`scratch_logs/task100-bm25-build.json`). The real BM25-only 37-case pilot
    records Recall@10 2/37 and MRR 0.008108; this is annotated-query retrieval,
    not E2E (`scratch_logs/task100-bm25-pilot.json`). A memory-bounded CPU
    evaluator now reuses the pinned BGE/FAISS/RRF/reranker implementations:
    `python -m src.evaluation.run_live_retrieval_eval`.
    The user selected DeepSeek Flash API instead of Qwen servers (ADR-060).
    API-backed NLU/Programmer and a real M9 export runner now exist:
    `python -m src.evaluation.run_live_e2e_eval`; deployment instructions are in
    `deploy/serving/README.md`. The user supplied the key in the ignored `.env`,
    and a live probe succeeded with response
    model `deepseek-flash` (`scratch_logs/task100-deepseek-probe.json`). Full vector
    SQLite integrity_check passed in 505.284 seconds
    (`scratch_logs/task100-vector-integrity.json`). The first raw-question M9 run
    completed 37/37 pilot cases: 35 CLARIFICATION, 2 ABSTAIN, 0 answers (0% answer
    accuracy); 28 MISSING_METRIC, 7 MISSING_PERIOD+MISSING_METRIC and 2
    MISSING_STATEMENT_SCOPE. No request reached retrieval/Programmer/sandbox/
    verification. Real terminal states and a failure summary are in
    `scratch_logs/task100-live-e2e.json` and `scratch_logs/task100-live-e2e-summary.json`.
    The current production CLI prerequisite check, supplied with these corpus/run
    paths, stops at MISSING_RETRIEVAL_VALIDATION
    (`scratch_logs/task100-baseline-current-gate.json`); it does not score the
    pilot or replace ADR-057 validation.
    Full annotated retrieval then completed in 4,338 seconds with no stage
    errors: Recall@10 BM25 0.0541, vector 0, RRF 0.0270, reranked 0.0541;
    reranked MRR 0.0297 (`scratch_logs/task100-live-retrieval.json`). Its low
    score is measured retrieval quality, not an accepted TASK-100 baseline.
    At that boundary the operational task remained open pending appropriate
    STRONG/STRICT gold, matching terminal M9 runs and ADR-057 validation.
  - Completed on 2026-10-10 for a bounded real-corpus pilot:
    `gold/retrieval/strong-strict.jsonl` contains three newly authored,
    independently VERIFIED AVERAGE/LNST questions (AAA consolidated, AAA
    separate, FPT consolidated; 2022/2023). Original code-60 income-statement
    cells, headers, VND units, source hashes and independent Decimal means are
    recorded in `strong-strict-source-audit.json`; comparative 2022 amounts
    also match the original 2022 reports. Gold validation reports no issues.
    The NLU prompt now specifies canonical LNST synonyms and `aggregate` for
    arithmetic mean; invalid `average` proposals remain rejected.
    Real raw-question M9 completed all three under natural STRONG/STRICT
    routing (`scratch_logs/task100-strong-live-e2e.json`). Production scoring,
    bound to the matching historical ADR-057 artifact/live validation, reports
    `MEASURED`, `established=true`, no blockers, and answer accuracy **0/3 (0%)**
    (`scratch_logs/task100-strong-baseline.json`). Every case abstained at
    Evidence with `EVIDENCE_LOCATION_AMBIGUOUS`; none reached Programmer,
    sandbox or verification. Per-question inference took 310.098, 306.673 and
    998.543 seconds; source reconstruction rescans corpus prefixes per candidate.
    Tests: 48 focused tests; Supervisor 14/14, oracle 7/7, E2E fixtures 12/12,
    baseline fixtures 3/3. This closes baseline measurement only: three curated
    AVERAGE/LNST cases do not establish corpus-wide quality or a release
    threshold. No fresh Mac HTTP endpoint parity is claimed; the reused ADR-057
    report validates the same immutable corpus/vector/BM25 build IDs.
  - Follow-up on 2026-10-10 (ADR-061, not a re-measured baseline): the
    evidence join, BM25 metadata filter, registry subquery wording, VAS header
    fallback and shared metric/period matching were fixed. Replaying the three
    recorded questions through real Batch3 retrieval and the real Evidence node
    ranks the LNST income-statement chunk first in all six subqueries and
    resolves one exact schema link per period whose value equals gold. All three
    now stop at `SCALE_RESOLUTION_NOT_RESOLVED`: persisted hints only cover
    `nghìn/triệu/tỷ/%`, so raw-VND statements have no scale evidence. FPT 2022
    links the 2023 report's comparative column (the 2022 report's label is
    OCR-merged), which TASK-080 grounding will reject on report year.
  - Follow-up on 2026-10-10 (ADR-062, not a re-measured baseline): the live
    path derives scale/unit hints at query time from the raw report, adding
    currency units (`Đơn vị tính: VND`, `2023 VND`) and a unit-declaration
    link. Replaying the recorded real Batch3 candidates through the real
    Evidence node, all three cases now resolve RAW/VND on the gold values and
    reach the Programmer (`scratch_logs/task100-adr062-evidence-replay.json`).
    Programmer, sandbox and verification were not run live in this follow-up.
  - Re-index on 2026-10-10 (ADR-062): offline builds now use the same grammar.
    Under `m2a-normalization-v2`/`m2a-representation-v2`, the corpus
    `7c14b549…`, both sidecars and BM25 `91071533…` were rebuilt on CPU into
    the ignored `reindex-adr062/`. Persisted v2 hints equal query-time hints,
    and the pilot reaches the Programmer on them
    (`scratch_logs/task100-adr062-v2-evidence-replay.json`).
  - [x] Vector index for v2 corpus `7c14b549…` (ADR-063): reuse v1 vectors
    for the 99.6% unchanged chunks and embed only 7,300 changed table chunks
    (about 25 h on this host's CPU, or a GPU host with `--device cuda`). Then
    apply `reindex-adr062/version-bump.patch`, switch the live roots and rerun
    ADR-057 validation and TASK-100.
  - Completed on RTX 3090, 2026-10-13: vector `f9cee82b…` is COMMITTED,
    1,743,967 vectors / 18 shards, integrity PASS. The version bump is applied;
    fresh validation and live runs use all five matching v2 roots. ADR-057
    artifact/live checks PASS (`scratch_logs/adr063-production-validation.json`).
    TASK-100 is re-measured at **1/3 (33.3%)**, `MEASURED`, `established=true`,
    no blockers (`scratch_logs/task100-adr063-baseline.json`). AAA RIENG passes;
    AAA HOP_NHAT and FPT HOP_NHAT stop at Programmer (AVERAGE arity and malformed
    JSON respectively). Missing gold was recreated by a user-authorized agent
    raw-source audit, not human review; the three curated cases do not establish
    general retrieval quality or a release threshold. The full suite has two
    failures from optional real v1 smoke artifacts; no checks were weakened.

- [x] **TASK-101 — Activate CHEAP routing for eligible simple cases**
  - `APIProgrammer` consumes `activated_model_tier`, supports separate serving
    clients, and preserves required/explicit STRONG escalation and verify profile.
  - Paired synthetic live measurements record quality, latency, actual tokens
    and failure modes; a separate three-case real-report LOOKUP pilot also passes
    3/3 in each mode. This deployment compares thinking presets on one endpoint;
    it does not establish production quality or a two-model comparison. See M11.

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

- [x] **TASK-109 — Retrieval model serving** — **COMPLETE**; retrieval portion of
  `GPU_PRODUCTION_VALIDATION_PENDING` **CLEARED** on 1× RTX 5090 (see run evidence
  below)
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
  - **Production run evidence (1× RTX 5090, retrieval only, no Qwen):**
    - Canonical M2 corpus artifact
      `4f39603a89e213129de6c57e1234456e88086832c8b61bd9821770d76a6dc78e`
      (`source_inventory_sha256 9ea9e073…f61c9`, `chunk_count 1743311`).
    - Committed vector family root
      `artifacts/m2-vector-index-v1/full-corpus-rtx5090`; `CURRENT` →
      `build_id 6e196ee45ef08dd1c85a2da647abdd36c9d4613b4de59cb2a381c9fd852a8069`,
      `artifact_status COMMITTED`, `vector_count 1743311`, 18 FAISS shards
      (Σ shard `vector_count` = 1743311), SQLite `integrity_check ok`,
      `embedding_fingerprint
      ebf2adc2a61d75a65db3829163a003e729095310360b9b162b570b34579585b3`.
      Verified with `embedding_artifact_builder.validate_published_artifact`.
    - Committed BM25 artifact
      `artifacts/m3-bm25-index-v1/full-corpus-rtx5090`
      (`build_id 35399789d6b5cb5cfb08168922dc5cce72b2003bf19827e83736a115493b4d51`,
      `candidate_count 1743311`, `corpus_fingerprint 9ea9e073…f61c9`,
      `bm25.sqlite sha256 9fcb552a2dbbebfdc98f2dcaf1d034809a86a5f1cabb6357f674a9de21a6523e`),
      built with `src.retrieval.bm25.build_bm25_index`; `validate_bm25_artifact_compatibility`
      + `BM25Index` load = PASS.
    - LIVE BGE-M3 (`5617a9f61b028005a4858fdac845db406aefb181`) + BGE-reranker-v2-m3
      (`953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`) served on CUDA over an
      OpenAI-compatible HTTP endpoint
      (`deploy/serving/serve_retrieval_models.py`, which wraps the pinned
      `load_bge_m3_encoder` / `load_bge_reranker`); probes via the production
      `HttpTransport`: embedding parity min cosine `0.9999999999990075` (dim 1024),
      reranker order parity `true` (max score abs diff `1.9e-06`).
    - Deterministic retrieval-execution smoke over
      `deploy/retrieval/smoke-queries.jsonl` through the real `HybridRetriever`
      (BM25 + FAISS + pinned BGE-M3 query encoder + pinned BGE-reranker):
      `ran true`, `byte_identical true`, `crashes 0`, `timeouts 0`,
      `sample_size 4`.
    - `python -m src.evaluation.run_retrieval_production_validation` →
      `overall_status PASS`, `artifact_status PASS`, `live_serving_status PASS`,
      `clears_gpu_pending true` (exit 0). All 10 `artifact_serving_checks` PASS.
      `RETRIEVAL_QUALITY` stays `BLOCKED_GOLD_DATA` (no gold retrieval-evidence
      annotations) and does not gate the flag.

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
      (`OrchestrationState._validate_pass` only constrains the `PASS` direction), or a
      `CLARIFICATION` terminal outcome with a non-empty attempt history.
    - `STALE_WORKER_STATE` — `FailureAttribution.attempt_index` does not equal
      the current `attempts[-1].attempt_index` (or is set with no attempts),
      i.e. attribution never advanced with the run.
    - `MESSAGE_SCHEMA_CORRUPTION` — `scan_coordination_payload` runs a raw
      checkpoint dict through the existing `OrchestrationState.from_dict` fail-closed edge
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
`HybridRetriever.retrieve_multi_table`).

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
graph calls `supervise` and `verify` as deterministic functions and takes
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

At the M10 Batch 7 boundary, TASK-100 and TASK-101 had CPU modules implemented
and unit-tested but remained unchecked (TASK-100's later pilot measurement is
recorded above). Classified by what that implementation could actually measure;
no numeric threshold is invented for either:

- **TASK-100** — `src/evaluation/baseline.py` + `run_baseline_eval` measure
  STRONG-tier + STRICT-verification correctness over the deterministic
  retrieved-evidence fixtures (3 cases, all PASS, `acceptance_thresholds = None`).
  In v1 "STRONG tier" is a deterministic routing label, not a model, so this
  task is **retrieval-gated, not Qwen-gated**: closing it needs the production
  vector artifact built + retrieval production validation (ADR-057) PASS, and
  real per-question gold answers to score real-corpus correctness against
  (`BLOCKED_GOLD_DATA`, like retrieval quality). It is not a live-LLM task in the
  current architecture. **M10 Batch 9** built the gold schema + validator +
  classifier (`m10-gold-retrieval-v1`, see that batch note) so the moment a
  ViFinQA question/answer source is available the pilot can be produced and
  scored; the source itself is still not on this instance, so `BLOCKED_GOLD_DATA`
  stands and the box stays unchecked.
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
- `GPU_PRODUCTION_VALIDATION_PENDING` — **retrieval portion CLEARED** (M10 Batch 8
  run, 1× RTX 5090): `run_retrieval_production_validation` returned
  `overall_status: PASS` / `clears_gpu_pending: true` on the real committed
  corpus/vector/BM25 artifacts with both LIVE probes and the deterministic
  retrieval-execution smoke passing. No LLM was involved. Evidence is recorded in
  the TASK-109 entry above and in ADR-057. `RETRIEVAL_QUALITY` stays
  independently `BLOCKED_GOLD_DATA`.

M10 Batch 9 builds the gold evaluation dataset infrastructure to unblock
**TASK-100** and **RETRIEVAL_QUALITY**. It adds no pipeline behavior, changes no
retrieval architecture, runs no Qwen, and invents no annotation.

Source audit (2026-08-31, canonical corpus artifact
`4f39603a89e213129de6c57e1234456e88086832c8b61bd9821770d76a6dc78e`):

- Present: 1973 reports, 146 246 tables, 1 591 389 paragraphs, 1 743 311
  `EmbeddingChunk`s, 6 212 883 source cells, 100 distinct tickers — each with
  full provenance (`report_id` / `table_id` / `paragraph_id` / `chunk_id` /
  `source_cell_ids` + ticker / year / `statement_scope`). Cross-checked against
  the corpus manifest totals (exact match).
- Absent: the ViFinQA question/answer pairs. `~/Documents/data/ViFinQA` is the
  offline-indexing raw root and is **not mounted on this instance**; the only
  question-like inputs available are the 4
  `deploy/retrieval/smoke-queries.jsonl` rows, which are forbidden as gold. So
  there are **0 questions to annotate** and no gold answers.

Gold schema `m10-gold-retrieval-v1` (`src/evaluation/gold_retrieval.py`
`GoldRetrievalCase`, JSONL; also `gold/retrieval/SCHEMA.md`): `question_id`,
`question`, `company{ticker,name}`, `period`, `period_kind`, `statement_scope`,
`metric`, `operation`, `gold_report_id`, `gold_table_id`, `gold_paragraph_id`,
`gold_chunk_ids[]`, `gold_source_cell_ids[]`,
`gold_answer{value,unit,scale}`, `provenance{source,method,notes}`,
`annotation_status` ∈ {`AUTO_DERIVABLE`, `NEEDS_MANUAL_ANNOTATION`,
`UNSUPPORTED`, `VERIFIED`}. `provenance.method == BOOTSTRAP_UNVERIFIED` can never
back a gold-bearing status; `gold_answer` is only scored when `VERIFIED`.

Tooling (all CPU-only, deterministic, no model):

- `src/evaluation/corpus_reference_index.py` — streams the committed corpus
  shards once (~39 s, cached under `artifacts/gold/`) into existence sets +
  minimal report metadata for referential-integrity checks.
- `src/evaluation/gold_retrieval.py` — `validate_gold_cases` (schema,
  duplicate `question_id`, duplicate evidence signature, missing-evidence,
  bootstrap-not-gold, and every referenced report/table/paragraph/chunk/cell
  must exist in the canonical corpus, table↔report ownership,
  ticker/scope/period consistency); `classify_question`
  (AUTO_DERIVABLE / NEEDS_MANUAL_ANNOTATION / UNSUPPORTED);
  `compute_gold_retrieval_metrics` (Recall@k + MRR over gold-bearing cases only,
  reusing `src/evaluation/retrieval.py` `recall_at_k` / `mrr`);
  `score_answer_correctness` (VERIFIED `gold_answer` only).
- `src/evaluation/run_gold_retrieval_validation.py` — CLI: build/load the corpus
  index, classify a questions source, validate a gold file (exit non-zero on any
  ERROR issue).
- `gold/retrieval/pilot.jsonl` — **empty** (0 cases); `README.md` + `SCHEMA.md`
  document the populate/validate/score flow.

Counts:

- TOTAL_QUESTIONS: 0 (no source)
- AUTO_DERIVABLE: 0 · NEEDS_MANUAL_ANNOTATION: 0 · UNSUPPORTED: 0
- PILOT_GOLD_SIZE: 0 · coverage: 0 / 1973 reports
- RECALL_AT_K / MRR: not computed (no gold-bearing case)
- ANSWER_CORRECTNESS: not computed (no VERIFIED `gold_answer`)

Tests: `tests/evaluation/test_gold_retrieval.py` (18 cases) — schema round-trip,
every validation rule, classification buckets, Recall@k/MRR bridge, answer
scoring, and a synthetic-corpus index build. Focused suite
`tests/evaluation/ tests/retrieval/` = 272 passed, 2 skipped.

Remaining blocker: **`RETRIEVAL_QUALITY` and TASK-100 stay `BLOCKED_GOLD_DATA`**
— the ViFinQA question/answer source must be made available on the production
host, then run `run_gold_retrieval_validation --questions-source …` to bucket it,
hand-annotate a 20–50 case `VERIFIED` pilot, validate it, and score Recall@k /
MRR (and answer correctness where `gold_answer` is trustworthy) through the real
`HybridRetriever`. TASK-100 is not closed.

M10 Batch 10 audits and confirms the public question source. HF dataset
`HuuDong03uet/ViFinQA` was fetched to a temp dir (`/workspace/vifinqa_candidate`,
not git) and its `financial_statements/` compared against the canonical corpus
artifact `4f39603a…dc78e`: **1973/1973** report paths exact-match canonical
`source_ref`; 100/100 tickers; years 2015–2025 with identical per-year counts;
byte-size 25/25 on a random sample; and **sha256 8/8 BYTE-IDENTICAL** to
canonical `report.content_sha256` (random reports across `HOP_NHAT`+`RIENG`,
2018–2025). The 146 246 vs companion-repo 143 815 table count is a downstream
M2-normalisation delta, not a source mismatch (raw `.txt` are byte-identical).
Question references resolve inside the corpus envelope (811/811 `năm YYYY` in
2015–2025; every real `(TICKER)` mention is a canonical ticker).
**SOURCE_DECISION: CANONICAL_SOURCE_CONFIRMED.**

The release ships `questions/questions.jsonl` = **1012 rows, `{id:int, question:str}`
only** — no answers, no ticker/year fields, no gold evidence (matches this repo's
own docs on the "public ViFinQA questions.jsonl"). Copied to `data/vifinqa/`
(git-ignored) with `PROVENANCE.md`. `run_gold_retrieval_validation
--questions-source data/vifinqa/questions.jsonl` →
**TOTAL_QUESTIONS 1012 · AUTO_DERIVABLE 0 · NEEDS_MANUAL 0 · UNSUPPORTED 1012**
— every row buckets `UNSUPPORTED` because `classify_question` needs a structured
`company.ticker` and the raw questions carry free-text only. In substance these
are all `NEEDS_MANUAL_ANNOTATION` (company/year/evidence must be bound by a human
or a VN-text→ticker resolver via `code_stock.csv`); no such resolver was built
here. `RETRIEVAL_QUALITY` / TASK-100 stay `BLOCKED_GOLD_DATA` — now blocked on
manual gold annotation, not on source availability. No gold annotations were
created.

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

- [x] **TASK-120 — Define memory-entry schema**
  - source question/context signature,
  - successful strategy findings,
  - failure-derived caution rules,
  - provenance.

- [x] **TASK-121 — Build offline memory distillation from train/dev trajectories**

- [x] **TASK-122 — Build memory embedding/index**

- [x] **TASK-123 — Similarity-gated memory retrieval**
  - threshold,
  - top-k cap,
  - deduplication,
  - base-policy fallback.

- [x] **TASK-124 — Memory injection policy**
  - selected roles only,
  - memory cannot override evidence or validators.

- [x] **TASK-125 — Held-out evaluation isolation**
  - memory read-only,
  - no test/gold writes.

- [x] **TASK-126 — Memory ablation**
  - base,
  - tools only,
  - tools + memory,
  - measure accuracy, retrieval quality, latency, and failure modes.

## M11 / TASK-101 completion evidence — synthetic mechanism validation

Scope was explicitly authorized by the user: use a separate synthetic TRAIN/DEV
dataset and state that production quality is unmeasured. No existing gold/test
answers or TASK-100 trajectories were used to distill memory. Memory remains
disabled unless a calibrated `MemoryIndex` is supplied to `build_live_graph`.
No dependency, additional generative call, or retry loop was added.

| Task | Implementation and proof |
|---|---|
| TASK-101 | `src/orchestration/api_stages.py`, `live.py`: actual client/settings selection consumes existing activation; tests cover eligible CHEAP, forced STRONG, escalation, required STRONG, unchanged Plan/profile; paired live report measures both modes |
| TASK-120 | `src/memory/schemas.py`: strict versioned STRATEGY/GUARD contracts, question/context signature, source/dataset/split/digest provenance; arbitrary text, hidden answer fields and incompatible specialized rules rejected |
| TASK-121 | `src/memory/distillation.py`, `run_memory_build.py`: validated terminal TRAIN/DEV trajectories only; separate success/guard rules; held-out IDs/question hashes rejected; separate synthetic data published offline |
| TASK-122 | `src/memory/index.py`: pinned BGE-M3/token guards, normalized 1024-dimensional vectors, immutable versioned artifact with embedding references/fingerprint/hash; real encoder build succeeds |
| TASK-123 | DEV-calibrated cosine threshold; exact type/formula/metric/period-kind compatibility; content deduplication, hard cap three, irrelevant/unavailable base fallback; two-period strategy retrieves for three-period AVERAGE |
| TASK-124 | Only Programmer receives fixed allowlisted advice within its existing call; payload/bindings unchanged; invalid placeholders still fail the same validator; embedding failure adds no generative call |
| TASK-125 | TRAIN/DEV split and held-out overlap checks; inference exposes no writer; publication requires a new safe path, rejects gold/test and symlink aliases; evaluator cannot overwrite index; held-out index SHA unchanged |
| TASK-126 | `src/evaluation/memory_experiment.py`, `run_memory_eval.py`: BASE/TOOLS_ONLY/TOOLS_MEMORY share grounded fixture evidence, real validator, OS sandbox and verifier; report includes accuracy, evidence coverage, latency percentiles, measured tokens, retries and failure codes |

The synthetic-v2 dataset is `artifacts/m11-synthetic-v2/`: three TRAIN
trajectories, four DEV examples, and four independently held-out questions.
Its four-entry index has artifact ID
`5734d2a2660e0faf7d04a7099d1a740c8fa40cb43951ddb128ea811d73482a07`.
DEV min-positive cosine is **0.9892331299507086**, max-negative is
**0.9818967904639953**, and threshold is **0.985564960207352**. DEV and held-out
AVERAGE use three periods, while the training strategy uses two. This verifies
context generalization rather than an identical-context lookup alone.

Latest live measurements: `scratch_logs/m11-memory-live-trace-eval.json`.

| Arm | Answers correct | Mean query latency | Prompt/completion tokens | Attempt failure codes |
|---|---|---|---|---|
| BASE | 4/4 | 11,047.78 ms | 5,267 / 7,561 | SERVING_RESPONSE_TRUNCATED ×1 |
| TOOLS_ONLY | 4/4 | 3,957.10 ms | 4,302 / 2,546 | None |
| TOOLS_MEMORY | 4/4 | 9,932.32 ms | 5,902 / 7,040 | SERVING_RESPONSE_TRUNCATED ×1 |

Memory answer-accuracy delta is **0**, including the three advised cases.
All final programs match the independently generated oracle after normalizing
graph IDs; program-trace match rate is 1.0 in each arm, scored separately from
answer accuracy. The preceding v2 run is preserved in
`scratch_logs/m11-memory-live-v2-eval.json`: BASE 3/4, tools 4/4, memory 4/4.
The comparison control received no advice but varied in retry behavior in that
run. The recorded memory IDs are post-run retrieval diagnostics; injection
behavior is separately verified by payload tests. These single, small, ordered
runs do not establish a causal speed/quality benefit. The current measurement
does not justify enabling memory by default.

TASK-101 compares two eligible cases: routed CHEAP **2/2**, forced STRONG
**1/2**; observed difference +0.5. Mean query latency is **1,832.95 ms** versus
**17,137.16 ms**; completion tokens **458** versus **5,959**. Retries/escalation
are included. Both use `deepseek-v4-flash` with disabled versus enabled/high
thinking. This is a measured inference-setting comparison, not proof that CHEAP
is generally more accurate or cheaper in money. Dollar costs remain null.

A separate real-report TASK-101 pilot evaluates three source-audited LOOKUP
questions: AAA consolidated, AAA separate, and FPT consolidated, all for 2023.
The original TASK-100 set contains only AVERAGE questions and therefore has no
CHEAP-eligible cases; its gold and artifacts remain unchanged. New lookup inputs
are stored separately in `scratch_logs/task101-lookup-gold.jsonl`, with raw-source
hash rechecks and source pointers in `task101-lookup-source-audit.json`. These
agent-audited questions are not an official or human-reviewed benchmark and never
enter memory distillation or calibration.

The live checkpoint score is
`scratch_logs/task101-real-lookup-routing-scored.json`: CHEAP **3/3** and STRONG
**3/3**, quality delta **0**, final program-trace match rate **1.0** in each arm,
and no attempt failures. Plans remain CHEAP/LIGHT in both arms; actual serving
observations confirm disabled versus enabled/high thinking for Programmer.
Prompt/completion tokens, including NLU, are **16,508/600** versus **16,583/1,731**.
Observed mean end-to-end latency is **54,324.08 ms** versus **33,274.99 ms**.
The sequential CHEAP-first run includes retrieval/storage warmup, so neither
end-to-end latency nor this three-case sample establishes a causal speed benefit
or broad production quality. The score parses gold scale through ExecutionDatum;
the initial pilot script passed a string scale and was corrected by re-scoring
the preserved checkpoints without repeating model calls. Raw run:
`scratch_logs/task101-real-lookup-routing.json`. Capture and scoring scripts are
`scratch_logs/run_task101_lookup_pilot.py` and `score_task101_lookup_pilot.py`.

All arms have synthetic required-evidence coverage 1.0 because the evaluation
supplies fixture evidence. Production Recall@k/MRR and production memory quality
are explicitly unmeasured. Actual report latency includes the Programmer's
memory embedding when advice is requested; encoder tokens/cost are not counted.
Index file SHA-256 remains
`259e3dc4ddfbd80b6f8e533fcc6f59fa4641aa452ccb186d452336f44bfc1794`
before and after held-out evaluation.

Validation: focused tests and final repository regression are recorded in
`scratch_logs/m11-focused-final-tests.txt` and
`scratch_logs/m11-final-repository-pytest.txt`: **1,111 passed, 350 subtests
passed, 2 skipped, 2 existing failures**. The focused run passes **90 tests and
11 subtests**. All **18 memory tests** pass, including
program-trace comparison and protected synthetic dataset publication.
Supervisor fixtures **14/14**,
oracle reasoning **7/7**, E2E fixtures **12/12**, baseline fixtures **3/3** pass.
Two existing optional real v1 retrieval artifacts still fail v2 compatibility;
no test or validation was weakened. Completion audit and artifact/log hashes:
`scratch_logs/m11-completion-audit.json`. Replay commands are in the README.

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


### Programmer failure recovery (ADR-064)

Implemented registry-derived formula input arity/order in the API prompt,
strict truncation detection, typed Programmer rejection codes, and bounded
Programmer-only retries using the existing attempt mechanism. Plan, evidence,
validator, and sandbox contracts remain unchanged. No JSON repair is allowed.

Validation: 194 Programmer/serving/orchestration tests pass. Supervisor fixtures
14/14, oracle reasoning 7/7, E2E fixtures 12/12, and baseline fixtures 3/3 pass.
The repository suite (`pytest tests -q`) reports 1081 passed, 2 skipped, and the
two existing real 500-vector v1 fixture incompatibilities. Default `pytest -q`
also collects the historical code snapshot under artifacts/reindex-adr062/code,
which retains the former exception assertion and causes duplicate-module test
collisions. These artifact fixtures and snapshots were not changed.

TASK-100 rerun on the same frozen three-case gold and matching v2 corpus/vector/
BM25/provenance/table-class roots is MEASURED, established=true, with no blockers:
**2/3 correct (66.7%), previously 1/3 (33.3%)**. Both AAA cases pass on their first
attempt. FPT executes valid Programs on all three attempts but STRICT verification
rejects each with REPORT_YEAR_MISMATCH; its two retrieval retries originate from
verification, not from Programmer. This grounding failure remains outside this
Programmer/retry change.

Recorded stage invocations (before and after): retrieval 5/5, Programmer 5/5,
sandbox 3/5; NLU 3/3. These are port invocation counts inferred from complete
checkpoints, not internal embedding/reranker HTTP request counts. All eight new
generative responses finish with stop. Programmer completion token counts are
1387, 762, 842, 1094, and 1045; no truncation was observed, so max_tokens stays
4096. The live run exercises the prompt change; malformed/truncated response
recovery is proved by deterministic tests, not claimed as an observed live event.

Evidence: scratch_logs/task100-programmer-retry-live-e2e.json,
task100-programmer-retry-live.stderr.txt, task100-programmer-retry-baseline.json,
and task100-programmer-retry-comparison.json. Gold remains agent-audited, not
human-reviewed; the three-case pilot does not establish production quality.
Thresholds remain unset. Existing ADR-063 LIVE retrieval validation is reused
for the identical immutable artifact IDs, not claimed as a new parity run.


### FPT grounding repair (ADR-065)

The raw 2022 consolidated LNST row includes the OCR-merged terminal heading
`Phân bổ cho:`. Exact matching now removes that heading only when it terminates
the label. Schema linking prefers already-retrieved exact matches from the
requested report year; same-year duplicates remain ambiguous. With no same-year
match, existing verification-driven retrieval retry remains intact. Mandatory
report-year verification is unchanged.

Regression proof: the new normalization and linking cases failed before the
patch. The focused suite passes 388 tests plus 129 subtests; the complete current
repository suite (`pytest tests -q`) passes 1089 tests plus 350 subtests, with
2 skipped and the same 2 pre-existing v1 real-vector artifact incompatibilities.
Supervisor fixtures 14/14, oracle reasoning 7/7, E2E fixtures 12/12, and baseline
fixtures 3/3 pass. The same-year duplicate case is included in the complete suite.

Recorded-real-evidence replay selects the original 2022 report cell and original
2023 report cell, both matching the frozen gold source-cell IDs. Original raw
labels and source paths remain intact. Evidence:
`scratch_logs/fpt-grounding-evidence-replay.json`. This replay changes only the
shared metric matching and schema linking; it is not a new full inference run.


Fresh LIVE TASK-100 rerun on the same frozen gold and v2 artifact IDs:
**3/3 correct (100%), previously 2/3 (66.7%)**. All three cases pass on their
first attempt. FPT now cites the exact gold 2022 source cell from the 2022 report
and the exact gold 2023 cell from the 2023 report; sandbox execution and STRICT
verification pass, returning `7139696597042.5 RAW/VND`. Retrieval and Programmer
port invocations fall from 5 each to 3 each; FPT uses one attempt instead of
three. No verification check was relaxed. All six generative responses end with
stop; completion token budget remains 4096.

Evidence: scratch_logs/task100-fpt-grounding-live-e2e.json,
task100-fpt-grounding-live.stderr.txt, task100-fpt-grounding-baseline.json,
task100-fpt-grounding-comparison.json, and task100-fpt-grounding-provenance.json.
The baseline is MEASURED, established=true, with zero blockers and no numeric
acceptance threshold. Existing ADR-063 retrieval validation binds the same
immutable artifacts; no new retrieval-parity run is claimed. The three-case,
agent-audited gold remains a bounded pilot, not a production-quality benchmark.

### Text-to-Pandas pipeline, repair, verification and live benchmark (ADR-069)

- [x] Connect catalog/discovery/generator/validator/isolated executor/verifier
  through `PandasPipeline.ask` and the opt-in `src.text_to_pandas.run` CLI.
- [x] Bound code repair to two retries and preserve request, every completed
  attempt and terminal response in a unique, append-only run directory.
- [x] Verify exact source facts, provenance, source path/report year, raw numeric
  parsing and scale, result unit/type, Decimal reference arithmetic and a
  perturbed-source program execution. Reject unverified extra columns and
  unsupported compound semantics. Monetary tolerance is four float ULPs with
  an absolute 1e-12 floor. Security/resource/infrastructure failures do not retry.
- [x] Add Text-to-Pandas tests, execute real DeepSeek demos and complete the
  wide real-model benchmark before any default-routing change.

Validation on this Linux instance: Python **3.12.14**, Pandas **2.3.3**, NumPy
**2.5.3**. `pytest tests -q`: **1222 passed, 2 skipped, 373 subtests passed**.
Focused Text-to-Pandas suite: **55 passed**. The existing Supervisor fixtures
remain **14/14**, oracle reasoning **7/7**, E2E fixtures **12/12**, and baseline
fixtures **3/3**. `requirements-test.txt` now declares the already installed
Pandas version; its existing NumPy 2.4.6 pin is unchanged. This run does not
claim a fresh installation of that complete pinned environment.

The live benchmark completes all **140 frozen LNST gold cases + 17 negative
controls**: **139/140 correct (99.2857%)**, **139/139 answered precision (100%)**,
**0 wrong answers**, **0 negative false answers**, **0 runner errors**. One VJC
growth case safely returns NO_DATA because the 2021 LNST row is absent from the
catalog. Accuracy by operation is LOOKUP 60/60, GROWTH_RATE 39/40, AVERAGE 40/40.
Sequential per-question latency: p50 **2.803 s**, p95 **3.302 s**, max **3.719 s**.
Mean estimated LLM cost at the frozen ADR-066 peak rates: **$0.00207192/query**;
p95 **$0.002434572**. Mean LLM calls across all 157 questions: **0.8917197**.
Every ADR-066 threshold passes; verdict is **ACCEPTED**, run_complete=true.

Gold source lists contain alternative cells for statement carry-over rows.
The scorer now requires one cell from each independently audited period/source
group, rather than all alternative IDs. All 12 initially flagged cases use the
same audited reports and valid listed cells; gold files and thresholds were
not changed. Group-coverage and missing-telemetry regressions cover the scorer.
The legacy six UNSUPPORTED_METRIC negatives remain intact for M9; the separate
Pandas negative set replaces them with market/subannual controls because the
larger catalog now supports their metrics.

Real lookup and ROE demos execute generated Pandas in the worker. The initial
lookup demo observes a real EVIDENCE_SHAPE rejection followed by successful
code repair; both attempts are retained. The final-current-code ROE demo returns
**5.0958778273483025 PERCENT**, with source and program checks passing.
Truncated-response recovery is proven by a regression test, not claimed as a
live event. A later tolerance/schema tightening and truncation-code correction
do not alter any recorded successful answer: all **157** recorded live cases
are re-executed under the current code and match status/reason/result/unit/source
output. This replay makes no new LLM, cost or latency claim. The full live run's
original source fingerprint is retained separately from the current replay's.

Evidence:

- `scratch_logs/t2p-wide-live.json`, `t2p-wide-benchmark.json`, `t2p-wide-history/`;
- `scratch_logs/t2p-final-live-demo.json`, `t2p-live-demo-lookup.json`, `t2p-history/`;
- `scratch_logs/t2p-current-code-replay.json`, `t2p-replay-history/`;
- `scratch_logs/t2p-final-verified-repository-pytest.txt`,
  `t2p-verified-focused-pytest.txt`, `t2p-component-prepatch-tests.txt`,
  `t2p-verifier-prepatch.txt`;
- `scratch_logs/t2p-supervisor.json`, `t2p-oracle.json`, `t2p-e2e.json`,
  `t2p-baseline.json`, `t2p-completion-audit.json`.

Default M9 routing remains unchanged. The accepted benchmark covers LNST annual
questions, not broad production quality for every catalog metric/ratio. Gold is
agent-built and cross-year checked, not human-reviewed. The pinned production
NsJail config is present, but no live production NsJail deployment or load test
is claimed by this work.

### Broad gold, sandbox load and fresh pinned environments (ADR-070)

- [x] Finish live CLI `--concurrency`, preserve unique request histories and
  locked atomic report writes, record wall time, reject mixed-concurrency
  resume and detect source changes after the final in-flight request.
- [x] Run the four sandbox matrices sequentially at 1,8,16,32,64: local and
  namespace-free NsJail, each as root and `sbxload`. Every level at or below 16
  passes; no wrong benign result or escaped/misclassified abuse is observed.
- [x] Run 158 broad questions at concurrency 8, score unchanged gold and compare
  every question/history with the sequential measurement. Zero cross-request
  violations; 156/158 status agreement, 157/158 exact result agreement and
  106/106 jointly answered result agreement. Wall time 64.277 s; estimated
  sequential span 480.264 s, estimated speedup 7.472x.
- [x] Recreate empty-cache pinned environments at final source: Python 3.12.3
  and 3.11.16 each pass 1,243 tests, 373 subtests, with two skips. All five
  fixture outputs are byte-identical between versions.
- [x] Document the NsJail 3.4 profile/seccomp/UID fixes and the three current
  fingerprints; retain full production namespace/cgroup validation as a
  deployment gate rather than claiming it from reduced profiles.
- [x] Investigate VSF 2022 LNST against hash-verified raw tables and extraction
  replay. Page-3 code-less management summary and page-14 primary line 60 have
  the same value; first-occurrence dedup keeps the summary and clears the
  primary row's metric. Verifier arithmetic passes against that catalog choice.
  Evidence: `scratch_logs/t2p-vsf-2022-grounding-investigation.json`.
- [x] Resolve the root NsJail-wrapper gate at 64 (ADR-071): the harness now
  waits up to 5 s for threads/children/descriptors/temp dirs to return to
  baseline and reports both end-of-level and settled values. A full root
  wrapped rerun passes with zero residual resources at every level end; the
  thread observation did not reproduce. Wrapped M7 at 64 still has 7/128
  WALL_CLOCK_TIMEOUT (p95 1,737 ms), so the per-host M7 ceiling under NsJail
  stays 32 concurrent workers. Evidence: `sandbox-load-nsjail-root-settle.json`.
- [x] Pass the broad quality gates on new held-out gold after fixes (ADR-071):
  `gold/broad-h2` (seed 20261015, no unit shared with `gold/broad`) scores
  **137/140 ACCEPTED** sequentially with catalog v2, zero wrong answers and zero
  negative false answers. Before the fixes, `gold/broad` scored 105/140 and
  106/140 (NOT_ACCEPTED); after them it scores 133/140 at concurrency 8, but that
  set was used to find the gaps and is reference only.
- [ ] Validate the full production NsJail profiles on a host with namespaces,
  writable cgroup v2 and the required Python/Pandas/NumPy runtime under `/usr`.
- [x] Repair equal-value catalog provenance selection (ADR-071): agreeing
  repeats keep the first coded row; conflicts and code-less bank layouts are
  unchanged. `artifacts/t2p-catalog-v2` moves exactly five VSF LNST facts to
  their line-60 cells with zero value, row or coverage changes.

Focused Text-to-Pandas validation: 62 tests pass. Fresh pinned fixtures remain
Supervisor 14/14, reasoning 7/7, E2E 12/12 and baseline 3/3; retrieval 8/12
contains four deliberate failure-attribution controls. The preceding ADR-069
LNST benchmark remains historical evidence, not a broad production claim.

Evidence: `scratch_logs/sandbox-load-matrix-summary.json`,
`scratch_logs/t2p-broad-concurrency-comparison.json`,
`scratch_logs/t2p-broad-benchmark-c8.json`, `scratch_logs/fresh-pinned-env/`.

### Production gates, catalog v3, platforms, providers and service (ADR-072)

- [x] Root-cause and fix the remaining catalog gaps (SSI, HHV, VIF, PNJ, plus MSN,
  DTK and bank statements found on the development split); rebuild as
  `artifacts/t2p-catalog-v4` with zero changed values against v2 and every
  earlier gold component still matching.
- [x] Build `gold/production-v1`: four splits of 127 positives + 20 must-refuse
  controls (dev, holdout, holdout2, holdout3 with LLM-paraphrased wording); no split
  shares a report with another or with earlier gold; each holdout frozen before use.
- [x] Score holdout once (104/127) and holdout2 once (104/127): all misses safe
  abstentions on unseen wording, zero wrong answers. Fix from each scored holdout
  (now development data): 127/127 and 126/126.
- [x] Gold QA (`src/evaluation/gold_qa.py`): one defect in 508 cases, excluded alike.
- [x] Add the production gate (`src/evaluation/production_gate.py`): accuracy and
  per-group accuracy, code execution success, provenance, refusals, security,
  stability, API reliability, capacity and human review; missing evidence blocks.
- [x] Configure LLM providers in `deploy/llm/providers.toml` (OpenAI-compatible and
  Anthropic Messages protocols, eleven entries) and select one with `--provider`.
- [x] One sandbox configuration for Linux, macOS and Windows (`deploy/sandbox/sandbox.toml`):
  host-OS local boundaries, `container-v1` on any OS, admission caps (M7 ≤ 32
  under NsJail) and a CI matrix.
- [x] Question API with SLOs, Prometheus metrics, alert rules, canary releases and
  automatic/manual rollback, exercised by a reliability drill.
- [x] Security: 27 canary probes, 0 escapes (local and reduced NsJail). Capacity:
  64 offered M7 under the 32 cap 128/128 (uncapped 2/128 timeouts). Stress 1–64 and a
  30-minute soak (26,611 requests) pass with flat drained-checkpoint resources.
  API drill 20/20. `pytest tests`: 1369 passed, 2 skipped.
- [x] Score holdout3 once: 122/127 (96.1%), every group ≥90%, execution 99.2%,
  provenance 100%, zero wrong answers, 20/20 refusals. Gate 8/9 PASS.
- [ ] Human review of all 254 cases (review page and CLI ready; needs people).
- [ ] Full NsJail profile (namespaces, cgroup v2) and the macOS/Windows CI matrix
  on capable hosts; live measurement of providers other than DeepSeek.

Evidence: `scratch_logs/goal-production/PROGRESS.md` and the files it lists;
gate reports `production-gate-holdout1.json`, `production-gate-holdout2.json`,
`production-gate-v3.json` (holdout3, final).

### Browser UI over the question API (ADR-073)

- [x] Serve a dependency-free chat UI (`src/service/web/`) at `/` with a fixed static
  map and a strict CSP: sidebar (sessions, catalog coverage, token), chat stream
  (all five statuses with their follow-up actions), detail canvas (result table with
  search, unit switch and CSV export; sources grouped by report; code).
- [x] Add `GET /v1/catalog`, `statement` on public sources and the opt-in `program`
  (`AIFIN_EXPOSE_PROGRAM=1`); tests in `tests/service/test_service.py`.
- [x] Check in a browser against catalog v4 with a replay release: desktop, dark mode
  and 390 px width, no console errors, no horizontal overflow.


### Official statement set as PDF (ADR-075)

- [x] Tag the pre-feature version `v1.0.0` (commit `07c11aa`, pushed) as the
  rollback point; develop on `feat/statements-pdf`.
- [x] Share the catalog reader (`statement_tables`, `lines`, `prior_column`); a
  catalog rebuild reproduces t2p-catalog-v4's `catalog_id`.
- [x] Add code-keyed TT200/TT202 identities (`src/formulas/identities.py`) with
  tests, including alignment with the catalog guard.
- [x] Build `artifacts/statements-v1` (`src/statements/builder.py`): 1,468 TT200
  sets, catalog parity 158,845 facts with 0 missing and 0 mismatched.
- [x] Render the PDF (`src/statements/pdf.py`, fpdf2 2.8.9, Noto Sans OFL) and
  serve `GET /v1/statements` from a release that pins the artifact; add the browser
  download form. Tests are in `tests/statements/`.
- [ ] Bank (TT49) and securities (CTCK) templates.
- [ ] Classify untitled statement pages (e.g. VNM 2024 separate B03 page 1) in the
  shared reader, together with a later catalog build.

### Flagged statement-set remediation (ADR-076)

- [x] Read source-delimited B02 code-22 financial-expense/interest cells in the
  shared reader; keep the raw cell and reject ambiguous concatenations.
- [x] Rebuild catalog v5 and statement-set v2 without changing or removing any
  previous catalog row or parsed statement figure. Catalog parity: 159,016/159,016.
- [x] Resolve 120 of the 915 flagged corporate sets; 673/1,468 now reconcile.
  Export the 795 remaining cases to `artifacts/statements-v2/review_queue.csv`.
- [ ] Verify the 795 remaining flagged cases against original PDFs/scans. Only OCR
  text is available in this workspace, so figures and lost codes are not inferred.
