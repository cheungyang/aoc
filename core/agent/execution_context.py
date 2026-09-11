from dataclasses import dataclass, field, replace
from typing import Optional, Any, Union
import contextvars
import discord

_CONTEXT_SECRET = object()

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


@dataclass(frozen=True)
class ExecutionContext:
    """
    Immutable encapsulation of *who is executing* and *on whose behalf*.

    - Direct instantiation is restricted; instances must be created via SessionManager.get_session(...).
    - Derives channel_name, thread_id, and channel_obj from `channel`.
    - Auto-generates job_id when source == "job" or stateless is True.
    - `graph_id` names the graph whose grants apply to this execution (None = the agent's own
      configured graph). It is an authority field: it decides which tools and skills are merged
      in, and is never inferred from ambient state at construction time.
    """
    agent_id: str
    source: str
    channel: Optional[Union[discord.TextChannel, discord.Thread, str, Any]] = None
    job_id: Optional[str] = None
    stateless: bool = False
    graph_id: Optional[str] = None
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
            raise ValueError("ExecutionContext requires a non-empty 'source' (e.g. 'discord', 'tool', 'job', 'scheduled', 'voice').")

        # Auto-generate job_id if not explicitly passed
        if not self.job_id:
            generated_job_id = self.new_job_id()
            object.__setattr__(self, "job_id", generated_job_id)
        if self.source == "job":
            object.__setattr__(self, "stateless", True)

        if not self.stateless and self.source != "job" and not self.channel_name:
            raise ValueError(f"Stateful ExecutionContext for agent '{self.agent_id}' requires a valid channel or channel name.")

    @classmethod
    def _create(
        cls,
        agent_id: str,
        source: str,
        channel: Optional[Union[discord.TextChannel, discord.Thread, str, Any]] = None,
        job_id: Optional[str] = None,
        stateless: bool = False,
        graph_id: Optional[str] = None,
    ) -> "ExecutionContext":
        """Internal constructor invoked exclusively by SessionManager."""
        return cls(
            agent_id=agent_id,
            source=source,
            channel=channel,
            job_id=job_id,
            stateless=stateless,
            graph_id=graph_id,
            _secret=_CONTEXT_SECRET,
        )

    def with_graph(self, graph_id: Optional[str]) -> "ExecutionContext":
        """
        Derives a child context bound to `graph_id`, preserving immutability.

        Used when a caller delegates into a subgraph: the callee must be evaluated against that
        graph's grants rather than against whatever happened to be ambient.
        """
        if graph_id == self.graph_id:
            return self
        return replace(self, graph_id=graph_id)

    def with_agent(self, agent_id: str) -> "ExecutionContext":
        """Derives a child context for a different agent, keeping the current graph binding."""
        if agent_id == self.agent_id:
            return self
        return replace(self, agent_id=agent_id)

    @property
    def channel_name(self) -> str:
        """Dynamically derives channel name from channel object or string."""
        if self.channel is None:
            return ""
        if isinstance(self.channel, str):
            return self.channel.lstrip("#")
        if isinstance(self.channel, discord.Thread):
            return self.channel.parent.name if self.channel.parent else self.channel.name
        if isinstance(self.channel, discord.TextChannel):
            return self.channel.name
        if hasattr(self.channel, "parent") and isinstance(getattr(self.channel, "parent", None), discord.TextChannel):
            return str(self.channel.parent.name)
        if hasattr(self.channel, "name") and isinstance(getattr(self.channel, "name"), str):
            return self.channel.name
        if hasattr(self.channel, "name"):
            return str(getattr(self.channel, "name", ""))
        return ""

    @property
    def discord_thread_id(self) -> Optional[str]:
        """Dynamically derives Discord thread id if channel is a Thread."""
        if self.channel is None:
            return None
        if isinstance(self.channel, discord.Thread):
            return str(self.channel.id)
        if hasattr(self.channel, "parent") and isinstance(getattr(self.channel, "parent", None), discord.TextChannel):
            return str(getattr(self.channel, "id", ""))
        return None

    @property
    def channel_obj(self) -> Optional[Any]:
        """Returns the Discord channel object if available (non-string)."""
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
