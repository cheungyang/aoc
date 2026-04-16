from core.util import split_message
from core.agent.logging_handler import LoggingHandler

import json
import ast
from typing import Any, Dict
import discord
import xml.etree.ElementTree as ET
import re
from core.agent.discord_ui import PollButtonView, PollSelectView
from core.agent.agent_response import AgentResponse

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

    async def execute(self, content: str, source: str, job_id: str = None, channel: discord.TextChannel = None, callbacks: list = None, role: str = "user") -> str:
        from core.agent.job_manager import JobManager
        from core.agent.session_manager import SessionManager
        from core.agent.logging_handler import LoggingHandler
        
        # Get the necessary ids
        session_id = SessionManager().get_session_id(self.agent_id, source, channel)
        if job_id is None:
            job_id = JobManager().new_job_id(self.agent_id)

        # Handle [new] command to clear session context
        if content.strip() == "[new]":    
            archive_status = SessionManager().clear_session(session_id)
            await channel.send(f"Session context cleared. {archive_status}")
            return

        # Handle [newall] command to clear all session contexts
        if content.strip() == "[newall]":    
            archive_status = SessionManager().clear_sessions()
            await channel.send(f"All session contexts cleared. {archive_status}")
            return

        # Lazy load langgraph graph object
        if self.graph is None:
            self.graph = await self._build_graph()

        JobManager().add_job(job_id, self.agent_id, session_id)
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
            JobManager().updateJob(job_id, "running")
            print(f"Invoking graph for {self.agent_id}")
            try:
                result = await self.graph.ainvoke(inputs, config=config)
            except Exception as e:
                if "tool_calls that do not have a corresponding ToolMessage" in str(e):
                    from core.memory.flat_file_checkpointer import FlatFileCheckpointer
                    FlatFileCheckpointer().delete_thread(session_id)
                    print(f"Deleted corrupt checkpointer thread for session: {session_id}, retrying...")
                    result = await self.graph.ainvoke(inputs, config=config)
                else:
                    raise e
            
            # Check if paused for human input
            state = self.graph.get_state(config)
            if state.next:
                JobManager().updateJob(job_id, "partial")
            else:
                JobManager().updateJob(job_id, "completed")
        except Exception as e:
            JobManager().updateJob(job_id, "error")
            import traceback
            traceback.print_exc()
            print(f"Error invoking graph: {e}")
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
            
        # Parse XML
        response = AgentResponse.from_string(reply_text)
        text_content = response.text
        poll_data = response.poll_data

        # Send message to channel
        if channel is not None:
            chunks = split_message(text_content)
            
            # If there is a poll, we attach the view to the last chunk
            view = None
            if poll_data and source == "discord":
                if poll_data["allow_multiple"]:
                    view = PollSelectView(poll_data, channel)
                else:
                    view = PollButtonView(poll_data, channel)
            
            for i, chunk in enumerate(chunks):
                if i == len(chunks) - 1 and view:
                    await channel.send(chunk, view=view)
                else:
                    await channel.send(chunk)

        # Return reponse regardless of channel
        return text_content
