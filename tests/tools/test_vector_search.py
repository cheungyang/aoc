import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.vector_search import vector_search

class TestVectorSearchTool(unittest.TestCase):

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('tools.vector_search.VaultVectorStore')
    @patch('os.path.exists')
    def test_vector_search(self, mock_exists, mock_vector_store, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True
        
        mock_store_instance = MagicMock()
        mock_vector_store.return_value = mock_store_instance
        mock_store_instance.search.return_value = [
            {'path': 'note1.md', 'content': 'content1', 'distance': 0.1},
            {'path': 'note2.md', 'content': 'content2', 'distance': 0.2}
        ]
        
        with patch('os.path.abspath') as mock_abspath:
            mock_abspath.return_value = "/workspace/pkm"
            
            result = vector_search.func(action="vector_search", vault_id="pkm", agent_id="test_agent", search_term="query")
            self.assertIn("File: note1.md", result)
            self.assertIn("Content: content1", result)
            self.assertIn("Distance: 0.1", result)

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('tools.vector_search.VaultVectorStore')
    @patch('os.path.exists')
    def test_update_vectors(self, mock_exists, mock_vector_store, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True
        
        mock_store_instance = MagicMock()
        mock_vector_store.return_value = mock_store_instance
        
        with patch('os.path.abspath') as mock_abspath:
            mock_abspath.return_value = "/workspace/pkm"
            
            result = vector_search.func(action="update_vectors", vault_id="pkm", agent_id="test_agent")
            self.assertEqual(result, "Vectors updated successfully.")
            mock_store_instance.index_vault.assert_called_once()

if __name__ == '__main__':
    unittest.main()
