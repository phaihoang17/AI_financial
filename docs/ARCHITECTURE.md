# ARCHITECTURE.md — AI Financial Data Assistant

## 1. Architecture Style

The system uses a **hierarchical supervisor-worker architecture** with an explicit **NLU / Understanding layer** before the Supervisor.

The architecture intentionally avoids a free-form mesh in which many autonomous agents reinterpret the request and negotiate with each other.

Core idea:

- NLU understands the request,
- Supervisor plans and routes,
- specialized workers execute grounded stages,
- deterministic components handle validation and numerical execution whenever possible.

## 2. Offline Evidence Indexing Pipeline

```text
Financial Reports / OCR / HTML / Narrative Text
                    |
                    v
          +--------------------+
          | Document/Page Parse|
          +---------+----------+
                    |
          +---------+----------+
          |                    |
          v                    v
+-------------------+   +-------------------+
| Table Normalizer  |   | Paragraph Extract |
| hierarchy + cells |   | narrative spans   |
+---------+---------+   +---------+---------+
          |                       |
          +-----------+-----------+
                      |
                      v
            +----------------------+
            | Association / Linking|
            | table <-> paragraphs |
            +----------+-----------+
                       |
                       v
            +----------------------+
            | Representation Build |
            | source_type + schema |
            | period/scope/scale   |
            +----------+-----------+
                       |
               +-------+-------+
               |               |
               v               v
          +---------+      +---------+
          | BM25    |      | BGE-M3  |
          | Index   |      | Embedder|
          +---------+      +----+----+
                                |
                                v
                          +------------+
                          |Vector Index|
                          +------------+
```

Index metadata should include, when available:

- company/ticker,
- report identifier,
- page/section,
- period,
- statement scope,
- source type (`TABLE` / `TEXT`),
- table identifier,
- associated paragraph/table links,
- row/column/header paths,
- raw unit/scale hints.

## 2.1 Canonical M2A Contracts

M2A consumes the corpus as strict UTF-8 raw OCR text with
`===== PAGE N =====` markers and inline HTML tables. The normalized-table
dataset referenced by ViFinQA is not available and is not an input contract.

Common contracts:

```yaml
SourceSpan: {start: integer, end: integer}  # zero-based, end-exclusive code points
ParseIssue:
  code: string
  severity: WARNING | ERROR | FATAL
  source_span: SourceSpan | null
  message: string

HeaderPathEntry:
  header_id: string
  label: string
```

Raw source contracts:

```yaml
ReportSource:
  schema_version: m2a.v1
  corpus_id: string
  report_id: string
  source_ref: string
  ticker: string
  company_name: string | null
  report_year: integer
  document_name: string
  statement_scope: HOP_NHAT | RIENG | null
  raw_text: string
  content_sha256: string

ParsedDocument:
  report_id: string
  content_sha256: string
  pages: [Page]
  issues: [ParseIssue]

Page:
  page_id: string
  report_id: string
  page_index: integer
  page_number: integer
  source_span: SourceSpan
  content_span: SourceSpan
  raw_text: string
  tables: [SourceTable]
  issues: [ParseIssue]

SourceTable:
  table_id: string
  report_id: string
  page_id: string
  table_index: integer
  source_span: SourceSpan
  raw_html: string
  parse_status: VALID | RECOVERED | UNPARSEABLE
  inline_caption_span: SourceSpan | null
  inline_caption_text: string | null
  cells: [SourceCell]
  issues: [ParseIssue]

SourceCell:
  cell_id: string
  table_id: string
  source_row_index: integer
  source_cell_index: integer
  source_span: SourceSpan
  raw_html: string
  raw_inner_html: string
  extracted_text: string
  rowspan: integer
  colspan: integer
```

The bundled corpus currently contains 1,973 report files. Every file has the
four-level relative layout `TICKER/YEAR/DOCUMENT/FILE`; all ticker directories
are non-empty uppercase identifiers present in `code_stock.csv`, and all year
directories contain a four-digit year from 2015 through 2025. Therefore
`ReportSource.ticker` and `ReportSource.report_year` are required for this corpus.
They are copied only from these path segments and must never be inferred from
OCR text or fabricated.

Normalized table contracts:

```yaml
MergedCellAnchor:
  source_cell_id: string
  anchor_row: integer
  anchor_column: integer
  rowspan: integer
  colspan: integer

LogicalTableGrid:
  row_count: integer
  column_count: integer
  anchors: [MergedCellAnchor]
  slots: [[string | null]]  # source_cell_id of the anchor

HeaderNode:
  header_id: string
  axis: ROW | COLUMN
  label: string
  source_cell_ids: [string]
  parent_header_id: string | null
  depth: integer
  resolution: EXPLICIT | INFERRED | AMBIGUOUS

HeaderHierarchy:
  nodes: [HeaderNode]

NumericParseResult:
  status: PARSED | MISSING | NOT_NUMERIC | AMBIGUOUS | MALFORMED
  raw_text: string
  normalized_lexeme: string | null
  decimal_value: string | null
  percent_literal: boolean

NormalizedCell:
  normalized_cell_id: string
  source_cell_id: string
  table_id: string
  anchor_row: integer
  anchor_column: integer
  rowspan: integer
  colspan: integer
  normalized_text: string
  role: DATA | ROW_HEADER | COLUMN_HEADER | CORNER | UNKNOWN
  numeric: NumericParseResult
  row_path: [HeaderPathEntry]
  column_path: [HeaderPathEntry]
  issues: [ParseIssue]

NormalizedTable:
  normalized_table_id: string
  source_table_id: string
  report_id: string
  page_id: string
  normalization_version: m2a-normalization-v1
  grid: LogicalTableGrid
  header_hierarchy: HeaderHierarchy
  cells: [NormalizedCell]
  period_labels: [string]
  issues: [ParseIssue]
```

Each normalized cell represents one source/anchor cell. Covered grid positions
reference that anchor; merged values are never copied into independent cells.
Header paths remain ordered `HeaderPathEntry` values so both `header_id` and
label provenance survive serialization.

Header inference v1 is structural and conservative:

- A body row is structurally recoverable only when a contiguous, nonblank,
  non-numeric prefix on the left precedes one or more numeric data cells. An
  explicit period label is not treated as a data value when finding this
  boundary. The row-header width must agree across all recovered body rows;
  otherwise roles, hierarchy, and paths remain unresolved.
- Candidate column-header rows are the contiguous rows at the top of the table
  before the first recovered body row. A nonblank cell is a column-header
  candidate only when it overlaps a recovered data column and either spans
  multiple columns, contains non-numeric text, or is an exact v1 period label.
- Row-header candidates are the recovered non-numeric left prefix cells.
  Corner cells lie wholly inside the deterministic intersection of the top
  header band and the recovered row-header region. Blank cells never produce
  `HeaderNode` values and remain unchanged in the grid.
- For `COLUMN`, containment is evaluated over column spans and candidate
  parents must be on the nearest preceding candidate header row. For `ROW`,
  containment is evaluated over row spans and candidate parents must be in the
  nearest preceding candidate header column. One containing parent creates the
  edge, more than one creates `AMBIGUOUS`, and no parent creates a root. A
  merged span gives `EXPLICIT`; uniquely recoverable positional structure gives
  `INFERRED`.
- Header paths are emitted root-to-leaf only when every node in the path is
  uniquely recoverable. Ambiguous or unresolved paths are empty. No financial
  vocabulary, fuzzy heuristic, LLM, cross-page merge, or fabricated parent is
  used.

Period-label grammar v1 accepts only exact standalone `YYYY`, `Q1/YYYY` through
`Q4/YYYY`, `Quý 1/YYYY` through `Quý 4/YYYY`, and `Quý 1 năm YYYY` through
`Quý 4 năm YYYY` forms. Values normalize to TASK-011 conventions (`YYYY` or
`YYYY-Qn`). Missing/contextual years, relative periods, and unlabeled date
columns are not inferred. Structurally recovered labels remain in source order.

Text, association, and hint contracts:

```yaml
Paragraph:
  paragraph_id: string
  report_id: string
  page_id: string
  paragraph_index: integer
  source_span: SourceSpan
  raw_text: string
  normalized_text: string
  section_ref: string | null
  kind: HEADING | BODY | LIST | OTHER

TableTextLink:
  link_id: string
  table_id: string
  paragraph_id: string
  relation: CAPTION | EXPLICIT_REFERENCE | NOTE | ADJACENT_CONTEXT
  basis: UNIQUE_TEXT_REFERENCE | IMMEDIATE_BEFORE | IMMEDIATE_AFTER
  evidence_span: SourceSpan
  issues: [ParseIssue]

ScaleUnitHint:
  hint_id: string
  report_id: string
  page_id: string
  table_id: string | null
  source_kind: HEADER | CELL | CAPTION | TEXT
  source_ref: string
  source_span: SourceSpan
  raw_hint_text: string
  normalized_hint_text: string
  scale_candidate: THOUSAND | MILLION | BILLION | PERCENT | null
  unit_candidate: string | null
  status: EXTRACTED | AMBIGUOUS
```

M2A extracts scale/unit hints but never resolves a final scale. It never emits a
`QUESTION` hint; question-derived scale remains TASK-017/TASK-054. Captions are
first-class provenance and are not collapsed into general narrative `TEXT`.

Table-text linking v1 emits only exact same-page immediate adjacency. A
candidate requires a whitespace-only raw-source gap between the paragraph and
table. Candidates are built in both directions first; a link is emitted only
when both the table and paragraph have exactly one candidate. Its relation is
`ADJACENT_CONTEXT`, its basis is `IMMEDIATE_BEFORE` or `IMMEDIATE_AFTER`, and
its evidence span is the real paragraph span. Caption, note, and explicit-
reference paragraph recognition are deferred. Inline HTML captions never
create paragraphs or links.

Scale/unit hint extraction v1 recognizes complete occurrences of only `nghìn`,
`ngàn`, `triệu`, `tỷ`, `phần trăm`, and `%` after Unicode NFKC normalization.
It emits one hint per exact source occurrence and never parses currency or
units (`unit_candidate` is always null). `HEADER` and `CELL` use
`SourceCell.cell_id` as `source_ref`; `CAPTION` uses `SourceTable.table_id`; and
`TEXT` uses `Paragraph.paragraph_id`. `HEADER` is limited to cells participating
in `HeaderHierarchy`. A text hint receives `table_id` only through one
deterministic v1 link. When one table has distinct scale candidates, every
associated hint is retained and marked `AMBIGUOUS`; no final scale is selected.

