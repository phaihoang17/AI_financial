# AI Financial Data Assistant

## Opt-in Text-to-Pandas API and real-model evaluation

The Text-to-Pandas components are connected through
`src.text_to_pandas.pipeline.PandasPipeline.ask`. The API accepts a question and
returns a JSON-safe answer with source citations, verification and bounded
attempt history. Generated code runs only in the isolated worker. Monetary
answers use RAW VND; growth/percentage ratios use PERCENT. Default M9 routing
has not changed (ADR-069).

Use an active Python environment with `requirements-test.txt` installed
(including Pandas 2.3.3). With an existing catalog and DeepSeek credentials in
the ignored `.env`:

```bash
python -m src.text_to_pandas.run \
  --catalog artifacts/t2p-catalog-v2 \
  --question 'ROE của AAA hợp nhất năm 2023 là bao nhiêu?' \
  --history-root scratch_logs/t2p-history \
  --output scratch_logs/t2p-demo.json

python -m pytest tests/text_to_pandas -q

python -m src.text_to_pandas.run \
  --catalog artifacts/t2p-catalog-v2 \
  --questions gold/wide/t2p-all-questions.jsonl \
  --history-root scratch_logs/t2p-wide-history \
  --output scratch_logs/t2p-wide-live.json --resume

python -m src.text_to_pandas.benchmark \
  --runs scratch_logs/t2p-wide-live.json \
  --output scratch_logs/t2p-wide-benchmark.json
```

Use a new output path after changing code, catalog, model, questions, concurrency or retry
budget. Resume rejects mixed measurements. The report evaluates 140 frozen
LNST cases and 17 Text-to-Pandas negative controls against ADR-066 thresholds.
The old negative controls remain intact; six metrics previously unsupported by
M9 are available in the broader catalog, so the Pandas negative set substitutes
market-data/subannual controls for those six. No benchmark step switches defaults.

Verification covers named metrics, ratios, growth, mean/sum and keyed results;
threshold screening, mixed-unit results and unknown semantics fail closed.
Truncated result/evidence frames are rejected. Local sandbox tests do not prove
that the pinned production NsJail deployment is installed and operational.

## Production gates (ADR-072)

`python -m src.evaluation.production_gate` checks every production threshold
from recorded evidence and marks a gate `BLOCKED` when its evidence is missing.
Current verdict: **8/9 gates pass; not production-ready until people review the gold** (`scratch_logs/goal-production/production-gate-v3.json`).

| Gate | Threshold | Status |
|---|---|---|
| human-reviewed gold | 200–300 cases, ≥100 holdout, all approved | BLOCKED on people (0 reviewed; page and CLI ready) |
| correct financial answer | ≥95%, every group ≥90% | PASS on holdout3: 122/127 = 96.1%, lowest group 90% (earlier holdouts 81.9% before their fixes) |
| code execution success | ≥99% | PASS: 124/125 = 99.2% on holdout3 |
| provenance | 100% | PASS: 100% |
| no fabricated numbers, refusals | 0 wrong, all refused | PASS in every split (0 wrong, 20/20 refusals) |
| sandbox security | 0 escapes | PASS (27 probes, local + reduced NsJail) |
| resource stability | no growth in stress and soak | PASS (stress 1–64, 30-min soak) |
| API reliability | SLO, monitoring, alerting, rollback tested | PASS (drill 20/20) |
| NsJail capacity | ≤32 M7 per host | PASS (cap 32: 128/128 at 64 offered) |

**Catalog v4.** Rebuild with the same command as v2 and a new output:

```bash
python -m src.text_to_pandas.catalog_builder --corpus-manifest <corpus>/manifest.json \
  --raw-root data/vifinqa/financial_statements --output artifacts/t2p-catalog-v4
```

**Production gold.** Four splits of 127 cases plus 20 must-refuse controls each.
Fix the system from development data only; score a frozen holdout once, then it
becomes development data and the next holdout (`holdout3`: LLM-paraphrased) is used.

```bash
python -m src.evaluation.production_gold --corpus-artifact <corpus> \
  --raw-corpus data/vifinqa/financial_statements --output gold/production-v1 \
  --earlier-audit gold/wide/wide-lnst-audit.json --earlier-audit gold/broad/broad-audit.json \
  --earlier-audit gold/broad-h2/broad-audit.json --earlier-audit gold/retrieval/strong-strict-source-audit.json
python -m src.text_to_pandas.run --catalog artifacts/t2p-catalog-v4 \
  --questions gold/production-v1/holdout/all-questions.jsonl --concurrency 8 \
  --history-root scratch_logs/holdout-history --output scratch_logs/holdout-live.json
```

**Human review.** Cases stay `AUTO_DERIVABLE` until a person approves them:

```bash
python -m src.evaluation.gold_review packet           # source rows per case
python -m src.evaluation.gold_review record --reviewer "Full Name"
python -m src.evaluation.gold_review page --output gold-review.html   # shared review page
python -m src.evaluation.gold_review ingest --file review-export.json
python -m src.evaluation.gold_review summarize        # review/summary.json for the gate
```

