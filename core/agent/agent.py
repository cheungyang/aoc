import asyncio
import os
import sys
import discord
from typing import Any, Dict, Optional, Tuple, List

from core.util import split_message, Config, format_error_message, save_agent_memory_log
from core.agent.base_agent import BaseAgent
from core.agent.job_manager import current_job_id, current_agent_id, current_channel_name
from core.agent.logging_handler import LoggingHandler
from core.agent.command_handler import CommandHandler
from core.agent.context_pruner import ContextPruner
from core.agent.discord_ui import PollButtonView, PollSelectView
from core.agent.agent_response import AgentResponse
from core.knowledge.memory.sqlite_checkpointer import SqliteCheckpointer


class Agent(BaseAgent):
    def __init__(self, agent_id: str, config: Dict[str, Any]):
        super().__init__(agent_id, config)
        self.graph = None

    @staticmethod
    def _is_empty_content(content: str | list) -> bool:
        """Checks if the user prompt is empty or blank whitespace."""
        if not content:
            return True
        if isinstance(content, str) and not content.strip():
            return True
        return False

    async def _prepare_execution(
        self,
        content: str | list,
        source: str,
        job_id: Optional[str] = None,
        channel: Optional[discord.TextChannel] = None,
        callbacks: Optional[list] = None,
        role: str = "user"
    ) -> Optional[Tuple[str, str, str, Dict[str, Any], Dict[str, Any]]]:
        """
        Validates session/job IDs, executes system commands, auto-prunes context,
        ensures graph compilation, and returns (job_id, session_id, channel_name, config, inputs).
        Returns None if handled by a system command.
        """
        from core.agent.job_manager import JobManager
        from core.agent.session_manager import SessionManager
        from core.agent.graph_builder import GraphBuilder

        if job_id is None:
            job_id = JobManager().new_job_id(self.agent_id)

        is_stateless = self.config.get("stateless", False)
        session_id = SessionManager().get_session_id(
            self.agent_id, source, channel, job_id=job_id, stateless=is_stateless
        )

        # Handle system commands ([new], [newall], [restart])
        if await CommandHandler().handle_command(content, session_id=session_id, channel=channel):
            return None

        channel_name = ""
        if channel is not None:
            channel_name = channel.name if hasattr(channel, "name") else str(channel.id)
            if isinstance(channel, discord.Thread) and channel.parent:
                channel_name = channel.parent.name

        # Auto-prune session checkpoint in SQLite storage before execution if history exceeds thresholds
        if not is_stateless:
            try:
                await ContextPruner().aauto_prune_session(session_id, channel=channel_name)
            except Exception as e:
                print(f"[Agent:{self.agent_id}] Warning: auto_prune_session failed for session '{session_id}': {e}", file=sys.stderr)

        # Lazy load langgraph graph object via GraphBuilder
        if self.graph is None:
            self.graph = await GraphBuilder().build_graph(self.agent_id, self.config)

        JobManager().add_job(
            job_id, self.agent_id, session_id,
            initial_prompt=content if isinstance(content, str) else str(content)
        )

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
        return job_id, session_id, channel_name, config, inputs

    def _update_job_status(self, job_id: str, config: Dict[str, Any]):
        """Updates job status based on final graph state."""
        from core.agent.job_manager import JobManager
        job = JobManager()._jobs.get(job_id)
        if job and job.status == "killed":
            return

        state = self.graph.get_state(config) if hasattr(self.graph, "get_state") else None
        if state and getattr(state, "next", None):
            JobManager().update_job(job_id, "partial")
        else:
            JobManager().update_job(job_id, "completed")

    def _parse_final_response(self, raw_reply: Any) -> AgentResponse:
        """Normalizes list content, parses XML AgentResponse, and persists in-band memory logs."""
        if isinstance(raw_reply, list):
            texts = []
            for part in raw_reply:
                if isinstance(part, dict) and part.get("type") == "text":
                    val = part.get("text")
                    texts.append(val if val is not None else "")
                elif isinstance(part, str):
                    texts.append(part)
            raw_reply = "".join(texts)
        elif not isinstance(raw_reply, str):
            raw_reply = str(raw_reply) if raw_reply is not None else ""

        response = AgentResponse.from_string(raw_reply)
        if response.system_memory_log:
            save_agent_memory_log(self.agent_id, response.system_memory_log)

        return response

    async def _dispatch_discord_output(
        self,
        channel: discord.TextChannel,
        source: str,
        response: AgentResponse
    ):
        """Sends split message chunks, poll UI components, and media files to Discord."""
        if channel is None or source not in ["discord", "scheduled"]:
            return

        text_content = response.text
        poll_data = response.poll_data
        image_paths = response.image_paths
        video_paths = response.video_paths

        chunks = split_message(text_content)

        view = None
        if poll_data and source == "discord" and poll_data.get("options"):
            if poll_data.get("allow_multiple"):
                view = PollSelectView(poll_data, channel)
            else:
                view = PollButtonView(poll_data, channel)

        files = []
        missing_files = []
        if (image_paths or video_paths) and source == "discord":
            pkm_dir = Config().pkm_dir
            media_items = []
            if image_paths:
                for p in image_paths:
                    media_items.append((p, "Image"))
            if video_paths:
                for p in video_paths:
                    media_items.append((p, "Video"))

            for path, media_type in media_items:
                # 1. Path is already an absolute path
                # 2. Path is a relative path resolved against pkm_dir (Config().pkm_dir)
                resolved_path = path if os.path.isabs(path) else os.path.join(pkm_dir, path)

                if os.path.exists(resolved_path):
                    files.append(discord.File(resolved_path))
                else:
                    missing_files.append((path, media_type))

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

    async def execute(
        self,
        content: str | list,
        source: str,
        job_id: str = None,
        channel: discord.TextChannel = None,
        callbacks: list = None,
        role: str = "user"
    ) -> str:
        """Executes the agent graph in batch mode and returns full response text."""
        if self._is_empty_content(content):
            msg = "I cannot process empty messages. Please provide some text."
            if channel is not None:
                await channel.send(msg)
            return msg

        prep = await self._prepare_execution(content, source, job_id, channel, callbacks, role)
        if prep is None:
            return ""
        job_id, session_id, channel_name, config, inputs = prep

        from core.agent.job_manager import JobManager, current_job_id, current_agent_id, current_channel_name
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
                        SqliteCheckpointer().rollback_last_step(session_id)
                        print(f"Rolled back corrupt checkpoint for session: {session_id}, retrying...")
                        result = await self.graph.ainvoke(inputs, config=config)
                    else:
                        raise e

                self._update_job_status(job_id, config)
            except Exception as e:
                JobManager().update_job(job_id, "error")
                err_msg = format_error_message(e)
                print(f"Error invoking graph: {err_msg}")
                if channel is not None and source in ["discord", "scheduled"]:
                    await channel.send(err_msg)
                return err_msg
        finally:
            current_job_id.reset(token)
            current_channel_name.reset(channel_token)
            current_agent_id.reset(agent_token)

        reply_message = result["messages"][-1]
        response = self._parse_final_response(reply_message.content)
        await self._dispatch_discord_output(channel, source, response)
        return response.text

    async def execute_stream(
        self,
        content: str | list,
        source: str,
        job_id: str = None,
        channel: discord.TextChannel = None,
        callbacks: list = None,
        role: str = "user"
    ):
        """
        Asynchronously streams LangGraph execution events (tokens, tool calls, and final response).
        Yields event dicts:
          - {"type": "token", "content": str}
          - {"type": "tool_start", "tool_name": str, "tool_args": dict, "run_id": str}
          - {"type": "tool_end", "tool_name": str, "output": str, "run_id": str}
          - {"type": "final_response", "text": str, "poll_data": dict, "image_paths": list, "video_paths": list, "system_memory_log": str, "response": AgentResponse}
        """
        if self._is_empty_content(content):
            msg = "I cannot process empty messages. Please provide some text."
            if channel is not None and source in ["discord", "scheduled"]:
                await channel.send(msg)
            yield {"type": "final_response", "text": msg, "poll_data": None, "image_paths": [], "video_paths": [], "system_memory_log": None, "response": None}
            return

        prep = await self._prepare_execution(content, source, job_id, channel, callbacks, role)
        if prep is None:
            return
        job_id, session_id, channel_name, config, inputs = prep

        from core.agent.job_manager import JobManager, current_job_id, current_agent_id, current_channel_name
        token = current_job_id.set(job_id)
        channel_token = current_channel_name.set(channel_name)
        agent_token = current_agent_id.set(self.agent_id)

        accumulated_tokens = []
        try:
            try:
                JobManager().update_job(job_id, "running")
                print(f"Streaming graph for {self.agent_id}")

                if hasattr(self.graph, "astream_events"):
                    async for event in self.graph.astream_events(inputs, config=config, version="v2"):
                        kind = event.get("event")
                        if kind == "on_chat_model_stream":
                            chunk = event.get("data", {}).get("chunk")
                            if chunk and hasattr(chunk, "content"):
                                content_delta = chunk.content
                                if isinstance(content_delta, str) and content_delta:
                                    accumulated_tokens.append(content_delta)
                                    yield {"type": "token", "content": content_delta}
                                elif isinstance(content_delta, list):
                                    for p in content_delta:
                                        if isinstance(p, dict) and p.get("type") == "text":
                                            t = p.get("text", "")
                                            if t:
                                                accumulated_tokens.append(t)
                                                yield {"type": "token", "content": t}
                                        elif isinstance(p, str) and p:
                                            accumulated_tokens.append(p)
                                            yield {"type": "token", "content": p}
                        elif kind == "on_tool_start":
                            yield {
                                "type": "tool_start",
                                "tool_name": event.get("name"),
                                "tool_args": event.get("data", {}).get("input", {}),
                                "run_id": event.get("run_id")
                            }
                        elif kind == "on_tool_end":
                            yield {
                                "type": "tool_end",
                                "tool_name": event.get("name"),
                                "output": event.get("data", {}).get("output"),
                                "run_id": event.get("run_id")
                            }
                else:
                    result = await self.graph.ainvoke(inputs, config=config)
                    reply_message = result["messages"][-1]
                    accumulated_tokens.append(str(reply_message.content))
                    yield {"type": "token", "content": str(reply_message.content)}

                self._update_job_status(job_id, config)
            except Exception as e:
                JobManager().update_job(job_id, "error")
                err_msg = format_error_message(e)
                print(f"Error streaming graph: {err_msg}")
                if channel is not None and source in ["discord", "scheduled"]:
                    await channel.send(err_msg)
                yield {"type": "error", "content": err_msg}
                return
        finally:
            current_job_id.reset(token)
            current_channel_name.reset(channel_token)
            current_agent_id.reset(agent_token)

        state = self.graph.get_state(config) if hasattr(self.graph, "get_state") else None
        raw_reply = ""
        if state and hasattr(state, "values") and state.values and "messages" in state.values and state.values["messages"]:
            raw_reply = state.values["messages"][-1].content
        elif accumulated_tokens:
            raw_reply = "".join(accumulated_tokens)

        response = self._parse_final_response(raw_reply)

        yield {
            "type": "final_response",
            "text": response.text,
            "poll_data": response.poll_data,
            "image_paths": response.image_paths,
            "video_paths": response.video_paths,
            "system_memory_log": response.system_memory_log,
            "response": response
        }
