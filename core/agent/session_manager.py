import os
import glob
import discord
import datetime
from core.memory.flat_file_session_store import FlatFileSessionStore

class SessionManager:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(SessionManager, cls).__new__(cls)
        return cls._instance

    def clear_session(self, session_id: str) -> str:
        store = FlatFileSessionStore()
        return store.archive_session(session_id)
        
    def clear_sessions(self):
        store = FlatFileSessionStore()
        sessions_dir = store.sessions_dir
        if not os.path.exists(sessions_dir):
            return
        responses = []
        for filepath in glob.glob(os.path.join(sessions_dir, "*.jsonl")):
            filename = os.path.basename(filepath)
            safe_id = filename[:-6]  # remove .jsonl
            responses.append(self.clear_session(safe_id))
        return "\n".join(responses)

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
