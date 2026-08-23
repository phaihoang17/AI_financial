# AI Financial Data Assistant

AI Financial Data Assistant is a Vietnamese financial-statement question answering system designed to answer factual and numerical questions over OCR-derived financial reports and tabular data.

The system is designed around a hierarchical supervisor-worker architecture with an explicit NLU/Understanding layer, grounded retrieval, program-aided numerical reasoning, sandboxed execution, and verification.

## Problem

Given a natural-language financial question, the system must:

1. understand the company, period, report scope, metric, and requested operation,
2. locate the correct financial report,
3. locate the correct table(s),
4. locate the correct evidence/cells,
5. construct the required reasoning plan,
6. generate a constrained Python/Pandas program when numerical reasoning is needed,
7. execute the program safely,
8. verify grounding, units, and financial logic,
9. return a final answer with traceable evidence.

Example:

> `ROE của AAA năm 2015 là bao nhiêu?`

This is not a single-cell lookup. The system may need profit after tax from the income statement, equity from the balance sheet for multiple periods, the correct ROE formula, and strict verification.

## Architecture at a Glance

The system has two core paths: **offline evidence indexing** and **online question answering**. A future optional experience-memory path is kept outside the v1 critical path.

### Offline evidence indexing

```text
Financial Reports / OCR / HTML / Narrative Text
                    |
                    v
          Document / Page Parser
             /              \
            v                v
   Table Normalization   Paragraph Extraction
   + hierarchy metadata  + table-text linking
            \                /
             \              /
              v            v
          Evidence Representation Builder
              |                       |
              v                       v
           BM25 Index              BGE-M3
                                 Embeddings
                                      |
                                      v
                                 Vector Index
```

Both table and text evidence must retain company/report/period/scope/source metadata. Scale and unit metadata should be preserved when available.

### Online query

```text
User Question
    |
    v
NLU / Understanding
    |
    | QueryUnderstanding
    v
Supervisor / Planner
    |
    | Plan
    v
Hybrid Retrieval Query Builder
    |
    +------------------------+
    |                        |
    v                        v
   BM25                    BGE-M3
    |                        |
    |                    Vector Search
    |                        |
    +-----------+------------+
                |
             RRF/Fusion
                |
                v
      BGE-reranker-v2-m3
                |
                v
        Evidence Builder
      (TABLE / TEXT / HYBRID)
                |
                v
       Scale / Unit Resolver
                |
                v
          Schema Linking
                |
        +-------+-------+
        |               |
        | complex table?|
        |               |
       no              yes
        |               |
        |       Bounded Table Transform
        |       Planner + Operation History
        |               |
        +-------+-------+
                |
                v
      Numeric Masking / Binding
                |
                v
       Programmer / Pandas
                |
                v
       Sandboxed Python
                |
                v
           Verification
                |
        +-------+-------+
        |               |
      pass             fail
        |               |
        v               v
 Answer Builder   targeted retry /
                  escalation / abstain
```

### Optional future experience memory

```text
Train/Dev Trajectories
        |
        v
Distill:
- successful strategies
- failure-derived guard rules
        |
        v
Experience Memory Index
        |
   relevance gate
        |
        +---- relevant ----> advise Supervisor / Programmer / Verifier
        |
        +---- not relevant -> base pipeline unchanged
```

Experience memory is optional and must be relevance-gated; it must not replace grounded evidence retrieval.

## Model / Engine Baseline

| Component | Recommended baseline |
|---|---|
| NLU / Understanding | Qwen3-8B or Qwen2.5-7B-Instruct |
| Supervisor / Planner | Qwen3-8B |
| Embedding | BGE-M3 |
| Sparse retrieval | BM25 |
| Fusion | RRF / deterministic scoring |
| Reranker | BGE-reranker-v2-m3 |
| Schema Linking | deterministic fuzzy/schema matching + optional Qwen2.5-7B-Instruct |
| Text-to-Pandas | Qwen2.5-Coder-14B-Instruct |
| Sandbox | isolated Python runtime |
| Verifier | deterministic code + optional Qwen3-8B |
| Narrative | template first; optional Qwen2.5-7B or Gemma 3 12B |

NLU and Supervisor remain separate logical stages even when both are served by the same Qwen3-8B weights with different prompts.

## Why This Architecture

The provided benchmark material shows that end-to-end accuracy drops substantially when the system must retrieve evidence instead of receiving oracle/gold tables.
Failure analysis also shows that wrong numerical extraction and insufficient evidence dominate the observed error profile.

Therefore retrieval and evidence grounding are treated as core product capabilities, not as a thin preprocessing step before code generation.

## Main Components

### NLU / Understanding

Normalizes the user's language into a structured `QueryUnderstanding` object.

Responsibilities:

- company/ticker resolution,
- period parsing,
- report-scope resolution,
- metric normalization,
- operation detection,
- ambiguity and missing-information detection.

### Supervisor / Planner

Consumes `QueryUnderstanding` and emits a deterministic `Plan`.

Responsibilities:

- classify the question,
- determine required tables and periods,
- decide retrieval requirements,
- choose model tier,
- choose verification profile,
- early abstain when the request cannot be safely grounded.

The Supervisor does **not** calculate the answer.

### Retrieval Layer

Locates relevant evidence from **tables and associated narrative text**.

The baseline is hybrid retrieval:

`BM25 + BGE-M3 -> RRF -> BGE-reranker-v2-m3`.

