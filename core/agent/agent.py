from core.util import split_message
from core.agent.logging_handler import LoggingHandler

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
            
        # Send message to channel
        if channel is not None:
            chunks = split_message(reply_text)
            for chunk in chunks:
                await channel.send(chunk)

        # Return reponse regardness of channel
        return reply_text