Model-independent representation contract:

```yaml
RetrievalRepresentation:
  representation_id: string
  representation_version: m2a-representation-v1
  source_type: TABLE | TEXT
  granularity: TABLE | PARAGRAPH
  source_ref: string
  report_id: string
  page_ids: [string]
  table_id: string | null
  paragraph_id: string | null
  ticker: string
  company_name: string | null
  report_year: integer
  statement_scope: HOP_NHAT | RIENG | null
  period_labels: [string]
  content: string
  row_paths: [[HeaderPathEntry]]
  column_paths: [[HeaderPathEntry]]
  linked_source_ids: [string]
  scale_unit_hint_ids: [string]
```

V1 emits one representation per normalized table and one per paragraph. It does
not combine sources, truncate content, perform retrieval/search, generate
embeddings, or define vector storage.

Representation `content` is compact deterministic JSON with UTF-8 characters
preserved (`ensure_ascii=false`) and the field order below. Internal IDs stay in
structured metadata and never appear inside `content`.

Table content fields are `ticker`, `company_name`, `report_year`,
`statement_scope`, `period_labels`, and `cells`, in that order. Cells are sorted
by anchor row/column and contain `row_path`, `column_path`, and `text`, in that
order. Content paths contain labels only; text is `NormalizedCell.normalized_text`.
Duplicate cells are preserved and no numeric conversion, HTML, or truncation is
applied.

Text content fields are `ticker`, `company_name`, `report_year`,
`statement_scope`, `section_ref`, `kind`, and `text`, in that order. Text is
`Paragraph.normalized_text`.

Structured representation paths retain `HeaderPathEntry` values. They are
deduplicated by the full ordered `(header_id, label)` sequence while preserving
first occurrence in normalized-cell row-major order; empty paths are omitted.
Table representations link paragraph IDs, text representations link table IDs,
and both preserve deterministic source order. Hint IDs include only hints
traceable to that representation.

### M2B Embedding boundary and persistence

M2A representations remain unchanged: one `TABLE` representation is emitted per
`NormalizedTable`, and one `TEXT` representation per `Paragraph`. M2B derives
one or more embedding-only chunks without changing M2A objects:

```yaml
CellFragment:
  source_cell_id: string
  fragment_index: integer
  fragment_count: integer
  text: string
  content_token_count: integer

EmbeddingChunk:
  schema_version: m2b-embedding-chunk-v1
  chunk_id: string
  representation_id: string
  source_type: TABLE | TEXT
  chunk_index: integer
  chunk_count: integer
  primary_source_cell_ids: [string]
  context_source_cell_ids: [string]
  anchor_row_start: integer | null
  anchor_row_end: integer | null
  anchor_column_start: integer | null
  anchor_column_end: integer | null
  content: string
  content_token_count: integer
  chunking_config_fingerprint: string
  cell_fragment: CellFragment | null
```

Chunks use the pinned `BAAI/bge-m3` tokenizer revision
`5617a9f61b028005a4858fdac845db406aefb181`. Final chunk content is counted with
special tokens and no truncation. The target is 7,168 tokens; 8,192 is a
defensive hard ceiling. Tables are packed by complete logical rows, then by
consecutive anchor-column groups. Structural header context may repeat; DATA
values may repeat only as ordered fragments of the same oversized source cell.
Fragment text concatenates exactly to the original normalized cell text;
`CellFragment.content_token_count` counts fragment text without wrapper special
tokens, while `EmbeddingChunk.content_token_count` counts final serialized
content with special tokens.

Document embeddings are 1024-dimensional, float32, L2-normalized dense vectors.
TASK-029 persists immutable `IndexFlatIP` FAISS shards (100,000 vectors per
shard), SQLite provenance metadata, and a final manifest with schema,
configuration, counts, and SHA-256 file hashes. No search behavior is defined
by M2B.

The approved streaming builder consumes only the committed M2 corpus artifact
and processes one JSONL record at a time in bounded embedding batches. It keeps
only the current FAISS shard in the FAISS process, commits SQLite rows per
embedding batch, and records per-shard checkpoints with input progress, last
chunk, vector/SQLite counts, model/chunking fingerprints, and FAISS SHA-256.
Resume validates all completed checkpoints, reuses only valid completed shards,
deletes rows from the incomplete shard, and rebuilds that shard from the first
uncommitted input record. It publishes under
`artifacts/m2-vector-index-v1/artifacts/<build-id>/` only after the complete
input count, FAISS/SQLite mapping, hashes, and SQLite integrity check pass; the
top-level `manifest.json` and `CURRENT` pointer are written only after that
immutable directory is in place. The pinned embedding configuration is
`BAAI/bge-m3@5617a9f61b028005a4858fdac845db406aefb181`, fingerprint
`ebf2adc2a61d75a65db3829163a003e729095310360b9b162b570b34579585b3`, dense
document pooling, dimension 1024, float32, L2 normalization, and no
truncation.

Before embedding or retrieval deployment, the complete M2 output may be
materialized as the immutable `m2-corpus-artifact-v1`: canonical UTF-8 JSONL
shards containing each `EmbeddingChunk`, its full parent
`RetrievalRepresentation`, report provenance, and source-cell coverage. The
artifact uses deterministic report/representation/chunk order, configurable
record/byte shard bounds, per-shard SHA-256 hashes, the pinned tokenizer
revision, and a committed top-level manifest. It contains no vectors, FAISS
index, BM25 index, or search behavior.

M2 implementation is complete. The full-corpus TASK-028A audit passed with no
omitted representations or cells and no chunks above the target. TASK-028B and
TASK-029 are implemented, but the production full-corpus artifact remains an
operational build on an approved higher-capacity machine; it is not present on
the 8 GiB development host.

### M2A Normalization and Failure Invariants

- Raw source strings and HTML are immutable; derived normalized strings are
  stored separately. All spans address `ReportSource.raw_text` by zero-based,
  end-exclusive Unicode code-point offsets.
- Derived normalization uses Unicode NFKC, decodes standard HTML character
  references, converts whitespace separators to ASCII spaces, collapses inline
  whitespace, and trims it. It preserves Vietnamese diacritics and stored case.
- Numeric v1 accepts plain integers, period/space-grouped integers, comma
  decimals, period-grouped comma decimals, optional signs/accounting
  parentheses, and optional trailing percent. Comma is decimal; period is
  grouping only. Unsupported or ambiguous forms are not guessed. Parsing never
  performs scale/unit conversion. Parsed values serialize as canonical decimal
  strings, not floats.
- Missing span attributes default to one. Explicit invalid spans, overlapping
  anchors, or HTML without uniquely recoverable cell spans produce issues and no
  guessed grid. Unparseable tables retain raw HTML and expose no parsed cells.
- Paragraphs are page-local blocks outside table spans, separated by blank lines
  or table/page boundaries. Wrapped nonblank lines remain one paragraph.
- Table-text links connect a source table to an actual paragraph only through
  unique v1 immediate adjacency. Inline HTML captions remain on `SourceTable`
  and never create a synthetic paragraph or `TableTextLink`. Ambiguous
  candidates remain unlinked; proximity and semantic matching are not used.
- Serialization is UTF-8 canonical JSON with explicit nulls, enum strings,
  ordered lists, sorted object keys, compact separators, no NaN/Infinity, and
  lossless raw-string round trips.
- IDs use full, untruncated, type-prefixed SHA-256 values. Report identity uses
  corpus ID plus relative source path; child identities also include source
  content hash and source coordinates. Derived IDs include their contract
  version.

## 3. Online Query Pipeline

```text
User Question
      |
      v
+------------------------+
| NLU / Understanding    |
| Qwen3-8B + resolvers   |
+-----------+------------+
            |
            | QueryUnderstanding
            v
+------------------------+
| Supervisor / Planner   |
| Qwen3-8B               |
+-----------+------------+
            |
            | Plan
            v
+------------------------+
| Hybrid Retrieval Query |
| Builder                |
+-----------+------------+
            |
      +-----+------+
      |            |
      v            v
   BM25         BGE-M3
      |            |
      |       Vector Search
      |            |
      +-----+------+
            |
            v
         RRF/Fusion
            |
            v
 BGE-reranker-v2-m3
            |
            v
+------------------------+
| Evidence Builder       |
| TABLE / TEXT / HYBRID  |
+-----------+------------+
            |
            v
+------------------------+
| Scale / Unit Resolver  |
+-----------+------------+
            |
            v
+------------------------+
| Schema Linker          |
+-----------+------------+
            |
       complex table?
        /          \
      no            yes
      |              |
      |      +----------------------+
      |      | Table Transform      |
      |      | Planner              |
      |      | bounded op history   |
      |      +----------+-----------+
      |                 |
      +--------+--------+
               |
               v
+------------------------+
| Numeric Mask / Bind    |
+-----------+------------+
            |
            v
+------------------------+
| Programmer / Pandas    |
| Qwen2.5-Coder-14B      |
+-----------+------------+
            |
            v
+------------------------+
| Sandboxed DSL          |
+-----------+------------+
            |
            v
+------------------------+
| Verification           |
| grounding / numeric /  |
| scale-unit / financial |
+-----------+------------+
            |
      +-----+------+
      |            |
    PASS          FAIL
      |            |
      v            v
 Answer       targeted retry /
 Builder      escalation / abstain
```

### Optional Experience-Memory Sidecar

```text
Train/Dev trajectories
       |
       v
Distill successful strategies
+ failure-derived guard rules
       |
       v
Experience Memory Index
       |
 semantic relevance gate
       |
  +----+--------------------+
  |                         |
relevant                not relevant
  |                         |
  v                         v
inject bounded advice    base pipeline
into selected roles      unchanged
```

Memory is not part of the mandatory v1 path. Held-out evaluation must be read-only with respect to memory.

## 4. Component Responsibilities

## 4.1 NLU / Understanding Layer

### Responsibility

Answer: **What is the user asking?**

### Inputs

```yaml
question: string
context: optional
```

### Proposed Output Contract

```yaml
QueryUnderstanding:
  raw_question: string

  company:
    raw: string | null
    name: string | null
    ticker: string | null
    confidence: float

  periods:
    - value: string
      kind: NAM | QUY | LUY_KE
      raw: string | null

  statement_scope:
    value: HOP_NHAT | RIENG | null
    inferred: boolean
    confidence: float

  metrics:
    - raw: string
      canonical: string
      confidence: float

  operation: none | ratio | growth | aggregate | compare | unknown

  requested_scale: string | null
  requested_unit: string | null

  missing_information: [string]
  ambiguities: [string]
  confidence: float
```

