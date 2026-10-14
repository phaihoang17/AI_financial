# Wide real-data LNST gold (ADR-066)

Text-to-Pandas (ADR-069) uses the same 140 positive gold cases. Its separate
`t2p-negative.jsonl` retains the 11 missing-input/out-of-coverage controls and
adds six market-data/subannual controls. The six original UNSUPPORTED_METRIC
cases remain untouched: their metrics are now available in the broader Pandas
catalog. `t2p-all-questions.jsonl` concatenates the frozen positives and these
17 controls. Score with `python -m src.text_to_pandas.benchmark`; use the README
commands for a live run. Exact gold-cell parity is reported separately from
value/unit correctness. Agent-derived gold remains unreviewed by a human.

Built by `python -m src.evaluation.wide_gold` from the raw ViFinQA annual
statements against corpus artifact
`7c14b5498ce3871adfc61d8a475e910ad90e2e2143f6298a3d1342c048499a66`.
Deterministic (seed 20261013); rerunning reproduces the same bytes.

| File | Content |
|---|---|
| `wide-lnst.jsonl` | 140 VERIFIED in-scope cases (`m10-gold-retrieval-v1`): 60 LOOKUP, 40 GROWTH_RATE, 40 AVERAGE over 75 tickers |
| `wide-negative.jsonl` | 17 questions v1 must not answer (unsupported metric, missing period/scope, unknown company, period outside corpus) |
| `wide-lnst-audit.json` | build summary, skip counts and, per case, the raw source row and the confirming next-report prior-year value |
| `wide-all-questions.jsonl` | both question sets concatenated for the live runner |

Each value is the LNST total row (TT200 `Mã số 60` or the bank-template
`Lợi nhuận sau thuế` line), with year columns oriented by their headers, the
unit scaled to RAW VND, and accepted only when the next year's report repeats
it as the prior-year value. Agent-built and cross-checked; not human-reviewed.

Validate, run and score:

```bash
python -m src.evaluation.run_gold_retrieval_validation --corpus-artifact <corpus> --gold gold/wide/wide-lnst.jsonl
scratch_logs/run_wide_eval.sh          # live DeepSeek + served BGE, resumable
python -m src.evaluation.wide_eval --runs scratch_logs/wide-live-e2e.json --output scratch_logs/wide-eval-report.json
```
