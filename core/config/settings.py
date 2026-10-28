"""Config: the deployment's settings, read from the environment and the .env
file at the project root, which is loaded on import.

Each setting is declared once, as a `Setting`: its environment variable (and
any older names still honoured), its default and its type. Reading a setting
gives its override if one was set, else the environment's value, else the
default. The environment is read on every access, so a test that patches
os.environ is seen at once. Assigning a setting overrides it; assigning None
removes the override, `reset()` removes them all, and `overriding(...)`
overrides some for the length of a `with` block.
"""
import os
import sys
from contextlib import contextmanager
from typing import Any, Optional, Tuple, Union

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

TRUE = ("true", "1", "yes", "t")


class Setting:
    """One setting of Config, declared once.

    `env` and then `fallback_env` are tried in order; the first that is set is
    used, even when empty. `kind` is str, bool (one of TRUE, any case), int or
    float. A number that doesn't parse, or is empty, gives the default, and
    `warn` says so on stderr. `minimum` bounds a number, from the environment or
    an override. `strip` trims the environment's value. A callable `default` is
    called with the Config, for a default that depends on another setting.
    """

    def __init__(self, env: str, default: Any = "", kind: type = str, *,
                 fallback_env: Union[str, Tuple[str, ...]] = (), minimum: Optional[float] = None,
                 strip: bool = False, warn: bool = False):
        fallbacks = (fallback_env,) if isinstance(fallback_env, str) else tuple(fallback_env)
        self.envs = (env,) + fallbacks
        self.default = default
        self.kind = kind
        self.minimum = minimum
        self.strip = strip
        self.warn = warn

    def __set_name__(self, owner, name):
        self.name = name

    def __get__(self, config, owner=None):
        if config is None:
            return self
        if self.name in config._overrides:
            return config._overrides[self.name]
        for env in self.envs:
            raw = os.environ.get(env)
            if raw is None:
                continue
            try:
                return self._bounded(self._parse(raw.strip() if self.strip else raw))
            except ValueError:
                if self.warn and raw.strip():
                    print(f"Config: ignoring non-integer {env}={raw!r}; using {self._default(config)}.",
                          file=sys.stderr)
                break
        return self._default(config)

    def __set__(self, config, value):
        if value is None:
            config._overrides.pop(self.name, None)
        else:
            config._overrides[self.name] = self._bounded(self._parse(value))

    def __delete__(self, config):
        config._overrides.pop(self.name, None)

    def _parse(self, value):
        if self.kind is bool:
            return value.lower() in TRUE if isinstance(value, str) else bool(value)
        return self.kind(value)

    def _bounded(self, value):
        return value if self.minimum is None else max(self.minimum, value)

    def _default(self, config):
        return self.default(config) if callable(self.default) else self.default