This schema is a proposed separation of the NLU responsibilities already present inside the provided Supervisor design.
The source materials define the fields/semantics but do not prescribe this exact object name or schema.

### Deterministic-first Rules

Where stable dictionaries/parsers can resolve a field, prefer deterministic normalization.

Examples:

- aliases -> ticker,
- Vietnamese financial synonyms -> canonical metric,
- common year/quarter/cumulative expressions -> temporal structure.

LLM assistance can be used for linguistic ambiguity, but the structured result must still pass deterministic validation.

### Canonical Company Resolver Contracts

```yaml
CompanyAlias:
  alias: string
  name: string
  ticker: string

CompanyCandidate:
  name: string
  ticker: string

CompanyResolution:
  status: RESOLVED | UNRESOLVED | AMBIGUOUS
  company: CompanyUnderstanding
  candidates: [CompanyCandidate]
```

`CompanyUnderstanding` is the canonical nested `QueryUnderstanding.company` contract.

Company resolver invariants:

- normalization v1 trims leading/trailing whitespace and compares aliases and tickers case-insensitively,
- normalization v1 does not use fuzzy matching, typo correction, substring guessing, or an LLM,
- `RESOLVED` requires exactly one candidate; `company` matches it and has confidence `1.0`,
- `UNRESOLVED` requires no candidates; `company.name` and `company.ticker` are `null` and confidence is `0.0`,
- `AMBIGUOUS` requires at least two distinct canonical-company candidates; `company.name` and `company.ticker` are `null` and confidence is `0.0`,
- the raw input is preserved in `company.raw`.

The authoritative alias dataset and its storage mechanism remain TBD.

### Canonical Temporal Parser Contracts

```yaml
TemporalParserInput:
  text: string

TemporalResolution:
  raw: string
  period: PeriodUnderstanding | null
  confidence: float

TemporalParseResult:
  resolutions: [TemporalResolution]
```

`PeriodUnderstanding` remains the canonical nested `QueryUnderstanding.periods` item.

Normalized period values:

- year: `YYYY` with kind `NAM`,
- quarter: `YYYY-QN` with kind `QUY`, where `N` is `1..4`,
- cumulative: `YYYY-NM` with kind `LUY_KE`, where `N` is `1..12` cumulative months (for example, `2015-9M`).

Temporal parser v1 supports:

- `năm YYYY`,
- `quý N/YYYY`,
- `quý N năm YYYY`,
- `lũy kế N tháng năm YYYY`,
- `lũy kế N tháng/YYYY`.

Multiple expressions are returned in source order, with the longest non-overlapping expression taking precedence. The exact matched source text is preserved in `raw` and `period.raw`. Incomplete expressions, cumulative expressions without an explicit year, and invalid quarter/month values produce `period: null` with confidence `0.0`. Complete supported expressions produce confidence `1.0`. The parser never obtains a missing year or quarter from nearby text and does not use an LLM.

Additional temporal forms and contextual year association remain TBD.

### Canonical Statement-Scope Resolver Contracts

```yaml
StatementScopeResolverInput:
  text: string
  context_scope: HOP_NHAT | RIENG | null

StatementScopeResolution:
  status: RESOLVED | UNRESOLVED | AMBIGUOUS
  statement_scope: StatementScopeUnderstanding
```

Statement-scope resolver v1 uses Unicode NFKC normalization, trims and collapses whitespace, compares case-insensitively, and matches complete token phrases.

Explicit `HOP_NHAT` indicators:

- `hợp nhất`,
- `báo cáo tài chính hợp nhất`,
- `BCTC hợp nhất`.

Explicit `RIENG` indicators:

- `riêng`,
- `riêng lẻ`,
- `báo cáo tài chính riêng`,
- `BCTC riêng`,
- `công ty mẹ`.

Resolver invariants:

- multiple indicators for the same scope remain `RESOLVED`,
- `AMBIGUOUS` occurs only when both scope classes are matched explicitly,
- one explicit scope overrides `context_scope` and sets `inferred: false`,
- `context_scope` accepts only an already-structured `HOP_NHAT`, `RIENG`, or `null` value and is used only when no explicit indicator matches,
- a context-derived result sets `inferred: true`,
- unresolved and ambiguous results keep the scope value `null` and confidence `0.0`,
- a resolved result has confidence `1.0`,
- there is no hidden default and no LLM behavior.

Aggregated and unlabeled reports do not produce an inferred scope. Their mapping remains a deferred design decision.

### Canonical Financial-Metric Resolver Contracts

```yaml
CanonicalMetric:
  canonical: string

MetricSynonym:
  synonym: string
  canonical: string

MetricResolution:
  status: RESOLVED | UNRESOLVED | AMBIGUOUS
  raw: string
  metric: MetricUnderstanding | null
  candidates: [CanonicalMetric]
  confidence: float
```

Metric normalization v1 applies Unicode NFKC normalization, trims leading/trailing whitespace, collapses internal whitespace, and compares complete strings case-insensitively. It preserves diacritics and does not use fuzzy matching, typo correction, substring guessing, or an LLM.

The initial registry is limited to the documented example:

```text
LNST <- LNST | lãi ròng | lợi nhuận sau thuế
```

Metric resolver invariants:

- candidates are deduplicated by canonical metric,
- multiple matching synonyms for one canonical metric remain `RESOLVED`,
- one normalized synonym mapped to multiple distinct canonical metrics produces `AMBIGUOUS`,
- `RESOLVED` requires exactly one candidate, `metric.raw == raw`, a matching `metric.canonical`, and result/metric confidence `1.0`,
- `UNRESOLVED` requires no candidates, a null metric, and confidence `0.0`,
- `AMBIGUOUS` requires at least two distinct candidates, a null metric, and confidence `0.0`.

The authoritative metric vocabulary, synonym source, and storage mechanism remain TBD.

### Canonical Operation Detector Contracts

```yaml
OperationDetectorInput:
  text: string

OperationDetection:
  operation: none | ratio | growth | aggregate | compare | unknown
```

Operation detection v1 applies Unicode NFKC normalization, trims and collapses
whitespace, compares case-insensitively, and matches only complete token phrases
or the exact documented period structure. It does not use fuzzy matching, typo
correction, substring guessing, or an LLM.

The initial indicator vocabulary is deliberately limited to:

- `ratio`: `ROE`, `tỷ lệ`, `tỷ suất`,
- `growth`: `tăng bao nhiêu %`,
- `aggregate`: `trung bình`,
- `compare`: `so sánh`, `so với`, and `từ YYYY sang YYYY`.

Detector invariants:

- empty or whitespace-only input produces `unknown`,
- non-empty input with no indicator produces `none`,
- multiple indicators for the same operation remain that operation,
- the documented `tăng bao nhiêu % từ YYYY sang YYYY` growth form produces
  `growth` even though its period structure also matches `compare`,
- every other conflict across distinct operations produces `unknown`.

Additional operation indicators remain TBD and require measured examples before
the registry is expanded.

### Canonical Requested Scale/Unit Parser Contracts

```yaml
RequestedScaleUnitParserInput:
  text: string

RequestedScaleUnit:
  requested_scale: THOUSAND | MILLION | BILLION | PERCENT | null
  requested_unit: string | null
```

Requested scale/unit parsing v1 applies Unicode NFKC normalization, casefolding,
leading/trailing whitespace trimming, and internal whitespace collapsing. It
matches complete tokens or phrases only and does not use fuzzy matching, typo
correction, substring guessing, or an LLM.

The complete v1 mapping is:

- `nghìn` or `ngàn` -> `THOUSAND`,
- `triệu` -> `MILLION`,
- `tỷ` -> `BILLION`,
- `%` or `phần trăm` -> `PERCENT`.

Parser invariants:

- empty input and input without an explicit supported expression produce both
  fields as `null`,
- repeated indicators for one canonical scale remain resolved to that scale,
- indicators for multiple distinct scales produce both fields as `null`,
- `requested_unit` is always `null` in v1,
- VND, đồng, USD, and all other currency/unit expressions are not parsed,
- the result populates the existing `QueryUnderstanding.requested_scale` and
  `QueryUnderstanding.requested_unit` fields without changing that contract.

This parser captures only the user's explicit requested output scale. Evidence
scale resolution and provenance remain TASK-054 responsibilities, and the
canonical `EvidenceItem.scale` contract is unchanged.

### Canonical Planning-Gate Contract

```yaml
FindingName: COMPANY | PERIOD | METRIC

PlanningGate:
  allowed: boolean
  missing_information: [FindingName]
  ambiguities: [FindingName]
```

Company, every requested period, and every requested metric are hard-required.
An unresolved finding is added to `missing_information`; an ambiguous finding is
added to `ambiguities`. Findings are deduplicated and ordered as `COMPANY`,
`PERIOD`, `METRIC`. `operation: unknown` blocks planning without adding a finding,
while `operation: none` is valid. The gate consumes resolver outputs without
modifying them. Statement-scope eligibility is deferred.

`PlanningGate.allowed: true` means only that these v1 hard requirements and the
operation gate passed. It does not guarantee that the canonical `Plan` can already
be built.

### Canonical NLU Evaluation Contracts

```yaml
NLUEvaluationField:
  COMPANY
  | PERIODS
  | STATEMENT_SCOPE
  | METRICS
  | OPERATION
  | MISSING_INFORMATION
  | AMBIGUITIES

NLUFieldComparison:
  field: NLUEvaluationField
  matches: boolean

NLUEvaluationCase:
  case_id: string
  question: string
  expected: QueryUnderstanding

NLUEvaluationResult:
  case: NLUEvaluationCase
  actual: QueryUnderstanding
  field_comparisons: [NLUFieldComparison]
```

`NLUEvaluationCase.question` must equal `expected.raw_question`.
`actual.raw_question` is neither required to equal the case question nor scored,
and it must not invalidate an otherwise valid evaluation result. The overall
`QueryUnderstanding.confidence`, `requested_scale`, and `requested_unit` fields
are also outside the v1 scored semantic fields.

The seven approved fields are compared exactly and structurally, with one
comparison per field in the canonical order shown above. The evaluator performs
no normalization and uses no LLM judge. V1 provides only a small approved fixture
set; thresholds and aggregate scoring remain TBD.

### Exit Conditions

If required identity cannot be safely resolved:

```text
NLU -> clarification/early abstain
```

