import os
from typing import Any, Optional
from dotenv import load_dotenv

# Ensure .env is loaded on import of config
load_dotenv()


class Config:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Config, cls).__new__(cls)
            cls._instance._is_debug = None
            cls._instance._debug_channel = None
            cls._instance._langsmith_tracing = None
            cls._instance._langsmith_project = None
            cls._instance._langsmith_endpoint = None
            cls._instance._langsmith_api_key = None
            cls._instance._langsmith_workspace_id = None
            cls._instance._gemini_api_key = None
            cls._instance._brave_api_key = None
            cls._instance._openai_api_key = None
            cls._instance._anthropic_api_key = None
            cls._instance._runway_api_key = None
            cls._instance._tasks_db_path = None
            cls._instance._projects_db_path = None
            cls._instance._projects_dir = None
            cls._instance._knowledge_db_path = None
            cls._instance._embedding_model = None
            cls._instance._embedding_dimensions = None
            cls._instance._pkm_dir = None
            cls._instance._codebase_dir = None
            cls._instance.load_from_env()
        return cls._instance

    def load_from_env(self):
        """Resets local overrides so properties dynamically read from updated os.environ."""
        self._is_debug = None
        self._debug_channel = None
        self._langsmith_tracing = None
        self._langsmith_project = None
        self._langsmith_endpoint = None
        self._langsmith_api_key = None
        self._langsmith_workspace_id = None
        self._gemini_api_key = None
        self._brave_api_key = None
        self._openai_api_key = None
        self._anthropic_api_key = None
        self._runway_api_key = None
        self._tasks_db_path = None
        self._projects_db_path = None
        self._projects_dir = None
        self._knowledge_db_path = None
        self._embedding_model = None
        self._embedding_dimensions = None
        self._pkm_dir = None
        self._codebase_dir = None

    def get(self, key: str, default: Any = None) -> Any:
        """Generic access to environment variables via the central Config."""
        return os.getenv(key, default)

    # -------------------------------------------------------------------------
    # Debug settings
    # -------------------------------------------------------------------------
    @property
    def is_debug(self) -> bool:
        if self._is_debug is not None:
            return self._is_debug
        env_debug = os.getenv("IS_DEBUG", os.getenv("DEBUG", "false")).lower()
        return env_debug in ("true", "1", "yes", "t")

    @is_debug.setter
    def is_debug(self, value):
        if isinstance(value, str):
            self._is_debug = value.lower() in ("true", "1", "yes", "t")
        elif value is not None:
            self._is_debug = bool(value)
        else:
            self._is_debug = None

    @property
    def debug_channel(self) -> str:
        if self._debug_channel is not None:
            return self._debug_channel
        return os.getenv("DEBUG_CHANNEL", "")

    @debug_channel.setter
    def debug_channel(self, value):
        self._debug_channel = str(value) if value is not None else None

    # -------------------------------------------------------------------------
    # LangSmith / Observability settings
    # -------------------------------------------------------------------------
    @property
    def langsmith_tracing(self) -> bool:
        if self._langsmith_tracing is not None:
            return self._langsmith_tracing
        env_tracing = os.getenv("LANGSMITH_TRACING", os.getenv("LANGCHAIN_TRACING_V2", "false")).lower()
        return env_tracing in ("true", "1", "yes", "t")

    @langsmith_tracing.setter
    def langsmith_tracing(self, value):
        if isinstance(value, str):
            self._langsmith_tracing = value.lower() in ("true", "1", "yes", "t")
        elif value is not None:
            self._langsmith_tracing = bool(value)
        else:
            self._langsmith_tracing = None

    @property
    def langsmith_project(self) -> str:
        if self._langsmith_project is not None:
            return self._langsmith_project
        return os.getenv("LANGSMITH_PROJECT", os.getenv("LANGCHAIN_PROJECT", "default"))

    @langsmith_project.setter
    def langsmith_project(self, value):
        self._langsmith_project = str(value) if value is not None else None

    @property
    def langsmith_endpoint(self) -> str:
        if self._langsmith_endpoint is not None:
            return self._langsmith_endpoint
        return os.getenv("LANGSMITH_ENDPOINT", os.getenv("LANGCHAIN_ENDPOINT", "https://api.smith.langchain.com"))

    @langsmith_endpoint.setter
    def langsmith_endpoint(self, value):
        self._langsmith_endpoint = str(value) if value is not None else None

    @property
    def langsmith_api_key(self) -> str:
        if self._langsmith_api_key is not None:
            return self._langsmith_api_key
        return os.getenv("LANGSMITH_API_KEY", os.getenv("LANGCHAIN_API_KEY", ""))

    @langsmith_api_key.setter
    def langsmith_api_key(self, value):
        self._langsmith_api_key = str(value) if value is not None else None

    @property
    def langsmith_workspace_id(self) -> str:
        if self._langsmith_workspace_id is not None:
            return self._langsmith_workspace_id
        return os.getenv("LANGSMITH_WORKSPACE_ID", "")

    @langsmith_workspace_id.setter
    def langsmith_workspace_id(self, value):
        self._langsmith_workspace_id = str(value) if value is not None else None

    # -------------------------------------------------------------------------
    # LLM & Tool API Keys
    # -------------------------------------------------------------------------
    @property
    def gemini_api_key(self) -> str:
        if self._gemini_api_key is not None:
            return self._gemini_api_key
        return os.getenv("GEMINI_API_KEY", "")

    @gemini_api_key.setter
    def gemini_api_key(self, value):
        self._gemini_api_key = str(value) if value is not None else None

    @property
    def brave_api_key(self) -> str:
        if self._brave_api_key is not None:
            return self._brave_api_key
        return os.getenv("BRAVE_API_KEY", "")

    @brave_api_key.setter
    def brave_api_key(self, value):
        self._brave_api_key = str(value) if value is not None else None

    @property
    def openai_api_key(self) -> str:
        if self._openai_api_key is not None:
            return self._openai_api_key
        return os.getenv("OPENAI_API_KEY", "")

    @openai_api_key.setter
    def openai_api_key(self, value):
        self._openai_api_key = str(value) if value is not None else None

    @property
    def anthropic_api_key(self) -> str:
        if self._anthropic_api_key is not None:
            return self._anthropic_api_key
        return os.getenv("ANTHROPIC_API_KEY", "")

    @anthropic_api_key.setter
    def anthropic_api_key(self, value):
        self._anthropic_api_key = str(value) if value is not None else None

    @property
    def runway_api_key(self) -> str:
        if self._runway_api_key is not None:
            return self._runway_api_key
        return os.getenv("RUNWAYML_API_SECRET", os.getenv("RUNWAY_API_KEY", ""))

    @runway_api_key.setter
    def runway_api_key(self, value):
        self._runway_api_key = str(value) if value is not None else None

    # -------------------------------------------------------------------------
    # PKM & Tasks Storage Paths
    # -------------------------------------------------------------------------
    @property
    def tasks_db_path(self) -> str:
        if self._tasks_db_path is not None:
            return self._tasks_db_path
        return os.getenv("TASKS_DB_PATH", os.path.expanduser("~/pkm/tasks.db"))

    @tasks_db_path.setter
    def tasks_db_path(self, value):
        self._tasks_db_path = str(value) if value is not None else None

    @property
    def projects_db_path(self) -> str:
        if self._projects_db_path is not None:
            return self._projects_db_path
        return os.getenv("PROJECTS_DB_PATH", os.path.expanduser("~/pkm/projects.db"))

    @projects_db_path.setter
    def projects_db_path(self, value):
        self._projects_db_path = str(value) if value is not None else None

    @property
    def projects_dir(self) -> str:
        if self._projects_dir is not None:
            return self._projects_dir
        return os.getenv("PROJECTS_DIR", os.path.join(self.pkm_dir, "vault", "projects"))

    @projects_dir.setter
    def projects_dir(self, value):
        self._projects_dir = str(value) if value is not None else None

    @property
    def knowledge_db_path(self) -> str:
        if self._knowledge_db_path is not None:
            return self._knowledge_db_path
        return os.getenv("KNOWLEDGE_DB_PATH", os.path.expanduser("~/pkm/.lancedb"))

    @knowledge_db_path.setter
    def knowledge_db_path(self, value):
        self._knowledge_db_path = str(value) if value is not None else None

    @property
    def embedding_model(self) -> str:
        if self._embedding_model is not None:
            return self._embedding_model
        return os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")

    @embedding_model.setter
    def embedding_model(self, value):
        self._embedding_model = str(value) if value is not None else None

    @property
    def embedding_dimensions(self) -> int:
        if self._embedding_dimensions is not None:
            return self._embedding_dimensions
        env_val = os.getenv("EMBEDDING_DIMENSIONS")
        if env_val:
            try:
                return int(env_val)
            except ValueError:
                pass
        return 1536

    @embedding_dimensions.setter
    def embedding_dimensions(self, value):
        self._embedding_dimensions = int(value) if value is not None else None

    @property
    def pkm_dir(self) -> str:
        if self._pkm_dir is not None:
            return self._pkm_dir
        return os.getenv("PKM_DIR", os.path.expanduser("~/pkm"))

    @pkm_dir.setter
    def pkm_dir(self, value):
        self._pkm_dir = str(value) if value is not None else None

    @property
    def codebase_dir(self) -> str:
        if self._codebase_dir is not None:
            return self._codebase_dir
        return os.getenv("CODEBASE_DIR", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

    @codebase_dir.setter
    def codebase_dir(self, value):
        self._codebase_dir = str(value) if value is not None else None

    # -------------------------------------------------------------------------
    # Channel Filtering Logic
    # -------------------------------------------------------------------------
    def is_channel_allowed(self, channel) -> bool:
        """
        Determines whether a given Discord channel or thread is allowed to be listened/responded to.
        - If is_debug is True: returns True ONLY if the channel or thread parent matches debug_channel.
        - If is_debug is False: returns True for all channels EXCEPT debug_channel (which is ignored).
        """
        debug_ch = self.debug_channel
        target = debug_ch.lstrip("#").strip() if debug_ch else ""

        def _is_debug_channel_match(ch) -> bool:
            if not target or ch is None:
                return False

            if isinstance(ch, str):
                return ch.lstrip("#").strip() == target

            channel_name = getattr(ch, "name", "")
            channel_id = str(getattr(ch, "id", ""))

            if channel_name == target or channel_id == target:
                return True

            parent = getattr(ch, "parent", None)
            if parent:
                parent_name = getattr(parent, "name", "")
                parent_id = str(getattr(parent, "id", ""))
                if parent_name == target or parent_id == target:
                    return True

            return False

        is_match = _is_debug_channel_match(channel)

        if self.is_debug:
            # When debug is ON: only debug_channel is allowed
            return is_match
        else:
            # When debug is OFF: all channels are allowed EXCEPT debug_channel
            if target and is_match:
                return False
            return True

    def reset(self):
        """Helper to reset state back to default/env values."""
        self._is_debug = None
        self._debug_channel = None
        self._langsmith_tracing = None
        self._langsmith_project = None
        self._langsmith_endpoint = None
        self._langsmith_api_key = None
        self._langsmith_workspace_id = None
        self._gemini_api_key = None
        self._brave_api_key = None
        self._openai_api_key = None
        self._anthropic_api_key = None
        self._runway_api_key = None
        self._tasks_db_path = None
        self._projects_db_path = None
        self._projects_dir = None
        self._knowledge_db_path = None
        self._embedding_model = None
        self._embedding_dimensions = None
        self._pkm_dir = None
        self._codebase_dir = None
        self.load_from_env()


