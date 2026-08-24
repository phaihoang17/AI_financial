"""Conservative structural header inference for TASK-023."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Dict, List, Optional, Sequence, Set, Tuple

from src.indexing.cell_normalizer import normalize_cell_text, normalize_source_cell_text
from src.indexing.ids import make_header_id, make_normalized_table_id
from src.indexing.numeric_parser import parse_numeric_string
from src.indexing.schemas import (
    CellRole,
    HeaderAxis,
    HeaderHierarchy,
    HeaderNode,
    HeaderPathEntry,
    HeaderResolution,
    LogicalTableGrid,
    MergedCellAnchor,
    NORMALIZATION_VERSION,
    NumericParseResult,
    NumericParseStatus,
    SourceCell,
    SourceTable,
    TableParseStatus,
)
from src.understanding.schemas import SchemaValidationError


_YEAR_LABEL = re.compile(r"(?P<year>[0-9]{4})\Z")
_Q_LABEL = re.compile(r"Q(?P<quarter>[1-4])/(?P<year>[0-9]{4})\Z", re.IGNORECASE)
_QUY_LABEL = re.compile(
    r"Quý (?P<quarter>[1-4])(?:/| năm )(?P<year>[0-9]{4})\Z",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _CellFacts:
    cell: SourceCell
    anchor: MergedCellAnchor
    normalized_text: str
    numeric_status: NumericParseStatus
    period_label: Optional[str]


@dataclass(frozen=True)
class _Segment:
    source_cell_id: Optional[str]
    start_column: int
    end_column: int


@dataclass
class HeaderInference:
    hierarchy: HeaderHierarchy
    roles: Dict[str, CellRole]
    row_paths: Dict[str, List[HeaderPathEntry]]
    column_paths: Dict[str, List[HeaderPathEntry]]
    period_labels: List[str]
    normalized_texts: Dict[str, str]
    numeric_results: Dict[str, NumericParseResult]


def normalize_period_label(value: str) -> Optional[str]:
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


def _row_segments(grid: LogicalTableGrid, row: int) -> List[_Segment]:
    segments: List[_Segment] = []
    column = 0
    while column < grid.column_count:
        source_cell_id = grid.slots[row][column]
        end = column + 1
        while end < grid.column_count and grid.slots[row][end] == source_cell_id:
            end += 1
        segments.append(_Segment(source_cell_id, column, end))
        column = end
    return segments


def _body_rows(
    grid: LogicalTableGrid, facts: Dict[str, _CellFacts]
) -> Dict[int, Tuple[int, List[str]]]:
    recovered: Dict[int, Tuple[int, List[str]]] = {}
    for row in range(grid.row_count):
        segments = _row_segments(grid, row)
        numeric_indexes = [
            index
            for index, segment in enumerate(segments)
            if segment.source_cell_id is not None
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
        prefix_ids: List[str] = []
        valid_prefix = True
        for segment in prefix:
            if segment.source_cell_id is None:
                valid_prefix = False
                break
            cell_facts = facts[segment.source_cell_id]
            if (
                not cell_facts.normalized_text
                or cell_facts.numeric_status is not NumericParseStatus.NOT_NUMERIC
            ):
                valid_prefix = False
                break
            if not prefix_ids or prefix_ids[-1] != segment.source_cell_id:
                prefix_ids.append(segment.source_cell_id)
        if valid_prefix:
            recovered[row] = (
                segments[first_numeric_index].start_column,
                prefix_ids,
            )
    return recovered


def _overlaps_columns(anchor: MergedCellAnchor, columns: Set[int]) -> bool:
    return any(
        column in columns
        for column in range(anchor.anchor_column, anchor.anchor_column + anchor.colspan)
    )


def _axis_interval(anchor: MergedCellAnchor, axis: HeaderAxis) -> Tuple[int, int]:
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
    facts: Dict[str, _CellFacts],
    normalized_table_id: str,
) -> Tuple[List[HeaderNode], Dict[str, str]]:
    nodes: List[HeaderNode] = []
    node_by_source: Dict[str, HeaderNode] = {}
    header_id_by_source: Dict[str, str] = {}

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
        nearest: List[_CellFacts] = []
        if containing:
            nearest_position = max(
                _axis_position(candidate.anchor, axis) for candidate in containing
            )
            nearest = [
                candidate
                for candidate in containing
                if _axis_position(candidate.anchor, axis) == nearest_position
            ]

        parent: Optional[HeaderNode] = None
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


def _node_paths(nodes: Sequence[HeaderNode]) -> Dict[str, List[HeaderPathEntry]]:
    by_id = {node.header_id: node for node in nodes}
    paths: Dict[str, List[HeaderPathEntry]] = {}
    for node in nodes:
        chain: List[HeaderNode] = []
        current: Optional[HeaderNode] = node
        seen: Set[str] = set()
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
    facts: Dict[str, _CellFacts],
    paths: Dict[str, List[HeaderPathEntry]],
) -> List[HeaderPathEntry]:
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
    normalized_texts: Dict[str, str] = {}
    numeric_results: Dict[str, NumericParseResult] = {}
    facts: Dict[str, _CellFacts] = {}
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

    column_candidate_ids: List[str] = []
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

    row_candidate_ids: List[str] = []
    seen_row_candidates: Set[str] = set()
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
