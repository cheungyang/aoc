import unittest
from unittest.mock import MagicMock, patch

from core.util.config import Config
from core.knowledge.vector import indexer
from core.knowledge.vector.indexer import (
    extract_frontmatter,
    extract_inline_tags,
    extract_title,
    split_markdown_into_chunks,
    get_embedding_client,
    generate_embeddings,
    generate_query_embedding,
    resolve_embedding_model,
    DEFAULT_EMBEDDING_MODEL,
    EmbeddingError,
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
        Config().gemini_api_key = ""
        client = get_embedding_client()
        self.assertIsNone(client)

    @patch("langchain_google_genai.GoogleGenerativeAIEmbeddings")
    def test_get_embedding_client_remaps_retired_model(self, mock_gemini_class):
        """text-embedding-004 was retired; the client must not request it anymore."""
        mock_instance = MagicMock()
        mock_gemini_class.return_value = mock_instance

        Config().gemini_api_key = "test-gemini-key"
        Config().embedding_model = "text-embedding-004"
        Config().embedding_dimensions = 1536

        client = get_embedding_client()
        self.assertEqual(client, mock_instance)
        mock_gemini_class.assert_called_once_with(
            model="models/gemini-embedding-001",
            google_api_key="test-gemini-key",
            output_dimensionality=1536
        )

    @patch("langchain_google_genai.GoogleGenerativeAIEmbeddings")
    def test_get_embedding_client_respects_custom_model(self, mock_gemini_class):
        mock_instance = MagicMock()
        mock_gemini_class.return_value = mock_instance

        Config().gemini_api_key = "test-gemini-key"
        Config().embedding_model = "gemini-embedding-2"
        Config().embedding_dimensions = 768

        client = get_embedding_client()
        self.assertEqual(client, mock_instance)
        mock_gemini_class.assert_called_once_with(
            model="models/gemini-embedding-2",
            google_api_key="test-gemini-key",
            output_dimensionality=768
        )

    def test_generate_embeddings_deterministic_fallback(self):
        # No patching needed: client=None now means "no client". It previously
        # had to be mocked out because the implementation replaced the None with
        # a live client, so this test only passed while the endpoint 404'd.
        Config().embedding_dimensions = 8
        texts = ["First chunk of text", "Second chunk of text"]
        vectors = generate_embeddings(texts, client=None)
        self.assertEqual(len(vectors), 2)
        self.assertEqual(len(vectors[0]), 8)

        query_vec = generate_query_embedding("First chunk of text", client=None)
        self.assertEqual(len(query_vec), 8)
        self.assertEqual(vectors[0], query_vec)

    def test_explicit_none_client_never_builds_one(self):
        """`--skip-embedding` promises zero quota cost; this is what enforces it.

        Previously `client or get_embedding_client()` turned an explicit "no
        client" into a live API client whenever a key was configured, so the
        flag silently billed a full re-index.
        """
        Config().embedding_dimensions = 4
        with patch("core.knowledge.vector.indexer.get_embedding_client") as mock_get:
            generate_embeddings(["some text"], client=None)
            generate_query_embedding("some text", client=None)
        mock_get.assert_not_called()

    def test_omitted_client_is_resolved_from_config(self):
        Config().embedding_dimensions = 2
        mock_client = MagicMock()
        mock_client.embed_documents.return_value = [[0.1, 0.2]]
        with patch("core.knowledge.vector.indexer.get_embedding_client",
                   return_value=mock_client) as mock_get:
            vectors = generate_embeddings(["some text"])
        mock_get.assert_called_once()
        self.assertEqual(vectors, [[0.1, 0.2]])


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


class TestEmbeddingFailures(unittest.TestCase):
    """A failed API call must never reach the index as a hash vector.

    The fallback used to be silent, which meant a 429 mid-rebuild wrote
    well-formed nonsense that no later inspection could distinguish from real
    embeddings. These tests pin the louder behaviour.
    """

    def setUp(self):
        Config().reset()
        Config().embedding_dimensions = 4
        # Keep the suite fast; the backoff itself is asserted separately.
        self._base = indexer.EMBEDDING_RETRY_BASE_SECONDS
        indexer.EMBEDDING_RETRY_BASE_SECONDS = 0

    def tearDown(self):
        indexer.EMBEDDING_RETRY_BASE_SECONDS = self._base
        Config().reset()

    def test_documents_failure_raises_instead_of_falling_back(self):
        client = MagicMock()
        client.embed_documents.side_effect = RuntimeError("400 INVALID_ARGUMENT")

        with self.assertRaises(EmbeddingError):
            generate_embeddings(["doc"], client=client)

    def test_query_failure_raises_instead_of_falling_back(self):
        client = MagicMock()
        client.embed_query.side_effect = RuntimeError("400 INVALID_ARGUMENT")

        with self.assertRaises(EmbeddingError):
            generate_query_embedding("q", client=client)

    def test_original_error_is_preserved_as_the_cause(self):
        original = RuntimeError("404 model not found")
        client = MagicMock()
        client.embed_documents.side_effect = original

        with self.assertRaises(EmbeddingError) as ctx:
            generate_embeddings(["doc"], client=client)
        self.assertIs(ctx.exception.__cause__, original)
        self.assertIn("404 model not found", str(ctx.exception))

    def test_transient_failure_is_retried(self):
        client = MagicMock()
        client.embed_documents.side_effect = [
            RuntimeError("429 RESOURCE_EXHAUSTED"),
            [[0.1, 0.2, 0.3, 0.4]],
        ]
        vectors = generate_embeddings(["doc"], client=client)
        self.assertEqual(vectors, [[0.1, 0.2, 0.3, 0.4]])
        self.assertEqual(client.embed_documents.call_count, 2)

    def test_transient_failure_gives_up_after_max_attempts(self):
        client = MagicMock()
        client.embed_documents.side_effect = RuntimeError("429 RESOURCE_EXHAUSTED")

        with self.assertRaises(EmbeddingError):
            generate_embeddings(["doc"], client=client)
        self.assertEqual(client.embed_documents.call_count,
                         indexer.EMBEDDING_MAX_ATTEMPTS)

    def test_permanent_failure_is_not_retried(self):
        # Retrying a retired model name or a bad key only delays the report.
        client = MagicMock()
        client.embed_documents.side_effect = RuntimeError("404 model not found")

        with self.assertRaises(EmbeddingError):
            generate_embeddings(["doc"], client=client)
        self.assertEqual(client.embed_documents.call_count, 1)

    def test_backoff_grows_between_attempts(self):
        indexer.EMBEDDING_RETRY_BASE_SECONDS = 1.0
        client = MagicMock()
        client.embed_documents.side_effect = RuntimeError("503 unavailable")

        with patch("core.knowledge.vector.indexer.time.sleep") as mock_sleep:
            with self.assertRaises(EmbeddingError):
                generate_embeddings(["doc"], client=client)

        self.assertEqual([c[0][0] for c in mock_sleep.call_args_list], [1.0, 2.0])

    def test_offline_path_still_returns_deterministic_vectors(self):
        # client=None is an explicit request for offline vectors, not a failure.
        vectors = generate_embeddings(["doc"], client=None)
        self.assertEqual(len(vectors[0]), 4)

    def test_transient_detection(self):
        for message in ("429 RESOURCE_EXHAUSTED", "503 Service Unavailable",
                        "Deadline exceeded", "connection timed out",
                        "quota exceeded for metric"):
            with self.subTest(message=message):
                self.assertTrue(indexer._is_transient(RuntimeError(message)))

        for message in ("404 model not found", "401 unauthorized",
                        "invalid api key"):
            with self.subTest(message=message):
                self.assertFalse(indexer._is_transient(RuntimeError(message)))


class TestResolveEmbeddingModel(unittest.TestCase):
    """The remap table is what keeps older .env files working.

    The config default is now a real Gemini model, but deployments that pinned
    EMBEDDING_MODEL to a retired or OpenAI-shaped name still have it in their
    .env, and those values reach the API unless this resolver rewrites them.
    """

    def test_retired_and_foreign_names_are_remapped(self):
        for name in ("text-embedding-004", "models/text-embedding-004",
                     "embedding-001", "models/embedding-001",
                     "text-embedding-3-small"):
            with self.subTest(model=name):
                self.assertEqual(resolve_embedding_model(name), DEFAULT_EMBEDDING_MODEL)

    def test_blank_values_fall_back_to_the_default(self):
        for name in (None, "", "   "):
            with self.subTest(model=name):
                self.assertEqual(resolve_embedding_model(name), DEFAULT_EMBEDDING_MODEL)

    def test_supported_names_are_normalized_not_replaced(self):
        self.assertEqual(
            resolve_embedding_model("gemini-embedding-001"),
            "models/gemini-embedding-001"
        )
        self.assertEqual(
            resolve_embedding_model("models/gemini-embedding-2"),
            "models/gemini-embedding-2"
        )

    def test_config_default_survives_resolution(self):
        # Guards the seam between the two changes: if the config default ever
        # drifts to something the resolver rejects, embeddings would silently
        # fall back to deterministic vectors again.
        Config().reset()
        self.assertEqual(
            resolve_embedding_model(Config().embedding_model),
            DEFAULT_EMBEDDING_MODEL
        )


if __name__ == "__main__":
    unittest.main()
