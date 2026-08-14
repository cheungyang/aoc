import os
import sys
import asyncio
import discord
from typing import Union, List, Optional
from core.agent.session_manager import SessionManager
from core.util import split_message

class CommandHandler:
    """
    Handles system-level bracket commands (e.g. [new], [newall], [restart])
    sent to the agent.
    """

    async def handle_command(
        self,
        content: Union[str, list],
        session_id: Optional[str] = None,
        channel: Optional[discord.TextChannel] = None
    ) -> bool:
        """
        Checks if content is a recognized command and executes it.
        Returns True if a command was handled, False otherwise.
        """
        if not isinstance(content, str):
            return False

        command = content.strip()
        if command == "[new]":
            await self._handle_new(session_id, channel)
            return True
        elif command == "[newall]":
            await self._handle_newall(channel)
            return True
        elif command == "[restart]":
            await self._handle_restart(channel)
            return True

        return False

    async def _handle_new(self, session_id: Optional[str], channel: Optional[discord.TextChannel] = None):
        if not session_id and channel is not None:
            session_id = SessionManager().get_session_id("main", "discord", channel)
        archive_status = SessionManager().clear_session(session_id)
        if channel is not None:
            await channel.send(f"Session context cleared. {archive_status}")

    async def _handle_newall(self, channel: Optional[discord.TextChannel] = None):
        archive_status = SessionManager().clear_sessions()
        full_msg = f"All session contexts cleared. {archive_status}"
        chunks = split_message(full_msg)
        for i, chunk in enumerate(chunks):
            if i > 0:
                await asyncio.sleep(1)
            if channel is not None:
                await channel.send(chunk)

    async def _handle_restart(self, channel: Optional[discord.TextChannel] = None):
        if channel is not None:
            await channel.send("System is restarting...")
        await asyncio.sleep(0.5)
        os.execv(sys.executable, [sys.executable] + sys.argv)