Do not continue into expensive retrieval/programming when core identity is missing.

## 4.2 Supervisor / Planner

### Responsibility

Answer: **What must the system do to answer the understood request?**

The Supervisor is the routing brain.
It does not calculate or directly read numeric evidence to produce the final result.

### Canonical Plan Contract

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
  tables_needed: [BALANCE_SHEET | INCOME_STATEMENT | CASH_FLOW_STATEMENT | NOTES]
  retrieval_requirements:
    - requirement_id: string
      source_type: TABLE | TEXT
      table_class: BALANCE_SHEET | INCOME_STATEMENT | CASH_FLOW_STATEMENT | NOTES | null
      metric: string | null
      period: string | null
      required: boolean
  evidence_sources: [TABLE | TEXT]
  reasoning_mode: DIRECT | PROGRAM | TABLE_TRANSFORM
  requires_scale_resolution: boolean
  model_tier: CHEAP | STRONG
  verify_profile: LIGHT | STRICT
  max_retries: integer
  confidence: float
  abstain: boolean
  abstain_reason: string | null
```

`Plan` is executable and therefore always keeps `abstain: false` and
`abstain_reason: null`. Early refusal is represented outside it:

```yaml
SupervisorResult:
  planning_gate: PlanningGate
  plan: Plan | null
  abstain: boolean
  abstain_reason: PLANNING_GATE_BLOCKED | UNKNOWN_OPERATION
    | UNSUPPORTED_QUESTION_TYPE | UNSUPPORTED_RATIO | MISSING_FORMULA
    | MISSING_TABLE_MAPPING | MISSING_STATEMENT_SCOPE
    | INVALID_PERIOD_REQUIREMENT | MIXED_PERIOD_KIND_UNSUPPORTED | INVALID_PLAN
    | null
  input_fingerprint: string
```

A blocked gate cannot produce a Plan. Requirement IDs are deterministic over
the requirement payload; exact duplicates are rejected. `tables_needed`,
`target_metrics`, `periods`, and `evidence_sources` are derived from ordered
requirements rather than independently authored.

M4 v1 uses homogeneous requested period kinds and never invents a prior year,
quarter, or cumulative period. Explicit requirements are additive in M4 Batch
1; M3 behavior remains unchanged until a separately approved integration uses
them instead of metric-by-period Cartesian expansion.

### Canonical M4 v1 Registries

```yaml
TableClass: BALANCE_SHEET | INCOME_STATEMENT | CASH_FLOW_STATEMENT | NOTES

MetricTableMapping:
  metric: string
  table_class: TableClass

FormulaDefinition:
  formula_id: string
  operation: ratio | growth | aggregate
  question_type: DERIVED_RATIO | MULTI_PERIOD | AGGREGATE
  derived_target: string
  required_metrics: [string]
  period_rule: SINGLE | EXACT_TWO | AT_LEAST_TWO
  reasoning_mode: PROGRAM
  evidence_sources: [TABLE | TEXT]
```

The M4 v1 metric-table registry contains only `LNST -> INCOME_STATEMENT`.
The formula registry contains `GROWTH_RATE` (exactly two requested periods) and
`AVERAGE` (at least two requested periods). Their empty `required_metrics`
lists mean they operate on the single requested target metric; they do not
invent operands. No ratio formula is registered.

### Canonical M4 v1 deterministic routing

Reasoning mode is selected before model tier:

- formula-free `LOOKUP` -> `DIRECT`,
- formula-free `MULTI_PERIOD` comparison -> `DIRECT`,
- `GROWTH_RATE` and `AVERAGE` -> `PROGRAM`,
- a registered ratio uses `FormulaDefinition.reasoning_mode`, currently
  `PROGRAM`.

`TABLE_TRANSFORM` is disabled and no M4 v1 path may emit it. Model-tier routing
is `DIRECT -> CHEAP` and `PROGRAM -> STRONG`. A future explicit registry
requirement may upgrade `CHEAP` to `STRONG`, but it may never downgrade a
required `STRONG` tier.

Verification is `LIGHT` only when the plan is a formula-free direct `LOOKUP`
with exactly one required `RetrievalRequirement`. `PROGRAM`, any formula,
`DERIVED_RATIO`, `MULTI_PERIOD`, `AGGREGATE`, or more than one required
requirement routes to `STRICT`.

Evidence-source precedence is formula metadata, then explicit requirement
source types, then the approved evidence-policy registry. The v1 numeric
financial-metric policy is `TABLE`; `GROWTH_RATE` and `AVERAGE` also require
only `TABLE`. `Plan.evidence_sources` is the ordered deduplicated source set
derived exactly from `Plan.retrieval_requirements`, so no unsupported source is
added. No generic narrative policy is approved in M4 v1.

These routers are pure deterministic functions. They do not use confidence,
retrieval results or scores, free-form wording, table size, token count, or an
LLM. `requires_scale_resolution` remains true when `TABLE` is required or the
question has a non-null requested scale or requested unit.

### Canonical Supervisor Evaluation Contracts

TASK-047 evaluates `QueryUnderstanding + PlanningGate -> SupervisorResult`
without invoking retrieval, execution, verification, final-answer generation,
an LLM, or a GPU.

```yaml
ExpectedRetrievalRequirement:
  source_type: TABLE | TEXT
  table_class: BALANCE_SHEET | INCOME_STATEMENT | CASH_FLOW_STATEMENT | NOTES | null
  metric: string | null
  period: string | null
  required: boolean

SupervisorField:
  abstain
  | question_type
  | reasoning_mode
  | formula_id
  | target_metrics
  | periods
  | tables_needed
  | evidence_sources
  | retrieval_requirements
  | requires_scale_resolution
  | model_tier
  | verify_profile

SupervisorEvaluationCase:
  case_id: string
  version: positive integer
  query_understanding: QueryUnderstanding
  planning_gate: PlanningGate
  expected:
    abstain: boolean
    abstain_reason: SupervisorAbstainReason | null
    question_type: QuestionType | null
    reasoning_mode: ReasoningMode | null
    formula_id: string | null
    target_metrics: [string]
    periods: [string]
    tables_needed: [TableClass]
    evidence_sources: [EvidenceSource]
    retrieval_requirements: [ExpectedRetrievalRequirement]
    requires_scale_resolution: boolean | null
    model_tier: ModelTier | null
    verify_profile: VerifyProfile | null

SupervisorFieldComparison:
  field: SupervisorField
  matches: boolean

SupervisorEvaluationCaseResult:
  case: SupervisorEvaluationCase
  actual: SupervisorResult
  field_comparisons: [SupervisorFieldComparison]
  passed: boolean
```

Comparisons are exact and structural. Ordered lists are order-sensitive. The
evaluator performs no normalization, fuzzy matching, synonym expansion, or
LLM judging. `ExpectedRetrievalRequirement` deliberately omits the derived
`requirement_id`; all remaining requirement fields compare exactly in order.

For an expected abstention, the `abstain` comparison passes only when actual
`abstain` is true, actual `plan` is null, and the typed `abstain_reason` matches.
Plan fields in an abstention expectation are null or empty and compare against
the absent actual plan.

The aggregate fixture report contains case, pass, and failure counts; exact-
match rate; per-field accuracy for all 12 fields; and abstain precision/recall.
No production threshold is defined. The approved v1 fixture set contains 14
deterministic cases covering valid lookup/compare/formula routes and every
requested early-abstain boundary. It uses only the current `LNST` table mapping
and the registered `GROWTH_RATE` and `AVERAGE` formulas; unsupported inputs are
present only to verify abstention.

The CPU-only command is:

```bash
python -m src.evaluation.run_supervisor_eval --mode fixture
```

### Question Classification

```text
LOOKUP
DERIVED_RATIO
MULTI_PERIOD
AGGREGATE
```

### Planning Dimensions

The Supervisor plans at least:

- number/type of tables,
- number/type of periods,
- statement scope,
- target metrics,
- formula target where applicable,
- model tier,
- verification strictness.

### Example: Growth

```yaml
question_type: MULTI_PERIOD
company: AAA
periods: [2014, 2015]
target_metrics:
  - LNST
derived_target: GROWTH
formula_id: GROWTH_RATE
tables_needed:
  - INCOME_STATEMENT
model_tier: STRONG
verify_profile: STRICT
```

Ratio planning abstains in M4 v1 because no metric-specific ratio formula is
registered. The Supervisor never authors formulas or performs this arithmetic.

## 4.3 Retrieval Layer

### Responsibility

Retrieve supporting evidence required by the `Plan` from both tables and narrative text.

Logical subcomponents:

```text
Retrieval Query Builder
        |
   +----+----+
   |         |
 BM25      BGE-M3
   |         |
   |     Vector Search
   |         |
   +----+----+
        |
      RRF
        |
BGE-reranker-v2-m3
        |
 TABLE/TEXT candidates
