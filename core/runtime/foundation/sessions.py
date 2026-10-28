from typing import Optional, Any, Union
from core.runtime.foundation.context import ExecutionContext, inherited_record_memory, inherited_surface
from core.storage.sessions import SessionStore


class SessionManager:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(SessionManager, cls).__new__(cls)
        return cls._instance

    @classmethod
    def get_session(
        cls,
        agent_id: str,
        source: str = "discord",
        channel: Optional[Union[str, Any]] = None,
        job_id: Optional[str] = None,
        stateless: bool = False,
        graph_id: Optional[str] = None,
        surface: Optional[str] = None,
        record_memory: bool = True,
        model: Optional[str] = None,
        thinking_level: Optional[str] = None,
        called_by_agent: bool = False,
        output: Optional[Any] = None,
    ) -> ExecutionContext:
        """
        Creates an ExecutionContext instance.

        `graph_id` binds the execution to a graph's grants; pass it when delegating into a
        graph so the callee is evaluated against that graph rather than ambient state.

        `surface` optionally tags which interface the turn came through (voice / text /
        scheduled / tool / job) for token accounting. It does not affect the session id.
        When omitted it is inherited (`inherited_surface`: the current context's explicit
        surface, then a `surface_scope`), and failing that derived from `source`.

        `record_memory=False` stops the runtime saving this turn's memory log. It can
        only be turned off, never back on: a context minted inside a non-recording one
        is non-recording too. `model` overrides the agent's model tier for this call.
        `thinking_level` overrides the agent's reasoning depth for this call (a schedule
        entry's `thinking_level`); like `model`, it is never inherited.
        `called_by_agent` marks a turn delegation handed over without the router
        choosing it (stream_delegate sets it): that run gets no `follow_up`.
        `output` is where the turn's output goes (foundation/output.py): the
        adapter starting the turn passes its own.
        """
        if surface is None:
            surface = inherited_surface()
        record_memory = bool(record_memory) and inherited_record_memory()
        return ExecutionContext._create(
            agent_id=str(agent_id or ""),
            source=source,
            channel=channel,
            job_id=job_id,
            stateless=stateless,
            graph_id=graph_id,
            surface=surface,
            record_memory=record_memory,
            model=model,
            thinking_level=thinking_level,
            called_by_agent=called_by_agent,
            output=output,
        )

    def clear_session(self, session: ExecutionContext) -> str:
        store = SessionStore()
        return store.archive_session(session.session_id)

    def clear_sessions(self) -> str:
        store = SessionStore()
        return store.archive_all_sessions()
