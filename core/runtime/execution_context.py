from dataclasses import dataclass, field, replace
from typing import Optional, Any, Union
import contextvars
import sys

_CONTEXT_SECRET = object()

# The interface a turn arrived through, recorded per token row so usage can be
# split by surface. Voice reuses the text channel's session, so it cannot be
# told apart by session id -- it has to be tagged explicitly (see `with_surface`).
#
# "job" is detached work minted with no ambient caller (source="job": callerless
# graph_call, run_tool, post-turn summarisation, the coding worker). It is NOT
# assumed to be scheduled -- a job can just as well trace back to a user's turn.
# Work that does have a caller inherits the caller's surface instead: async
# agent_call delegations are source="tool", and `with_graph`/`with_agent`
# carry an explicit surface across.
SURFACES = frozenset({"voice", "text", "scheduled", "tool", "job"})

# Default surface for contexts that were not tagged explicitly. Any source not
# listed here (e.g. 'discord') is an interactive text surface.
_SOURCE_SURFACES = {
    "voice": "voice",
    "scheduled": "scheduled",
    "job": "job",
    "tool": "tool",
}

# The single ambient identity for the currently executing agent.
# Replaces the former `current_execution_context` and `current_graph_id` pair.
#
# Rule of thumb:
#   - Reading this at CALL time (inside a tool body, inside dynamic_prompt) is safe and expected.
#   - Reading it at CONSTRUCTION time (while building a graph / resolving a tool roster) is not:
#     constructed objects get cached for the process lifetime, so they must receive an explicit
#     ExecutionContext parameter instead.
current_execution_context = contextvars.ContextVar("current_execution_context", default=None)


def try_context() -> Optional["ExecutionContext"]:
    """Returns the ambient ExecutionContext, or None when running outside an agent execution."""
    return current_execution_context.get()


def require_context() -> "ExecutionContext":
    """
    Returns the ambient ExecutionContext, raising if there is none.

    Used by code paths that are only reachable from inside an agent execution; failing loudly
    beats silently falling back to a default identity.
    """
    ctx = current_execution_context.get()
    if ctx is None:
        raise RuntimeError(
            "No ExecutionContext is active. This code must run inside Agent.execute()/execute_stream() "
            "or an explicit `current_execution_context` scope."
        )
    return ctx


def _is_thread(channel: Any) -> bool:
    """Checks whether a channel object represents a thread without hard import."""
    if channel is None or isinstance(channel, str):
        return False
    if getattr(channel, "is_thread", False) is True:
        return True
    discord_mod = sys.modules.get("discord")
    if discord_mod:
        thread_cls = getattr(discord_mod, "Thread", None)
        if thread_cls and isinstance(channel, thread_cls):
            return True
        text_cls = getattr(discord_mod, "TextChannel", None)
        if text_cls and isinstance(channel, text_cls):
            return False
    spec = getattr(channel, "_spec_class", None)
    if spec and "Thread" in getattr(spec, "__name__", ""):
        return True
    cls_name = getattr(type(channel), "__name__", "")
    if "Thread" in cls_name:
        return True
    return False


