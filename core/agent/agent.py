import asyncio
import os
import sys
import discord
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional, Tuple, List, Union

from core.util import split_message, Config, format_error_message, save_agent_memory_log
from core.agent.base_agent import BaseAgent
from core.agent.job_manager import current_session_identifier, JobManager
from core.agent.logging_handler import LoggingHandler
from core.agent.command_handler import CommandHandler
from core.agent.context_pruner import ContextPruner
from core.agent.discord_ui import PollButtonView, PollSelectView
from core.agent.agent_response import AgentResponse
from core.agent.session_identifier import SessionIdentifier
from core.knowledge.memory.sqlite_checkpointer import SqliteCheckpointer
from core.agent.stream_handler import (
    StreamHandler,
    EVENT_TOKEN,
    EVENT_FINAL_RESPONSE,
    EVENT_ERROR,
    EVENT_SUBAGENT_FINAL,
    SUBAGENT_STREAM_TOKEN,
    SUBAGENT_STREAM_FINAL,
)


class Agent(BaseAgent):
    def __init__(self, agent_id: str, config: Dict[str, Any]):
        super().__init__(agent_id, config)
        self.graph = None

    @staticmethod
    def _is_empty_prompt(prompt: str | list) -> bool:
        """Checks if the user prompt is empty or blank whitespace."""
        if not prompt:
            return True
        if isinstance(prompt, str) and not prompt.strip():
            return True
        return False

    async def _prepare_execution(
        self,
        prompt: str | list,
        session: SessionIdentifier,
        callbacks: Optional[list] = None,
        role: str = "user"
    ) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
        """
        Executes system commands, auto-prunes context,
        ensures graph compilation, and returns (config, inputs).
        Returns None if handled by a system command.
        """
        session_id = session.session_id
        channel_name = session.channel_name
        job_id = session.job_id

        # Handle system commands ([new], [newall], [restart])
        if await CommandHandler().handle_command(prompt, session=session):
            return None

        # Auto-prune session checkpoint in SQLite storage before execution if history exceeds thresholds
        if not session.is_stateless():
            try:
                await ContextPruner().aauto_prune_session(session=session)
            except Exception as e:
                print(f"[Agent:{self.agent_id}] Warning: auto_prune_session failed for session '{session_id}': {e}", file=sys.stderr)

        # Lazy load langgraph graph object via GraphBuilder
        if self.graph is None:
            from core.agent.graph_builder import GraphBuilder
            self.graph = await GraphBuilder().build_graph(self.agent_id, self.config)

        prompt_str = prompt if isinstance(prompt, str) else str(prompt)
        JobManager().add_job(session=session, prompt=prompt_str)

        logging_handler = LoggingHandler(session=session, role=role, human_message=prompt)
        config = {
            "configurable": {
                "agent_id": self.agent_id,
                "session_id": session_id,
                "thread_id": session.get_session_thread_id(),
                "job_id": job_id,
            },
            "callbacks": [logging_handler] + (callbacks or []),
            "recursion_limit": 50,
            "run_name": f"agent:{self.agent_id}",
            "tags": [self.agent_id, session.source, f"role:{role}"],
            "metadata": {
                "agent_id": self.agent_id,
                "session_id": session_id,
                "source": session.source,
                "job_id": job_id,
                "channel": channel_name,
                "role": role,
            }
        }

        inputs = {"messages": [{"role": role, "content": prompt}]}
        return config, inputs

    def _update_job_status(self, session: SessionIdentifier, config: Dict[str, Any]):
        """Updates job status based on final graph state."""
        job_id = session.job_id
        if not job_id:
            return

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
        session: SessionIdentifier,
        response: AgentResponse
    ):
        """Sends split message chunks, poll UI components, and media files to Discord."""
        channel = session.channel_obj
        source = session.source

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
            media_items = []
            if image_paths:
                for p in image_paths:
                    media_items.append((p, "Image"))
            if video_paths:
                for p in video_paths:
                    media_items.append((p, "Video"))

            for path, media_type in media_items:
                # 1. Path is already an absolute path
                # 2. Path is a relative path resolved against system root ("./")
                resolved_path = os.path.abspath(os.path.join(os.getcwd(), path))
                

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

    async def _handle_empty_content(self, channel: Optional[discord.TextChannel], source: str) -> str:
        """Emits warning for empty user prompts."""
        msg = "I cannot process empty messages. Please provide some text."
        if channel is not None:
            try:
                await channel.send(msg)
            except Exception as e:
                print(f"[Agent:{self.agent_id}] Error sending empty content warning: {e}")
        return msg

    @asynccontextmanager
    async def _execution_context(self, session: SessionIdentifier):
        """Context manager managing ContextVars and initial job running status."""
        session_token = current_session_identifier.set(session)

        job_id = session.job_id or ""
        if job_id:
            JobManager().update_job(job_id, "running")

        try:
            yield
        finally:
            current_session_identifier.reset(session_token)

    @staticmethod
    def _is_corrupt_checkpoint_error(error: Exception) -> bool:
        """Detects dangling tool call exceptions resulting from interrupted/corrupted checkpoints."""
        return "tool_calls that do not have a corresponding ToolMessage" in str(error)

    def _recover_corrupt_checkpoint(self, session: SessionIdentifier):
        """Rolls back the corrupt checkpoint step in SQLite storage to restore a valid state."""
        session_id = session.session_id
        SqliteCheckpointer().rollback_last_step(session_id)
        print(f"[Agent:{self.agent_id}] Rolled back corrupt checkpoint for session: {session_id}, retrying...")

    async def _handle_execution_error(
        self,
        session: SessionIdentifier,
        error: Exception
    ) -> str:
        """Updates job status to error, formats error message, and dispatches to channel."""
        JobManager().update_job(session.job_id, "error")
        err_msg = format_error_message(error)
        print(f"[Agent:{self.agent_id}] Error executing graph: {err_msg}")
        if session.channel is not None and session.source in ["discord", "scheduled"]:
            try:
                await session.channel.send(err_msg)
            except Exception as se:
                print(f"[Agent:{self.agent_id}] Error sending failure message: {se}")
        return err_msg

    async def execute(
        self,
        prompt: Union[str, list],
        session: SessionIdentifier,
        callbacks: Optional[list] = None,
        role: str = "user"
    ) -> str:
        """Executes the agent graph in batch mode and returns full response text."""
        if self._is_empty_prompt(prompt):
            return await self._handle_empty_content(session.channel_obj, session.source)

        prep = await self._prepare_execution(prompt, session, callbacks, role)
        if prep is None:
            return ""
        config, inputs = prep

        async with self._execution_context(session):
            try:
                print(f"Invoking graph for {self.agent_id}")
                try:
                    result = await self.graph.ainvoke(inputs, config=config)
                except Exception as e:
                    if self._is_corrupt_checkpoint_error(e):
                        self._recover_corrupt_checkpoint(session)
                        result = await self.graph.ainvoke(inputs, config=config)
                    else:
                        raise e
                self._update_job_status(session, config)
            except Exception as e:
                return await self._handle_execution_error(session, e)

        reply_message = result["messages"][-1]
        response = self._parse_final_response(reply_message.content)
        await self._dispatch_discord_output(session, response)
        return response.text

    async def execute_stream(
        self,
        prompt: Union[str, list],
        session: SessionIdentifier,
        callbacks: Optional[list] = None,
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
        if self._is_empty_prompt(prompt):
            msg = await self._handle_empty_content(session.channel_obj, session.source)
            yield {
                "type": EVENT_FINAL_RESPONSE,
                "text": msg,
                "poll_data": None,
                "image_paths": [],
                "video_paths": [],
                "system_memory_log": None,
                "response": None
            }
            return

        prep = await self._prepare_execution(prompt, session, callbacks, role)
        if prep is None:
            return
        config, inputs = prep

        accumulated_tokens = []
        subagent_final_response = None
        async with self._execution_context(session):
            try:
                print(f"Streaming graph for {self.agent_id}")
                async for event in StreamHandler.stream_with_recovery(
                    graph=self.graph,
                    inputs=inputs,
                    config=config,
                    session=session,
                    recover_checkpoint_fn=self._recover_corrupt_checkpoint,
                    is_corrupt_checkpoint_fn=self._is_corrupt_checkpoint_error
                ):
                    if event.get("type") == EVENT_TOKEN:
                        accumulated_tokens.append(event.get("content", ""))
                    elif event.get("type") == EVENT_SUBAGENT_FINAL:
                        subagent_final_response = event.get("response")
                    yield event
                self._update_job_status(session, config)
            except Exception as e:
                err_msg = await self._handle_execution_error(session, e)
                yield {"type": EVENT_ERROR, "content": err_msg}
                return

        response = subagent_final_response
        if not response:
            """Extracts final response from graph state or accumulated stream tokens and parses AgentResponse."""
            response = StreamHandler.resolve_final_response(
                graph=self.graph,
                config=config,
                accumulated_tokens=accumulated_tokens,
                parse_fn=self._parse_final_response
            )

        yield {
            "type": EVENT_FINAL_RESPONSE,
            "text": response.text,
            "poll_data": response.poll_data,
            "image_paths": response.image_paths,
            "video_paths": response.video_paths,
            "system_memory_log": response.system_memory_log,
            "response": response
        }