```

### Batch-1 retrieval contracts and gate

`RetrievalQuery` is built deterministically from the approved `Plan` and
`QueryUnderstanding`. It preserves the raw question, resolved company,
periods, scope, canonical metrics, requested source types, canonical
`eligible_source_types`, and requested scale/unit. Its query-text list contains
the raw question plus exact canonical field values only; it never paraphrases
or adds aliases.

`RetrievalCandidate` is the common contract for sparse search, dense search,
fusion, reranking, multi-table retrieval, and evidence building. It preserves
chunk-to-representation-to-report/table-or-paragraph provenance, page IDs, and
hierarchical header paths. Its identity is derived from source type plus
`chunk_id` (or `representation_id` when no chunk exists), not a score or rank.
BM25, vector, RRF, and reranker scores remain separate fields. Every scored
stage orders by its own score descending, then `candidate_id` ascending.

Before a retrieval backend is opened, the compatibility gate validates the
committed M2 corpus and M2B artifact metadata exactly: schema versions,
chunking and embedding fingerprints, model/revision, vector configuration,
FAISS metric/index version, and SQLite metadata version. Any mismatch or
missing required metadata is a typed hard failure. Compatible fixture and
smoke artifacts are valid for integration tests; production-scale vector
validation remains GPU-dependent.

The report-level metadata filter applies only resolved ticker, explicit
statement scope, and report year derived from canonical period strings. It is
exact, preserves candidate order, and returns an empty result rather than
relaxing filters.

### Batch-2.5 provenance sidecar and source eligibility

The canonical M2 corpus remains unchanged.  Batch 2.5 publishes the additive,
immutable `m2-provenance-sidecar-v1` artifact, whose manifest records the exact
M2 corpus identity, schema versions, counts, and hashes.  Its SQLite database
persists the complete M2A `ScaleUnitHint` and `TableTextLink` contracts,
including canonical JSON and indexed source/table/paragraph references.  The
sidecar builder rejects a publish unless every `scale_unit_hint_id` and every
listed TABLE--TEXT link in the materialized M2 representations resolves in the
sidecar.  Read-only lookup APIs support lookup by hint ID, source reference,
table ID, and paragraph ID.

Source eligibility is a separate retrieval constraint from TASK-030's exact
report metadata filter.  Each BM25 or vector request resolves a non-empty,
first-occurrence-preserving set of `TABLE` and/or `TEXT` source types (defaulting
to the query's requested evidence sources).  BM25 applies it in its SQLite FTS
query and vector search applies it in its SQLite vector-ID selection, both
before native per-query top-k selection.  Neither backend relaxes this filter.

### Batch-2 lexical and dense search contracts

TASK-031 indexes each `EmbeddingChunk` as one independent `RetrievalCandidate`
source in a persisted SQLite FTS5 artifact.  TABLE and TEXT chunks remain
separate entries.  The v1 FTS tokenizer is SQLite `unicode61` with
`remove_diacritics 0`: it performs deterministic Unicode tokenization,
preserves Vietnamese diacritics, and permits deterministic case-insensitive
matching without stemming, fuzzy matching, word segmentation, synonym
expansion, or LLM processing.  The published index manifest has schema version
`m3-bm25-index-v1`, a corpus fingerprint, a deterministic logical index
fingerprint, and SQLite file hash.  BM25's native lower-is-better score is
negated so public `bm25_score` has canonical higher-is-better semantics.
Every deterministic query text is searched independently; duplicate candidates
retain their highest BM25 score, then final results sort by score descending and
`candidate_id` ascending.  Empty tokenized query text returns no candidates.

TASK-032 creates one dense-only `QueryEmbedding` for each
`RetrievalQuery.query_texts` item, preserving source order and its zero-based
query-text index.  It uses the same pinned BGE-M3 model, revision, tokenizer,
pooling, 1024-dimensional float32 vector representation, L2 normalization,
and approved M2B embedding fingerprint as document embeddings.  Input over the
approved model limit is a typed failure; query embedding never truncates,
retrieves, or creates document embeddings.

TASK-033 validates the complete M2 corpus and M2B vector artifact through the
TASK-03C gate before serving.  It applies TASK-030's exact metadata constraints
and source eligibility once to SQLite metadata to identify eligible IDs, groups
them deterministically by shard, and constructs an exact temporary
`IndexIDMap2(IndexFlatIP(1024))`
over only those vectors for each query embedding.  This gives exact filtered
inner-product (cosine-equivalent) search without oversampling, automatic filter
relaxation, or partial-shard tolerance.  All compatible shards must validate
and participate; a missing, corrupt, or incompatible shard is a typed hard
failure.  Results merge duplicate candidates by their best `vector_score`,
retain per-query provenance for diagnostics, and sort by score descending then
`candidate_id` ascending.  RRF is deliberately not part of this stage.

### Batch-3 fusion, reranking, and retrieval views

TASK-034 accepts independently canonical BM25 and vector ranked lists.  It
validates one-based contiguous backend ranks and unique candidate IDs, merges
only provenance-compatible duplicates, and computes unweighted reciprocal rank
fusion as `sum(1 / (60 + rank_i))`.  It preserves both backend score fields,
sets only `rrf_score`, and applies top-k only after complete fusion.  RRF output
orders by `rrf_score` descending then `candidate_id` ascending.

TASK-035 reranks TASK-034 output with the exact
`BAAI/bge-reranker-v2-m3` revision
`953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`, using
`XLMRobertaTokenizerFast`.  Its configuration fingerprint covers model,
revision, tokenizer, 8,192-token pair maximum, no-truncation policy, and raw
`logits[:, 0]` scoring.  Each pair is the raw user question and candidate
content; oversized pairs fail with `RERANKER_INPUT_TOO_LONG`.  No sigmoid,
softmax, score normalization, or partial rerank is permitted.  Output preserves
prior scores and orders by rerank score, RRF score, then candidate ID.

TASK-037 decomposes multi-table requests deterministically.  It creates a
TABLE-only required subquery for every metric--period pair (metric outer,
period inner), or one per present dimension when one dimension is absent.  Each
subquery runs metadata filtering, BM25, vector retrieval, RRF, and reranking
independently.  A zero-result or typed backend failure remains visible in
`missing_subquery_ids`; successful subqueries are retained and the result is
never marked complete incorrectly.

TASK-03D is the parallel TEXT-only view of the same pipeline.  It preserves
TEXT/paragraph provenance and reranker order.  Linked table IDs are exposed
only from persisted `TableTextLink` records in the M2 provenance sidecar;
unlinked paragraphs remain valid and receive no synthetic association.

TASK-03F enriches retrieved candidates with persisted scale/unit hints without
selecting a scale or converting a number.  TABLE-direct hints must originate in
the chunk's primary/context source-cell IDs or its exact table caption; TEXT-
direct hints must originate in its paragraph ID.  Linked hints require a
persisted table--text link and retain `LINKED`, rather than being relabelled
`DIRECT`.  Conflicting hints and requested-scale mismatches remain present in
deterministic span/ID order for later verification.

### Batch-4 exact evidence boundary

TASK-038 resolves TABLE candidates only through their committed M2 chunk's
primary/context source-cell IDs. It reconstructs the source-backed normalized
cell, source cell, table, page, and report chain, never searching another cell
in the parent table. Exact normalized row/column labels may match a canonical
metric; period labels must match an existing canonical period exactly. TEXT
candidates intentionally produce no `CellLocation`.

TASK-039 builds `EvidenceItem` values only from those exact locations or an
exact paragraph. `normalized_value` is `CanonicalDecimal | null`, the lossless
JSON decimal string from `NumericParseResult.decimal_value`; it is never cast
to binary float, scaled, or divided for percentages at this boundary. TEXT
table associations are accepted only when supplied from a persisted
`TableTextLink` result.

TASK-03E checks requirement presence rather than answer correctness. Required
TABLE metric/period combinations and explicitly requested TEXT evidence are
checked independently. Structural or unresolved cell locations cannot satisfy
an exact TABLE requirement; missing requirements preserve valid evidence and
do not trigger filter relaxation or abstention here.

### Hybrid evidence policy

The plan may request:

- `TABLE`,
- `TEXT`,
- both (`HYBRID`).

For hybrid questions, do not force all facts into one representation. Preserve source boundaries and links between table evidence and associated narrative.

### Retrieval priorities

Guard against:

- wrong company/report,
- wrong period,
- wrong consolidated/standalone scope,
- wrong table,
- wrong paragraph/text span,
- wrong hierarchical row/header path,
- insufficient cross-source evidence,
- omitted scale/unit context.

### Evidence budget

More context is not automatically better. FinQA-style retriever-generator results motivate retrieving compact supporting facts rather than passing the whole document to the program generator.

The exact `top_k` is an evaluated configuration, not a hardcoded constant.

## 4.4 Evidence Builder

### Responsibility

Turn retrieval candidates into the smallest sufficient, traceable evidence bundle for downstream reasoning.

### Proposed Evidence Contract

```yaml
EvidenceItem:
  evidence_id: string
  source_type: TABLE | TEXT

  report_ref: string
  page_ref: string | null
  table_ref: string | null
  associated_table_ref: string | null

  statement_scope: HOP_NHAT | RIENG | null
  period: string | null
  metric: string | null

  row_label: string | null
  column_label: string | null
  row_path: [string]
  column_path: [string]

  text_span: string | null

  raw_value: string | number | null
  normalized_value: CanonicalDecimal | null  # canonical JSON decimal string

  unit: string | null
  scale: RAW | THOUSAND | MILLION | BILLION | PERCENT | OTHER | null
  scale_source: HEADER | CELL | CAPTION | TEXT | QUESTION | null

  retrieval_score: float | null
  rerank_score: float | null
```

### Constraint

`CanonicalDecimal` is exactly the validated canonical decimal string emitted by
`NumericParseResult.decimal_value`; Evidence/retrieval do not cast it to a
binary float or change percent literals. The later arithmetic execution layer
may construct `decimal.Decimal(CanonicalDecimal)` inside its approved sandbox.
Scale/unit resolution remains a separate later stage.

Do not pass a large unstructured top-k dump to the Programmer when a smaller grounded bundle can be built first.

A hybrid bundle may contain both a table cell/row and a narrative span when the calculation requires both.

## 4.5 Scale / Unit Resolver

### Responsibility

Resolve numeric magnitude and units before program execution.

Document source scale/unit may be stated in:

- a table header,
- a row/column label,
- an associated paragraph.

The user question may instead state the requested output scale/unit. It does
not redefine the document source scale/unit.

The resolved value must retain provenance so Verification can audit the decision.

### Canonical Batch 1 contract

```yaml
ScaleUnitResolution:
  evidence_id: string
  status: RESOLVED | UNRESOLVED | AMBIGUOUS
  source_scale: RAW | THOUSAND | MILLION | BILLION | PERCENT | null
  source_unit: string | null
  requested_output_scale: THOUSAND | MILLION | BILLION | PERCENT | null
  requested_output_unit: string | null
  winning_hint_ids: [string]
  considered_hint_ids: [string]
```

`source_scale` describes the document evidence. `requested_output_scale` is
copied from `QueryUnderstanding.requested_scale`; neither value overwrites the
other. TASK-054 performs no numeric conversion and leaves `CanonicalDecimal`
unchanged.

Source precedence v1 is direct cell, direct header, direct caption, direct
text, then linked hints. At the highest level with a usable clue, one unique
scale/unit resolves, conflicting values are ambiguous, and no usable clue is
unresolved. Missing scale never defaults to `RAW`. A percent literal in the
exact numeric cell is a direct-cell `PERCENT` clue. All candidate-scoped hint
IDs remain in `considered_hint_ids`; only a resolved highest-precedence set is
listed in `winning_hint_ids`.

TASK-055 regression coverage fixes this behavior across `RAW`, `THOUSAND`,
`MILLION`, `BILLION`, and `PERCENT`; all direct/linked hint sources; the full
precedence chain; conflict and missing-hint outcomes; source/request
separation; value immutability; provenance; deterministic repetition; and the
required-scale numeric-masking gate. It changes no TASK-054 contract or
production behavior.

## 4.6 Schema Linking

Map natural-language concepts to actual table structure.

Examples:

```text
"lãi ròng"
     -> canonical metric
     -> actual row/header path