@dataclass(frozen=True)
class ExecutionContext:
    """
    Immutable encapsulation of *who is executing* and *on whose behalf*.

    - Direct instantiation is restricted; use SessionManager.get_session(...).
    - Derives channel_name, thread_id, and channel_obj from `channel`.
    - Auto-generates job_id when source == "job" or stateless is True.
    - `graph_id` names the graph whose grants apply to this execution (None =
      the agent's own configured graph). It is an authority field: it decides
      which tools and skills are merged in, and is never inferred from ambient
      state at construction time.
    - `surface` is reporting metadata only (see `SURFACES`): which interface
      the turn arrived through, recorded on token rows. It never affects the
      session id, so a voice turn still shares its text channel's session.
      When unset it is derived from `source` (see `get_surface`).
    """
    agent_id: str
    source: str
    channel: Optional[Union[str, Any]] = None
    job_id: Optional[str] = None
    stateless: bool = False
    graph_id: Optional[str] = None
    surface: Optional[str] = field(default=None, compare=False)
    _secret: Any = field(default=None, repr=False, compare=False)

    @staticmethod
    def new_job_id() -> str:
        """Generates an 8-character unique hexadecimal job ID."""
        import uuid
        return uuid.uuid4().hex[:8]

    def __post_init__(self):
        if self._secret is not _CONTEXT_SECRET:
            raise RuntimeError(
                "Direct instantiation of ExecutionContext is prohibited. "
                "Use SessionManager.get_session(...) instead."
            )
        if not self.agent_id:
            raise ValueError("ExecutionContext requires a non-empty 'agent_id'.")
        if not self.source:
            raise ValueError(
                "ExecutionContext requires a non-empty 'source' "
                "(e.g. 'discord', 'tool', 'job', 'scheduled', 'voice')."
            )
        if self.surface is not None and self.surface not in SURFACES:
            raise ValueError(
                f"Unknown surface '{self.surface}'; expected one of {sorted(SURFACES)}."
            )

        # Auto-generate job_id if not explicitly passed
        if not self.job_id:
            generated_job_id = self.new_job_id()
            object.__setattr__(self, "job_id", generated_job_id)
        if self.source == "job":
            object.__setattr__(self, "stateless", True)

        if not self.stateless and self.source != "job" and not self.channel_name:
            raise ValueError(
                f"Stateful ExecutionContext for agent '{self.agent_id}' "
                "requires a valid channel or channel name."
            )

    @classmethod
    def _create(
        cls,
        agent_id: str,
        source: str,
        channel: Optional[Union[str, Any]] = None,
        job_id: Optional[str] = None,
        stateless: bool = False,
        graph_id: Optional[str] = None,
        surface: Optional[str] = None,
    ) -> "ExecutionContext":
        """Internal constructor invoked exclusively by SessionManager."""
        return cls(
            agent_id=agent_id,
            source=source,
            channel=channel,
            job_id=job_id,
            stateless=stateless,
            graph_id=graph_id,
            surface=surface,
            _secret=_CONTEXT_SECRET,
        )

    def with_graph(self, graph_id: Optional[str]) -> "ExecutionContext":
        """
        Derives a child context bound to `graph_id`, preserving immutability.

        Used when a caller delegates into a subgraph: the callee must be
        evaluated against that graph's grants rather than against ambient state.
        """
        if graph_id == self.graph_id:
            return self
        return replace(self, graph_id=graph_id)

    def with_agent(self, agent_id: str) -> "ExecutionContext":
        """Derives a child context for a different agent, keeping graph."""
        if agent_id == self.agent_id:
            return self
        return replace(self, agent_id=agent_id)

    def with_surface(self, surface: Optional[str]) -> "ExecutionContext":
        """Derives a context tagged with `surface`, keeping the same session identity."""
        if surface == self.surface:
            return self
        return replace(self, surface=surface)

    def get_surface(self) -> str:
        """The explicit surface if set, otherwise the one implied by `source`."""
        if self.surface:
            return self.surface
        return _SOURCE_SURFACES.get(self.source, "text")

    @property
    def channel_name(self) -> str:
        """Dynamically derives channel name from channel object or string."""
        if self.channel is None:
            return ""
        if isinstance(self.channel, str):
            return self.channel.lstrip("#")
        if _is_thread(self.channel):
            parent = getattr(self.channel, "parent", None)
            if parent is not None and hasattr(parent, "name"):
                return str(parent.name).lstrip("#")
            if hasattr(self.channel, "name"):
                return str(getattr(self.channel, "name", "")).lstrip("#")
        if hasattr(self.channel, "name"):
            name = getattr(self.channel, "name", "")
            if isinstance(name, str):
                return name.lstrip("#")
            return str(name).lstrip("#")
        return ""

    @property
    def thread_id(self) -> Optional[str]:
        """Dynamically derives thread id if channel is a thread/subchannel."""
        if self.channel is None or isinstance(self.channel, str):
            return None
        if _is_thread(self.channel):
            if hasattr(self.channel, "id"):
                return str(getattr(self.channel, "id", ""))
            if hasattr(self.channel, "thread_id"):
                return str(getattr(self.channel, "thread_id"))
        return None

    @property
    def discord_thread_id(self) -> Optional[str]:
        """Backwards compatibility alias for thread_id."""
        return self.thread_id

    @property
    def channel_obj(self) -> Optional[Any]:
        """Returns the channel object if available (non-string)."""
        if self.channel is not None and not isinstance(self.channel, str):
            return self.channel
        return None

    def get_session_id(self) -> str:
        """Generates canonical session ID string."""
        if self.stateless or self.source == "job":
            return f"{self.agent_id}:job:{self.job_id}"
        if self.discord_thread_id:
            return f"{self.agent_id}:{self.source}:{self.channel_name}:{self.discord_thread_id}"
        return f"{self.agent_id}:{self.source}:{self.channel_name}"

    @property
    def session_id(self) -> str:
        return self.get_session_id()

    def get_agent_id(self) -> str:
        return self.agent_id

    def get_graph_id(self) -> Optional[str]:
        return self.graph_id

    def get_source(self) -> str:
        return self.source

    def get_channel_name(self) -> str:
        return self.channel_name

    def get_discord_thread_id(self) -> Optional[str]:
        return self.discord_thread_id

    def get_job_id(self) -> Optional[str]:
        return self.job_id

    def get_channel_obj(self) -> Optional[Any]:
        return self.channel_obj

    def is_stateless(self) -> bool:
        return self.stateless or self.source == "job"

    def is_thread(self) -> bool:
        return self.discord_thread_id is not None

    def matches_channel(self, channel_target: Union[str, int, Any]) -> bool:
        """Checks if the session's channel, thread, or parent matches the target name or ID."""
        if not channel_target or self.channel is None:
            return False
        clean = str(channel_target).lstrip("#").lower()
        if self.discord_thread_id and self.discord_thread_id.lower() == clean:
            return True
        if hasattr(self.channel, "name") and str(self.channel.name).lower() == clean:
            return True
        if hasattr(self.channel, "id") and str(self.channel.id) == clean:
            return True
        if self.channel_name and self.channel_name.lower() == clean:
            return True
        if isinstance(self.channel, str) and self.channel.lstrip("#").lower() == clean:
            return True
        return False

    def get_session_thread_id(self, graph_id: Optional[str] = None) -> str:
        """
        Derives the LangGraph checkpointer thread_id.
        - For the main/agent execution (graph_id is None, 'main', or self.agent_id): returns self.session_id
        - For subgraphs (e.g. graph_id='coding'): returns the subgraph checkpoint thread ID

        Note: this deliberately does NOT default to self.graph_id. `graph_id` on the context is an
        authority field; conflating it with checkpoint identity would silently re-key the memory of
        every agent invoked from inside a graph.
        """
        if not graph_id or graph_id in ("main", self.agent_id):
            return self.session_id
        return f"{graph_id}:{self.session_id}"
