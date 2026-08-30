from typing import Optional, Any, Union
import discord
from core.knowledge.memory.sqlite_session_store import SqliteSessionStore
from core.agent.session_identifier import SessionIdentifier


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
        channel: Optional[Union[discord.TextChannel, discord.Thread, str]] = None,
        job_id: Optional[str] = None,
        stateless: bool = False
    ) -> SessionIdentifier:
        """
        Creates a SessionIdentifier instance.
        """
        return SessionIdentifier._create(
            agent_id=str(agent_id or ""),
            source=source,
            channel=channel,
            job_id=job_id,
            stateless=stateless,
        )

    def clear_session(self, session: SessionIdentifier) -> str:
        store = SqliteSessionStore()
        return store.archive_session(session.session_id)

    def clear_sessions(self) -> str:
        store = SqliteSessionStore()
        return store.archive_all_sessions()