```

Schema linking should preserve hierarchical context when a label alone is ambiguous.

### Canonical Batch 1 contract

```yaml
SchemaLinkResult:
  requirement_id: string
  evidence_id: string
  status: RESOLVED | UNRESOLVED | AMBIGUOUS
  metric: string | null
  period: string | null
  row_path: [HeaderPathEntry]
  column_path: [HeaderPathEntry]
  matched_source_cell_id: string | null
  match_basis: EXACT_METRIC_PATH | EXACT_PERIOD_PATH |
               EXACT_METRIC_AND_PERIOD | STRUCTURAL | NONE
```

The linker consumes ordered `Plan.retrieval_requirements` and only the supplied
`EvidenceItem -> CellLocation` boundary. Metric labels use the existing
canonical metric registry; periods require exact canonical string equality.
There is no new synonym registry and no fuzzy, semantic, or LLM fallback.
Exactly one valid location resolves, multiple valid locations are ambiguous,
and no valid location is unresolved. Result paths retain the structured
`HeaderPathEntry` hierarchy; existing flattened `EvidenceItem` path fields are
not changed.

Batch 1 stage order is:

```text
Plan + Evidence
  -> scale/unit resolution
  -> schema linking
  -> later numeric masking/binding
```

These stages never scan outside already-retrieved M3 evidence and never mutate
raw M2/M3 provenance.

## 4.7 Numeric Masking / De-lexicalization

### Goal

Reduce numeric hallucination and copied constants during code generation.

### Flow

```text
real values
   -> symbolic placeholders
   -> program generation
   -> deterministic value binding
   -> execution
```

Example concept:

```text
val_<full_sha256>
```

### Canonical Batch 2 contracts

```yaml
MaskedEvidence:
  placeholder: string
  evidence_id: string
  requirement_id: string
  metric: string | null
  period: string | null
  row_path: [HeaderPathEntry]
  column_path: [HeaderPathEntry]

MaskedEvidenceBundle:
  items: [MaskedEvidence]

ValueBinding:
  placeholder: string
  evidence_id: string
  value: CanonicalDecimal
  source_scale: RAW | THOUSAND | MILLION | BILLION | PERCENT | null
  source_unit: string | null
  requested_output_scale: THOUSAND | MILLION | BILLION | PERCENT | null
  requested_output_unit: string | null

BindingMap:
  bindings: [ValueBinding]
```

The masking contract version is `m5-numeric-masking-v1`. A placeholder is
`val_` followed by the full lowercase SHA-256 of canonical JSON containing only
that contract version and `evidence_id`. Reuse of one evidence ID therefore
reuses one placeholder; different evidence IDs do not share placeholders.

Only a resolved `SchemaLinkResult` backed by an exact supplied
`EvidenceItem -> CellLocation` chain and a `CanonicalDecimal` can be masked.
Required ambiguous, unresolved, missing, or non-numeric evidence is a typed
failure. When `Plan.requires_scale_resolution` is true, scale resolution must
also be present and `RESOLVED`; missing scale never silently becomes `RAW`.

`MaskedEvidenceBundle` is the only programmer-facing numeric evidence contract.
It contains neither `raw_value` nor `normalized_value`. `BindingMap` is
execution-only and is built from the approved mask plus the original exact
grounded evidence. It preserves `CanonicalDecimal` as a string and copies
resolved scale/unit metadata without conversion. It accepts no program or code
source and performs no source substitution. A later Sandbox may explicitly
construct `decimal.Decimal` at its approved arithmetic boundary.

The complete Batch 2 boundary is:

```text
Plan
  -> ScaleUnitResolution
  -> SchemaLinkResult
  -> MaskedEvidenceBundle       # Programmer-facing, no values
  -> BindingMap                 # execution-only
  -> later Sandbox
```

Every binding retains the auditable chain `placeholder -> evidence_id -> exact
EvidenceItem -> CellLocation -> source provenance`. Original `EvidenceItem` and
M2/M3 values/provenance are never mutated.

## 4.8 Complex Table Reasoning Fallback

### Responsibility

Handle cases where one-shot reasoning over a static complex table is unreliable.

Inspired by Chain-of-Table, use a bounded operation pool such as:

- `select_row`,
- `select_column`,
- `add_column`,
- `group_by`,
- `sort_by`.

At each step:

1. inspect the latest intermediate table,
2. select one permitted operation,
3. generate/validate its arguments,
4. execute the operation deterministically,
5. append `(operation, args)` to history,
6. stop at an explicit terminal state or retry budget.

This is an optional targeted mode selected by `Plan.reasoning_mode = TABLE_TRANSFORM`; it is not the default path and is not an unbounded reflection loop.

## 4.9 Programmer / Symbolic Program Generator

### Responsibility

Generate constrained symbolic logic from:

```text
Plan + MaskedEvidenceBundle + FormulaRegistry fingerprint
```

The Programmer should not independently perform open-ended retrieval.

### Canonical M6 Batch 1 contracts

```yaml
ProgrammerInput:
  plan: Plan
  masked_evidence: MaskedEvidenceBundle
  formula_registry_fingerprint: string

ProgrammerResult:
  status: GENERATED | REJECTED
  program: Program | null
  failure_code: string | null
  failure_message: string | null

Program:
  schema_version: m6-program-v1
  program_id: string
  formula_registry_fingerprint: string
  question_type: LOOKUP | DERIVED_RATIO | MULTI_PERIOD | AGGREGATE
  formula_id: string | null
  inputs: [ProgramInput]
  steps: [ProgramStep]
  output_ref: string
  output_kind: SCALAR | ORDERED_VALUES

ProgramInput:
  input_id: string
  placeholder: string
  evidence_id: string
  requirement_id: string

ProgramStep:
  step_id: string
  operation: IDENTITY | COLLECT | APPLY_REGISTERED_FORMULA
  input_refs: [string]
  formula_id: string | null
```

The only M6 v1 DSL forms are `IDENTITY(ref)`, `COLLECT(ref+)`, and
`APPLY_REGISTERED_FORMULA(formula_id, ref+)`. References address Program input
IDs or earlier step IDs in one shared namespace. No numeric literal, source
code, import, arbitrary arithmetic, arbitrary call, attribute access,
filesystem/network operation, `eval`, or `exec` is representable.

`ProgrammerInput` has an exact schema and cannot contain `BindingMap`.
`ProgramInput` binds only a M5 placeholder and its evidence/requirement
identities. Every required masked evidence item must be declared and must be in
the dependency closure of the single `output_ref`. Unknown placeholders,
duplicate IDs/bindings, non-topological references, and invalid outputs are
typed validation failures.

Program IDs are the full lowercase SHA-256 of canonical JSON over all symbolic
Program fields except `program_id`. Canonical serialization uses UTF-8 JSON,
sorted object keys, compact separators, explicit nulls, ordered lists, and no
NaN/Infinity. Neither Program identity nor serialization accepts or depends on
`BindingMap` values.

### Formula implementation boundary

```yaml
FormulaImplementation:
  schema_version: m6-formula-implementation-v1
  formula_id: string
  implementation_kind: PERCENT_GROWTH | ARITHMETIC_MEAN
  input_order: [string]
  min_arity: positive integer
  max_arity: positive integer | null
  constants: [CanonicalDecimal]
```

The existing FormulaRegistry remains the single source of truth. Its
fingerprint covers each ordered `FormulaDefinition` and matching
`FormulaImplementation`. Definitions and implementations must have exactly the
same unique formula IDs.

Approved v1 implementations are:

- `GROWTH_RATE`: `PERCENT_GROWTH`, ordered inputs `previous, current`, exact
  arity two, semantics `(current - previous) / previous * 100`, and the only
  approved constant `100`.
- `AVERAGE`: `ARITHMETIC_MEAN`, ordered variadic `values`, minimum arity two,
  and arithmetic-mean semantics.

Constants may exist only in approved `FormulaImplementation` metadata, never
in Program. No ratio implementation is registered. Batch 1 defines and
validates these contracts but performs no model inference, formula execution,
numeric parsing, scale conversion, or sandbox execution.

### M6 v1 validation boundary

Before later sandbox execution, validation checks exact schema,
Plan/question/formula consistency, registry fingerprint and registration,
formula arity and Plan requirement order, placeholder membership, complete
required-evidence consumption, unique IDs/bindings, topological references,
operation allowlisting, one valid output, canonical Program identity, and
deterministic serialization. Failures use `ProgramValidationFailureCode`.

`CanonicalDecimal` remains a string through M5 and the Program boundary.
TASK-066 conversion infrastructure may construct `decimal.Decimal` internally
and must serialize its result back to `CanonicalDecimal`; it does not execute a
Program or mutate a binding. M7 remains the first approved Program-execution
boundary. Binary float is not part of either contract. TASK-065 reuses the M2
numeric parser rather than creating a second Vietnamese-number parser.

### Canonical M6 Batch 2 generation

M6 Batch 2 is a deterministic symbolic generator. Its complete input is one
`ProgrammerInput`; it has no `BindingMap`, model, retrieval, raw evidence,
numeric parser, scale converter, or execution dependency. It first revalidates
the nested `Plan + MaskedEvidenceBundle`, maps exactly one masked item for each
required retrieval requirement, and orders Program inputs by
`Plan.retrieval_requirements` rather than bundle arrival order.

The complete v1 generation table is:

| Plan shape | Program step | Output kind |
|---|---|---|
| `LOOKUP`, `formula_id=null` | `IDENTITY(input)` | `SCALAR` |
| `MULTI_PERIOD`, `formula_id=null` | `COLLECT(inputs)` | `ORDERED_VALUES` |
| `MULTI_PERIOD`, `GROWTH_RATE` | `APPLY_REGISTERED_FORMULA(GROWTH_RATE, inputs)` | `SCALAR` |
| `AGGREGATE`, `AVERAGE` | `APPLY_REGISTERED_FORMULA(AVERAGE, inputs)` | `SCALAR` |

`DERIVED_RATIO` is rejected with `UNSUPPORTED_DERIVED_RATIO` because no ratio
formula is registered. The Programmer never authors or infers a replacement.
Missing required evidence, multiple candidates for one required requirement,
unknown requirements, invalid nested placeholders, unsupported shapes, and
unsupported formulas return typed `ProgrammerResult.REJECTED` outcomes.

Every constructed Program is passed through the TASK-068 validator before a
`GENERATED` result is returned. A validator failure is returned as `REJECTED`
with the exact typed `ProgramValidationFailureCode`. Program inputs and IDs are
therefore derived only from approved symbolic placeholder/evidence/requirement
identities; financial values and numeric constants cannot affect generation.

TASK-053 tests this boundary against real generated Programs and real M5
masking output. They mutate binding values, placeholders, evidence coverage,
formula IDs, and symbolic references. The tests establish that BindingMap
values cannot change Program serialization or identity, all required evidence
must reach the output, and the growth constant `100` exists only in the
approved `FormulaImplementation` metadata.

### Canonical M6 Batch 3 numeric and trace boundaries

TASK-065 exposes only an adapter over the existing M2
`parse_numeric_string -> NumericParseResult` contract. A `PARSED` result is
wrapped as the existing string subtype `CanonicalDecimal`. `MISSING`,
`NOT_NUMERIC`, `AMBIGUOUS`, and `MALFORMED` are raised as
`NumericBoundaryError` with the unchanged `NumericParseStatus` as its typed
code. The adapter adds no numeric grammar and performs no float conversion.

TASK-066 returns this exact result contract:

```yaml
ScaleConversionResult:
  status: SUCCESS | REJECTED
  canonical_value: CanonicalDecimal | null
  output_scale: RAW | THOUSAND | MILLION | BILLION | PERCENT | OTHER | null
  output_unit: string | null
  failure_code: INVALID_INPUT | SOURCE_SCALE_REQUIRED |
                UNSUPPORTED_SCALE_CONVERSION |
                UNSUPPORTED_UNIT_CONVERSION | null
  failure_message: string | null
