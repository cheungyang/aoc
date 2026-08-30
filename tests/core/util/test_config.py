import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.util.config import Config

class TestConfig(unittest.TestCase):

    def setUp(self):
        self.config = Config()
        self.config.reset()

    def tearDown(self):
        self.config.reset()

    def test_default_values(self):
        self.config.is_debug = False
        self.config.debug_channel = ""
        self.assertFalse(self.config.is_debug)
        self.assertEqual(self.config.debug_channel, "")

    def test_setter_and_getter(self):
        self.config.is_debug = True
        self.assertTrue(self.config.is_debug)

        self.config.is_debug = False
        self.assertFalse(self.config.is_debug)

        self.config.is_debug = "true"
        self.assertTrue(self.config.is_debug)

        self.config.is_debug = "false"
        self.assertFalse(self.config.is_debug)

        self.config.debug_channel = "debug-room"
        self.assertEqual(self.config.debug_channel, "debug-room")

    @patch.dict(os.environ, {"IS_DEBUG": "true", "DEBUG_CHANNEL": "test-debug-channel"}, clear=False)
    def test_load_from_env_is_debug_true(self):
        self.config.load_from_env()
        self.assertTrue(self.config.is_debug)
        self.assertEqual(self.config.debug_channel, "test-debug-channel")

    @patch.dict(os.environ, {"IS_DEBUG": "false", "DEBUG_CHANNEL": "general"}, clear=False)
    def test_load_from_env_is_debug_false(self):
        self.config.load_from_env()
        self.assertFalse(self.config.is_debug)
        self.assertEqual(self.config.debug_channel, "general")

    def test_is_channel_allowed_when_debug_disabled(self):
        self.config.is_debug = False
        self.config.debug_channel = "debug-room"

        # Normal channels allowed
        self.assertTrue(self.config.is_channel_allowed("general"))
        self.assertTrue(self.config.is_channel_allowed("random"))
        
        # debug_channel should be ignored when debug is disabled
        self.assertFalse(self.config.is_channel_allowed("debug-room"))
        self.assertFalse(self.config.is_channel_allowed("#debug-room"))

        mock_general = MagicMock()
        mock_general.name = "general"
        mock_general.id = "111"
        mock_general.parent = None
        self.assertTrue(self.config.is_channel_allowed(mock_general))

        mock_debug = MagicMock()
        mock_debug.name = "debug-room"
        mock_debug.id = "222"
        mock_debug.parent = None
        self.assertFalse(self.config.is_channel_allowed(mock_debug))

        # Thread under debug_room should also be ignored
        mock_thread_in_debug = MagicMock()
        mock_thread_in_debug.name = "sub-thread"
        mock_thread_in_debug.id = "333"
        mock_thread_in_debug.parent = mock_debug
        self.assertFalse(self.config.is_channel_allowed(mock_thread_in_debug))

    def test_is_channel_allowed_when_debug_enabled_string(self):
        self.config.is_debug = True
        self.config.debug_channel = "debug-room"

        self.assertTrue(self.config.is_channel_allowed("debug-room"))
        self.assertTrue(self.config.is_channel_allowed("#debug-room"))
        self.assertFalse(self.config.is_channel_allowed("general"))
        self.assertFalse(self.config.is_channel_allowed(""))
        self.assertFalse(self.config.is_channel_allowed(None))

    def test_is_channel_allowed_when_debug_enabled_channel_object(self):
        self.config.is_debug = True
        self.config.debug_channel = "debug-room"

        # Matching channel object
        match_ch = MagicMock()
        match_ch.name = "debug-room"
        match_ch.id = "12345"
        match_ch.parent = None
        self.assertTrue(self.config.is_channel_allowed(match_ch))

        # Non-matching channel object
        other_ch = MagicMock()
        other_ch.name = "general"
        other_ch.id = "67890"
        other_ch.parent = None
        self.assertFalse(self.config.is_channel_allowed(other_ch))

    def test_is_channel_allowed_by_channel_id(self):
        self.config.is_debug = True
        self.config.debug_channel = "12345"

        match_ch = MagicMock()
        match_ch.name = "some-name"
        match_ch.id = "12345"
        match_ch.parent = None
        self.assertTrue(self.config.is_channel_allowed(match_ch))

        other_ch = MagicMock()
        other_ch.name = "other-name"
        other_ch.id = "99999"
        other_ch.parent = None
        self.assertFalse(self.config.is_channel_allowed(other_ch))

    def test_is_channel_allowed_thread_in_debug_channel(self):
        self.config.is_debug = True
        self.config.debug_channel = "debug-room"

        parent_ch = MagicMock()
        parent_ch.name = "debug-room"
        parent_ch.id = "11111"

        thread = MagicMock()
        thread.name = "test-thread"
        thread.id = "22222"
        thread.parent = parent_ch

        self.assertTrue(self.config.is_channel_allowed(thread))

    def test_is_channel_allowed_thread_in_other_channel(self):
        self.config.is_debug = True
        self.config.debug_channel = "debug-room"

        parent_ch = MagicMock()
        parent_ch.name = "general"
        parent_ch.id = "33333"

        thread = MagicMock()
        thread.name = "test-thread"
        thread.id = "44444"
        thread.parent = parent_ch

        self.assertFalse(self.config.is_channel_allowed(thread))

    def test_is_channel_allowed_when_debug_channel_empty(self):
        self.config.is_debug = True
        self.config.debug_channel = ""

        self.assertFalse(self.config.is_channel_allowed("general"))
        self.assertFalse(self.config.is_channel_allowed("debug"))

    @patch.dict(os.environ, {}, clear=True)
    def test_langsmith_default_values(self):
        self.config.reset()
        self.assertFalse(self.config.langsmith_tracing)
        self.assertEqual(self.config.langsmith_project, "default")
        self.assertEqual(self.config.langsmith_endpoint, "https://api.smith.langchain.com")
        self.assertEqual(self.config.langsmith_api_key, "")
        self.assertEqual(self.config.langsmith_workspace_id, "")

    def test_langsmith_setters(self):
        self.config.langsmith_tracing = True
        self.assertTrue(self.config.langsmith_tracing)
        self.config.langsmith_tracing = "true"
        self.assertTrue(self.config.langsmith_tracing)
        self.config.langsmith_tracing = "false"
        self.assertFalse(self.config.langsmith_tracing)

        self.config.langsmith_project = "my-custom-project"
        self.assertEqual(self.config.langsmith_project, "my-custom-project")

        self.config.langsmith_endpoint = "https://eu.api.smith.langchain.com"
        self.assertEqual(self.config.langsmith_endpoint, "https://eu.api.smith.langchain.com")

        self.config.langsmith_api_key = "lsv2_pt_testkey"
        self.assertEqual(self.config.langsmith_api_key, "lsv2_pt_testkey")

        self.config.langsmith_workspace_id = "ws-12345"
        self.assertEqual(self.config.langsmith_workspace_id, "ws-12345")

    @patch.dict(os.environ, {
        "LANGSMITH_TRACING": "true",
        "LANGSMITH_PROJECT": "project-test",
        "LANGSMITH_ENDPOINT": "https://eu.api.smith.langchain.com",
        "LANGSMITH_API_KEY": "lsv2_pt_123",
        "LANGSMITH_WORKSPACE_ID": "ws-test"
    }, clear=False)
    def test_load_from_env_langsmith(self):
        self.config.load_from_env()
        self.assertTrue(self.config.langsmith_tracing)
        self.assertEqual(self.config.langsmith_project, "project-test")
        self.assertEqual(self.config.langsmith_endpoint, "https://eu.api.smith.langchain.com")
        self.assertEqual(self.config.langsmith_api_key, "lsv2_pt_123")
        self.assertEqual(self.config.langsmith_workspace_id, "ws-test")

    @patch.dict(os.environ, {
        "LANGCHAIN_TRACING_V2": "true",
        "LANGCHAIN_PROJECT": "v2-project",
        "LANGCHAIN_ENDPOINT": "https://apac.api.smith.langchain.com",
        "LANGCHAIN_API_KEY": "lsv2_pt_v2",
    }, clear=True)
    def test_load_from_env_langchain_v2_fallback(self):
        self.config.load_from_env()
        self.assertTrue(self.config.langsmith_tracing)
        self.assertEqual(self.config.langsmith_project, "v2-project")
        self.assertEqual(self.config.langsmith_endpoint, "https://apac.api.smith.langchain.com")
        self.assertEqual(self.config.langsmith_api_key, "lsv2_pt_v2")

    def test_generic_get_method(self):
        with patch.dict(os.environ, {"CUSTOM_KEY": "custom_value"}, clear=False):
            self.assertEqual(self.config.get("CUSTOM_KEY"), "custom_value")
            self.assertEqual(self.config.get("NON_EXISTENT_KEY", "default_val"), "default_val")

    def test_api_keys_and_paths(self):
        # Default / empty state
        with patch.dict(os.environ, {}, clear=True):
            self.config.reset()
            self.assertEqual(self.config.gemini_api_key, "")
            self.assertEqual(self.config.brave_api_key, "")
            self.assertEqual(self.config.openai_api_key, "")
            self.assertEqual(self.config.anthropic_api_key, "")
            self.assertEqual(self.config.tasks_db_path, os.path.expanduser("~/pkm/tasks.db"))
            self.assertEqual(self.config.projects_db_path, os.path.expanduser("~/pkm/projects.db"))
            self.assertEqual(self.config.projects_dir, os.path.join(os.path.expanduser("~/pkm"), "vault", "projects"))
            self.assertEqual(self.config.knowledge_db_path, os.path.expanduser("~/pkm/.lancedb"))
            self.assertEqual(self.config.embedding_model, "text-embedding-3-small")
            self.assertEqual(self.config.embedding_dimensions, 1536)
            self.assertEqual(self.config.pkm_dir, os.path.expanduser("~/pkm"))

        # Programmatic setters
        self.config.gemini_api_key = "gem_key"
        self.assertEqual(self.config.gemini_api_key, "gem_key")

        self.config.brave_api_key = "brave_key"
        self.assertEqual(self.config.brave_api_key, "brave_key")

        self.config.openai_api_key = "openai_key"
        self.assertEqual(self.config.openai_api_key, "openai_key")

        self.config.anthropic_api_key = "anthropic_key"
        self.assertEqual(self.config.anthropic_api_key, "anthropic_key")

        self.config.tasks_db_path = "/custom/path/tasks.db"
        self.assertEqual(self.config.tasks_db_path, "/custom/path/tasks.db")

        self.config.projects_db_path = "/custom/path/projects.db"
        self.assertEqual(self.config.projects_db_path, "/custom/path/projects.db")

        self.config.projects_dir = "/custom/path/vault/projects"
        self.assertEqual(self.config.projects_dir, "/custom/path/vault/projects")

        self.config.knowledge_db_path = "/custom/path/.lancedb"
        self.assertEqual(self.config.knowledge_db_path, "/custom/path/.lancedb")

        self.config.embedding_model = "text-embedding-3-large"
        self.assertEqual(self.config.embedding_model, "text-embedding-3-large")

        self.config.embedding_dimensions = 3072
        self.assertEqual(self.config.embedding_dimensions, 3072)

        self.config.pkm_dir = "/custom/pkm"
        self.assertEqual(self.config.pkm_dir, "/custom/pkm")

        # Environment variable loading
        with patch.dict(os.environ, {
            "GEMINI_API_KEY": "env_gem_key",
            "BRAVE_API_KEY": "env_brave_key",
            "OPENAI_API_KEY": "env_openai_key",
            "ANTHROPIC_API_KEY": "env_anthropic_key",
            "TASKS_DB_PATH": "/env/tasks.db",
            "PROJECTS_DB_PATH": "/env/projects.db",
            "PROJECTS_DIR": "/env/projects",
            "KNOWLEDGE_DB_PATH": "/env/.lancedb",
            "EMBEDDING_MODEL": "bge-m3",
            "EMBEDDING_DIMENSIONS": "1024",
            "PKM_DIR": "/env/pkm"
        }, clear=True):
            self.config.reset()
            self.assertEqual(self.config.gemini_api_key, "env_gem_key")
            self.assertEqual(self.config.brave_api_key, "env_brave_key")
            self.assertEqual(self.config.openai_api_key, "env_openai_key")
            self.assertEqual(self.config.anthropic_api_key, "env_anthropic_key")
            self.assertEqual(self.config.tasks_db_path, "/env/tasks.db")
            self.assertEqual(self.config.projects_db_path, "/env/projects.db")
            self.assertEqual(self.config.projects_dir, "/env/projects")
            self.assertEqual(self.config.knowledge_db_path, "/env/.lancedb")
            self.assertEqual(self.config.embedding_model, "bge-m3")
            self.assertEqual(self.config.embedding_dimensions, 1024)
            self.assertEqual(self.config.pkm_dir, "/env/pkm")

    def test_context_pruning_settings(self):
        # Default values
        with patch.dict(os.environ, {}, clear=True):
            self.config.reset()
            self.assertTrue(self.config.context_pruning_enabled)
            self.assertEqual(self.config.context_max_tokens, 30000)
            self.assertEqual(self.config.context_window_messages, 30)
            self.assertEqual(self.config.context_summary_max_tokens, 1000)

        # Setters
        self.config.context_pruning_enabled = False
        self.assertFalse(self.config.context_pruning_enabled)
        self.config.context_pruning_enabled = "true"
        self.assertTrue(self.config.context_pruning_enabled)

        self.config.context_max_tokens = 20000
        self.assertEqual(self.config.context_max_tokens, 20000)

        self.config.context_window_messages = 10
        self.assertEqual(self.config.context_window_messages, 10)

        self.config.context_summary_max_tokens = 500
        self.assertEqual(self.config.context_summary_max_tokens, 500)

        # Environment loading
        with patch.dict(os.environ, {
            "CONTEXT_PRUNING_ENABLED": "false",
            "CONTEXT_MAX_TOKENS": "15000",
            "CONTEXT_WINDOW_MESSAGES": "8",
            "CONTEXT_SUMMARY_MAX_TOKENS": "400"
        }, clear=True):
            self.config.reset()
            self.assertFalse(self.config.context_pruning_enabled)
            self.assertEqual(self.config.context_max_tokens, 15000)
            self.assertEqual(self.config.context_window_messages, 8)
            self.assertEqual(self.config.context_summary_max_tokens, 400)


if __name__ == '__main__':
    unittest.main()


