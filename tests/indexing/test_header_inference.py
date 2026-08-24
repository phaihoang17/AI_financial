import unittest

from src.indexing.document_parser import parse_document
from src.indexing.grid_builder import build_logical_table_grid
from src.indexing.header_inference import (
    infer_header_structure,
    normalize_period_label,
)
from tests.indexing.test_document_parser import make_source


def infer(raw_table):
    source = make_source(f"===== PAGE 1 =====\n{raw_table}\n")
    table = parse_document(source).pages[0].tables[0]
    grid = build_logical_table_grid(table)
    return table, grid, infer_header_structure(table, grid)


class HeaderInferenceTests(unittest.TestCase):
    def test_merged_column_headers_preserve_root_to_leaf_paths(self):
        table, _, result = infer(
            "<table>"
            '<tr><td rowspan="2">CHỈ TIÊU</td><td colspan="2">Tại ngày</td></tr>'
            "<tr><td>2024</td><td>Q4/2023</td></tr>"
            "<tr><td>Tài sản</td><td>1.250</td><td>1.000</td></tr>"
            "</table>"
        )
        by_label = {node.label: node for node in result.hierarchy.nodes}
        parent = by_label["Tại ngày"]
        year = by_label["2024"]

        self.assertEqual(year.parent_header_id, parent.header_id)
        self.assertEqual(year.depth, 1)
        self.assertEqual(year.resolution.value, "EXPLICIT")
        value_cell = next(cell for cell in table.cells if cell.extracted_text == "1.250")
        self.assertEqual(
            [(item.header_id, item.label) for item in result.column_paths[value_cell.cell_id]],
            [(parent.header_id, "Tại ngày"), (year.header_id, "2024")],
        )
        self.assertEqual(result.period_labels, ["2024", "2023-Q4"])

    def test_rowspan_builds_row_hierarchy_without_copying_headers(self):
        table, _, result = infer(
            "<table>"
            "<tr><td>CHỈ TIÊU</td><td>2024</td></tr>"
            '<tr><td rowspan="2">A</td><td>Tài sản ngắn hạn</td><td>10</td></tr>'
            "<tr><td>Tiền</td><td>5</td></tr>"
            "</table>"
        )
        parent = next(node for node in result.hierarchy.nodes if node.label == "A")
        child = next(node for node in result.hierarchy.nodes if node.label == "Tiền")
        value_cell = next(cell for cell in table.cells if cell.extracted_text == "5")

        self.assertEqual(child.parent_header_id, parent.header_id)
        self.assertEqual(
            [item.label for item in result.row_paths[value_cell.cell_id]],
            ["A", "Tiền"],
        )
        self.assertEqual(len([node for node in result.hierarchy.nodes if node.label == "A"]), 1)

    def test_blank_corner_is_preserved_without_header_node(self):
        table, grid, result = infer(
            "<table>"
            "<tr><td></td><td>2024</td></tr>"
            "<tr><td>Doanh thu</td><td>10</td></tr>"
            "</table>"
        )
        blank = next(cell for cell in table.cells if cell.extracted_text == "")

        self.assertNotIn(blank.cell_id, {source_id for node in result.hierarchy.nodes for source_id in node.source_cell_ids})
        self.assertIn(blank.cell_id, {slot for row in grid.slots for slot in row})

    def test_unrecoverable_structure_emits_no_hierarchy_or_paths(self):
        table, _, result = infer(
            "<table><tr><td>A</td><td>B</td></tr><tr><td>C</td><td>D</td></tr></table>"
        )

        self.assertEqual(result.hierarchy.nodes, [])
        self.assertTrue(all(role.value == "UNKNOWN" for role in result.roles.values()))
        self.assertTrue(all(path == [] for path in result.row_paths.values()))
        self.assertTrue(all(path == [] for path in result.column_paths.values()))
        self.assertEqual(result.period_labels, [])

    def test_period_grammar_is_exact_and_preserves_source_order(self):
        _, _, result = infer(
            "<table>"
            "<tr><td>CHỈ TIÊU</td><td>Q1/2024</td><td>Quý 2 năm 2023</td><td>2022</td></tr>"
            "<tr><td>Doanh thu</td><td>1</td><td>2</td><td>3</td></tr>"
            "</table>"
        )
        self.assertEqual(result.period_labels, ["2024-Q1", "2023-Q2", "2022"])

    def test_period_grammar_does_not_use_context_or_unapproved_forms(self):
        self.assertEqual(normalize_period_label("Quý 4/2024"), "2024-Q4")
        self.assertEqual(normalize_period_label("  Ｑ1/2024  "), "2024-Q1")
        for value in ("Năm 2024", "Q1", "Quý 2", "31/12/2024", "năm trước"):
            with self.subTest(value=value):
                self.assertIsNone(normalize_period_label(value))


if __name__ == "__main__":
    unittest.main()