```

Magnitude conversion uses `decimal.Decimal` only and computes
`value * source_factor / requested_factor` with factors `1`, `1000`,
`1000000`, and `1000000000`. It serializes exactly back to
`CanonicalDecimal` without rounding. A null requested scale preserves the
source value and scale. Requested conversion requires a resolved source scale.
`PERCENT -> PERCENT` is identity; percent/magnitude conversion is rejected.
Units are either preserved or required to match exactly; currency/FX
conversion is not supported. The utility receives scalar contract fields and
does not mutate `EvidenceItem`, `ValueBinding`, or `BindingMap`.

TASK-069 compares Programs independently of execution. Exact `Program`
equality yields `EXACT`. A caller may then supply the deterministic
`normalized_program_equivalence` `TraceEquivalenceHook`, which ignores only
`program_id`, `ProgramInput.input_id`, and `ProgramStep.step_id` while
normalizing their references. It retains schema/fingerprint, question type,
top-level and step formula IDs, ordered placeholder/evidence/requirement
inputs, ordered operations and input references, output reference topology,
and output kind. Any difference in operand order, grounding, formula,
operation, or output is `DIFFERENT`. `evaluate_program_trace` packages that
comparison in the existing `ReasoningEvaluationResult`, but requires
execution and answer correctness to be supplied by their independent future
evaluators; it does not derive either outcome.

TASK-067 remains `BLOCKED_BY_M7_EXECUTION_BOUNDARY`. Batch 3 defines no oracle
execution fixture scoring, formula execution, sandbox execution, or model
inference.

FinQA-style evaluation suggests reporting both program/trace correctness and final execution correctness.

### Failure Types

- invalid schema or forbidden code/literal,
- wrong evidence or placeholder reference,
- missing required evidence,
- invalid topological reference or output,
- formula registration, arity, or ordering mismatch,
- non-canonical identity or serialization.

These failures should be classified for targeted retry.

## 4.10 Sandboxed DSL Executor

### Responsibility

Execute only validated symbolic Programs safely and deterministically. M7 never
executes arbitrary Python or compiles model output to Python source.

### Security Boundary

Language-level restrictions are a first filter, not the primary isolation boundary.

Production execution should support process/OS isolation such as:

- container isolation,
- stronger syscall isolation (e.g. gVisor-style),
- microVM isolation for higher security requirements.

The final deployment choice is an infrastructure decision.

### Canonical M7 Batch 1 contracts

```yaml
SandboxExecutionRequest:
  schema_version: m7-execution-request-v1
  program: Program
  programmer_input: ProgrammerInput
  binding_map: BindingMap
  limits_profile_id: m7-limits-v1

ExecutionDatum:
  value: CanonicalDecimal
  scale: RAW | THOUSAND | MILLION | BILLION | PERCENT | null
  unit: string | null

ExecutionOutput:
  kind: SCALAR | ORDERED_VALUES
  values: [ExecutionDatum]

ExecutionFailure:
  stage: POLICY | VALIDATION | BINDING | CONVERSION | ARITHMETIC |
         RESOURCE | SECURITY | INFRASTRUCTURE
  code: string
  message: string

ExecutionResult:
  schema_version: m7-execution-result-v1
  program_id: string
  success: boolean
  output: ExecutionOutput | null
  failure: ExecutionFailure | null
  execution_ms: integer
```

Success requires a non-null output and null failure. Failure requires null
output and a typed non-null failure. `SCALAR` contains exactly one datum;
`ORDERED_VALUES` contains one or more ordered data. Execution values are always
`CanonicalDecimal`; binary float is forbidden.

TASK-070 accepts only `m6-program-v1`, reruns TASK-068 validation, and enforces
the exact operation allowlist (`IDENTITY`, `COLLECT`,
`APPLY_REGISTERED_FORMULA`). It also enforces an exact placeholder/evidence
bijection across Program inputs, `MaskedEvidenceBundle`, and execution-only
`BindingMap`. Missing, extra, duplicate, unknown, or evidence-mismatched
bindings fail deterministically. Policy constants cap Programs at 256 inputs,
256 steps, 256 references per step, and dependency depth 64. This policy is a
pre-dispatch filter, not an OS security boundary.

The canonical arithmetic context has precision 50, `ROUND_HALF_EVEN`, and
traps `DivisionByZero`, `InvalidOperation`, and `Overflow`; `Inexact` and
`Rounded` remain allowed. Serialization uses fixed-point notation, removes
unnecessary fractional zeroes, normalizes negative zero to `0`, and validates
the result as `CanonicalDecimal`. M7 does not quantize currency values.

### Canonical M7 Batch 2 runtime semantics

TASK-071 owns both the trusted DSL interpreter and isolated worker process. It
lowers validated Programs to internal instructions only and never compiles a
Program or model output to Python source.

- `GROWTH_RATE` consumes ordered `previous,current` values, normalizes
  magnitude operands to `RAW`, requires compatible units, computes
  `(current - previous) / previous * Decimal("100")`, rejects zero previous,
  and emits `PERCENT`.
- `AVERAGE` preserves Plan order and compatible units. It uses a common
  explicitly agreed requested magnitude scale, otherwise `RAW`, converts
  operands before arithmetic, and computes the Decimal arithmetic mean.
- `IDENTITY` and `COLLECT` apply an approved requested-output conversion when
  present, otherwise preserve source value and scale.
- Percent and magnitude scales cannot mix. Units must be identical or all
  null; null/non-null mixtures and different non-null units are rejected. No
  unit/FX conversion or ratio fallback is permitted.

Batch 1 defines contracts, Decimal policy, and static admission only. It does
not interpret formulas, execute a Program, create a worker, impose runtime
resource limits, perform sandbox-abuse testing, or select production sandbox
technology.

TASK-071 implements this exact flow:

```text
SandboxExecutionRequest
  -> parent TASK-070 validation
  -> bounded canonical JSON
  -> separate trusted worker process
  -> worker TASK-070/TASK-068 revalidation
  -> BindingMap-to-Decimal binding
  -> ordered DSL interpretation
  -> canonical ExecutionResult
  -> parent protocol/result validation
```

The interpreter evaluates Program steps in validated order and resolves only
prior Program input/step references. `IDENTITY` returns one converted scalar;
`COLLECT` preserves input order and converts every scalar independently;
`APPLY_REGISTERED_FORMULA` dispatches only by the built-in
`FormulaImplementationKind`. There is no callable, source-code, generated
import, `eval`, `exec`, filesystem, network, subprocess, or model surface in
the DSL.

Parent and worker exchange canonical UTF-8 JSON only, bounded to 1,048,576
bytes in each direction. Pickle is forbidden. The parent launches one fixed
module without a shell and supplies only `PYTHONIOENCODING` and
`PYTHONDONTWRITEBYTECODE`; worker stderr and request values are never copied
into `ExecutionResult`. A non-zero exit, malformed/non-canonical response, or
invalid/mismatched result maps to a typed `INFRASTRUCTURE` failure.

Batch 2 uses process separation but does not claim the hard resource or final
OS isolation boundary. Time/memory/filesystem/network enforcement belongs to
TASK-072, abuse testing to TASK-074, and sandbox technology selection to
TASK-075. With the deterministic execution boundary available, TASK-067 is
unblocked but remains unimplemented.

## 4.11 Verification Layer

### Responsibility

Decide whether the grounded evidence and computed result are acceptable.

Logical checks:

### Grounding verification

- company,
- report,
- statement scope,
- period,
- table,
- row/column/header path.

### Numeric / scale / unit verification

- Vietnamese number parsing,
- sign conventions,
- scale resolution,
- unit conversion,
- scale/unit provenance,
- missing/NaN handling,
- divide-by-zero,
- magnitude sanity.

### Financial-logic verification

- correct formula family,
- required periods present,
- accounting consistency checks when applicable.

Independent verification checks may be parallelized.

### Canonical Verification Result

```yaml
VerificationResult:
  passed: boolean
  failure_category:
    GROUNDING
    | INSUFFICIENT_EVIDENCE
    | NUMERIC
    | SCALE_UNIT
    | FINANCIAL_LOGIC
    | null
  failure_reason: string | null
```

Pass/fail invariants:

- `passed: true` requires `failure_category` and `failure_reason` to be `null`.
- `passed: false` requires a non-null `failure_category` and a non-empty `failure_reason`.

Failure categories:

- `GROUNDING`: company, report, statement scope, period, table, row, column, or header-path grounding failure.
- `INSUFFICIENT_EVIDENCE`: required table and/or text evidence is missing.
- `NUMERIC`: numeric parsing, sign, missing/NaN, divide-by-zero, conversion, or result-sanity failure.
- `SCALE_UNIT`: scale, unit, or scale/unit-provenance failure.
- `FINANCIAL_LOGIC`: formula, required-period, or accounting-consistency failure.

Execution and runtime failures belong to `ExecutionResult`, not `VerificationResult`.

## 4.12 Answer Builder

### Responsibility

Build the user-facing answer from verified output.

It should not silently change the computed value or invent missing evidence.

The exact answer schema/citation format is not specified in the provided sources and must be defined by the implementation/product layer.

## 4.13 Optional Experience Memory

Experience memory is deferred until the non-memory baseline is stable.

If implemented:

```yaml
MemoryEntry:
  source_question: string
  strategy_findings: [string]
  caution_rules: [string]
  embedding_ref: string
  provenance: string
