import unittest
from unittest.mock import MagicMock, patch

from core.util.config import Config
from core.knowledge.vector.indexer import (
    extract_frontmatter,
    extract_inline_tags,
    extract_title,
    split_markdown_into_chunks,
    get_embedding_client,
    generate_embeddings,
    generate_query_embedding,
)


class TestKnowledgeIndexer(unittest.TestCase):

    def setUp(self):
        Config().reset()

    def tearDown(self):
        Config().reset()

    def test_extract_frontmatter_full(self):
        doc = """---
title: "My Architecture Doc"
tags: [ai, agents, langgraph]
draft: false
pinned: true
---
# Main Content
This is the actual content.
"""
        fm, clean = extract_frontmatter(doc)
        self.assertEqual(fm.get("title"), "My Architecture Doc")
        self.assertEqual(fm.get("tags"), ["ai", "agents", "langgraph"])
        self.assertEqual(fm.get("draft"), False)
        self.assertEqual(fm.get("pinned"), True)
        self.assertTrue(clean.startswith("# Main Content"))

    def test_extract_frontmatter_edge_cases(self):
        # No frontmatter
        doc1 = "# Regular Document"
        fm1, clean1 = extract_frontmatter(doc1)
        self.assertEqual(fm1, {})
        self.assertEqual(clean1, doc1)

        # Incomplete frontmatter
        doc2 = "---\ntitle: incomplete\n"
        fm2, clean2 = extract_frontmatter(doc2)
        self.assertEqual(fm2, {})
        self.assertEqual(clean2, doc2)

        # Frontmatter with comma string tags
        doc3 = """---
tag: research, dev
---
Content"""
        fm3, clean3 = extract_frontmatter(doc3)
        self.assertEqual(fm3.get("tag"), "research, dev")

    def test_extract_inline_tags(self):
        text = "This is a note mentioning #project/aoc and #python with #lance-db and #tag1."
        tags = extract_inline_tags(text)
        self.assertIn("project/aoc", tags)
        self.assertIn("python", tags)
        self.assertIn("lance-db", tags)
        self.assertIn("tag1", tags)

    def test_extract_title(self):
        fm = {"title": "Explicit Title"}
        self.assertEqual(extract_title("# Header Title", "file.md", fm), "Explicit Title")

        fm_empty = {}
        self.assertEqual(extract_title("# Header Title\nContent", "file.md", fm_empty), "Header Title")
        self.assertEqual(extract_title("No header", "my-note.md", fm_empty), "my-note")

    def test_split_markdown_into_chunks(self):
        doc = """---
title: System Overview
tags: [architecture]
---
# System Overview
Introduction to the platform #platform.

## Database Layer
We use SQLite and LanceDB for data storage.

### LanceDB Integration
LanceDB powers hybrid vector search and BM25 full-text indexing.

## Tooling
Tools allow agents to query data.
"""
        chunks = split_markdown_into_chunks("vault/overview.md", doc)
        self.assertTrue(len(chunks) >= 3)

        # Check breadcrumbs
        breadcrumbs = [c["header_path"] for c in chunks]
        self.assertIn("System Overview", breadcrumbs)
        self.assertIn("System Overview > Database Layer", breadcrumbs)
        self.assertIn("System Overview > Database Layer > LanceDB Integration", breadcrumbs)
        self.assertIn("System Overview > Tooling", breadcrumbs)

        # Verify chunk structure
        for chunk in chunks:
            self.assertIn("id", chunk)
            self.assertIn("file_path", chunk)
            self.assertIn("text", chunk)
            self.assertIn("raw_content", chunk)
            self.assertIn("content_hash", chunk)
            self.assertEqual(chunk["file_path"], "vault/overview.md")

    def test_split_markdown_long_section_splitting(self):
        # Generate a section with multiple paragraphs exceeding max_chunk_chars
        p1 = "Paragraph 1: " + ("word " * 150)
        p2 = "Paragraph 2: " + ("word " * 150)
        p3 = "Paragraph 3: " + ("word " * 150)
        doc = f"# Section Header\n\n{p1}\n\n{p2}\n\n{p3}"

        chunks = split_markdown_into_chunks("notes/long.md", doc, max_chunk_chars=400)
        self.assertTrue(len(chunks) >= 2)
        for c in chunks:
            self.assertEqual(c["header_path"], "Section Header")

    def test_split_markdown_empty_or_whitespace(self):
        chunks = split_markdown_into_chunks("empty.md", "   \n\n  ")
        self.assertEqual(chunks, [])

    def test_split_markdown_no_headers(self):
        chunks = split_markdown_into_chunks("plain.md", "Just plain text without headers.")
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["header_path"], "General")

    def test_get_embedding_client_without_api_key(self):
        Config().openai_api_key = ""
        client = get_embedding_client()
        self.assertIsNone(client)

    @patch("langchain_openai.OpenAIEmbeddings")
    def test_get_embedding_client_with_api_key(self, mock_embeddings_class):
        mock_instance = MagicMock()
        mock_embeddings_class.return_value = mock_instance

        Config().openai_api_key = "test-sk-key"
        Config().embedding_model = "text-embedding-3-small"

        client = get_embedding_client()
        self.assertEqual(client, mock_instance)
        mock_embeddings_class.assert_called_once_with(
            model="text-embedding-3-small",
            openai_api_key="test-sk-key"
        )

    def test_generate_embeddings_deterministic_fallback(self):
        Config().embedding_dimensions = 8
        texts = ["First chunk of text", "Second chunk of text"]
        vectors = generate_embeddings(texts, client=None)
        self.assertEqual(len(vectors), 2)
        self.assertEqual(len(vectors[0]), 8)

        query_vec = generate_query_embedding("First chunk of text", client=None)
        self.assertEqual(len(query_vec), 8)
        self.assertEqual(vectors[0], query_vec)

    def test_generate_embeddings_with_mock_client(self):
        mock_client = MagicMock()
        mock_client.embed_documents.return_value = [[0.1, 0.2], [0.3, 0.4]]
        mock_client.embed_query.return_value = [0.1, 0.2]

        vectors = generate_embeddings(["doc1", "doc2"], client=mock_client)
        self.assertEqual(vectors, [[0.1, 0.2], [0.3, 0.4]])
        mock_client.embed_documents.assert_called_once_with(["doc1", "doc2"])

        q_vec = generate_query_embedding("query text", client=mock_client)
        self.assertEqual(q_vec, [0.1, 0.2])
        mock_client.embed_query.assert_called_once_with("query text")


if __name__ == "__main__":
    unittest.main()
