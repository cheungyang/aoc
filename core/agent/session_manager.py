import os
import discord
import datetime
from core.knowledge.memory.sqlite_session_store import SqliteSessionStore

class SessionManager:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(SessionManager, cls).__new__(cls)
        return cls._instance

    def clear_session(self, session_id: str) -> str:
        store = SqliteSessionStore()
        return store.archive_session(session_id)
        
    def clear_sessions(self) -> str:
        store = SqliteSessionStore()
        return store.archive_all_sessions()

    def get_session_id(self, agent_id: str, source: str, channel: discord.TextChannel = None) -> str:
        postfix = ""
        if channel is not None:
            channel_name = channel.name if hasattr(channel, "name") else str(channel.id)
            thread_id = ""
            if isinstance(channel, discord.Thread):
                thread_id = str(channel.id)
                if channel.parent:
                    channel_name = channel.parent.name
            postfix = f"{channel_name}:{thread_id}" if thread_id else channel_name

        session_id = f"{agent_id}:{source}:{postfix}" if postfix else f"{agent_id}:{source}"
        return session_id
