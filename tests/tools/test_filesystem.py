import unittest
from unittest.mock import patch, mock_open, MagicMock
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.filesystem import filesystem
from core.util import format_tool_response

class TestFilesystemTool(unittest.TestCase):

    # --- Permission Tests ---

    @patch('core.loaders.tools_loader.ToolsLoader')
    def test_permission_denied_tools_loader(self, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = False
        
        instructions = [{"action": "read", "path": "secret/file.txt"}]
        result = filesystem.func(agent_id="software-coder", instructions=instructions)
        
        expected_errors = '<instruction_error action="read" path="secret/file.txt">Error: Agent software-coder does not have permission to perform \'read\' on path secret/file.txt</instruction_error>'
        expected = format_tool_response("filesystem", payload="", errors=expected_errors)
        self.assertEqual(result, expected)

    # --- Read Tests ---

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    @patch('os.path.isfile')
    @patch('builtins.open', new_callable=mock_open, read_data="line 1\nline 2\nline 3\nline 4\n")
    def test_read_full(self, mock_file, mock_isfile, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True
        mock_isfile.return_value = True
        
        instructions = [{"action": "read", "path": "allowed_folder/file.txt"}]
        result = filesystem.func(agent_id="software-coder", instructions=instructions)
        
        expected_payload = '<instruction_result action="read" path="allowed_folder/file.txt">line 1\nline 2\nline 3\nline 4\n</instruction_result>'
        expected = format_tool_response("filesystem", payload=expected_payload, errors="None")
        self.assertEqual(result, expected)

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    @patch('os.path.isfile')
    @patch('builtins.open', new_callable=mock_open, read_data="line 1\nline 2\nline 3\nline 4\n")
    def test_read_sliced_with_line_numbers(self, mock_file, mock_isfile, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True
        mock_isfile.return_value = True
        
        instructions = [{"action": "read", "path": "allowed_folder/file.txt", "start_line": 2, "end_line": 3}]
        result = filesystem.func(agent_id="software-coder", instructions=instructions)
        
        expected_payload = '<instruction_result action="read" path="allowed_folder/file.txt">2: line 2\n3: line 3</instruction_result>'
        expected = format_tool_response("filesystem", payload=expected_payload, errors="None")
        self.assertEqual(result, expected)

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    @patch('os.path.isfile')
    @patch('builtins.open', new_callable=mock_open, read_data="line 1\nline 2\n")
    def test_read_invalid_range(self, mock_file, mock_isfile, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True
        mock_isfile.return_value = True
        
        instructions = [{"action": "read", "path": "allowed_folder/file.txt", "start_line": 5, "end_line": 6}]
        result = filesystem.func(agent_id="software-coder", instructions=instructions)
        self.assertIn("Error: Invalid line range", result)

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    @patch('os.path.isfile')
    def test_read_large_file_truncation_guardrail(self, mock_isfile, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True
        mock_isfile.return_value = True
        
        # 500 lines of data (exceeds 250 lines)
        large_content = "\n".join([f"line {i}: some text content here" for i in range(500)])
        
        with patch('builtins.open', mock_open(read_data=large_content)):
            instructions = [{"action": "read", "path": "allowed_folder/large_file.txt"}]
            result = filesystem.func(agent_id="software-coder", instructions=instructions)
            
            self.assertIn("--- [TRUNCATED: File has 500 lines", result)
            self.assertIn("Use 'start_line' and 'end_line' parameters to inspect specific sections.", result)
            self.assertIn("line 0: some text content here", result)

    # --- Read Image Tests ---

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    @patch('os.path.isfile')
    @patch('PIL.Image.open')
    def test_read_image_success(self, mock_image_open, mock_isfile, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True
        mock_isfile.return_value = True
        
        mock_img = MagicMock()
        mock_img.mode = "RGBA"
        mock_rgb_img = MagicMock()
        mock_img.convert.return_value = mock_rgb_img
        
        # Mock save to write bytes into BytesIO
        def mock_save(buffer, format, quality):
            buffer.write(b"fake_jpeg_data")
        mock_rgb_img.save.side_effect = mock_save
        mock_image_open.return_value.__enter__.return_value = mock_img
        
        instructions = [{"action": "read_image", "path": "images/photo.png"}]
        result = filesystem.func(agent_id="software-coder", instructions=instructions)
        
        import base64
        expected_base64 = base64.b64encode(b"fake_jpeg_data").decode("utf-8")
        self.assertIn(expected_base64, result)
        self.assertIn('<instruction_result action="read_image" path="images/photo.png">', result)

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    def test_read_image_not_found(self, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = False
        
        instructions = [{"action": "read_image", "path": "images/missing.png"}]
        result = filesystem.func(agent_id="software-coder", instructions=instructions)
        self.assertIn("Error: File not found at images/missing.png", result)

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    @patch('os.path.isfile')
    def test_read_image_not_a_file(self, mock_isfile, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True
        mock_isfile.return_value = False
        
        instructions = [{"action": "read_image", "path": "images/folder"}]
        result = filesystem.func(agent_id="software-coder", instructions=instructions)
        self.assertIn("Error: Path images/folder is not a file.", result)

    # --- Write, Overwrite, Append Tests ---

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    @patch('os.makedirs')
    @patch('builtins.open', new_callable=mock_open)
    def test_write_creates_dirs_if_not_exists(self, mock_file, mock_makedirs, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = False
        
        instructions = [{"action": "write", "path": "allowed_folder/new_dir/file.txt", "content": "data"}]
        result = filesystem.func(agent_id="software-coder", instructions=instructions)
        
        expected_payload = '<instruction_result action="write" path="allowed_folder/new_dir/file.txt">Successfully wrote to allowed_folder/new_dir/file.txt</instruction_result>'
        expected = format_tool_response("filesystem", payload=expected_payload, errors="None")
        self.assertEqual(result, expected)
        mock_makedirs.assert_called_once()

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    def test_write_fails_if_exists(self, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True
        
        instructions = [{"action": "write", "path": "allowed_folder/file.txt", "content": "data"}]
        result = filesystem.func(agent_id="software-coder", instructions=instructions)
        
        expected_errors = '<instruction_error action="write" path="allowed_folder/file.txt">Error: File already exists at allowed_folder/file.txt. Use \'overwrite\' if intentional.</instruction_error>'
        expected = format_tool_response("filesystem", payload="", errors=expected_errors)
        self.assertEqual(result, expected)

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    @patch('os.makedirs')
    @patch('builtins.open', new_callable=mock_open)
    def test_overwrite_succeeds_if_exists(self, mock_file, mock_makedirs, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True
        
        instructions = [{"action": "overwrite", "path": "allowed_folder/file.txt", "content": "data"}]
        result = filesystem.func(agent_id="software-coder", instructions=instructions)
        
        expected_payload = '<instruction_result action="overwrite" path="allowed_folder/file.txt">Successfully overwrote allowed_folder/file.txt</instruction_result>'
        expected = format_tool_response("filesystem", payload=expected_payload, errors="None")
        self.assertEqual(result, expected)

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    @patch('os.path.isfile')
    @patch('builtins.open', new_callable=mock_open)
    def test_append_allowed(self, mock_file, mock_isfile, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True
        mock_isfile.return_value = True
        
        instructions = [{"action": "append", "path": "allowed_folder/file.txt", "content": "new_line\n"}]
        result = filesystem.func(agent_id="software-coder", instructions=instructions)
        
        expected_payload = '<instruction_result action="append" path="allowed_folder/file.txt">Successfully appended to allowed_folder/file.txt</instruction_result>'
        expected = format_tool_response("filesystem", payload=expected_payload, errors="None")
        self.assertEqual(result, expected)
        mock_file().write.assert_called_with("new_line\n")

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    @patch('os.makedirs')
    @patch('builtins.open', new_callable=mock_open)
    def test_append_creates_dirs_and_file_if_not_exists(self, mock_file, mock_makedirs, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = False
        
        instructions = [{"action": "append", "path": "allowed_folder/nested/new_file.txt", "content": "hello\n"}]
        result = filesystem.func(agent_id="software-coder", instructions=instructions)
        
        expected_payload = '<instruction_result action="append" path="allowed_folder/nested/new_file.txt">Successfully appended to allowed_folder/nested/new_file.txt</instruction_result>'
        expected = format_tool_response("filesystem", payload=expected_payload, errors="None")
        self.assertEqual(result, expected)
        mock_makedirs.assert_called_once()
        mock_file().write.assert_called_with("hello\n")

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    @patch('os.path.isfile')
    def test_append_fails_if_path_is_directory(self, mock_isfile, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True
        mock_isfile.return_value = False
        
        instructions = [{"action": "append", "path": "allowed_folder/existing_directory", "content": "hello\n"}]
        result = filesystem.func(agent_id="software-coder", instructions=instructions)
        
        expected_errors = '<instruction_error action="append" path="allowed_folder/existing_directory">Error: Path allowed_folder/existing_directory is not a file.</instruction_error>'
        expected = format_tool_response("filesystem", payload="", errors=expected_errors)
        self.assertEqual(result, expected)

    def test_append_integration_auto_create(self):
        import tempfile
        from tools.filesystem import _append
        with tempfile.TemporaryDirectory() as tmp_dir:
            nested_file = os.path.join(tmp_dir, "new_folder", "log.md")
            
            # 1. Append to non-existent file in non-existent directory -> should auto-create directory and file
            res, err = _append(nested_file, "Line 1\n")
            self.assertEqual(err, "None")
            self.assertTrue(os.path.exists(nested_file))
            with open(nested_file, "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), "Line 1\n")
                
            # 2. Append again -> should append without overwriting
            res2, err2 = _append(nested_file, "Line 2\n")
            self.assertEqual(err2, "None")
            with open(nested_file, "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), "Line 1\nLine 2\n")

    # --- Replace Block Tests ---

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    @patch('os.path.isfile')
    @patch('builtins.open', new_callable=mock_open, read_data="def foo():\n    return 1\n")
    def test_replace_block_exact(self, mock_file, mock_isfile, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True
        mock_isfile.return_value = True
        
        instructions = [{
            "action": "replace_block",
            "path": "file.py",
            "old_block": "    return 1",
            "new_block": "    return 2"
        }]
        result = filesystem.func(agent_id="software-coder", instructions=instructions)
        
        expected_payload = '<instruction_result action="replace_block" path="file.py">Successfully replaced block in file.py</instruction_result>'
        expected = format_tool_response("filesystem", payload=expected_payload, errors="None")
        self.assertEqual(result, expected)
        mock_file().write.assert_called_with("def foo():\n    return 2\n")

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    @patch('os.path.isfile')
    @patch('builtins.open', new_callable=mock_open, read_data="def foo():  \r\n    return 1  \r\n")
    def test_replace_block_whitespace_resilient(self, mock_file, mock_isfile, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True
        mock_isfile.return_value = True
        
        # old_block without trailing spaces and with \n
        instructions = [{
            "action": "replace_block",
            "path": "file.py",
            "old_block": "def foo():\n    return 1",
            "new_block": "def foo():\n    return 42"
        }]
        result = filesystem.func(agent_id="software-coder", instructions=instructions)
        
        expected_payload = '<instruction_result action="replace_block" path="file.py">Successfully replaced block in file.py</instruction_result>'
        expected = format_tool_response("filesystem", payload=expected_payload, errors="None")
        self.assertEqual(result, expected)

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    @patch('os.path.isfile')
    @patch('builtins.open', new_callable=mock_open, read_data="val = 1\nval = 1\n")
    def test_replace_block_ambiguous(self, mock_file, mock_isfile, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True
        mock_isfile.return_value = True
        
        instructions = [{
            "action": "replace_block",
            "path": "file.py",
            "old_block": "val = 1",
            "new_block": "val = 2"
        }]
        result = filesystem.func(agent_id="software-coder", instructions=instructions)
        self.assertIn("ambiguous", result)

    # --- Ls, Move, Delete, Rmdir Tests ---

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    @patch('os.path.isdir')
    @patch('os.listdir')
    def test_ls_allowed(self, mock_listdir, mock_isdir, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True
        mock_isdir.return_value = True
        mock_listdir.return_value = ["file1.txt", "folder1"]
        
        instructions = [{"action": "ls", "path": "allowed_folder/"}]
        result = filesystem.func(agent_id="software-coder", instructions=instructions)
        
        expected_payload = '<instruction_result action="ls" path="allowed_folder/">file1.txt\nfolder1</instruction_result>'
        expected = format_tool_response("filesystem", payload=expected_payload, errors="None")
        self.assertEqual(result, expected)

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    @patch('os.makedirs')
    @patch('shutil.move')
    def test_move_allowed(self, mock_shutil_move, mock_makedirs, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True
        
        instructions = [{"action": "move", "path": "source.txt", "destination": "dest/target.txt"}]
        result = filesystem.func(agent_id="software-coder", instructions=instructions)
        
        expected_payload = '<instruction_result action="move" path="source.txt">Successfully moved source.txt to dest/target.txt</instruction_result>'
        expected = format_tool_response("filesystem", payload=expected_payload, errors="None")
        self.assertEqual(result, expected)
        mock_shutil_move.assert_called_once_with("source.txt", "dest/target.txt")

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    @patch('os.path.isfile')
    @patch('os.remove')
    def test_delete_allowed(self, mock_remove, mock_isfile, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True
        mock_isfile.return_value = True
        
        instructions = [{"action": "delete", "path": "allowed_folder/file.txt"}]
        result = filesystem.func(agent_id="software-coder", instructions=instructions)
        
        expected_payload = '<instruction_result action="delete" path="allowed_folder/file.txt">Successfully deleted file allowed_folder/file.txt</instruction_result>'
        expected = format_tool_response("filesystem", payload=expected_payload, errors="None")
        self.assertEqual(result, expected)

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    @patch('os.path.isdir')
    @patch('os.rmdir')
    def test_rmdir_empty(self, mock_rmdir, mock_isdir, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True
        mock_isdir.return_value = True
        
        instructions = [{"action": "rmdir", "path": "empty_folder"}]
        result = filesystem.func(agent_id="software-coder", instructions=instructions)
        
        expected_payload = '<instruction_result action="rmdir" path="empty_folder">Successfully removed directory empty_folder</instruction_result>'
        expected = format_tool_response("filesystem", payload=expected_payload, errors="None")
        self.assertEqual(result, expected)
        mock_rmdir.assert_called_once_with("empty_folder")

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    @patch('os.path.isdir')
    @patch('os.rmdir', side_effect=OSError("Directory not empty"))
    def test_rmdir_non_empty_fails(self, mock_rmdir, mock_isdir, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True
        mock_isdir.return_value = True
        
        instructions = [{"action": "rmdir", "path": "non_empty_folder"}]
        result = filesystem.func(agent_id="software-coder", instructions=instructions)
        
        self.assertIn("Directory not empty or cannot be removed", result)

    # --- Find & Grep (Search & Guardrail) Tests ---

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    @patch('os.path.isdir')
    @patch('os.walk')
    def test_find_matching(self, mock_walk, mock_isdir, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True
        mock_isdir.return_value = True
        
        mock_walk.return_value = [
            ("src", ["utils"], ["main.py", "helper.py"]),
            ("src/utils", [], ["calc.py", "readme.md"])
        ]
        
        instructions = [{"action": "find", "path": "src", "pattern": "*.py"}]
        result = filesystem.func(agent_id="software-coder", instructions=instructions)
        
        self.assertIn("main.py", result)
        self.assertIn("helper.py", result)
        self.assertIn("calc.py", result)
        self.assertNotIn("readme.md", result)

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    @patch('os.path.isdir')
    @patch('os.walk')
    def test_find_guardrail_50_limit(self, mock_walk, mock_isdir, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True
        mock_isdir.return_value = True
        
        # 55 matching files
        file_list = [f"file_{i}.py" for i in range(55)]
        mock_walk.return_value = [
            ("src", [], file_list)
        ]
        
        instructions = [{"action": "find", "path": "src", "pattern": "*.py"}]
        result = filesystem.func(agent_id="software-coder", instructions=instructions)
        
        self.assertIn("Error: Your search returned 55 results. This is too broad and will exceed your context window. Please refine your search_string or specify a deeper directory path.", result)

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    @patch('os.path.isdir')
    @patch('os.path.isfile', return_value=False)
    @patch('os.walk')
    @patch('builtins.open', new_callable=mock_open, read_data="line 1\nTARGET_STRING in line 2\nline 3\n")
    def test_grep_matching(self, mock_file, mock_walk, mock_isfile, mock_isdir, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True
        mock_isdir.return_value = True
        
        mock_walk.return_value = [
            ("src", [], ["main.py"])
        ]
        
        instructions = [{"action": "grep", "path": "src", "search_string": "TARGET_STRING"}]
        result = filesystem.func(agent_id="software-qa", instructions=instructions)
        
        self.assertIn("main.py:2: TARGET_STRING in line 2", result)

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    @patch('os.path.isdir')
    @patch('os.path.isfile', return_value=False)
    @patch('os.walk')
    def test_grep_guardrail_50_limit(self, mock_walk, mock_isfile, mock_isdir, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True
        mock_isdir.return_value = True
        
        file_list = [f"file_{i}.py" for i in range(60)]
        mock_walk.return_value = [
            ("src", [], file_list)
        ]
        
        with patch('builtins.open', mock_open(read_data="MATCH\n")):
            instructions = [{"action": "grep", "path": "src", "search_string": "MATCH"}]
            result = filesystem.func(agent_id="software-coder", instructions=instructions)
            self.assertIn("This is too broad and will exceed your context window", result)

    # --- Additional Edge Case & Batch Tests ---

    @patch('core.loaders.tools_loader.ToolsLoader')
    def test_move_missing_destination(self, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        
        instructions = [{"action": "move", "path": "source.txt"}]
        result = filesystem.func(agent_id="software-coder", instructions=instructions)
        self.assertIn("Error: 'destination' is required for 'move' action.", result)

    @patch('core.loaders.tools_loader.ToolsLoader')
    def test_move_destination_permission_denied(self, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        # Source allowed, destination denied
        mock_loader.check_permission.side_effect = lambda aid, tool, act, path: path == "source.txt"
        
        instructions = [{"action": "move", "path": "source.txt", "destination": "forbidden/target.txt"}]
        result = filesystem.func(agent_id="software-coder", instructions=instructions)
        self.assertIn("does not have permission to perform 'move' on destination forbidden/target.txt", result)

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    @patch('os.path.isfile')
    @patch('builtins.open', new_callable=mock_open, read_data="hello world\n")
    def test_replace_block_not_found(self, mock_file, mock_isfile, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True
        mock_isfile.return_value = True
        
        instructions = [{
            "action": "replace_block",
            "path": "file.py",
            "old_block": "nonexistent block",
            "new_block": "replacement"
        }]
        result = filesystem.func(agent_id="software-coder", instructions=instructions)
        self.assertIn("Error: old_block not found in file.py", result)

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    @patch('os.path.isfile')
    @patch('builtins.open', new_callable=mock_open, read_data="line 1\nline 2\n")
    def test_grep_single_file(self, mock_file, mock_isfile, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True
        mock_isfile.return_value = True
        
        instructions = [{"action": "grep", "path": "file.py", "search_string": "line 2"}]
        result = filesystem.func(agent_id="software-qa", instructions=instructions)
        self.assertIn("file.py:2: line 2", result)

    def test_missing_agent_id(self):
        result = filesystem.func(agent_id="", instructions=[{"action": "read", "path": "a.txt"}])
        self.assertIn("Error: agent_id is required", result)

    def test_permission_denied_single_instruction_in_batch(self):
        instructions = [
            {"action": "read", "path": "a.txt"},
            {"action": "delete", "path": "b.txt"}
        ]
        with patch('core.loaders.tools_loader.ToolsLoader') as mock_tools_loader:
            mock_loader = MagicMock()
            mock_tools_loader.return_value = mock_loader
            # Allow read on a.txt, deny delete on b.txt
            mock_loader.check_permission.side_effect = lambda aid, tool, act, path: act == "read"
            
            with patch('os.path.exists', return_value=True), patch('os.path.isfile', return_value=True), patch('builtins.open', mock_open(read_data="content")):
                result = filesystem.func(agent_id="software-coder", instructions=instructions)
                self.assertIn('<instruction_result action="read"', result)
                self.assertIn('<instruction_error action="delete" path="b.txt">Error: Agent software-coder does not have permission to perform \'delete\' on path b.txt</instruction_error>', result)

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists', return_value=True)
    @patch('os.path.isfile', return_value=True)
    @patch('builtins.open', new_callable=mock_open, read_data="data")
    def test_multi_instruction_chain(self, mock_file, mock_isfile, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        
        instructions = [
            {"action": "read", "path": "file1.txt"},
            {"action": "append", "path": "file2.txt", "content": "more"}
        ]
        result = filesystem.func(agent_id="software-coder", instructions=instructions)
        self.assertIn('<instruction_result action="read" path="file1.txt">data</instruction_result>', result)
        self.assertIn('<instruction_result action="append" path="file2.txt">Successfully appended to file2.txt</instruction_result>', result)

    def test_agent_config_permissions_from_agent_json(self):
        from core.loaders.tools_loader import ToolsLoader
        tools_loader = ToolsLoader()
        tools_loader.clear_permissions_cache()
        
        # software-planner has full permissions on pkm/wiki/software
        planner_path = "pkm/wiki/software/spec.md"
        self.assertTrue(tools_loader.check_permission("software-planner", "filesystem", "write", planner_path))
        self.assertTrue(tools_loader.check_permission("software-planner", "filesystem", "read", planner_path))
        self.assertTrue(tools_loader.check_permission("software-planner", "filesystem", "ls", planner_path))
        
        # software-planner only has read/find/grep/ls on root . (no write)
        root_file = "README.md"
        self.assertTrue(tools_loader.check_permission("software-planner", "filesystem", "read", root_file))
        self.assertFalse(tools_loader.check_permission("software-planner", "filesystem", "write", root_file))

if __name__ == '__main__':
    unittest.main()

