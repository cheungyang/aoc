import unittest
from unittest.mock import patch, MagicMock
import sys
import os

# Inject root (2 levels up from tests/scripts)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from scripts import query_vault

class TestQueryVaultScript(unittest.TestCase):

    @patch('scripts.query_vault.VaultVectorStore')
    def test_main_with_args(self, mock_vector_store):
        mock_store_instance = MagicMock()
        mock_vector_store.return_value = mock_store_instance
        mock_store_instance.search.return_value = [
            {'path': 'note1.md', 'content': 'content1', 'distance': 0.1}
        ]
        
        with patch('sys.argv', ['query_vault.py', 'test query']):
            with patch('builtins.print') as mock_print:
                query_vault.main()
                
                mock_store_instance.search.assert_called_once_with("test query", limit=10)
                mock_print.assert_any_call("Searching for: 'test query'...")
                mock_print.assert_any_call("[1] File: note1.md")

    @patch('scripts.query_vault.VaultVectorStore')
    @patch('builtins.input', return_value='interactive query')
    def test_main_interactive(self, mock_input, mock_vector_store):
        mock_store_instance = MagicMock()
        mock_vector_store.return_value = mock_store_instance
        mock_store_instance.search.return_value = [
            {'path': 'note1.md', 'content': 'content1', 'distance': 0.1}
        ]
        
        with patch('sys.argv', ['query_vault.py']):
            with patch('builtins.print') as mock_print:
                query_vault.main()
                
                mock_input.assert_called_once()
                mock_store_instance.search.assert_called_once_with("interactive query", limit=10)
                mock_print.assert_any_call("Searching for: 'interactive query'...")

    def test_main_empty_query(self):
        with patch('sys.argv', ['query_vault.py']):
            with patch('builtins.input', return_value=''):
                with patch('builtins.print') as mock_print:
                    query_vault.main()
                    mock_print.assert_any_call("Empty query.")

if __name__ == '__main__':
    unittest.main()