The architecture preserves table hierarchy, evidence source type, period/scope metadata, and source links between narrative paragraphs and tables where available.

### Evidence Builder

Transforms retrieval results into a stable, traceable evidence contract for downstream reasoning.

Evidence can be `TABLE`, `TEXT`, or a hybrid bundle. Scale/unit provenance is retained rather than inferred late inside the Programmer.

### Schema Linking + Numeric Masking

Links natural-language concepts to table schema and uses symbolic numeric placeholders where appropriate to reduce copied or hallucinated numeric constants.

### Programmer

Generates constrained Python/Pandas logic from the plan and grounded evidence.

### Sandbox

Executes generated code in an isolated runtime.
Generated code must not execute directly inside the application process.

### Verification

Checks grounding, units, numerical handling, and financial logic before an answer is accepted.

## Research-Informed Design Additions

The v3 architecture incorporates six additional research lessons:

1. **Hybrid evidence is required.** TAT-QA and FinQA both show that financial questions can require tables, narrative text, or both.
2. **Scale/unit must be resolved explicitly.** A raw number is not sufficient when `thousand`, `million`, `billion`, or `%` may be encoded in a header or paragraph.
3. **Executable reasoning should be evaluated structurally.** FinQA provides explicit reasoning programs, while PAL supports delegating execution to Python.
4. **Complex tables may benefit from question-conditioned transforms.** Chain-of-Table motivates a bounded operation history over intermediate tables rather than only a one-shot static-table program.
5. **Hierarchical orchestration remains the default.** The 2026 financial-document benchmark reports a favorable cost/accuracy position for hierarchical supervisor-worker architectures; reflexive loops gain accuracy but add cost, latency, and coordination risk.
6. **Memory should be selective, not always-on.** FinAcumen motivates a future experience-memory sidecar that injects strategies/guard rules only when similarity clears a threshold.

## Supported Question Types

The current design supports four main classes:

| Type | Description | Example |
|---|---|---|
| `LOOKUP` | Single metric, usually one period, no derived calculation | `LNST của AAA năm 2015?` |
| `DERIVED_RATIO` | Derived financial metric requiring formula/evidence | `ROE của AAA năm 2015?` |
| `MULTI_PERIOD` | Comparison/growth across periods | `Doanh thu tăng bao nhiêu % từ 2014 sang 2015?` |
| `AGGREGATE` | Sum/average/min/max across rows or periods | `Trung bình LNST 3 năm gần nhất?` |

Questions that cannot be safely identified or grounded should be clarified or abstained rather than guessed.

## Model Routing

The design supports two model tiers:

- `CHEAP`: simple lookup-style requests,
- `STRONG`: derived, multi-period, aggregate, or cross-table requests.

Verification profiles:

- `LIGHT`: grounding + units for simple lookups,
- `STRICT`: stronger numerical/financial checks for more complex reasoning.

A failed cheap-tier result can be escalated to the strong tier before final abstain.

## Proposed Repository Layout

```text
.
├── AGENTS.md
├── README.md
├── docs/
│   ├── PRODUCT.md
│   ├── ARCHITECTURE.md
│   ├── DECISIONS.md
│   └── TASKS.md
├── src/
│   ├── indexing/
│   ├── understanding/
│   ├── supervisor/
│   ├── retrieval/
│   │   ├── sparse/
│   │   ├── dense/
│   │   ├── fusion/
│   │   └── reranker/
│   ├── evidence/
│   ├── programmer/
│   ├── sandbox/
│   ├── verification/
│   ├── answer/
│   └── orchestration/
├── tests/
└── evaluation/
```

This layout is a proposed implementation structure. The provided source materials define the architecture concepts but do not prescribe a repository layout.

## Development Workflow

```text
PRODUCT
   -> ARCHITECTURE
   -> DECISIONS
   -> TASKS
   -> AGENTS
   -> inspect
   -> plan
   -> implement
   -> unit/integration tests
   -> evaluation
   -> review diff
   -> human review
```

Use `docs/TASKS.md` as the implementation roadmap and `AGENTS.md` as the working rules for Codex.

## Running the Project

Concrete install, run, test, lint, and evaluation commands are intentionally not invented here because the current source materials do not define the repository toolchain.

When the codebase is initialized, replace this section with the actual commands, for example:

- environment setup,
- dependency installation,
- local API/server start,
- unit test command,
- retrieval evaluation command,
- end-to-end evaluation command.

## Documentation

- `docs/PRODUCT.md`: product behavior and scope.
- `docs/ARCHITECTURE.md`: runtime architecture and contracts.
- `docs/DECISIONS.md`: architecture decisions and rationale.
- `docs/TASKS.md`: implementation roadmap and acceptance criteria.
- `AGENTS.md`: coding-agent execution rules.

## Source Basis

This README is grounded in:

- the canonical Supervisor/Plan contract in `docs/ARCHITECTURE.md` and routing decisions in `docs/DECISIONS.md`,
- the provided Stage 2 Financial Tabular QA materials,
- the provided Finance Tabular QA materials.


### Additional v3 research sources

- TAT-QA — arXiv:2105.07624
- FinQA — arXiv:2109.00122
- PAL — arXiv:2211.10435
- Chain-of-Table — arXiv:2401.04398
- Benchmarking Multi-Agent LLM Architectures for Financial Document Processing — arXiv:2603.22651
- FinAcumen — arXiv:2606.17642
