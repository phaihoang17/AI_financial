# Broad independent multi-metric gold (m10-broad-gold-v1)

A held-out set that measures Text-to-Pandas beyond the single LNST row of the
wide set (`gold/wide`). It covers ten Thông tư 200 lines across all three
statements and the six ADR-069 ratios. Nothing in it was used to build or tune
the pipeline, and its values never come from the pipeline or its catalog.

Built by `python -m src.evaluation.broad_gold` from the raw ViFinQA annual
statements against corpus artifact
`7c14b5498ce3871adfc61d8a475e910ad90e2e2143f6298a3d1342c048499a66`.
Deterministic (seed 20261014); rerunning reproduces the same bytes.

| File | Content |
|---|---|
| `broad.jsonl` | 140 VERIFIED cases over 55 tickers: 50 LOOKUP, 24 GROWTH, 30 AVERAGE, 36 RATIO |
| `broad-negative.jsonl` | 18 questions v1 must not answer (market data, quarterly period, missing period/scope, unknown company, period outside the corpus) |
| `broad-audit.json` | build summary, skip counts and, per case, every raw source row with the confirming next-report value |
| `broad-all-questions.jsonl` | both sets concatenated for the live runner |

Metrics: NET_REVENUE, GROSS_PROFIT, PROFIT_BEFORE_TAX (income statement);
CURRENT_ASSETS, TOTAL_ASSETS, LIABILITIES, CURRENT_LIABILITIES, EQUITY (balance
sheet); CFO, CFI (cash flow). Ratios: ROE, ROA, GROSS_MARGIN, NET_MARGIN,
DEBT_TO_EQUITY, CURRENT_RATIO, with definitions from `src/formulas/ratios.py`
(ROE/ROA use the average of opening and closing balances).

Each value is the raw-HTML row whose `Mã số` and whole-word, accent-folded label
both agree, read from header-located current/prior columns (dates or words, never
a bare year for balance sheets), scaled to RAW VND by the unit note, and kept
only when report Y+1 repeats it as its prior-year or opening value. Note tables
whose cells merely equal a line code are rejected. Bank templates have no line
codes for these rows and are not sampled. Questions use several natural
phrasings per metric, including variants absent from the pipeline's synonym
lists. Agent-built and cross-checked; not human-reviewed.

Run and score:

```bash
python -m src.text_to_pandas.run --catalog artifacts/t2p-catalog-v1 \
  --questions gold/broad/broad-all-questions.jsonl \
  --history-root scratch_logs/t2p-broad-history --output scratch_logs/t2p-broad-live.json --resume
python -m src.text_to_pandas.benchmark --gold gold/broad/broad.jsonl \
  --negatives gold/broad/broad-negative.jsonl --audit gold/broad/broad-audit.json \
  --runs scratch_logs/t2p-broad-live.json --output scratch_logs/t2p-broad-benchmark.json
```
