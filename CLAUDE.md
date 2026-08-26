# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An **AI Financial Data Assistant** for Vietnamese financial-statement question answering over OCR-derived tables / narrative text. Given a question like `ROE của AAA năm 2015 là bao nhiêu?`, the system understands it, retrieves grounded evidence, generates a program, executes it in a sandbox, verifies the result, and returns a traceable answer (or abstains).

The repo is currently a **deterministic, CPU-only, pure-Python implementation** of the pipeline contracts. The models named in the docs (Qwen, BGE-M3, BGE-reranker) are the intended production baseline, **not** wired in — retrieval/reasoning are exercised through deterministic fixtures. Almost all code is standard-library only; the sole third-party runtime dependency is `langgraph` (M9 orchestration graph).

## Read the docs before implementing

This is a spec-driven repo. `AGENTS.md` is the authoritative execution policy. Before changing anything non-trivial, read in order: `AGENTS.md` → `docs/PRODUCT.md` → `docs/ARCHITECTURE.md` → `docs/DECISIONS.md` → `docs/TASKS.md`. Do not infer architecture from memory when these documents say otherwise.

Work is organized into milestones **M0–M9** (see `docs/TASKS.md`), each broken into batches. `docs/TASKS.md` is the roadmap; `docs/DECISIONS.md` holds the ADRs.

## Commands

```bash
# Full test suite (unittest tests discovered by pytest)
python -m pytest -q

# Single file / single test
python -m pytest tests/verification/test_numeric.py -q
python -m pytest tests/verification/test_numeric.py::ClassName::test_name -q

# Lint (ruff cache is present in-repo)
ruff check src tests

# Deterministic CPU-only fixture evaluations (each prints a JSON report,
# exits non-zero on failure — usable as CI gates):
python -m src.evaluation.run_supervisor_eval --mode fixture   # planning only
python -m src.evaluation.run_retrieval_eval  --mode fixture   # retrieval stages
python -m src.evaluation.run_reasoning_eval  --mode oracle    # oracle-evidence reasoning
python -m src.evaluation.run_e2e_eval        --mode fixture   # retrieved-evidence E2E
```

Notes:
- `pytest.ini` sets `--import-mode=importlib` and adds `.` and `tests/indexing` to `pythonpath`. Run pytest from the repo root.
- Retrieval eval also accepts `--mode full-corpus`, which only reports `GPU_PRODUCTION_VALIDATION_PENDING` (no GPU work is implemented).
- `requirements-orchestration.txt` pins `langgraph==1.2.10`, needed only for `src/orchestration/graph.py` and its tests. The rest of the suite runs without it.

## Pipeline architecture

Each stage is a `src/<stage>/` package, and the stage boundary is a typed contract in that package's `schemas.py`. Workers consume the previous stage's structured output — never re-interpret the raw user question.

```
Question
  → understanding/   NLU: QueryUnderstanding (company, period, scope, metric, operation, ambiguity)
  → supervisor/      Planner: Plan (question_type, tables_needed, reasoning_mode, model_tier, verify_profile) or SupervisorResult abstain
  → retrieval/       BM25 + dense → RRF fusion → reranker → candidates
  → evidence/        Evidence contract (TABLE/TEXT/HYBRID) + scale/unit resolution + schema linking + numeric masking
  → programmer/      constrained program from Plan + grounded evidence
  → sandbox/         OS-isolated worker executes the program
  → verification/    grounding / units / financial-logic checks → pass, or targeted retry / abstain
  → orchestration/   answer builder; the whole flow as a LangGraph state machine
```

Supporting packages: `indexing/` (offline: document parsing, table normalization, embeddings/BM25 artifact building, provenance sidecars), `formulas/` (single source of truth for financial formulas), `numeric/` (scale conversion / decimal policy), `evaluation/` (harness + per-stage evaluators and fixtures).

The offline indexing pipeline and online query pipeline are the two core paths (`docs/ARCHITECTURE.md` §2–3).

## Hard invariants (from AGENTS.md — enforce, don't violate)

- **NLU only understands; Supervisor only plans.** The Supervisor must not read numeric cells, compute ratios/growth, bypass retrieval, or execute code.
- **Evidence is grounded and traceable.** Preserve report / statement scope / period / table identity and row/column/header path. Never hide a retrieval failure with a hardcoded table ID.
- **Scale and unit are first-class.** A correct raw value with the wrong scale is wrong. Track raw value, normalized value, unit, scale, and the source the scale was inferred from.
- **LLM proposes programs; Python does the arithmetic.** No free-form LLM arithmetic in the production path. Validate program *structure* before execution.
- **Generated code runs only in the sandbox** (`src/sandbox/`, `worker.py` is a stdin/stdout subprocess with `resource`/`signal` OS-level limits). Never execute model output in-process.
- **Never hardcode answer values** copied from evaluation examples to make a test pass. Prefer symbolic evidence references + deterministic binding. There must be exactly one source of truth for each financial formula (`src/formulas/`).
- **Retry is targeted by failure type** (wrong evidence → retrieval; program error → programmer; cheap-tier verify fail → escalate to strong; unresolved → abstain). No unbounded "try again" loops.
- **The `Plan` contract is architecture.** Changing its fields means updating `docs/ARCHITECTURE.md` and `docs/DECISIONS.md` in the same change (canonical fields listed in `AGENTS.md`).
- **Routing is deterministic:** `DIRECT`→`CHEAP`, `PROGRAM`→`STRONG`; `LIGHT` verify only for a formula-free single-requirement `LOOKUP`, else `STRICT`. `TABLE_TRANSFORM` is disabled in M4 v1. Routing uses no confidence scores or LLM.

## Task workflow (from AGENTS.md)

Inspect existing code first; make the smallest viable change; reuse existing utilities and schemas; keep the diff inside task scope; preserve public contracts unless the task changes them. Don't refactor unrelated code, add agents/LLM calls without cost justification, or weaken tests. A task is done only when implementation matches the contract, relevant tests + the declared evaluation pass, the diff has no unrelated changes, and docs are updated if a contract changed.

## Communication style (from AGENTS.md)

Answer first, then only the necessary explanation. Prefer simple language, one idea at a time, short bullets. If it fits in 5 lines, don't use 20.
