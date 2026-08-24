# AGENTS.md

## Purpose

This file defines how Codex and other coding agents must work in this repository.
It is an execution policy for the coding agent, not the product specification and not the system architecture document.

The project is an AI Financial Data Assistant for Vietnamese financial-statement question answering over OCR-derived tables / CSV / DataFrames.

## Required Reading Order

Before implementing any task, read:

1. `AGENTS.md`
2. `docs/PRODUCT.md`
3. `docs/ARCHITECTURE.md`
4. `docs/DECISIONS.md`
5. `docs/TASKS.md`
6. The existing code relevant to the current task

Do not assume the architecture from memory when the repository documents say otherwise.

## Core Engineering Principle

Use LLMs for language understanding, planning, and program generation.
Use deterministic code for grounding, validation, execution control, and numerical computation whenever possible.

The system must preserve a clear separation between:

- understanding the user request,
- planning the work,
- retrieving evidence,
- generating a symbolic/Pandas program,
- executing the program,
- verifying the result.

## Architecture Invariants

The following rules are hard constraints.

### 1. NLU / Understanding is a separate logical layer

The NLU layer answers: **What is the user asking?**

It should normalize at least:

- company / ticker,
- period,
- period kind,
- statement scope,
- financial metric,
- requested operation,
- missing or ambiguous information.

Do not put retrieval planning, numerical computation, or answer generation inside NLU.

### 2. Supervisor / Planner does not calculate financial values

The Supervisor answers: **What must the system do to answer the understood request?**

It may:

- classify the question,
- determine required tables and periods,
- choose model tier,
- choose verification profile,
- decide early abstain / clarification,
- emit a structured `Plan`.

It must not:

- read raw numeric cells to compute an answer,
- directly calculate ratios or growth,
- bypass retrieval,
- directly execute generated code.

### 3. Workers consume structured contracts

Downstream workers should consume the structured output of the previous stage.

Preferred flow:

`Question -> NLU -> QueryUnderstanding -> Supervisor -> Plan -> Retrieval -> Evidence -> Schema Linking -> Numeric Masking -> Programmer -> Sandbox -> Verification -> Answer`

Avoid re-interpreting the raw user question independently in every worker.

### 4. Retrieval and evidence grounding are first-class concerns

Do not optimize only for code generation.

The project materials show that end-to-end failures are dominated by wrong extraction and insufficient evidence, while pure arithmetic and formula errors are much smaller contributors.

Therefore:

- preserve report identity,
- preserve statement scope,
- preserve period identity,
- preserve table identity,
- preserve row / column / header path when available,
- make evidence traceable to its source.


### 4.1 Retrieval stack is explicit

The baseline retrieval stack is:

- `BM25` for lexical/exact matching,
- `BGE-M3` for dense multilingual embeddings,
- a vector index / vector database for dense search,
- `RRF` or equivalent deterministic fusion,
- `BGE-reranker-v2-m3` for final candidate reranking.

Conceptual flow:

```text
Retrieval Query Builder
        |
   +----+----+
   |         |
  BM25     BGE-M3
   |         |
   |      Vector Search
   |         |
   +----+----+
        |
      Fusion
       RRF
        |
BGE-reranker-v2-m3
        |
      top-k
        |
 Evidence Builder
```

`BGE-M3` and `BGE-reranker-v2-m3` are retrieval models/tools, not autonomous agents.


### 4.2 Retrieval must support hybrid table + text evidence

Financial questions may require:

- table-only evidence,
- text-only evidence,
- evidence jointly drawn from tables and associated narrative paragraphs.

Do not assume that every answerable metric is stored in a table cell.
If the `Plan` requests hybrid evidence, retrieval must search both source types and the `Evidence` contract must preserve `source_type`.

Do not declare evidence unavailable until all source types requested by the `Plan` have been searched.

### 4.3 Scale and unit are first-class evidence

A numerically correct raw value with the wrong scale is a wrong financial answer.

Preserve and verify:

- raw value,
- normalized value,
- unit,
- scale (`raw`, thousand, million, billion, percent, etc.),
- the source from which scale/unit was inferred.

Scale may appear in a table header or surrounding narrative rather than next to the number itself.

### 4.4 Complex tables may use bounded question-conditioned table transformations

The default reasoning path remains grounded `Plan + Evidence -> Programmer -> Sandbox`.

For complex tables that are difficult to reason over statically, an optional bounded table-reasoning path may use atomic operations such as:

- select rows,
- select columns,
- add derived columns,
- group,
- sort.

Each operation must be executed deterministically and appended to an operation history.
Do not create an open-ended self-reflection loop.
Use this path only when a task or measured failure mode justifies it.

### 5. Preserve hierarchical table structure

Do not flatten complex financial tables in a way that loses header hierarchy, merged-cell meaning, parent-child structure, or cell metadata.

If a task changes table normalization, demonstrate that the resulting representation still allows the system to distinguish the correct row, column, period, and statement scope.