**LLM providers.** `deploy/llm/providers.toml` lists DeepSeek (default), OpenAI,
Azure OpenAI, Anthropic, Gemini, Qwen/DashScope, OpenRouter, Groq, Mistral, vLLM
and Ollama. Select with `--provider NAME` or `LLM_PROVIDER`; keys come from the
environment or `.env` (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, …). Only DeepSeek
has been measured live; run the benchmark before using another provider.

```bash
python -m src.text_to_pandas.run --provider anthropic --catalog artifacts/t2p-catalog-v4 \
  --question 'Tổng tài sản của SSI năm 2023 theo báo cáo hợp nhất là bao nhiêu?' \
  --history-root scratch_logs/t2p-history --output scratch_logs/anthropic-demo.json
```

**Sandbox on Linux, macOS and Windows.** `deploy/sandbox/sandbox.toml` and
`M7_SANDBOX_ISOLATION`: `local` (rlimits on Linux, `sandbox-exec` on macOS, a job
object on Windows), `linux-nsjail-v1` (Linux production), `container-v1` (the
Linux worker in a digest-pinned image through Docker or Podman, on any OS; build
`deploy/sandbox/container/Dockerfile`, set `SANDBOX_CONTAINER_IMAGE=<name>@sha256:…`).
Admission caps M7 at 32 workers under NsJail; override with
`M7_MAX_CONCURRENT_WORKERS`. `.github/workflows/sandbox-platforms.yml` runs the
platform tests and a real worker smoke on Ubuntu, macOS and Windows.

```bash
python -m src.evaluation.run_security_suite --output scratch_logs/security.json
python -m src.evaluation.run_sandbox_load_eval --concurrency 1,8,16,32,64 --output scratch_logs/stress.json
python -m src.evaluation.run_soak_eval --duration-s 1800 --concurrency 16 \
  --stress scratch_logs/stress.json --output scratch_logs/stability.json
```

**Question API.** Releases pin the catalog, generator and code; a release is
served only after preflight and a canary set pass, and rolls back on a page alert.

```bash
python -m src.service.app --release-root deploy/releases --port 8080   # chat UI at http://127.0.0.1:8080/
curl -s localhost:8080/v1/ask -d '{"question": "ROE của FPT năm 2023 theo báo cáo hợp nhất là bao nhiêu?"}'
python -m src.service.release rollback --root deploy/releases --reason "..."   # then POST /admin/reload
python -m src.service.run_reliability_eval --catalog artifacts/t2p-catalog-v4 \
  --history <run history> --dev-runs <dev run report> --output scratch_logs/api-reliability.json
```

SLOs: `deploy/service/slo.toml`. Prometheus alert rules: `deploy/service/alerts.yml`
(generated from `src/service/alerting.py`).

## Broad independent gold and concurrent load (ADR-070)

Build from the committed corpus manifest and raw OCR, independently of the
pipeline/catalog. The default seed reproduces the existing 140 positives and
18 negatives. Use a new seed and output directory for held-out measurement
after any fixes informed by this set; do not tune NLU against the frozen set.

```bash
python -m src.evaluation.broad_gold \
  --corpus-artifact artifacts/reindex-adr062/m2-corpus/7c14b5498ce3871adfc61d8a475e910ad90e2e2143f6298a3d1342c048499a66 \
  --raw-corpus data/vifinqa/financial_statements \
  --seed 20261014 --output gold/broad

python -m src.text_to_pandas.run \
  --catalog artifacts/t2p-catalog-v2 \
  --questions gold/broad/broad-all-questions.jsonl --concurrency 8 \
  --history-root scratch_logs/t2p-broad-history-c8 \
  --output scratch_logs/t2p-broad-live-c8.json --resume

python -m src.text_to_pandas.benchmark \
  --gold gold/broad/broad.jsonl --negatives gold/broad/broad-negative.jsonl \
  --audit gold/broad/broad-audit.json --runs scratch_logs/t2p-broad-live-c8.json \
  --output scratch_logs/t2p-broad-benchmark-c8.json

# Run sandbox matrices sequentially, without other heavy work.
python -m src.evaluation.run_sandbox_load_eval \
  --concurrency 1,8,16,32,64 --target-concurrency 16 \
  --output scratch_logs/sandbox-load-local-root.json
```

Do not edit `src/` during a live run. `--concurrency` defaults to 1;
`wall_clock_s` measures pending work in that invocation, not cumulative resumed
work. Before ADR-071 the broad benchmark was NOT_ACCEPTED (sequential 105/140,
concurrency 8 at 106/140); the 158-request concurrent run took 64.277 seconds
with zero request/history mix-ups (`scratch_logs/t2p-broad-concurrency-comparison.json`).

ADR-071 fixes the phrasing gaps that set exposed and rebuilds the catalog as
`artifacts/t2p-catalog-v2` (`python -m src.text_to_pandas.catalog_builder
--corpus-manifest <corpus>/manifest.json --raw-root data/vifinqa/financial_statements
--output artifacts/t2p-catalog-v2`). It is measured on a new held-out sample that
shares no unit with `gold/broad`:

