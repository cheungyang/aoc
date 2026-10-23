"""`!compact` (or `!summarize`): this channel's session history, compacted now
rather than after a turn: older messages become a summary."""
from core.knowledge.memory.sqlite_checkpointer import SqliteCheckpointer
from core.runtime.commands.base import ChannelCommand, TypedContext
from core.runtime.history.pruner import ContextPruner, estimate_total_tokens


class CompactCommand(ChannelCommand):
    name = "compact"
    aliases = ("summarize",)
    summary = "Compacts this channel's session history now: older messages become a summary."

    async def run(self, ctx: TypedContext, args: str) -> None:
        session = ctx.session
        session_id = session.session_id

        checkpointer = SqliteCheckpointer()
        tuple_res = checkpointer.get_tuple({"configurable": {"thread_id": session_id}})
        if not tuple_res or not tuple_res.checkpoint:
            await ctx.send("No active checkpoint messages found to compact.")
            return

        channel_values = tuple_res.checkpoint.get("channel_values", {})
        messages = channel_values.get("messages", [])
        if not messages or len(messages) <= 1:
            await ctx.send(f"Session history is already minimal ({len(messages)} messages).")
            return

        orig_count = len(messages)
        orig_tokens = estimate_total_tokens(messages)

        pruner = ContextPruner()
        pruned = await pruner.aauto_prune_session(session, force=True)

        if not pruned:
            await ctx.send(f"Session history is already minimal ({len(messages)} messages).")
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
        await ctx.reply(msg)
