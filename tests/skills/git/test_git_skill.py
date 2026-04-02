import unittest
from unittest.mock import patch, MagicMock
import sys
import io
import os

# Ensure the root workspace with skills directory is in path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from skills.git.scripts.git import main

class TestGitSkillScript(unittest.TestCase):

    @patch('sys.argv', ['git.py', '.', 'status'])
    @patch('subprocess.run')
    def test_git_status_success(self, mock_subprocess):
        # Setup mock subprocess
        mock_subprocess.return_value = MagicMock(returncode=0, stdout="On branch main\nClean", stderr="")
        
        # Execute and capture output
        f = io.StringIO()
        with patch('sys.stdout', f):
            with self.assertRaises(SystemExit) as cm:
                main()
                
        # Assertions
        self.assertEqual(cm.exception.code, 0)
        output = f.getvalue()
        self.assertIn("Success:", output)
        self.assertIn("On branch main", output)
        mock_subprocess.assert_called_once_with(["git", "status"], cwd=os.path.abspath("."), capture_output=True, text=True, check=False)

    @patch('sys.argv', ['git.py', '.', 'commit', '-m', 'test'])
    @patch('subprocess.run')
    def test_git_commit_error(self, mock_subprocess):
        # Setup mock error
        mock_subprocess.return_value = MagicMock(returncode=1, stdout="", stderr="Nothing to commit")
        
        # Execute and capture output
        f = io.StringIO()
        with patch('sys.stdout', f):
            with self.assertRaises(SystemExit) as cm:
                main()
                
        # Assertions
        self.assertEqual(cm.exception.code, 1)
        output = f.getvalue()
        self.assertIn("Error: Exit code 1", output)
        self.assertIn("Stderr:\nNothing to commit", output)

    @patch('sys.argv', ['git.py', '.']) # Missing git command arguments
    def test_git_missing_args(self):
        f = io.StringIO()
        with patch('sys.stdout', f):
            with patch('sys.stderr', f):
                with self.assertRaises(SystemExit) as cm:
                    main()
        
        self.assertEqual(cm.exception.code, 1)
        output = f.getvalue()
        self.assertIn("Error: Git command args are required.", output)

    @patch('sys.argv', ['git.py', 'non_existent_folder', 'status'])
    def test_git_directory_not_found(self):
        f = io.StringIO()
        with patch('sys.stdout', f):
            with self.assertRaises(SystemExit) as cm:
                main()
                
        self.assertEqual(cm.exception.code, 1)
        output = f.getvalue()
        self.assertIn("Error: Directory not found", output)

if __name__ == "__main__":
    unittest.main()
