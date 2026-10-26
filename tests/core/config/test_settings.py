import io
import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.config.settings import Config, Setting

class TestConfig(unittest.TestCase):

    def setUp(self):
        self.config = Config()
        self.config.reset()

    def tearDown(self):
        self.config.reset()

    def test_max_concurrency_default_env_and_clamping(self):
        from core.config.settings import DEFAULT_MAX_CONCURRENCY
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AOC_MAX_CONCURRENCY", None)
            self.assertEqual(self.config.max_concurrency, DEFAULT_MAX_CONCURRENCY)
        with patch.dict(os.environ, {"AOC_MAX_CONCURRENCY": "5"}):
            self.assertEqual(self.config.max_concurrency, 5)
        with patch.dict(os.environ, {"AOC_MAX_CONCURRENCY": "0"}):
            self.assertEqual(self.config.max_concurrency, 1)
        with patch.dict(os.environ, {"AOC_MAX_CONCURRENCY": "lots"}):
            self.assertEqual(self.config.max_concurrency, DEFAULT_MAX_CONCURRENCY)

    def test_max_concurrency_setter_overrides_env(self):
        with patch.dict(os.environ, {"AOC_MAX_CONCURRENCY": "5"}):
            self.config.max_concurrency = 2
            self.assertEqual(self.config.max_concurrency, 2)
            self.config.max_concurrency = 0
            self.assertEqual(self.config.max_concurrency, 1)
            self.config.max_concurrency = None
            self.assertEqual(self.config.max_concurrency, 5)

    def test_control_thread_id_comes_only_from_the_env(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CONTROL_THREAD_ID", None)
            self.assertEqual(self.config.control_thread_id, "")
        with patch.dict(os.environ, {"CONTROL_THREAD_ID": " 42 "}):
            self.assertEqual(self.config.control_thread_id, "42")

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
    def test_langsmith_from_env(self):
        self.config.reset()
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
    def test_langsmith_langchain_v2_fallback(self):
        self.config.reset()
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
            self.assertEqual(self.config.gog_keyring_backend, "file")
            self.assertEqual(self.config.gog_keyring_password, "")
            self.assertEqual(self.config.tasks_db_path, os.path.expanduser("~/pkm/tasks.db"))
            self.assertEqual(self.config.projects_db_path, os.path.expanduser("~/pkm/projects.db"))
            self.assertEqual(self.config.projects_dir, os.path.join(os.path.expanduser("~/pkm"), "vault", "projects"))
            self.assertEqual(self.config.knowledge_db_path, os.path.expanduser("~/pkm/.lancedb"))
            self.assertEqual(self.config.embedding_model, "gemini-embedding-001")
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

        self.config.gog_keyring_backend = "kwallet"
        self.assertEqual(self.config.gog_keyring_backend, "kwallet")

        self.config.gog_keyring_password = "mysecretpassword"
        self.assertEqual(self.config.gog_keyring_password, "mysecretpassword")

        # Environment variable loading
        with patch.dict(os.environ, {
            "GEMINI_API_KEY": "env_gem_key",
            "BRAVE_API_KEY": "env_brave_key",
            "OPENAI_API_KEY": "env_openai_key",
            "ANTHROPIC_API_KEY": "env_anthropic_key",
            "GOG_KEYRING_BACKEND": "pass",
            "GOG_KEYRING_PASSWORD": "env_secret_keyring_pass",
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
            self.assertEqual(self.config.gog_keyring_backend, "pass")
            self.assertEqual(self.config.gog_keyring_password, "env_secret_keyring_pass")
            self.assertEqual(self.config.tasks_db_path, "/env/tasks.db")
            self.assertEqual(self.config.projects_db_path, "/env/projects.db")
            self.assertEqual(self.config.projects_dir, "/env/projects")
            self.assertEqual(self.config.knowledge_db_path, "/env/.lancedb")
            self.assertEqual(self.config.embedding_model, "bge-m3")
            self.assertEqual(self.config.embedding_dimensions, 1024)
            self.assertEqual(self.config.pkm_dir, "/env/pkm")

    def test_local_llm_settings(self):
        # Defaults. localhost, not host.docker.internal: the compose file
        # overrides it for containers, but a direct run talks to this machine.
        with patch.dict(os.environ, {}, clear=True):
            self.config.reset()
            self.assertEqual(self.config.local_llm_base_url, "http://localhost:9379/v1")
            self.assertEqual(self.config.local_llm_timeout, 300.0)

        # Programmatic setters
        self.config.local_llm_base_url = "http://192.168.1.50:9379/v1"
        self.assertEqual(self.config.local_llm_base_url, "http://192.168.1.50:9379/v1")

        self.config.local_llm_timeout = 45
        self.assertEqual(self.config.local_llm_timeout, 45.0)

        # Environment variable loading
        with patch.dict(os.environ, {
            "LOCAL_LLM_BASE_URL": "http://host.docker.internal:9379/v1",
            "LOCAL_LLM_TIMEOUT": "120",
        }, clear=True):
            self.config.reset()
            self.assertEqual(self.config.local_llm_base_url, "http://host.docker.internal:9379/v1")
            self.assertEqual(self.config.local_llm_timeout, 120.0)

        # A non-numeric timeout falls back rather than raising at import time,
        # which would take the whole process down over one typo in .env.
        with patch.dict(os.environ, {"LOCAL_LLM_TIMEOUT": "five minutes"}, clear=True):
            self.config.reset()
            self.assertEqual(self.config.local_llm_timeout, 300.0)

    def test_local_llm_has_no_api_key_setting(self):
        """There is deliberately no `local_llm_api_key`.

        The server does not authenticate, and a knob here would invite someone
        to point the paid OPENAI_API_KEY at a local socket. GraphBuilder passes
        a placeholder constant instead, which cannot be misconfigured.
        """
        self.assertFalse(hasattr(self.config, "local_llm_api_key"))

    def test_context_pruning_settings(self):
        # Default values
        with patch.dict(os.environ, {}, clear=True):
            self.config.reset()
            self.assertTrue(self.config.context_pruning_enabled)
            self.assertEqual(self.config.context_max_tokens, 10000)
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

    def test_reset_forgets_every_override(self):
        """Each setting is declared once, so reset can't miss one. It used to:
        google_tts_api_key kept a test's override through a reset."""
        settings = [name for name, value in vars(Config).items() if isinstance(value, Setting)]
        self.assertGreater(len(settings), 30)
        for name in settings:
            setattr(self.config, name, "1")
        self.config.reset()
        with patch.dict(os.environ, {"GOOGLE_TTS_API_KEY": "env-key"}):
            self.assertEqual(self.config.google_tts_api_key, "env-key")
        self.assertEqual(self.config._overrides, {})

    def test_every_setting_can_be_overridden_and_deleted(self):
        with patch.dict(os.environ, {"LLM_MAX_RETRIES": "4", "TOOL_OUTPUT_MAX_CHARS": "10"}):
            self.config.llm_max_retries = 0
            self.assertEqual(self.config.llm_max_retries, 1)  # the minimum holds for an override too
            self.config.tool_output_max_chars = 500
            self.assertEqual(self.config.tool_output_max_chars, 500)
            del self.config.llm_max_retries
            self.assertEqual(self.config.llm_max_retries, 4)

    def test_a_default_can_follow_another_setting(self):
        with patch.dict(os.environ, {}, clear=True):
            self.config.pkm_dir = "/elsewhere"
            self.assertEqual(self.config.projects_dir, os.path.join("/elsewhere", "vault", "projects"))
            self.config.local_llm_context_tokens = 8192
            self.assertEqual(self.config.local_llm_history_tokens, 4096)

    def test_overriding_puts_back_what_was_there(self):
        self.config.pkm_dir = "/before"
        with self.config.overriding(pkm_dir="/during", tasks_db_path="/during/tasks.db"):
            self.assertEqual(self.config.pkm_dir, "/during")
            self.assertEqual(self.config.tasks_db_path, "/during/tasks.db")
        self.assertEqual(self.config.pkm_dir, "/before")  # the earlier override, back
        self.assertNotIn("tasks_db_path", self.config._overrides)  # none before, none after
        with self.assertRaises(AttributeError):
            with self.config.overriding(pkm_dri="/typo"):
                pass

    def test_a_bad_concurrency_value_is_reported(self):
        from core.config.settings import DEFAULT_MAX_CONCURRENCY
        with patch.dict(os.environ, {"AOC_MAX_CONCURRENCY": "lots"}), \
                patch("sys.stderr", new_callable=io.StringIO) as err:
            self.assertEqual(self.config.max_concurrency, DEFAULT_MAX_CONCURRENCY)
        self.assertIn("ignoring non-integer AOC_MAX_CONCURRENCY='lots'", err.getvalue())

if __name__ == '__main__':
    unittest.main()


