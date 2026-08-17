from core.util import split_message, Config
from core.agent.logging_handler import LoggingHandler
from core.agent.base_agent import BaseAgent
from core.agent.job_manager import current_job_id, current_agent_id
import asyncio
import os

import json
import ast
from typing import Any, Dict
import discord
import xml.etree.ElementTree as ET
import re
from core.agent.discord_ui import PollButtonView, PollSelectView
from core.agent.agent_response import AgentResponse

class Agent(BaseAgent):
    def __init__(self, agent_id, config):
        super().__init__(agent_id, config)
        self.graph = None

    async def _build_graph(self):
        from .graph_builder import GraphBuilder
        builder = GraphBuilder()
        return await builder.build_graph(self.agent_id, self.config)

    async def execute(self, content: str | list, source: str, job_id: str = None, channel: discord.TextChannel = None, callbacks: list = None, role: str = "user") -> str:
        if not content:
            msg = "I cannot process empty messages. Please provide some text."
            if channel is not None:
                await channel.send(msg)
            return msg

        if isinstance(content, str) and not content.strip():
            msg = "I cannot process empty messages. Please provide some text."
            if channel is not None:
                await channel.send(msg)
            return msg

        from core.agent.job_manager import JobManager
        from core.agent.session_manager import SessionManager
        from core.agent.logging_handler import LoggingHandler
        from core.agent.command_handler import CommandHandler
        
        # Get the necessary ids
        session_id = SessionManager().get_session_id(self.agent_id, source, channel)
        if job_id is None:
            job_id = JobManager().new_job_id(self.agent_id)

        # Handle system commands ([new], [newall], [restart])
        if await CommandHandler().handle_command(content, session_id=session_id, channel=channel):
            return

        # Lazy load langgraph graph object
        if self.graph is None:
            self.graph = await self._build_graph()

        channel_name = ""
        if channel is not None:
            channel_name = channel.name if hasattr(channel, "name") else str(channel.id)
            if isinstance(channel, discord.Thread) and channel.parent:
                channel_name = channel.parent.name

        JobManager().add_job(job_id, self.agent_id, session_id, initial_prompt=content if isinstance(content, str) else str(content))
        logging_handler = LoggingHandler(session_id=session_id, role=role, human_message=content)
        config = {
            "configurable": {
                "thread_id": session_id,
                "agent_id": self.agent_id
            },
            "callbacks": [logging_handler] + (callbacks or []),
            "recursion_limit": 100,
            "run_name": f"agent:{self.agent_id}",
            "tags": [self.agent_id, source, f"role:{role}"],
            "metadata": {
                "agent_id": self.agent_id,
                "session_id": session_id,
                "source": source,
                "job_id": job_id,
                "channel": channel_name,
                "role": role
            }
        }

        inputs = {"messages": [{"role": role, "content": content}]}

        from core.agent.job_manager import current_channel_name
        token = current_job_id.set(job_id)
        channel_token = current_channel_name.set(channel_name)
        agent_token = current_agent_id.set(self.agent_id)
        try:
            try:
                JobManager().update_job(job_id, "running")
                print(f"Invoking graph for {self.agent_id}")
                try:
                    result = await self.graph.ainvoke(inputs, config=config)
                except Exception as e:
                    if "tool_calls that do not have a corresponding ToolMessage" in str(e):
                        from core.knowledge.memory.sqlite_checkpointer import SqliteCheckpointer
                        SqliteCheckpointer().rollback_last_step(session_id)
                        print(f"Rolled back corrupt checkpoint for session: {session_id}, retrying...")
                        result = await self.graph.ainvoke(inputs, config=config)
                    else:
                        raise e
                
                # Check if paused for human input
                state = self.graph.get_state(config)
                
                from core.agent.job_manager import JobManager
                job = JobManager()._jobs.get(job_id)
                if job and job.status == "killed":
                    pass
                elif state.next:
                    JobManager().update_job(job_id, "partial")
                else:
                    JobManager().update_job(job_id, "completed")
            except Exception as e:
                JobManager().update_job(job_id, "error")
                import traceback
                traceback.print_exc()
                print(f"Error invoking graph: {e}")
                return "Sorry, I encountered an error processing the request."
        finally:
            current_job_id.reset(token)
            current_channel_name.reset(channel_token)
            current_agent_id.reset(agent_token)

        # Extract the last response message
        reply_message = result["messages"][-1]
        reply_text = reply_message.content
        
        # Handle list content (common with block format/tool responses)
        if isinstance(reply_text, list):
            texts = []
            for part in reply_text:
                if isinstance(part, dict) and part.get("type") == "text":
                    val = part.get("text")
                    texts.append(val if val is not None else "")
                elif isinstance(part, str):
                    texts.append(part)
            reply_text = "".join(texts)
            
        # Parse XML
        response = AgentResponse.from_string(reply_text)
        text_content = response.text
        poll_data = response.poll_data
        image_paths = response.image_paths
        video_paths = response.video_paths

        # Send message to channel only for direct Discord or scheduled invocations
        if channel is not None and source in ["discord", "scheduled"]:
            chunks = split_message(text_content)
            
            # If there is a poll, we attach the view to the last chunk
            view = None
            if poll_data and source == "discord" and poll_data.get("options"):
                if poll_data["allow_multiple"]:
                    view = PollSelectView(poll_data, channel)
                else:
                    view = PollButtonView(poll_data, channel)
            
            files = []
            missing_files = []
            if (image_paths or video_paths) and source == "discord":
                pkm_dir = Config().pkm_dir
                media_items = []
                if image_paths:
                    for path in image_paths:
                        media_items.append((path, "Image"))
                if video_paths:
                    for path in video_paths:
                        media_items.append((path, "Video"))

                for path, media_type in media_items:
                    resolved_path = path
                    if not os.path.exists(resolved_path):
                        clean_path = path[4:] if path.startswith("pkm/") else path
                        candidates = [
                            os.path.join(pkm_dir, clean_path),
                            os.path.expanduser(f"~/{path}"),
                        ]
                        for cand in candidates:
                            if cand and os.path.exists(cand):
                                resolved_path = cand
                                break

                    if os.path.exists(resolved_path):
                        files.append(discord.File(resolved_path))
                    else:
                        missing_files.append((path, media_type))
            
            # If no text content, but we have files or view, create an empty chunk to carry them
            if not chunks and (files or view):
                chunks = [""]

            if chunks:
                for i, chunk in enumerate(chunks):
                    if i > 0:
                        await asyncio.sleep(1)
                    if i == len(chunks) - 1:
                        kwargs = {}
                        if view:
                            kwargs["view"] = view
                        if files:
                            kwargs["files"] = files
                        try:
                            await channel.send(chunk, **kwargs)
                        except discord.HTTPException as e:
                            if view:
                                print(f"Warning: Failed to send message with view ({e}). Retrying without view.")
                                kwargs.pop("view", None)
                                await channel.send(chunk, **kwargs)
                            else:
                                raise
                    else:
                        await channel.send(chunk)
            
            if missing_files:
                for path, media_type in missing_files:
                    await channel.send(f"{media_type} file not found: {path}")

        # Return reponse regardless of channel
        return text_content
