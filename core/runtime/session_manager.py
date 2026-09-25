from typing import Optional, Any, Union
from core.knowledge.memory.sqlite_session_store import SqliteSessionStore
from core.runtime.execution_context import ExecutionContext, inherited_record_memory, inherited_surface


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
        )

    def clear_session(self, session: ExecutionContext) -> str:
        store = SqliteSessionStore()
        return store.archive_session(session.session_id)

    def clear_sessions(self) -> str:
        store = SqliteSessionStore()
        return store.archive_all_sessions()