### 6. LLM proposes programs; Python performs arithmetic

For numerical reasoning, prefer a Program-of-Thought / program-aided pattern:

- LLM generates a constrained Python/Pandas program,
- the runtime binds grounded evidence,
- Python performs the arithmetic,
- verification checks the result.

Do not rely on free-form LLM arithmetic for production calculations when a deterministic execution path exists.

### 7. Do not hardcode answer values

Generated or handwritten logic must not contain constants copied from evaluation examples merely to make tests pass.

Prefer symbolic references to evidence and deterministic binding.

### 7.1 Program structure must be validated

For program-aided reasoning:

- prefer a constrained DSL or constrained Python/Pandas contract,
- validate generated structure before execution,
- use grammar/schema constraints where available,
- keep explicit intermediate references for multi-step calculations,
- evaluate program correctness separately from final execution correctness.

A correct final number can occur from an incorrect program by chance.
Conversely, multiple semantically equivalent programs may produce the same correct result.
Do not use only one of these signals for evaluation.

### 7.2 Reasoning depth is a risk signal

Questions requiring multiple reasoning steps are harder and should receive stronger verification and, where configured, a stronger model tier.

Do not assume a one-step program is sufficient merely because one value is requested.

### 8. Generated code must run in a sandbox

Never execute model-generated Python directly in the main application process.

Language-level restrictions are not considered a sufficient security boundary by themselves.
The execution design must support operating-system/process isolation.

### 9. Verification is mandatory

A successful Python execution is not enough to declare an answer correct.

Verification should cover, when applicable:

- company grounding,
- report / statement scope,
- period grounding,
- table grounding,
- row / column grounding,
- unit conversion,
- numeric parsing,
- financial formula logic,
- result sanity checks.

### 10. Retry is targeted, not a free-form agent loop

Retry based on failure type:

- missing/wrong evidence -> retrieval/evidence stage,
- program syntax/runtime error -> programmer stage,
- cheap model verification failure -> escalate to strong model,
- unsupported or unresolved request -> abstain / clarify.

Do not add unbounded `try again` loops.

## Supported Question Classes

The current design supports:

- `LOOKUP`
- `DERIVED_RATIO`
- `MULTI_PERIOD`
- `AGGREGATE`

Unsupported or unresolved questions should not be silently forced into one of these classes.

## NLU Rules

Prefer deterministic normalization for stable entities and conventions where possible.

Examples:

- `"Nhựa An Phát"`, `"An Phát"`, `"AAA"` -> canonical ticker `AAA` when uniquely resolvable.
- `"lãi ròng"`, `"LNST"`, `"lợi nhuận sau thuế"` -> one canonical metric.
- `"năm 2015"`, `"quý 3/2015"`, `"lũy kế 9 tháng"` -> explicit temporal structure.

If statement scope is inferred rather than explicitly stated, keep an `inferred` flag so verification can treat it more strictly.

Do not silently invent missing company, period, metric, or scope values when no safe default exists.

## Supervisor Rules

The canonical `Plan` should remain a deterministic structured contract.

Expected fields include:

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
  model_tier: CHEAP | STRONG
  verify_profile: LIGHT | STRICT
  max_retries: integer
  confidence: float
  abstain: boolean
  abstain_reason: string | null
