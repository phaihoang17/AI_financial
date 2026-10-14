# Broad held-out gold h2 (m10-broad-gold-v1, seed 20261015)

A second held-out sample from the same builder as `gold/broad`, drawn after the
ADR-071 phrasing and catalog fixes to measure them on unseen cases. It reuses no
(metric, ticker, scope, year) unit of `gold/broad/broad.jsonl` and shares no
question text with it. It uses the same question templates, so it measures the
fixes on new companies/years/values, not on unseen phrasings.

```bash
python -m src.evaluation.broad_gold --corpus-artifact <corpus> \
  --raw-corpus data/vifinqa/financial_statements --output gold/broad-h2 \
  --seed 20261015 --exclude-gold gold/broad/broad.jsonl
```

140 VERIFIED cases over 58 tickers (50 LOOKUP, 24 GROWTH, 30 AVERAGE, 36 RATIO)
and 18 negatives; byte-reproducible. Method, files and limits are as in
`gold/broad/README.md`. Agent-built and cross-checked; not human-reviewed.
