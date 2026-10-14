"""Conservative structural header inference for TASK-023."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import re

from src.indexing.cell_normalizer import normalize_cell_text, normalize_source_cell_text
from src.indexing.ids import make_header_id, make_normalized_table_id
from src.indexing.numeric_parser import parse_numeric_string
from src.indexing.schemas import (
    NORMALIZATION_VERSION,
    CellRole,
    HeaderAxis,
    HeaderHierarchy,
    HeaderNode,
    HeaderPathEntry,
    HeaderResolution,
    LogicalTableGrid,
    MergedCellAnchor,
    NumericParseResult,
    NumericParseStatus,
    SourceCell,
    SourceTable,
    TableParseStatus,
)
from src.understanding.schemas import SchemaValidationError

# A year column may carry its VND currency and scale, e.g. "2023 VND", "2023 Triệu đồng",
# or OCR-joined "2023VND".
_YEAR_LABEL = re.compile(
    r"(?P<year>[0-9]{4})(?: ?(?:(?:triệu|nghìn|ngàn) )?(?:VND|VNĐ|đồng))?\Z", re.IGNORECASE
)
_Q_LABEL = re.compile(r"Q(?P<quarter>[1-4])/(?P<year>[0-9]{4})\Z", re.IGNORECASE)
_QUY_LABEL = re.compile(
    r"Quý (?P<quarter>[1-4])(?:/| năm )(?P<year>[0-9]{4})\Z",
    re.IGNORECASE,
)
# Compared with whitespace removed: OCR writes "Mãsố"/"Thuyếtminh", headers abbreviate
# ("Mã", "TM") or merge the code and note columns into one "Mã số Thuyết minh" cell.
_LABEL_COLUMN_HEADERS = frozenset({"mãsố", "mã", "thuyếtminh", "tm", "stt", "mãsốthuyếtminh"})
# Line ordinals of a leading "STT" column: "1", "18.1", "I", "XIII" (bank TT49 layout).
_LINE_ORDINAL = re.compile(r"(?:[0-9]{1,2}(?:\.[0-9]{1,2})*|[IVXLC]{1,6})[.)]?\Z|stt\Z", re.IGNORECASE)
# Fallback tolerance: rows off the dominant row-header width (e.g. one merged EPS
# line) are left unstructured instead of discarding the whole table.
_DOMINANT_WIDTH_SHARE = 0.9


@dataclass(frozen=True)
class _CellFacts:
    cell: SourceCell
    anchor: MergedCellAnchor
    normalized_text: str
    numeric_status: NumericParseStatus
    period_label: str | None


@dataclass(frozen=True)
class _Segment:
    source_cell_id: str | None
    start_column: int
    end_column: int


@dataclass
class HeaderInference:
    hierarchy: HeaderHierarchy
    roles: dict[str, CellRole]
    row_paths: dict[str, list[HeaderPathEntry]]
    column_paths: dict[str, list[HeaderPathEntry]]
    period_labels: list[str]
    normalized_texts: dict[str, str]
    numeric_results: dict[str, NumericParseResult]


def normalize_period_label(value: str) -> str | None:
    """Normalize only the approved exact standalone period-label grammar."""

    value = normalize_cell_text(value)
    year_match = _YEAR_LABEL.fullmatch(value)
    if year_match is not None:
        return year_match.group("year")
    for pattern in (_Q_LABEL, _QUY_LABEL):
        match = pattern.fullmatch(value)
        if match is not None:
            return f"{match.group('year')}-Q{match.group('quarter')}"
    return None


def _validate_inputs(table: SourceTable, grid: LogicalTableGrid) -> None:
    if not isinstance(table, SourceTable):
        raise SchemaValidationError("table must be a SourceTable")
    if not isinstance(grid, LogicalTableGrid):
        raise SchemaValidationError("grid must be a LogicalTableGrid")
    if table.parse_status is TableParseStatus.UNPARSEABLE:
        raise SchemaValidationError(
            "cannot infer headers for an unparseable source table"
        )
    cells = {cell.cell_id: cell for cell in table.cells}
    anchors = {anchor.source_cell_id: anchor for anchor in grid.anchors}
    if set(cells) != set(anchors):
        raise SchemaValidationError(
            "grid anchors must match source table cells exactly"
        )
    for source_cell_id, anchor in anchors.items():
        cell = cells[source_cell_id]
        if (anchor.rowspan, anchor.colspan) != (cell.rowspan, cell.colspan):
            raise SchemaValidationError(
                "grid anchor spans must match source cell spans"
            )


def _row_segments(grid: LogicalTableGrid, row: int) -> list[_Segment]:
    segments: list[_Segment] = []
    column = 0
    while column < grid.column_count:
        source_cell_id = grid.slots[row][column]
        end = column + 1
        while end < grid.column_count and grid.slots[row][end] == source_cell_id:
            end += 1
        segments.append(_Segment(source_cell_id, column, end))
        column = end
    return segments


def _label_columns(facts: dict[str, _CellFacts]) -> set[int]:
    """Columns headed "Mã số"/"Thuyết minh": VAS line codes and note refs, not data."""
    return {
        column
        for cell_facts in facts.values()
        if "".join(cell_facts.normalized_text.split()).casefold() in _LABEL_COLUMN_HEADERS
        for column in range(
            cell_facts.anchor.anchor_column,
            cell_facts.anchor.anchor_column + cell_facts.anchor.colspan,
        )
    }


def _ordinal_columns(facts: dict[str, _CellFacts]) -> set[int]:
    """A first column holding only line ordinals is a label column, not data."""
    cells = [
        cell_facts.normalized_text
        for cell_facts in facts.values()
        if cell_facts.anchor.anchor_column == 0
        and cell_facts.anchor.colspan == 1
        and cell_facts.normalized_text
    ]
    if len(cells) >= 3 and all(_LINE_ORDINAL.match(text) for text in cells):
        return {0}
    return set()


def _body_rows(
    grid: LogicalTableGrid,
    facts: dict[str, _CellFacts],
    label_columns: set[int] = frozenset(),
) -> dict[int, tuple[int, list[str]]]:
    recovered: dict[int, tuple[int, list[str]]] = {}
    # VAS line labels carry ordinals ("18. Lợi nhuận ...") that parse MALFORMED.
    label_statuses = {NumericParseStatus.NOT_NUMERIC}
    if label_columns:
        label_statuses.add(NumericParseStatus.MALFORMED)
    for row in range(grid.row_count):
        segments = _row_segments(grid, row)
        numeric_indexes = [
            index
            for index, segment in enumerate(segments)
            if segment.source_cell_id is not None
            and segment.start_column not in label_columns
            and facts[segment.source_cell_id].numeric_status
            is NumericParseStatus.PARSED
            and facts[segment.source_cell_id].period_label is None
        ]
        if not numeric_indexes:
            continue
        first_numeric_index = numeric_indexes[0]
        prefix = segments[:first_numeric_index]
        if not prefix or prefix[0].start_column != 0:
            continue
        prefix_ids: list[str] = []
        valid_prefix = True
        for segment in prefix:
            if segment.start_column in label_columns:
                continue
            if segment.source_cell_id is None:
                valid_prefix = False
                break
            cell_facts = facts[segment.source_cell_id]
            if (
                not cell_facts.normalized_text
                or cell_facts.numeric_status not in label_statuses
            ):
                valid_prefix = False
                break
            if not prefix_ids or prefix_ids[-1] != segment.source_cell_id:
                prefix_ids.append(segment.source_cell_id)
        if valid_prefix and prefix_ids:
            recovered[row] = (
                segments[first_numeric_index].start_column,
                prefix_ids,
            )
    return recovered


def _overlaps_columns(anchor: MergedCellAnchor, columns: set[int]) -> bool:
    return any(
        column in columns
        for column in range(anchor.anchor_column, anchor.anchor_column + anchor.colspan)
    )


def _axis_interval(anchor: MergedCellAnchor, axis: HeaderAxis) -> tuple[int, int]:
    if axis is HeaderAxis.COLUMN:
        return anchor.anchor_column, anchor.anchor_column + anchor.colspan
    return anchor.anchor_row, anchor.anchor_row + anchor.rowspan


def _axis_position(anchor: MergedCellAnchor, axis: HeaderAxis) -> int:
    return anchor.anchor_row if axis is HeaderAxis.COLUMN else anchor.anchor_column


def _axis_span(anchor: MergedCellAnchor, axis: HeaderAxis) -> int:
    return anchor.colspan if axis is HeaderAxis.COLUMN else anchor.rowspan


def _contains(parent: MergedCellAnchor, child: MergedCellAnchor, axis: HeaderAxis) -> bool:
    parent_start, parent_end = _axis_interval(parent, axis)
    child_start, child_end = _axis_interval(child, axis)
    return parent_start <= child_start and child_end <= parent_end


def _build_axis_nodes(
    axis: HeaderAxis,
    candidate_ids: Sequence[str],
    facts: dict[str, _CellFacts],
    normalized_table_id: str,
) -> tuple[list[HeaderNode], dict[str, str]]:
    nodes: list[HeaderNode] = []
    node_by_source: dict[str, HeaderNode] = {}
    header_id_by_source: dict[str, str] = {}

    for source_cell_id in candidate_ids:
        cell_facts = facts[source_cell_id]
        anchor = cell_facts.anchor
        position = _axis_position(anchor, axis)
        containing = [
            facts[parent_id]
            for parent_id in candidate_ids
            if parent_id in node_by_source
            and _axis_position(facts[parent_id].anchor, axis) < position
            and _contains(facts[parent_id].anchor, anchor, axis)
        ]
        nearest: list[_CellFacts] = []
        if containing:
            nearest_position = max(
                _axis_position(candidate.anchor, axis) for candidate in containing
            )
            nearest = [
                candidate
                for candidate in containing
                if _axis_position(candidate.anchor, axis) == nearest_position
            ]

        parent: HeaderNode | None = None
        if len(nearest) == 1:
            parent = node_by_source[nearest[0].cell.cell_id]
        if len(nearest) > 1:
            resolution = HeaderResolution.AMBIGUOUS
        elif (
            _axis_span(anchor, axis) > 1
            or parent is not None
            and _axis_span(facts[parent.source_cell_ids[0]].anchor, axis) > 1
        ):
            resolution = HeaderResolution.EXPLICIT
        elif parent is not None and parent.resolution is HeaderResolution.AMBIGUOUS:
            resolution = HeaderResolution.AMBIGUOUS
        else:
            resolution = HeaderResolution.INFERRED

        header_id = make_header_id(
            normalized_table_id, axis.value, [source_cell_id]
        )
        node = HeaderNode(
            header_id=header_id,
            axis=axis,
            label=cell_facts.normalized_text,
            source_cell_ids=[source_cell_id],
            parent_header_id=None if parent is None else parent.header_id,
            depth=0 if parent is None else parent.depth + 1,
            resolution=resolution,
        )
        nodes.append(node)
        node_by_source[source_cell_id] = node
        header_id_by_source[source_cell_id] = header_id
    return nodes, header_id_by_source


def _node_paths(nodes: Sequence[HeaderNode]) -> dict[str, list[HeaderPathEntry]]:
    by_id = {node.header_id: node for node in nodes}
    paths: dict[str, list[HeaderPathEntry]] = {}
    for node in nodes:
        chain: list[HeaderNode] = []
        current: HeaderNode | None = node
        seen: set[str] = set()
        while current is not None:
            if (
                current.header_id in seen
                or current.resolution is HeaderResolution.AMBIGUOUS
            ):
                chain = []
                break
            seen.add(current.header_id)
            chain.append(current)
            current = (
                None
                if current.parent_header_id is None
                else by_id[current.parent_header_id]
            )
        paths[node.header_id] = [
            HeaderPathEntry(header_id=item.header_id, label=item.label)
            for item in reversed(chain)
        ]
    return paths


def _deepest_covering_path(
    cell_anchor: MergedCellAnchor,
    axis: HeaderAxis,
    nodes: Sequence[HeaderNode],
    facts: dict[str, _CellFacts],
    paths: dict[str, list[HeaderPathEntry]],
) -> list[HeaderPathEntry]:
    covering = [
        node
        for node in nodes
        if _contains(facts[node.source_cell_ids[0]].anchor, cell_anchor, axis)
    ]
    if not covering:
        return []
    deepest = max(node.depth for node in covering)
    leaves = [node for node in covering if node.depth == deepest]
    if len(leaves) != 1:
        return []
    return list(paths[leaves[0].header_id])


def infer_header_structure(
    table: SourceTable, grid: LogicalTableGrid
) -> HeaderInference:
    """Infer only uniquely recoverable top-band and left-prefix structure."""

    _validate_inputs(table, grid)
    anchor_by_id = {anchor.source_cell_id: anchor for anchor in grid.anchors}
    normalized_texts: dict[str, str] = {}
    numeric_results: dict[str, NumericParseResult] = {}
    facts: dict[str, _CellFacts] = {}
    for cell in table.cells:
        normalized_text = normalize_source_cell_text(cell)
        numeric_result = parse_numeric_string(cell.extracted_text)
        normalized_texts[cell.cell_id] = normalized_text
        numeric_results[cell.cell_id] = numeric_result
        facts[cell.cell_id] = _CellFacts(
            cell=cell,
            anchor=anchor_by_id[cell.cell_id],
            normalized_text=normalized_text,
            numeric_status=numeric_result.status,
            period_label=normalize_period_label(normalized_text),
        )
    unknown_roles = {cell.cell_id: CellRole.UNKNOWN for cell in table.cells}
    empty_paths = {cell.cell_id: [] for cell in table.cells}

    body_rows = _body_rows(grid, facts)
    widths = {width for width, _ in body_rows.values()}
    label_columns = _label_columns(facts) | _ordinal_columns(facts)
    if label_columns:
        # The label-column rule replaces the generic one only where the generic
        # rule recovers nothing consistent, or fewer rows: it drops lines whose
        # labels carry formulas ("(60=50-51-52)") and reads VAS codes as data.
        fallback = _body_rows(grid, facts, label_columns)
        counts: dict[int, int] = {}
        for width, _ in fallback.values():
            counts[width] = counts.get(width, 0) + 1
        if counts:
            dominant = max(counts, key=lambda width: (counts[width], -width))
            if counts[dominant] >= _DOMINANT_WIDTH_SHARE * len(fallback):
                fallback = {row: value for row, value in fallback.items() if value[0] == dominant}
        fallback_widths = {width for width, _ in fallback.values()}
        generic_ok = bool(body_rows) and len(widths) == 1
        if len(fallback_widths) == 1 and (not generic_ok or len(fallback) > len(body_rows)):
            body_rows, widths = fallback, fallback_widths
    if not body_rows or len(widths) != 1:
        return HeaderInference(
            hierarchy=HeaderHierarchy(nodes=[]),
            roles=unknown_roles,
            row_paths={key: [] for key in empty_paths},
            column_paths={key: [] for key in empty_paths},
            period_labels=[],
            normalized_texts=normalized_texts,
            numeric_results=numeric_results,
        )

    row_header_width = next(iter(widths))
    first_body_row = min(body_rows)
    data_columns = {
        column
        for row in body_rows
        for column in range(row_header_width, grid.column_count)
        if grid.slots[row][column] is not None
        and facts[grid.slots[row][column]].numeric_status
        is NumericParseStatus.PARSED
        and facts[grid.slots[row][column]].period_label is None
    }

    column_candidate_ids: list[str] = []
    for row in range(first_body_row):
        row_candidates = [
            anchor.source_cell_id
            for anchor in grid.anchors
            if anchor.anchor_row == row
            and facts[anchor.source_cell_id].normalized_text
            and _overlaps_columns(anchor, data_columns)
            and (
                anchor.colspan > 1
                or facts[anchor.source_cell_id].numeric_status
                is NumericParseStatus.NOT_NUMERIC
                or facts[anchor.source_cell_id].period_label is not None
            )
        ]
        if not row_candidates:
            break
        column_candidate_ids.extend(row_candidates)

    row_candidate_ids: list[str] = []
    seen_row_candidates: set[str] = set()
    for row in sorted(body_rows):
        _, prefix_ids = body_rows[row]
        for source_cell_id in prefix_ids:
            if source_cell_id not in seen_row_candidates:
                row_candidate_ids.append(source_cell_id)
                seen_row_candidates.add(source_cell_id)

    normalized_table_id = make_normalized_table_id(
        table.table_id, NORMALIZATION_VERSION
    )
    column_nodes, column_node_ids = _build_axis_nodes(
        HeaderAxis.COLUMN, column_candidate_ids, facts, normalized_table_id
    )
    row_nodes, row_node_ids = _build_axis_nodes(
        HeaderAxis.ROW, row_candidate_ids, facts, normalized_table_id
    )
    hierarchy = HeaderHierarchy(nodes=[*column_nodes, *row_nodes])
    node_paths = _node_paths(hierarchy.nodes)

    roles = dict(unknown_roles)
    for source_cell_id in column_candidate_ids:
        roles[source_cell_id] = CellRole.COLUMN_HEADER
    for source_cell_id in row_candidate_ids:
        roles[source_cell_id] = CellRole.ROW_HEADER

    header_band_rows = {
        facts[source_cell_id].anchor.anchor_row
        for source_cell_id in column_candidate_ids
    }
    header_band_end = max(header_band_rows) + 1 if header_band_rows else 0
    for anchor in grid.anchors:
        if (
            anchor.source_cell_id not in column_node_ids
            and anchor.source_cell_id not in row_node_ids
            and anchor.anchor_row < header_band_end
            and anchor.anchor_row + anchor.rowspan <= first_body_row
            and anchor.anchor_column < row_header_width
            and anchor.anchor_column + anchor.colspan <= row_header_width
        ):
            roles[anchor.source_cell_id] = CellRole.CORNER
        elif (
            anchor.source_cell_id not in column_node_ids
            and anchor.source_cell_id not in row_node_ids
            and anchor.anchor_row >= first_body_row
            and anchor.anchor_column >= row_header_width
        ):
            roles[anchor.source_cell_id] = CellRole.DATA

    row_paths = {cell.cell_id: [] for cell in table.cells}
    column_paths = {cell.cell_id: [] for cell in table.cells}
    for cell in table.cells:
        source_cell_id = cell.cell_id
        anchor = facts[source_cell_id].anchor
        if source_cell_id in row_node_ids:
            row_paths[source_cell_id] = list(node_paths[row_node_ids[source_cell_id]])
        elif roles[source_cell_id] is CellRole.DATA:
            row_paths[source_cell_id] = _deepest_covering_path(
                anchor, HeaderAxis.ROW, row_nodes, facts, node_paths
            )
        if source_cell_id in column_node_ids:
            column_paths[source_cell_id] = list(
                node_paths[column_node_ids[source_cell_id]]
            )
        elif roles[source_cell_id] is CellRole.DATA:
            column_paths[source_cell_id] = _deepest_covering_path(
                anchor, HeaderAxis.COLUMN, column_nodes, facts, node_paths
            )

    period_labels = [
        facts[source_cell_id].period_label
        for source_cell_id in column_candidate_ids
        if facts[source_cell_id].period_label is not None
        and node_paths[column_node_ids[source_cell_id]]
    ]
    return HeaderInference(
        hierarchy=hierarchy,
        roles=roles,
        row_paths=row_paths,
        column_paths=column_paths,
        period_labels=period_labels,
        normalized_texts=normalized_texts,
        numeric_results=numeric_results,
    )


def build_header_hierarchy(
    table: SourceTable, grid: LogicalTableGrid
) -> HeaderHierarchy:
    return infer_header_structure(table, grid).hierarchy
