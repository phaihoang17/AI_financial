import json
import unittest

from src.indexing.document_parser import parse_document
from src.indexing.embedding_chunker import (
    CHUNKING_CONFIG_FINGERPRINT,
    build_embedding_chunks,
)
from src.indexing.grid_builder import build_logical_table_grid
from src.indexing.normalized_table_assembler import assemble_normalized_table
from src.indexing.paragraph_extractor import extract_paragraphs
from src.indexing.retrieval_representation_builder import build_retrieval_representations
from src.indexing.scale_unit_hint_extractor import extract_scale_unit_hints
from src.indexing.table_text_linker import link_tables_to_paragraphs

from tests.indexing.test_document_parser import make_source


class CharTokenizer:
    """Deterministic test tokenizer: one code point is one token."""

    def __call__(self, text, **kwargs):
        if isinstance(text, list):
            return {"input_ids": [[ord(char) for char in item] for item in text]}
        return {"input_ids": [ord(char) for char in text]}


def build_fixture(value="1.250"):
    source = make_source(
        "===== PAGE 1 =====\n"
        "Narrative.\n"
        "<table><tr><td>Chỉ tiêu</td><td>2024</td></tr>"
        f"<tr><td>Doanh thu</td><td>{value}</td></tr></table>\n"
    )
    document = parse_document(source)
    paragraphs = extract_paragraphs(source, document)
    tables = [
        assemble_normalized_table(table, build_logical_table_grid(table))
        for page in document.pages
        for table in page.tables
    ]
    links = link_tables_to_paragraphs(source, document, paragraphs)
    hints = extract_scale_unit_hints(source, document, tables, paragraphs, links)
    representations = build_retrieval_representations(
        source, tables, paragraphs, links, hints
    )
    return tables, representations


class EmbeddingChunkerTests(unittest.TestCase):
    def test_text_representation_is_one_chunk_without_m2a_changes(self):
        tables, representations = build_fixture()
        chunks = build_embedding_chunks(
            representations,
            {table.normalized_table_id: table for table in tables},
            CharTokenizer(),
        )
        text_chunks = [chunk for chunk in chunks if chunk.source_type.value == "TEXT"]
        self.assertEqual(len(text_chunks), 1)
        self.assertEqual(text_chunks[0].content, representations[-1].content)
        self.assertEqual(text_chunks[0].chunking_config_fingerprint, CHUNKING_CONFIG_FINGERPRINT)
        self.assertEqual(text_chunks[0].primary_source_cell_ids, [])

    def test_oversized_cell_fragments_losslessly_and_deterministically(self):
        tables, representations = build_fixture("x" * 20000)
        table = representations[0]
        first = build_embedding_chunks(
            [table],
            {tables[0].normalized_table_id: tables[0]},
            CharTokenizer(),
        )
        second = build_embedding_chunks(
            [table],
            {tables[0].normalized_table_id: tables[0]},
            CharTokenizer(),
        )
        fragments = [chunk.cell_fragment for chunk in first if chunk.cell_fragment is not None]
        self.assertGreater(len(fragments), 1)
        self.assertEqual("".join(fragment.text for fragment in fragments), "x" * 20000)
        self.assertEqual([chunk.to_dict() for chunk in first], [chunk.to_dict() for chunk in second])
        self.assertTrue(all(chunk.content_token_count <= 7168 for chunk in first))
        self.assertEqual(
            [fragment.fragment_index for fragment in fragments],
            list(range(len(fragments))),
        )
        self.assertEqual({fragment.fragment_count for fragment in fragments}, {len(fragments)})

    def test_normal_chunks_preserve_primary_cells_and_structural_context(self):
        tables, representations = build_fixture()
        chunks = build_embedding_chunks(
            [representations[0]],
            {tables[0].normalized_table_id: tables[0]},
            CharTokenizer(),
        )
        table = tables[0]
        primary = [cell_id for chunk in chunks for cell_id in chunk.primary_source_cell_ids]
        self.assertEqual(set(primary), {cell.source_cell_id for cell in table.cells})
        self.assertEqual(len(primary), len(set(primary)))
        for chunk in chunks:
            self.assertTrue(set(chunk.primary_source_cell_ids).isdisjoint(chunk.context_source_cell_ids))
            payload = json.loads(chunk.content)
            self.assertEqual(list(payload), [
                "ticker", "company_name", "report_year", "statement_scope", "period_labels", "cells"
            ])


if __name__ == "__main__":
    unittest.main()

