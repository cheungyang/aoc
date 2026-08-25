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
        elif command == "[compact]" or command == "[summarize]":
            await self._handle_compact(session_id, channel)
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

    async def _handle_compact(self, session_id: Optional[str], channel: Optional[discord.TextChannel] = None):
        if not session_id and channel is not None:
            session_id = SessionManager().get_session_id("main", "discord", channel)
        if not session_id:
            if channel is not None:
                await channel.send("No active session found to compact.")
            return

        from core.knowledge.memory.sqlite_checkpointer import SqliteCheckpointer
        from core.agent.context_pruner import ContextPruner, estimate_total_tokens

        checkpointer = SqliteCheckpointer()
        tuple_res = checkpointer.get_tuple({"configurable": {"thread_id": session_id}})
        if not tuple_res or not tuple_res.checkpoint:
            if channel is not None:
                await channel.send("No active checkpoint messages found to compact.")
            return

        channel_values = tuple_res.checkpoint.get("channel_values", {})
        messages = channel_values.get("messages", [])
        if not messages or len(messages) <= 1:
            if channel is not None:
                await channel.send(f"Session history is already minimal ({len(messages)} messages).")
            return

        orig_count = len(messages)
        orig_tokens = estimate_total_tokens(messages)

        channel_name = channel.name if channel and hasattr(channel, "name") else "general"
        pruner = ContextPruner()
        pruned = await pruner.aauto_prune_session(session_id, channel=channel_name, force=True)

        if not pruned:
            if channel is not None:
                await channel.send(f"Session history is already minimal ({len(messages)} messages).")
            return

        updated_tuple = checkpointer.get_tuple({"configurable": {"thread_id": session_id}})
        new_messages = updated_tuple.checkpoint.get("channel_values", {}).get("messages", []) if updated_tuple else []
        new_count = len(new_messages)
        new_tokens = estimate_total_tokens(new_messages)

        msg = (
            f"**Session Context Compacted**\n"
            f"- Previous: {orig_count} messages (~{orig_tokens:,} tokens)\n"
            f"- Compacted: {new_count} messages (~{new_tokens:,} tokens)\n"
            f"- Savings: ~{max(0, orig_tokens - new_tokens):,} tokens ({((orig_tokens - new_tokens) / max(1, orig_tokens) * 100):.1f}%)"
        )
        if channel is not None:
            chunks = split_message(msg)
            for chunk in chunks:
                await channel.send(chunk)
