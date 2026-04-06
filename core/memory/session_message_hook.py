import sys
import os
import discord
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

# Inject workspace root into modules path resolving
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.memory.flat_file_session_store import FlatFileSessionStore

# Instantiate store helper
manager = FlatFileSessionStore()

async def hook_pre_message(message, bot):
    from core.util import get_session_id
    session_id = get_session_id(message)
    manager.append_message(session_id, "human", message.content)

    # Clear session shortcut
    if message.content.strip() == "[new]":
        archive_status = manager.archive_session(session_id)
        await message.channel.send(f"Session context cleared. {archive_status}")
        return None

async def hook_post_message(message, bot, reply_text):
    from core.util import get_session_id
    session_id = get_session_id(message)
    manager.append_message(session_id, "agent", reply_text)

def register_hooks():
    return {
        "pre_message": hook_pre_message,
        "post_message": hook_post_message
    }