```

Inference policy:

1. embed the current question + relevant context,
2. retrieve candidate memory entries,
3. discard entries below a calibrated similarity threshold,
4. deduplicate,
5. cap retrieval depth,
6. inject only the remaining strategies/guards,
7. otherwise fall back to the base policy.

Memory cannot override current evidence or deterministic verification.

Held-out evaluation/test must not write new memory entries.

## 5. Routing Policy

Current deterministic M4 v1 policy:

| Planned shape | Reasoning mode | Model tier | Verify profile | Retry budget |
|---|---|---|---|---|
| Single formula-free `LOOKUP` requirement | `DIRECT` | `CHEAP` | `LIGHT` | 1 |
| Formula-free `MULTI_PERIOD` comparison | `DIRECT` | `CHEAP` | `STRICT` | 2 |
| Registered `DERIVED_RATIO` | registry (`PROGRAM` v1) | `STRONG` | `STRICT` | 2 |
| `GROWTH_RATE` | `PROGRAM` | `STRONG` | `STRICT` | 2 |
| `AVERAGE` | `PROGRAM` | `STRONG` | `STRICT` | 2 |

Retry budgets remain question-type based and are unchanged from Batch 1.
Routing uses no confidence or retrieval-score threshold.

A cheap-tier failure should escalate to the strong tier before final abstain where the routing policy allows it.

## 6. Retry / Failure Routing

```text
Verification failure
        |
        v
  classify failure
        |
  +-----+----------------+----------------+----------------+
  |                      |                |                |
missing evidence     wrong program     cheap model     unsupported/
  |                      |                |            unresolved
  v                      v                v                v
Retrieval retry     Programmer retry    STRONG         Abstain/
                                      escalation       Clarify
```

No unbounded retry loops.

## 7. Parallelization

The source materials explicitly distinguish real data dependencies from steps that can run independently.

### Can be parallelized

- retrieval of independent tables in a multi-hop question,
- independent verifier checks.

### Must remain sequential

- Programmer waits for evidence,
- Sandbox waits for generated program.

## 8. Model Specialization

Logical roles have different model requirements:

- NLU / Supervisor: language understanding + planning,
- Retriever: retrieval/ranking models plus optional small LLM,
- Programmer: code-generation capability,
- Critic/Verifier: small/fast model only where deterministic checks are insufficient.

Do not assume every component requires a large LLM.

## 9. Observability

Every request should eventually carry a traceable execution record containing at least:

- NLU output,
- Plan,
- retrieval candidates/scores,
- selected Evidence,
- generated program/version,
- execution result,
- verification outcomes,
- retry/escalation decisions,
- final status.

This observability contract is proposed for debugging/evaluation; exact logging/storage is not specified in the sources.

## 10. Evaluation Boundaries

Evaluate stages separately so failures are attributable.

### Canonical Evaluation Result

```yaml
EvaluationResult:
  mode:
    NLU
    | SUPERVISOR
    | RETRIEVAL
    | ORACLE_EVIDENCE
    | RETRIEVED_EVIDENCE_E2E

  passed: boolean

  failure_stage:
    NLU
    | SUPERVISOR
    | RETRIEVAL
    | EVIDENCE
    | PROGRAMMER
    | SANDBOX
    | VERIFICATION
    | null

  failure_reason: string | null
```

Pass/fail invariants:

- `passed: true` requires `failure_stage` and `failure_reason` to be `null`.
- `passed: false` requires a non-null `failure_stage` and a non-empty `failure_reason`.

### Canonical Reasoning Evaluation Result

```yaml
ReasoningEvaluationResult:
  execution_correct: boolean
  trace_comparison: EXACT | NORMALIZED_EQUIVALENT | DIFFERENT
  answer_correct: boolean
```

The three correctness outcomes are independent. This contract does not define a Program schema or an Answer schema.

Minimal equivalence-hook interface:

```text
TraceEquivalenceHook(expected_trace: opaque, actual_trace: opaque) -> boolean
```

Exact equality is checked first. The hook is called only when traces differ: `true` produces `NORMALIZED_EQUIVALENT`; `false` produces `DIFFERENT`. Hook implementations must be deterministic.

### Canonical Evaluation Slice

```yaml
EvaluationSlice:
  evidence_source: TABLE | TEXT | HYBRID
  reasoning_depth: ONE_STEP | TWO_STEP | THREE_PLUS_STEPS
  scale_unit_sensitive: boolean

SlicedEvaluationResult:
  result: EvaluationResult
  slice: EvaluationSlice
```

`EvaluationSlice` attaches to `EvaluationResult` through `SlicedEvaluationResult`; the canonical `EvaluationResult` remains unchanged. `HYBRID` is an evaluation-only classification and is not an `EvidenceItem.source_type`.

Thresholds and full-corpus acceptance criteria remain deferred.

### M3 Retrieval Evaluation

`RetrievalEvaluationCase` records exact fixture report/table/paragraph/source-cell
expectations where available. It separately reports Recall@K for BM25, vector,
RRF, and reranker, MRR, multi-table completeness, exact `EvidenceItem`
grounding, and the direct TASK-03E hybrid-completeness result. `FIXTURE` runs
deterministically on CPU; `FULL_CORPUS` is reserved and remains
`GPU_PRODUCTION_VALIDATION_PENDING` until the production vector artifact is
available. A `RetrievalFailureEvent` is diagnostic-only and attributes the
earliest unrecoverable typed retrieval boundary without changing execution.

### NLU / Planning

- field-level semantic accuracy,
- question type,
- required evidence source (`TABLE/TEXT/HYBRID`),
- required periods/tables,
- abstain/clarification,
- routing/confidence calibration.

TASK-047 implements exact Supervisor planning evaluation for the approved
fields above. It intentionally does not score retrieval, execution,
verification, or answer correctness. Aggregate thresholds remain TBD.

### Retrieval

- table Recall@K,
- paragraph/text Recall@K,
- hybrid evidence coverage,
- reranker quality,
- scale/unit evidence recall.

### Reasoning

Run both:

- oracle/gold-evidence reasoning,
- retrieved-evidence reasoning.

Report:

- execution accuracy,
- program/trace accuracy,
- final answer accuracy,
- breakdown by reasoning steps (`1`, `2`, `3+`),
- breakdown by evidence source,
- complex-table fallback performance.

### Numerical correctness

- scale accuracy,
- unit accuracy,
- formula correctness,
- grounding correctness.

### Production

- p50 / p95 / p99 latency,
- cost/query,
- tokens/query,
- cache hit rate,
- retry/escalation rate,
- coordination-failure rate,
- throughput under target load.

Execution accuracy and program accuracy are complementary: execution can be accidentally correct; exact program matching can reject semantically equivalent programs.

## 11. Proposed Code Layout

```text
src/
├── indexing/
│   ├── document_parser.py
│   ├── table_normalizer.py
│   ├── paragraph_extractor.py
│   ├── table_text_linker.py
│   ├── representation_builder.py
│   ├── bm25_indexer.py
│   ├── embedding_indexer.py
│   └── metadata_indexer.py
├── understanding/
│   ├── pipeline.py
│   ├── company_resolver.py
│   ├── temporal_parser.py
│   ├── metric_resolver.py
│   ├── ambiguity.py
│   └── schemas.py
├── supervisor/
│   ├── classifier.py
│   ├── planner.py
│   ├── router.py
│   └── schemas.py
├── retrieval/
│   ├── query_builder.py
│   ├── sparse/
│   │   └── bm25.py
│   ├── dense/
│   │   ├── embedder.py
│   │   └── vector_store.py
│   ├── fusion/
│   │   └── rrf.py
│   ├── reranker/
│   │   └── bge_reranker.py
│   ├── report_retriever.py
│   ├── table_retriever.py
│   ├── evidence_locator.py
│   └── schemas.py
├── evidence/
│   ├── builder.py
│   ├── scale_unit_resolver.py
│   ├── schema_linker.py
│   ├── numeric_masking.py
│   └── schemas.py
├── table_reasoning/
│   ├── planner.py
│   ├── operations.py
│   ├── executor.py
│   └── schemas.py
├── programmer/
│   ├── agent.py
│   ├── prompt.py
│   └── schemas.py
├── sandbox/
│   ├── executor.py
│   ├── policy.py
│   └── schemas.py
├── verification/
│   ├── grounding.py
│   ├── numerical.py
│   ├── scale_unit.py
│   ├── financial.py
│   └── schemas.py
├── memory/                 # optional post-v1
│   ├── bank.py
│   ├── retriever.py
│   ├── distiller.py
│   └── schemas.py
├── answer/
│   └── builder.py
└── orchestration/
    ├── graph.py
    └── state.py
```

This folder structure is a proposed implementation mapping, not a requirement from the source materials.

## 12. Source Basis

Architecture is grounded in:

- Supervisor NLU -> classification -> retrieval planning -> model/verify routing,
- deterministic Plan contract,
- TableRAG + hierarchical index,
- numeric masking + schema linking,
- program-aided/code-based reasoning,
- sandboxed Python execution,
- dual/multi-layer verification,
- role-specialized multi-model architecture,
- latency guidance to parallelize only truly independent work,
- retrieval-heavy benchmark failure analysis.


### Additional v3 source basis

- TAT-QA (arXiv:2105.07624): hybrid table/text evidence and scale prediction.
- FinQA (arXiv:2109.00122): retriever-generator, supporting facts, executable reasoning programs, program/execution metrics.
- PAL (arXiv:2211.10435): delegate execution to Python.
- Chain-of-Table (arXiv:2401.04398): dynamic bounded table-operation chains.
- Multi-Agent Financial Document Processing benchmark (arXiv:2603.22651): hierarchical orchestration and production cost/latency/retry/caching tradeoffs.
- FinAcumen (arXiv:2606.17642): selective experience memory and deterministic financial tools.

The 2603.22651 benchmark is a financial-document extraction study rather than the exact QA task, so its quantitative results are treated as orchestration guidance.
