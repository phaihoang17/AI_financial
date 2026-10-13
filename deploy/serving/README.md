# DeepSeek API deployment

Fill the ignored repository `.env` file:

```dotenv
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
```

The runner reads this file directly; do not execute it as a shell script.
Environment variables override file values. The key is sent only in an HTTPS
Authorization header. Never commit the key or paste it into run artifacts.

As of 2026-10-09, [DeepSeek documentation](https://api-docs.deepseek.com/quick_start/pricing)
states that the legacy `deepseek-v4-flash` alias is served by V4.1 Flash;
`deepseek-flash` is its current name. This deployment records the requested model
ID and does not assert a pinned V4 checkpoint. NLU uses non-thinking mode;
STRONG Programmer requests use thinking with high reasoning effort.

Run the real raw-question M9 path:

The local BGE execution was set up with `torch==2.8.0`,
`transformers==4.46.3` and `sentencepiece==0.2.1` in the Python 3.11 virtualenv.
It uses the repository's pinned BGE model revisions, float32 inference and
existing attention-mask mean pooling/L2 normalization.

On this macOS virtualenv, PyTorch and FAISS wheels bundle separate OpenMP
libraries. Set `DYLD_LIBRARY_PATH` to FAISS's library directory so both use one
runtime; do not set `KMP_DUPLICATE_LIB_OK`. A real matrix multiply and FAISS
search confirmed one loaded OpenMP library in
`scratch_logs/task100-openmp-probe.json`.

```bash
DYLD_LIBRARY_PATH="$PWD/.venv/lib/python3.11/site-packages/faiss/.dylibs" \
OMP_NUM_THREADS=2 \
.venv/bin/python -m src.evaluation.run_live_e2e_eval \
  --questions gold/retrieval/pilot.jsonl \
  --corpus-artifact m2-corpus-v1/4f39603a89e213129de6c57e1234456e88086832c8b61bd9821770d76a6dc78e \
  --vector-artifact m2-vector-index-v1/full-corpus-rtx5090 \
  --bm25-artifact artifacts/m3-bm25-v1 \
  --provenance-artifact m2-provenance-v1 \
  --table-class-artifact m2-table-class-v1 \
  --raw-corpus data/vifinqa/financial_statements \
  --output scratch_logs/task100-live-e2e.json
```

Only ID and question enter inference. Gold metadata is discarded by this
runner. The existing business registry supports LNST; unsupported metrics and
missing scope abstain through existing gates. The Mac CPU path loads BGE models
inside their stages, uses batch size 1 and preserves no-truncation guards.
It rebuilds exact source evidence; full-corpus evidence scans can take time.

The output checkpoints terminal M9 states after each request. Execution completion
does not close TASK-100: the 37-case LOOKUP pilot naturally routes CHEAP/LIGHT,
and ADR-057 live retrieval validation plus appropriate STRONG/STRICT gold are
still required for the correctness baseline. No tier is forcibly changed.

The first real API run on 2026-10-09 completed all 37 pilot questions in 40.367
seconds of inference after artifact validation: 35 CLARIFICATION, 2 ABSTAIN,
0 answers. There were 28 MISSING_METRIC, 7 MISSING_PERIOD+MISSING_METRIC and
2 MISSING_STATEMENT_SCOPE failures. No question reached retrieval, Programmer,
sandbox or verification. This is a measured failure of raw-question M9, not a
passing baseline; see `scratch_logs/task100-live-e2e-summary.json` and the
terminal state export `scratch_logs/task100-live-e2e.json`.