```bash
python -m src.evaluation.broad_gold --corpus-artifact <corpus> \
  --raw-corpus data/vifinqa/financial_statements --seed 20261015 \
  --exclude-gold gold/broad/broad.jsonl --output gold/broad-h2
```

`gold/broad-h2` scores **137/140, ACCEPTED** (sequential, catalog v2), with zero
wrong answers and zero negative false answers. Evidence:
`scratch_logs/t2p-broad-h2-live.json`, `t2p-broad-h2-benchmark.json`.

The sandbox harness checks unique benign results, interleaved abuse and leaked
processes/descriptors/threads/temp directories. It exits nonzero for wrong,
escaped, misclassified or leaked results at any level, or benign failures at
or below the target. Larger-level benign failures remain visible even when
they do not fail that target gate. On an installed production host, set
`M7_SANDBOX_ISOLATION=linux-nsjail-v1` and run from `/opt/ai-financial` with the
pinned profiles. This container can measure only local execution and a reduced
namespace-free NsJail wrapper. Leak checks wait up to 5 s for resources to
return to baseline; the root wrapper rerun passes with no residue, but wrapped
M7 at 64 still times out on 7/128 requests, so plan for at most 32 concurrent
M7 workers per host. Full production namespace/cgroup conformance remains
unverified.

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

The system has two core paths: **offline evidence indexing** and **online question answering**. Optional experience memory stays outside the mandatory v1 critical path.

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

### Optional experience memory

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
        +---- relevant ----> advise Programmer
        |
        +---- not relevant -> base pipeline unchanged
```

Experience memory is optional and must be relevance-gated; it must not replace grounded evidence retrieval.

M11 implements immutable TRAIN/DEV-derived strategy and guard entries, a
DEV-calibrated BGE-M3 relevance gate, and a maximum of three entries per
Programmer call. It stays off until `build_live_graph(..., memory=index)` receives
a validated `MemoryIndex`. A separate `strong_client` can be supplied for tier
routing; otherwise CHEAP/STRONG use different thinking settings on one endpoint.

The authorized validation uses independent synthetic TRAIN/DEV and held-out
cases. Tools and tools+memory each answer 4/4 in the latest live run: no accuracy
gain is established, and production quality remains unmeasured. Full evidence,
latency, tokens, failure modes and limitations are in `docs/TASKS.md` (M11).
TASK-101 also passes a separate three-case real-report LOOKUP pilot in both
serving modes, with zero answer-quality delta. This small agent-audited pilot
does not establish broad production quality or a causal latency benefit.

Run these commands with the activated project Python environment and a running
pinned BGE-M3 endpoint. This session used `/venv/main/bin/python`. The serving
configuration must contain the existing `embedding` endpoint contract; the local
measured configuration is `scratch_logs/adr063-retrieval-endpoints.json`.
Use a new dataset directory and new artifact/report paths on each run.

```bash
python -c 'from src.evaluation.memory_fixtures import write_synthetic_training; write_synthetic_training("artifacts/m11-demo")'
python -m src.memory.run_memory_build \
  --trajectories artifacts/m11-demo/train.jsonl \
  --development artifacts/m11-demo/dev.jsonl \
  --held-out-manifest artifacts/m11-demo/held-out-manifest.json \
  --serving-config scratch_logs/adr063-retrieval-endpoints.json \
  --dataset-id m11-independent-synthetic-v2 \
  --output artifacts/m11-demo/memory-index.json
python -m src.evaluation.run_memory_eval \
  --memory-index artifacts/m11-demo/memory-index.json \
  --serving-config scratch_logs/adr063-retrieval-endpoints.json \
  --output scratch_logs/m11-demo-fixture-eval.json
```

The default evaluation uses deterministic generation and real OS sandbox
execution, with no fabricated model token counts. Add `--live-programmer` for
measured generation using the existing `.env` deployment. Held-out evaluation
checks index hashes, never learns from its cases, and refuses protected output
paths or overwriting an existing report/index. Currency costs stay null until
pricing is supplied; fixture evidence coverage is not production retrieval quality.

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
6. **Memory should be selective, not always-on.** The optional M11 memory index injects strategies/guard rules only when similarity clears a DEV-calibrated threshold.

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

The current development host is macOS on Apple M1 with 8 GiB RAM. Historical
RTX 5090 validation logs do not describe the current deployment. Local tests and
fixture evaluations use CPU and need no CUDA or model serving.

Create the tested Python 3.11 environment from the repository root:

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements-test.txt
.venv/bin/python -m pytest -q
.venv/bin/python -m src.evaluation.run_supervisor_eval --mode fixture
.venv/bin/python -m src.evaluation.run_reasoning_eval --mode oracle
.venv/bin/python -m src.evaluation.run_e2e_eval --mode fixture
.venv/bin/python -m src.evaluation.run_baseline_eval --mode fixture
```

The sandbox uses `sandbox-exec` on Mac and fails closed if it is unavailable.
Real-model smoke tests are opt-in; this environment does not install or load
BGE model weights. Fixtures do not establish TASK-100's real-corpus baseline.
Full-corpus embedding/index construction remains outside the 8 GiB local run.

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