class Config:
    """The settings. `Config()` is the same object everywhere."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Config, cls).__new__(cls)
            cls._instance._overrides = {}
        return cls._instance

    def get(self, key: str, default: Any = None) -> Any:
        """Generic access to environment variables via the central Config."""
        return os.getenv(key, default)

    def reset(self):
        """Removes every override, so each setting reads the environment again."""
        self._overrides.clear()

    @contextmanager
    def overriding(self, **settings):
        """Overrides `settings` for the length of a `with` block, then puts back the
        overrides they had before, or none."""
        for name in settings:
            if not isinstance(getattr(type(self), name, None), Setting):
                raise AttributeError(f"Config has no setting {name!r}")
        saved = {name: self._overrides[name] for name in settings if name in self._overrides}
        try:
            for name, value in settings.items():
                setattr(self, name, value)
            yield self
        finally:
            for name in settings:
                self._overrides.pop(name, None)
            self._overrides.update(saved)

    # -------------------------------------------------------------------------
    # Prod/dev coordination
    # -------------------------------------------------------------------------
    # Discord thread where dev instances claim agents from prod; see
    # core/channel/discord/ownership.py. `CONTROL_THREAD_ID`, e.g. the
    # "control" thread under #general; empty when unset, which turns that
    # coordination off.
    control_thread_id = Setting("CONTROL_THREAD_ID", "", strip=True)

    # -------------------------------------------------------------------------
    # Proactive team (core/proactive/README.md)
    # -------------------------------------------------------------------------
    # The team's one switch. Off: shadow. Every agent turn is still tapped and
    # decided, and team radio shows each decision, but nothing reaches an agent;
    # a route only records when it would have been delivered. On: approved
    # routes are delivered. `PROACTIVE_DELIVERY`.
    proactive_delivery = Setting("PROACTIVE_DELIVERY", False, bool)

    # -------------------------------------------------------------------------
    # LangSmith / Observability settings
    # -------------------------------------------------------------------------
    langsmith_tracing = Setting("LANGSMITH_TRACING", False, bool, fallback_env="LANGCHAIN_TRACING_V2")
    langsmith_project = Setting("LANGSMITH_PROJECT", "default", fallback_env="LANGCHAIN_PROJECT")
    langsmith_endpoint = Setting("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com",
                                 fallback_env="LANGCHAIN_ENDPOINT")
    langsmith_api_key = Setting("LANGSMITH_API_KEY", "", fallback_env="LANGCHAIN_API_KEY")
    langsmith_workspace_id = Setting("LANGSMITH_WORKSPACE_ID", "")

    # -------------------------------------------------------------------------
    # LLM & Tool API Keys
    # -------------------------------------------------------------------------
    gemini_api_key = Setting("GEMINI_API_KEY", "")
    # Cloud Text-to-Speech, for the voices (Chirp 3: HD).
    google_tts_api_key = Setting("GOOGLE_TTS_API_KEY", "", strip=True)
    brave_api_key = Setting("BRAVE_API_KEY", "")
    openai_api_key = Setting("OPENAI_API_KEY", "")
    anthropic_api_key = Setting("ANTHROPIC_API_KEY", "")
    runway_api_key = Setting("RUNWAYML_API_SECRET", "", fallback_env="RUNWAY_API_KEY")
    seats_aero_api_key = Setting("SEATS_AERO_API_KEY", "", fallback_env="SEATSAERO_API_KEY")
    rapidapi_key = Setting("RAPIDAPI_KEY", "", fallback_env=("ZILLOW_RAPIDAPI_KEY", "RAPID_API_KEY"))

    # -------------------------------------------------------------------------
    # On-device LLM (OpenAI-compatible server)
    # -------------------------------------------------------------------------
    # Read by GraphBuilder when an agent sets `"provider": "local"`. Only the
    # endpoint and the timeout live here. Which model a tier maps to is in
    # core/config/models.py, and there is deliberately no API key setting: the
    # server does not authenticate, and offering the knob would invite pointing
    # the paid OPENAI_API_KEY at a local socket.
    local_llm_base_url = Setting("LOCAL_LLM_BASE_URL", "http://localhost:9379/v1")

    # Seconds before a request to the on-device server is abandoned.
    #
    # Far above the openai client's 60s default, and not because generation
    # is slow -- short turns measure about a second. The server handles one
    # request at a time, so a turn's latency includes everything queued ahead
    # of it, and the default would abort turns that were merely waiting.
    local_llm_timeout = Setting("LOCAL_LLM_TIMEOUT", 300.0, float)

    # The on-device server's hard context window, in tokens.
    #
    # This is a property of how the server was launched, not of the model
    # file: LiteRT-LM applies its own maximum, and loading a model advertising
    # a larger window does not change it. Measure it rather than assume it --
    # overflow the streaming endpoint and the server states the limit in its
    # rejection ("... too long ... 7142 >= 4096").
    local_llm_context_tokens = Setting("LOCAL_LLM_CONTEXT_TOKENS", 4096, int)

    # Pruning threshold for agents running on the on-device server.
    #
    # `context_max_tokens` (30000) is sized for Gemini's million-token window
    # and is over seven times the local server's entire capacity, so the
    # pruner never fires before the server rejects the request. This gives
    # local agents their own budget.
    #
    # Only conversation history is measured by the pruner -- the system
    # prompt and tool schemas are charged to the same window but counted
    # nowhere, so half the window is reserved for them and for the reply.
    # Agents whose static prompt already exceeds that reservation cannot run
    # locally at any history budget.
    local_llm_history_tokens = Setting("LOCAL_LLM_HISTORY_TOKENS",
                                       lambda config: max(512, config.local_llm_context_tokens // 2), int)

    # -------------------------------------------------------------------------
    # Gogcli Keyring Settings
    # -------------------------------------------------------------------------
    gog_keyring_backend = Setting("GOG_KEYRING_BACKEND", "file")
    gog_keyring_password = Setting("GOG_KEYRING_PASSWORD", "")

    # -------------------------------------------------------------------------
    # Locale settings
    # -------------------------------------------------------------------------
    # IANA timezone name used for every user-facing date/time.
    #
    # The container runs on UTC, so relying on the host clock made the agent
    # believe it was already tomorrow for the whole PST evening. Pinning the
    # user's zone here keeps "today" meaningful no matter where the process
    # happens to run. TZ is honoured as a fallback so a deployment that
    # already sets the standard variable does not need a second one.
    timezone = Setting("TIMEZONE", "America/Los_Angeles", fallback_env="TZ")

    # -------------------------------------------------------------------------
    # PKM & Tasks Storage Paths
    # -------------------------------------------------------------------------
    tasks_db_path = Setting("TASKS_DB_PATH", lambda config: os.path.expanduser("~/pkm/tasks.db"))
    projects_db_path = Setting("PROJECTS_DB_PATH", lambda config: os.path.expanduser("~/pkm/projects.db"))

    # The runtime SQLite file: sessions and checkpoints, jobs, schedule state, the channel inbox and the gate log.
    #
    # Tests and the voice dry run point it elsewhere with MEMORY_DB_PATH, which
    # survives `reset()`, unlike an override.
    memory_db_path = Setting("MEMORY_DB_PATH", os.path.join(workspace_root, "sessions", "memory.db"))

    projects_dir = Setting("PROJECTS_DIR", lambda config: os.path.join(config.pkm_dir, "vault", "projects"))
    knowledge_db_path = Setting("KNOWLEDGE_DB_PATH", lambda config: os.path.expanduser("~/pkm/.lancedb"))

    # Which vector store implementation to use: 'auto', 'lancedb', or 'numpy'.
    #
    # 'auto' probes the machine at startup and prefers lancedb, falling back
    # to the numpy store on CPUs whose instruction set lancedb's wheels assume
    # but do not have. The explicit values exist to skip that probe -- useful
    # in CI, and necessary if you want to force one backend on hardware where
    # either would work.
    knowledge_backend = Setting("KNOWLEDGE_BACKEND", "auto")

    # Names an actual Gemini model rather than the OpenAI-shaped
    # "text-embedding-3-small" this used to default to. That name survived
    # a provider switch and was never sent anywhere: the indexer treated it
    # as a sentinel meaning "pick something Gemini serves", so the default
    # advertised a provider the code cannot use. Retired names, that one
    # included, are still remapped for existing .env files.
    embedding_model = Setting("EMBEDDING_MODEL", "gemini-embedding-001")
    embedding_dimensions = Setting("EMBEDDING_DIMENSIONS", 1536, int)

    pkm_dir = Setting("PKM_DIR", lambda config: os.path.expanduser("~/pkm"))
    codebase_dir = Setting("CODEBASE_DIR", workspace_root)

    # -------------------------------------------------------------------------
    # Context Pruning & Summarization Settings
    # -------------------------------------------------------------------------
    context_pruning_enabled = Setting("CONTEXT_PRUNING_ENABLED", True, bool)

    # History budget for agents on a remote (Gemini) model.
    #
    # Lowered from 30000 to 10000. Simulating a realistic turn (~724 tokens:
    # a user line, a tool call, a tool result and a reply) against the real
    # pruner puts the cost of this at roughly 12% of turns triggering a
    # summarisation, against 10% at 30000 -- so the saving in carried context
    # is large and the extra summarisation is marginal.
    #
    # Local agents do not use this; see `local_llm_history_tokens`.
    context_max_tokens = Setting("CONTEXT_MAX_TOKENS", 10000, int)
    context_window_messages = Setting("CONTEXT_WINDOW_MESSAGES", 30, int)
    context_summary_max_tokens = Setting("CONTEXT_SUMMARY_MAX_TOKENS", 1000, int)
    context_pruning_timeout = Setting("CONTEXT_PRUNING_TIMEOUT", 30, int)

    # Ceiling on one tool result, in characters, before it enters history.
    #
    # The pruner cannot shrink a result in the turn being answered -- it keeps
    # the latest turn whole -- so an unbounded tool output lands in the
    # checkpoint verbatim and is re-sent every turn after. A Home Assistant
    # error log measured at 150 MB (~37M tokens) did exactly that.
    #
    # 200k chars (~50k tokens) is well above any legitimate text result;
    # tools with larger data should page it. Inline images are exempt (see
    # `cap_tool_output`). `TOOL_OUTPUT_MAX_CHARS`, 0 disables.
    tool_output_max_chars = Setting("TOOL_OUTPUT_MAX_CHARS", 200000, int, minimum=0)

    # Attempts (including the first) per Gemini call. Passed as
    # `ChatGoogleGenerativeAI.max_retries`, so it bounds both the SDK's own
    # retries (408/429/500/502/504) and the 503 retry loop in
    # `core.runtime.model.retry`. 6 is langchain-google-genai's default, ~31s
    # of backoff. `LLM_MAX_RETRIES`, min 1 (1 = no retries).
    llm_max_retries = Setting("LLM_MAX_RETRIES", 6, int, minimum=1)

    # The one cap on concurrent model-driven executions across the system
    # (scheduled runs, dream fan-out, ...). `AOC_MAX_CONCURRENCY`, min 1.
    max_concurrency = Setting("AOC_MAX_CONCURRENCY", DEFAULT_MAX_CONCURRENCY, int, minimum=1, warn=True)
