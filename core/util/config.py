import os
import sys
from typing import Any, Optional
from dotenv import load_dotenv

# Ensure .env is loaded on import of config
workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
env_path = os.path.join(workspace_root, ".env")
if os.path.exists(env_path):
    load_dotenv(dotenv_path=env_path, override=True)
else:
    load_dotenv(override=True)

# The one cap on concurrent model-driven executions across the system.
DEFAULT_MAX_CONCURRENCY = 3


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
            cls._instance._seats_aero_api_key = None
            cls._instance._rapidapi_key = None
            cls._instance._local_llm_base_url = None
            cls._instance._local_llm_timeout = None
            cls._instance._local_llm_context_tokens = None
            cls._instance._local_llm_history_tokens = None
            cls._instance._tasks_db_path = None
            cls._instance._projects_db_path = None
            cls._instance._projects_dir = None
            cls._instance._knowledge_db_path = None
            cls._instance._knowledge_backend = None
            cls._instance._embedding_model = None
            cls._instance._embedding_dimensions = None
            cls._instance._pkm_dir = None
            cls._instance._codebase_dir = None
            cls._instance._context_pruning_enabled = None
            cls._instance._context_max_tokens = None
            cls._instance._context_window_messages = None
            cls._instance._context_summary_max_tokens = None
            cls._instance._context_pruning_timeout = None
            cls._instance._max_concurrency = None
            cls._instance._gog_keyring_backend = None
            cls._instance._gog_keyring_password = None
            cls._instance._timezone = None
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
        self._seats_aero_api_key = None
        self._rapidapi_key = None
        self._local_llm_base_url = None
        self._local_llm_timeout = None
        self._local_llm_context_tokens = None
        self._local_llm_history_tokens = None
        self._tasks_db_path = None
        self._projects_db_path = None
        self._projects_dir = None
        self._knowledge_db_path = None
        self._knowledge_backend = None
        self._embedding_model = None
        self._embedding_dimensions = None
        self._pkm_dir = None
        self._codebase_dir = None
        self._context_pruning_enabled = None
        self._context_max_tokens = None
        self._context_window_messages = None
        self._context_summary_max_tokens = None
        self._context_pruning_timeout = None
        self._max_concurrency = None
        self._gog_keyring_backend = None
        self._gog_keyring_password = None
        self._timezone = None

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

    @property
    def seats_aero_api_key(self) -> str:
        if self._seats_aero_api_key is not None:
            return self._seats_aero_api_key
        return os.getenv("SEATS_AERO_API_KEY", os.getenv("SEATSAERO_API_KEY", ""))

    @seats_aero_api_key.setter
    def seats_aero_api_key(self, value):
        self._seats_aero_api_key = str(value) if value is not None else None

    @property
    def rapidapi_key(self) -> str:
        if self._rapidapi_key is not None:
            return self._rapidapi_key
        return os.getenv("RAPIDAPI_KEY", os.getenv("ZILLOW_RAPIDAPI_KEY", os.getenv("RAPID_API_KEY", "")))

    @rapidapi_key.setter
    def rapidapi_key(self, value):
        self._rapidapi_key = str(value) if value is not None else None

    # -------------------------------------------------------------------------
    # On-device LLM (OpenAI-compatible server)
    # -------------------------------------------------------------------------
    # Read by graph_builder when an agent sets `"provider": "local"`. Only the
    # endpoint and the timeout live here. Which model a tier maps to is in
    # core/util/models.py, and there is deliberately no API key setting: the
    # server does not authenticate, and offering the knob would invite pointing
    # the paid OPENAI_API_KEY at a local socket.
    @property
    def local_llm_base_url(self) -> str:
        if self._local_llm_base_url is not None:
            return self._local_llm_base_url
        return os.getenv("LOCAL_LLM_BASE_URL", "http://localhost:9379/v1")

    @local_llm_base_url.setter
    def local_llm_base_url(self, value):
        self._local_llm_base_url = str(value) if value is not None else None

    @property
    def local_llm_timeout(self) -> float:
        """Seconds before a request to the on-device server is abandoned.

        Far above the openai client's 60s default, and not because generation
        is slow -- short turns measure about a second. The server handles one
        request at a time, so a turn's latency includes everything queued ahead
        of it, and the default would abort turns that were merely waiting.
        """
        if self._local_llm_timeout is not None:
            return self._local_llm_timeout
        try:
            return float(os.getenv("LOCAL_LLM_TIMEOUT", "300"))
        except (TypeError, ValueError):
            return 300.0

    @local_llm_timeout.setter
    def local_llm_timeout(self, value):
        self._local_llm_timeout = float(value) if value is not None else None

    @property
    def local_llm_context_tokens(self) -> int:
        """The on-device server's hard context window, in tokens.

        This is a property of how the server was launched, not of the model
        file: LiteRT-LM applies its own maximum, and loading a model advertising
        a larger window does not change it. Measure it rather than assume it --
        overflow the streaming endpoint and the server states the limit in its
        rejection ("... too long ... 7142 >= 4096").
        """
        if self._local_llm_context_tokens is not None:
            return self._local_llm_context_tokens
        try:
            return int(os.getenv("LOCAL_LLM_CONTEXT_TOKENS", "4096"))
        except (TypeError, ValueError):
            return 4096

    @local_llm_context_tokens.setter
    def local_llm_context_tokens(self, value):
        self._local_llm_context_tokens = int(value) if value is not None else None

    @property
    def local_llm_history_tokens(self) -> int:
        """Pruning threshold for agents running on the on-device server.

        `context_max_tokens` (30000) is sized for Gemini's million-token window
        and is over seven times the local server's entire capacity, so the
        pruner never fires before the server rejects the request. This gives
        local agents their own budget.

        Only conversation history is measured by the pruner -- the system
        prompt and tool schemas are charged to the same window but counted
        nowhere, so half the window is reserved for them and for the reply.
        Agents whose static prompt already exceeds that reservation cannot run
        locally at any history budget.
        """
        if self._local_llm_history_tokens is not None:
            return self._local_llm_history_tokens
        env_val = os.getenv("LOCAL_LLM_HISTORY_TOKENS")
        if env_val:
            try:
                return int(env_val)
            except ValueError:
                pass
        return max(512, self.local_llm_context_tokens // 2)

    @local_llm_history_tokens.setter
    def local_llm_history_tokens(self, value):
        self._local_llm_history_tokens = int(value) if value is not None else None

    # -------------------------------------------------------------------------
    # Gogcli Keyring Settings
    # -------------------------------------------------------------------------
    @property
    def gog_keyring_backend(self) -> str:
        if self._gog_keyring_backend is not None:
            return self._gog_keyring_backend
        return os.getenv("GOG_KEYRING_BACKEND", "file")

    @gog_keyring_backend.setter
    def gog_keyring_backend(self, value):
        self._gog_keyring_backend = str(value) if value is not None else None

    @property
    def gog_keyring_password(self) -> str:
        if self._gog_keyring_password is not None:
            return self._gog_keyring_password
        return os.getenv("GOG_KEYRING_PASSWORD", "")

    @gog_keyring_password.setter
    def gog_keyring_password(self, value):
        self._gog_keyring_password = str(value) if value is not None else None

    # -------------------------------------------------------------------------
    # Locale settings
    # -------------------------------------------------------------------------
    @property
    def timezone(self) -> str:
        """IANA timezone name used for every user-facing date/time.

        The container runs on UTC, so relying on the host clock made the agent
        believe it was already tomorrow for the whole PST evening. Pinning the
        user's zone here keeps "today" meaningful no matter where the process
        happens to run. TZ is honoured as a fallback so a deployment that
        already sets the standard variable does not need a second one.
        """
        if self._timezone is not None:
            return self._timezone
        return os.getenv("TIMEZONE", os.getenv("TZ", "America/Los_Angeles"))

    @timezone.setter
    def timezone(self, value):
        self._timezone = str(value) if value is not None else None

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
    def knowledge_backend(self) -> str:
        """Which vector store implementation to use: 'auto', 'lancedb', or 'numpy'.

        'auto' probes the machine at startup and prefers lancedb, falling back
        to the numpy store on CPUs whose instruction set lancedb's wheels assume
        but do not have. The explicit values exist to skip that probe -- useful
        in CI, and necessary if you want to force one backend on hardware where
        either would work.
        """
        if self._knowledge_backend is not None:
            return self._knowledge_backend
        return os.getenv("KNOWLEDGE_BACKEND", "auto")

    @knowledge_backend.setter
    def knowledge_backend(self, value):
        self._knowledge_backend = str(value) if value is not None else None

    @property
    def embedding_model(self) -> str:
        if self._embedding_model is not None:
            return self._embedding_model
        # Names an actual Gemini model rather than the OpenAI-shaped
        # "text-embedding-3-small" this used to default to. That name survived
        # a provider switch and was never sent anywhere: the indexer treated it
        # as a sentinel meaning "pick something Gemini serves", so the default
        # advertised a provider the code cannot use. Retired names, that one
        # included, are still remapped for existing .env files.
        return os.getenv("EMBEDDING_MODEL", "gemini-embedding-001")

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
    # Context Pruning & Summarization Settings
    # -------------------------------------------------------------------------
    @property
    def context_pruning_enabled(self) -> bool:
        if self._context_pruning_enabled is not None:
            return self._context_pruning_enabled
        env_val = os.getenv("CONTEXT_PRUNING_ENABLED", "true").lower()
        return env_val in ("true", "1", "yes", "t")

    @context_pruning_enabled.setter
    def context_pruning_enabled(self, value):
        if isinstance(value, str):
            self._context_pruning_enabled = value.lower() in ("true", "1", "yes", "t")
        elif value is not None:
            self._context_pruning_enabled = bool(value)
        else:
            self._context_pruning_enabled = None

    @property
    def context_max_tokens(self) -> int:
        """History budget for agents on a remote (Gemini) model.

        Lowered from 30000 to 10000. Simulating a realistic turn (~724 tokens:
        a user line, a tool call, a tool result and a reply) against the real
        pruner puts the cost of this at roughly 12% of turns triggering a
        summarisation, against 10% at 30000 -- so the saving in carried context
        is large and the extra summarisation is marginal.

        Local agents do not use this; see `local_llm_history_tokens`.
        """
        if self._context_max_tokens is not None:
            return self._context_max_tokens
        env_val = os.getenv("CONTEXT_MAX_TOKENS")
        if env_val:
            try:
                return int(env_val)
            except ValueError:
                pass
        return 10000

    @context_max_tokens.setter
    def context_max_tokens(self, value):
        self._context_max_tokens = int(value) if value is not None else None

    @property
    def context_window_messages(self) -> int:
        if self._context_window_messages is not None:
            return self._context_window_messages
        env_val = os.getenv("CONTEXT_WINDOW_MESSAGES")
        if env_val:
            try:
                return int(env_val)
            except ValueError:
                pass
        return 30

    @context_window_messages.setter
    def context_window_messages(self, value):
        self._context_window_messages = int(value) if value is not None else None

    @property
    def context_summary_max_tokens(self) -> int:
        if self._context_summary_max_tokens is not None:
            return self._context_summary_max_tokens
        env_val = os.getenv("CONTEXT_SUMMARY_MAX_TOKENS")
        if env_val:
            try:
                return int(env_val)
            except ValueError:
                pass
        return 1000

    @context_summary_max_tokens.setter
    def context_summary_max_tokens(self, value):
        self._context_summary_max_tokens = int(value) if value is not None else None

    @property
    def context_pruning_timeout(self) -> int:
        if self._context_pruning_timeout is not None:
            return self._context_pruning_timeout
        env_val = os.getenv("CONTEXT_PRUNING_TIMEOUT")
        if env_val:
            try:
                return int(env_val)
            except ValueError:
                pass
        return 30

    @context_pruning_timeout.setter
    def context_pruning_timeout(self, value):
        self._context_pruning_timeout = int(value) if value is not None else None

    @property
    def tool_output_max_chars(self) -> int:
        """Ceiling on one tool result, in characters, before it enters history.

        The pruner cannot shrink a result in the turn being answered -- it keeps
        the latest turn whole -- so an unbounded tool output lands in the
        checkpoint verbatim and is re-sent every turn after. A Home Assistant
        error log measured at 150 MB (~37M tokens) did exactly that.

        200k chars (~50k tokens) is well above any legitimate text result;
        tools with larger data should page it. Inline images are exempt (see
        `cap_tool_output`). `TOOL_OUTPUT_MAX_CHARS`, 0 disables.
        """
        env_val = os.getenv("TOOL_OUTPUT_MAX_CHARS")
        if env_val:
            try:
                return max(0, int(env_val))
            except ValueError:
                pass
        return 200000

    @property
    def max_concurrency(self) -> int:
        """The one cap on concurrent model-driven executions across the system
        (scheduled runs, dream fan-out, ...). `AOC_MAX_CONCURRENCY`, min 1."""
        if self._max_concurrency is not None:
            return self._max_concurrency
        env_val = os.getenv("AOC_MAX_CONCURRENCY")
        if env_val and env_val.strip():
            try:
                return max(1, int(env_val))
            except ValueError:
                print(
                    f"Config: ignoring non-integer AOC_MAX_CONCURRENCY={env_val!r}; "
                    f"using {DEFAULT_MAX_CONCURRENCY}.",
                    file=sys.stderr,
                )
        return DEFAULT_MAX_CONCURRENCY

    @max_concurrency.setter
    def max_concurrency(self, value):
        self._max_concurrency = max(1, int(value)) if value is not None else None

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
        self._seats_aero_api_key = None
        self._rapidapi_key = None
        self._local_llm_base_url = None
        self._local_llm_timeout = None
        self._local_llm_context_tokens = None
        self._local_llm_history_tokens = None
        self._tasks_db_path = None
        self._projects_db_path = None
        self._projects_dir = None
        self._knowledge_db_path = None
        self._knowledge_backend = None
        self._embedding_model = None
        self._embedding_dimensions = None
        self._pkm_dir = None
        self._codebase_dir = None
        self._context_pruning_enabled = None
        self._context_max_tokens = None
        self._context_window_messages = None
        self._context_summary_max_tokens = None
        self._context_pruning_timeout = None
        self._max_concurrency = None
        self._gog_keyring_backend = None
        self._gog_keyring_password = None
        self._timezone = None
        self.load_from_env()


