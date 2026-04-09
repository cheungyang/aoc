import sys
import os
import discord
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

# Inject workspace root into modules path resolving
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.memory.flat_file_session_store import FlatFileSessionStore

# Instantiate store helper
manager = FlatFileSessionStore()

async def hook_pre_message(thread_id, role, content):
    manager.append_message(thread_id, role, content)

async def hook_post_message(thread_id, role, reply_text):
    manager.append_message(thread_id, role, reply_text)

def register_hooks():
    return {
        "pre_message": hook_pre_message,
        "post_message": hook_post_message
    }
