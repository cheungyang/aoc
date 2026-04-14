import unittest
from unittest.mock import patch, MagicMock, mock_open
import os
import sys

# Inject root (3 levels up from tests/core/memory)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.memory.vault_vector_store import VaultVectorStore

class TestVaultVectorStore(unittest.TestCase):

    @patch('core.memory.vault_vector_store.chromadb.PersistentClient')
    @patch('core.memory.vault_vector_store.GoogleGenerativeAIEmbeddings')
    def setUp(self, mock_embeddings, mock_chroma):
        self.mock_chroma = mock_chroma
        self.mock_embeddings = mock_embeddings
        
        self.mock_client = MagicMock()
        self.mock_chroma.return_value = self.mock_client
        
        self.mock_collection = MagicMock()
        self.mock_client.get_or_create_collection.return_value = self.mock_collection
        
        self.mock_embed_instance = MagicMock()
        self.mock_embeddings.return_value = self.mock_embed_instance
        
        self.vault_dir = "/mock/vault"
        self.persist_dir = "/mock/persist"
        
        # Mock environment variable
        with patch.dict('os.environ', {'GEMINI_API_KEY': 'test_key'}):
            self.store = VaultVectorStore(self.vault_dir, self.persist_dir)

    def test_init(self):
        self.mock_chroma.assert_called_once_with(path=self.persist_dir)
        self.mock_client.get_or_create_collection.assert_called_once_with(name="pkm_vault")
        self.mock_embeddings.assert_called_once_with(model="models/gemini-embedding-001", api_key="test_key")

    @patch('os.walk')
    @patch('builtins.open', new_callable=mock_open, read_data="content paragraph1\n\ncontent paragraph2")
    def test_index_vault(self, mock_file, mock_walk):
        mock_walk.return_value = [
            (os.path.join(self.vault_dir, 'ticktick'), [], ['file1.md'])
        ]
        
        self.mock_embed_instance.embed_documents.return_value = [[0.1, 0.2], [0.3, 0.4]]
        
        self.store.index_vault()
        
        self.mock_embed_instance.embed_documents.assert_called_once()
        self.mock_collection.upsert.assert_called_once()
        
        # Check that documents were split
        call_args = self.mock_collection.upsert.call_args[1]
        self.assertEqual(len(call_args['documents']), 2)
        self.assertEqual(call_args['documents'][0], "content paragraph1")
        self.assertEqual(call_args['documents'][1], "content paragraph2")

    def test_search(self):
        self.mock_embed_instance.embed_query.return_value = [0.1, 0.2]
        self.mock_collection.query.return_value = {
            'documents': [["result content"]],
            'metadatas': [[{'path': 'file1.md'}]],
            'distances': [[0.1]]
        }
        
        results = self.store.search("query")
        
        self.mock_embed_instance.embed_query.assert_called_once_with("query")
        self.mock_collection.query.assert_called_once()
        
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['path'], 'file1.md')
        self.assertEqual(results[0]['content'], 'result content')
        self.assertEqual(results[0]['distance'], 0.1)

    @patch('os.walk')
    @patch('builtins.open', new_callable=mock_open, read_data="---\ntitle: Test File\n---\n#a/tag1 #p/tag2\n🔺 Priority High\n- [ ] Task 1\n- [ ] Task 2\n\nParagraph 1 after tasks.")
    def test_index_vault_with_extraction(self, mock_file, mock_walk):
        mock_walk.return_value = [
            (os.path.join(self.vault_dir, 'vault', 'pages', 'projects'), [], ['file2.md'])
        ]
        
        self.mock_embed_instance.embed_documents.return_value = [[0.1], [0.2], [0.3]]
        
        self.store.index_vault()
        
        self.mock_collection.upsert.assert_called_once()
        call_args = self.mock_collection.upsert.call_args[1]
        
        self.assertEqual(len(call_args['documents']), 3)
        
        self.assertIn("File Summary for vault/pages/projects/file2.md", call_args['documents'][0])
        self.assertIn("Tags: a/tag1, p/tag2", call_args['documents'][0])
        self.assertIn("Priorities: Highest", call_args['documents'][0])
        self.assertIn("Tasks:\n- Task 1\n- Task 2", call_args['documents'][0])
        
        self.assertEqual(call_args['metadatas'][0]['tags'], "a/tag1,p/tag2")
        self.assertEqual(call_args['metadatas'][0]['priority'], "Highest")

if __name__ == '__main__':
    unittest.main()
