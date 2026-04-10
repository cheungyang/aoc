from core.agent.logging_handler import LoggingHandler
from langchain_core.callbacks import AsyncCallbackHandler
from core.agent.reaction_handler import ReactionCallbackHandler
from core.util import split_message
import json
import ast
from typing import Any, Dict
import discord

class Agent:
    def __init__(self, agent_id, config):
        self.agent_id = agent_id
        self.config = config
        self.graph = None

    def get_config(self, key, default_value=None):
        return self.config.get(key, default_value)

    async def _build_graph(self):
        from .graph_builder import GraphBuilder
        builder = GraphBuilder()
        return await builder.build_graph(self.agent_id, self.config)

    async def execute(self, content: str, session_id: str, channel: discord.TextChannel = None, callbacks: list = None, role: str = "user") -> str:
        # Lazy load langgraph graph object
        if self.graph is None:
            self.graph = await self._build_graph()

        logging_handler = LoggingHandler(session_id=session_id, role=role, human_message=content)
        config = {
            "configurable": {
                "thread_id": session_id,
                "agent_id": self.agent_id
            },
            "callbacks": [logging_handler] + (callbacks or [])
        }

        inputs = {"messages": [{"role": role, "content": content}]}
        
        try:
            print(f"Invoking graph for {self.agent_id}")
            result = await self.graph.ainvoke(inputs, config=config)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"Error invoking graph: {e}")
            
            if "tool_calls that do not have a corresponding ToolMessage" in str(e):
                from core.memory.flat_file_checkpointer import FlatFileCheckpointer
                FlatFileCheckpointer().delete_thread(session_id)
                print(f"Deleted corrupt checkpointer thread for session: {session_id}")
                
            return "Sorry, I encountered an error processing the request."

        # Extract the last response message
        reply_message = result["messages"][-1]
        reply_text = reply_message.content
        
        # Handle list content (common with block format/tool responses)
        if isinstance(reply_text, list):
            texts = []
            for part in reply_text:
                if isinstance(part, dict) and part.get("type") == "text":
                    texts.append(part.get("text", ""))
                elif isinstance(part, str):
                    texts.append(part)
            reply_text = "".join(texts)
            
        # Send message to channel
        if channel is not None:
            chunks = split_message(reply_text)
            for chunk in chunks:
                await channel.send(chunk)

        # Return reponse regardness of channel
        return reply_text




