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
| Sandboxed Python       |
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
  tables_needed: [string]
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

### Example: ROE

```yaml
question_type: DERIVED_RATIO
company: AAA
periods: [2014, 2015]
target_metrics:
  - Lợi nhuận sau thuế
  - Vốn chủ sở hữu
derived_target: ROE
tables_needed:
  - KQKD
  - CDKT
model_tier: STRONG
verify_profile: STRICT
```

The earlier period is needed for average equity in the example design.

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
  normalized_value: number | null

  unit: string | null
  scale: RAW | THOUSAND | MILLION | BILLION | PERCENT | OTHER | null
  scale_source: HEADER | CELL | CAPTION | TEXT | QUESTION | null

  retrieval_score: float | null
  rerank_score: float | null
```

### Constraint

Do not pass a large unstructured top-k dump to the Programmer when a smaller grounded bundle can be built first.

A hybrid bundle may contain both a table cell/row and a narrative span when the calculation requires both.

## 4.5 Scale / Unit Resolver

### Responsibility

Resolve numeric magnitude and units before program execution.

Scale/unit may be stated in:

- a table header,
- a row/column label,
- an associated paragraph,
- the user question.

The resolved value must retain provenance so Verification can audit the decision.

## 4.6 Schema Linking

Map natural-language concepts to actual table structure.

Examples:

```text
"lãi ròng"
     -> canonical metric
     -> actual row/header path
```

Schema linking should preserve hierarchical context when a label alone is ambiguous.

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
[NUM_0], [NUM_1]
```

The LLM generates the symbolic relationship rather than memorizing the literal values.

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

## 4.9 Programmer / Pandas Coder

### Responsibility

Generate constrained executable logic from:

```text
Plan + Evidence + Schema
```

The Programmer should not independently perform open-ended retrieval.

### Program Contract

The exact code contract must be finalized during implementation.

Required properties:

- use Python/Pandas or a constrained financial reasoning DSL that compiles to Python/Pandas,
- operate only over provided grounded evidence,
- preserve row/column/evidence identifiers,
- avoid intermediate rounding where not required,
- expose intermediate references for multi-step reasoning,
- assign the final numerical result to a known output variable,
- validate grammar/schema before sandbox execution.

FinQA-style evaluation suggests reporting both program/trace correctness and final execution correctness.

### Failure Types

- syntax error,
- missing key/column,
- wrong evidence reference,
- invalid numeric parse,
- unsupported operation.

These failures should be classified for targeted retry.

## 4.10 Sandboxed Python Executor

### Responsibility

Execute generated programs safely and deterministically.

### Security Boundary

Language-level restrictions are a first filter, not the primary isolation boundary.

Production execution should support process/OS isolation such as:

- container isolation,
- stronger syscall isolation (e.g. gVisor-style),
- microVM isolation for higher security requirements.

The final deployment choice is an infrastructure decision.

### Proposed Execution Result

```yaml
ExecutionResult:
  success: boolean
  result: number | string | null
  error_type: string | null
  error_message: string | null
  execution_ms: integer
```

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

Current policy from the Supervisor design:

| Question type | Model tier | Verify profile | Retry budget |
|---|---|---|---|
| `LOOKUP` | `CHEAP` | `LIGHT` | 1 |
| `DERIVED_RATIO` | `STRONG` | `STRICT` | 2 |
| `MULTI_PERIOD` | `STRONG` | `STRICT` | 2 |
| `AGGREGATE` | `STRONG` | `STRICT` | 2 |

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

Thresholds, aggregate metrics/reporting, per-case identifiers and metadata beyond `EvaluationSlice`, and aggregation are deferred.

### NLU / Planning

- field-level semantic accuracy,
- question type,
- required evidence source (`TABLE/TEXT/HYBRID`),
- required periods/tables,
- abstain/clarification,
- routing/confidence calibration.

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