```

Changing this contract is an architecture change. Update `docs/ARCHITECTURE.md` and `docs/DECISIONS.md` before or together with the code change.

## Model Routing Rules

Current routing policy:

- simple single-table, single-period lookup -> `CHEAP` + `LIGHT` verification,
- derived ratios, multi-period reasoning, joins, and aggregates -> `STRONG` + `STRICT` verification,
- if a cheap-tier attempt fails verification, escalate before final abstain.

Model names are deployment choices. Do not hardwire a provider/model into business logic unless the task explicitly requires it.

## Model and Engine Inventory

Current recommended baseline:

| Component | Model / Engine | Policy |
|---|---|---|
| NLU / Understanding | `Qwen3-8B` or `Qwen2.5-7B-Instruct` + deterministic resolvers | Understand Vietnamese financial language; output structured semantics |
| Supervisor / Planner | `Qwen3-8B` | Build `Plan`, route complexity, choose verify profile |
| Sparse Retrieval | `BM25` | Exact lexical matching |
| Embedding | `BGE-M3` | Dense retrieval over report/table representations |
| Fusion | `RRF` / deterministic scoring | Merge sparse + dense candidates |
| Reranker | `BGE-reranker-v2-m3` | Cross-encoder reranking |
| Schema Linking | deterministic fuzzy/schema matching + optional `Qwen2.5-7B-Instruct` | LLM only for ambiguous mappings |
| Numeric Masking / Binding | pure code | Symbolic placeholders and deterministic binding |
| Text-to-Pandas / Programmer | `Qwen2.5-Coder-14B-Instruct` | Generate constrained Python/Pandas |
| Sandbox | isolated Python runtime | Execute code, not an LLM |
| Verifier | deterministic rules + optional `Qwen3-8B` | Prefer code; use LLM only for semantic edge cases |
| Narrative / Answer | template first; optional `Qwen2.5-7B` or `Gemma 3 12B` | Not required in the v1 critical path |

NLU and Supervisor are separate logical components even when they reuse one `Qwen3-8B` model server. They must have separate prompts, schemas, and responsibility boundaries.

## Optional Experience Memory Policy

Experience memory is **not part of the mandatory v1 critical path**.

If implemented later:

- store reusable strategies separately from failure-derived caution/guard rules,
- retrieve memory by semantic similarity,
- activate memory only above a calibrated relevance threshold,
- use a deterministic fallback to base behavior when memory is not relevant,
- cap the number of injected memory entries,
- deduplicate entries,
- keep evaluation/test-time memory read-only,
- never write test answers or hidden gold data into memory,
- measure whether memory improves reasoning instead of assuming more memory is always better.

Memory may advise the Supervisor/Programmer/Verifier, but it must not override evidence grounding or deterministic safety rules.

## Production Optimization Policy

Only optimize after a correctness baseline exists.

Permitted measured optimizations include:

- semantic caching for sufficiently similar, stable/standardized inputs,
- model routing by estimated difficulty,
- confidence-gated retry,
- model escalation on failed verification,
- parallel execution of truly independent work.

Track at least:

- p50 / p95 / p99 latency,
- cost per query,
- token usage,
- retry/escalation rate,
- cache hit rate,
- stage-level failures,
- coordination failures.

Do not adopt a reflexive/multi-loop architecture globally only because it improves raw accuracy; justify the latency/cost/coordination tradeoff for this product.

## Scope Rules for Coding Agents

For every task:

- inspect the existing implementation first,
- identify affected modules,
- propose the smallest viable change,
- reuse existing utilities and schemas,
- keep changes inside task scope,
- preserve public contracts unless the task explicitly changes them.

Do not:

- refactor unrelated code,
- rename public APIs without need,
- add a new agent because it looks cleaner,
- introduce an additional LLM call without latency/cost justification,
- bypass validation to make a failing test pass,
- delete or weaken tests to make CI green,
- hide retrieval failures with hardcoded table IDs,
- create a second competing source of truth for financial formulas.

## Before Coding

For each task, report:

1. what the current code does,
2. which files/modules are relevant,
3. which contracts are involved,
4. the implementation plan,
5. the tests/evaluation that will prove completion.

If the task conflicts with the architecture, stop and report the conflict before implementing.

## Validation Workflow

After implementation, run the repository's actual validation commands.

At minimum, the project should eventually have:

- unit tests,
- integration tests,
- retrieval evaluation,
- oracle-evidence reasoning evaluation,
- retrieved-evidence end-to-end evaluation.

Do not invent command names if the repository does not contain them yet.
When concrete commands are added to the repo, update this section.

## Definition of Done

A task is complete only when:

- implementation matches the documented contract,
- relevant tests pass,
- relevant evaluation passes its declared acceptance criteria,
- no unrelated changes remain in the diff,
- documentation is updated if behavior or contracts changed,
- known limitations are explicitly reported.

## Source Basis

This policy is grounded in the provided project materials:

- `supervisor_routing.md`
- `2026 R2AI Stage2 Finance Tabular QA` materials
- `2026 AIGuru Finance Tabular QA` materials

The files define the supervisor/worker split, NLU slot extraction, model-tier routing, early abstain, hierarchical-table concerns, TableRAG, numeric masking, program-aided reasoning, sandbox execution, verification, and retrieval-heavy failure profile.


Additional research basis added in v3:

- TAT-QA (arXiv:2105.07624): hybrid table/text evidence, symbolic operators, answer scale.
- FinQA (arXiv:2109.00122): retriever-generator architecture, gold reasoning programs, program/execution evaluation.
- PAL (arXiv:2211.10435): program-aided reasoning with execution delegated to Python.
- Chain-of-Table (arXiv:2401.04398): bounded dynamic table operations with intermediate table state.
- Multi-Agent Financial Document Processing benchmark (arXiv:2603.22651): hierarchical vs. sequential/parallel/reflexive tradeoffs, semantic caching, routing, retry, latency/cost/failure taxonomy.
- FinAcumen (arXiv:2606.17642): selective experience memory, relevance gating, strategies vs. caution rules, deterministic financial tools.

## Communication Style

When explaining anything to the user:

- Keep explanations short and easy to understand.
- Prefer simple language over academic language.
- Explain one idea at a time.
- Use short bullet points when helpful.
- Give the answer first, then only the necessary explanation.
- Avoid long background explanations unless explicitly requested.
- Avoid repeating the same idea.
- Use concrete examples for difficult concepts.
- For complex topics, break them into small steps.
- If a response can be explained in 5 lines, do not use 20 lines.

Default format:

1. What it is.
2. Why it matters.
3. What to do next.