# Production gold v1 (m10-production-gold-v1)

The gold set behind the ADR-072 production gates: 254 questions with answers read
independently from the raw ViFinQA annual statements, in two splits, plus 40
questions the system must refuse. Built by `python -m src.evaluation.production_gold`
from corpus artifact `4f39603a…` (seeds 20261016 dev, 20261017 holdout).

| Path | Content |
|---|---|
| `dev/` | 127 cases (45 lookup, 24 growth, 28 average, 30 ratio; 97 corporate, 30 bank) and 20 must-refuse controls. Fixes may be derived from this split. |
| `holdout/` | 127 cases and 20 controls with the same composition, frozen in `holdout/FROZEN.json` before any measurement. Scored once per frozen system; never used to fix anything. |
| `*/gold.jsonl`, `*/negative.jsonl`, `*/all-questions.jsonl` | cases, controls, and both for the live runner |
| `*/audit.json` | per case: every source row (report, printed label and value, unit scale, VND value, the next report's prior-year value, table and cell ids), the template and statement group |
| `review/` | human review: packets, decisions, summary (see below) |

**Independence.** No report cited by any earlier gold file (wide, broad,
broad-h2, retrieval) is used. The holdout shares no report with `dev`, and its
questions use phrasings and metric names written only for it.

**Method.** A value is the raw-HTML statement row whose TT200 `Mã số` and label
agree (corporate) or whose whole, ordinal-stripped label names the line (bank
statements, detected by a "Thu nhập lãi thuần" line); columns are oriented by
their headers, the unit note scales to VND, and the value is kept only when
report Y+1 repeats it as its prior-year figure. Ratios use `src/formulas/ratios.py`.
Bank sources also list the page carry-over copy of the same line as an
alternative cell.

**Must-refuse kinds** (two per split each): unsupported metric (market data,
headcount), sub-annual period, missing period, missing scope, unknown company,
period outside the corpus, missing report (scope absent for an existing year),
a line banks do not report, a future period, and a question that injects a
number for a period without data.

**Review status.** Every case is `AUTO_DERIVABLE` until a named person approves
it. `python -m src.evaluation.gold_review packet` writes the review packets;
reviewers use the terminal (`record`) or the shared review page (`page`, then
`ingest` its exported decisions); `summarize` writes `review/summary.json`, which
the production gate reads.
