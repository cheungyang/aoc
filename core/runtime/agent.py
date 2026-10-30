import sys
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional, Tuple, Union

from core.util.errors import format_error_message
from core.prompts.system import stamp_user_message
from core.runtime.base_agent import BaseAgent
from core.runtime.foundation.context import current_execution_context
from core.runtime.observability.transcript import LoggingHandler
from core.channel.response_parser import AgentResponse
from core.runtime.foundation.context import ExecutionContext
from core.knowledge.memory.sqlite_checkpointer import SqliteCheckpointer
from core.runtime.foundation import events
from core.runtime.graph_builder import GraphBuilder
from core.runtime.hooks import Turn, turn_hooks
from core.runtime.streaming import StreamHandler
from core.loaders.tools_loader import FOLLOW_UP, gets_follow_up


class Agent(BaseAgent):
    """Runs one turn: binds the context, finds the graph, runs it, recovers a
    corrupt checkpoint, and parses the reply. The side work (the job, pruning,
    the memory log) is the turn hooks' (hooks.py); where the output goes is the
    adapter's (foundation/output.py)."""

    def __init__(self, agent_id: str, config: Dict[str, Any]):
        super().__init__(agent_id, config)
        # Compiled graphs are cached per graph binding: the same agent invoked under two
        # different graphs gets two different tool rosters, so it needs two compiled graphs.
        self._graphs: Dict[str, Any] = {}

    @staticmethod
    def _is_empty_prompt(prompt: str | list) -> bool:
        """Checks if the user prompt is empty or blank whitespace."""
        if not prompt:
            return True
        if isinstance(prompt, str) and not prompt.strip():
            return True
        return False

    async def _get_graph(self, ctx: ExecutionContext):
        """Returns the compiled graph for this agent under `ctx`'s graph binding, building it once."""
        # A per-call model override (the dream's floor) is a different model, so a
        # different compiled graph.
        cache_key = ctx.graph_id or ""
        if ctx.model:
            cache_key += f"|{ctx.model}"
        # A run that gets the follow_up tool has another roster, so another compiled graph
        # too: a stateless or non-recording run of the same agent doesn't get it, nor a run
        # another agent asked for (core/loaders/tools_loader.py).
        if gets_follow_up(ctx, self.config):
            cache_key += f"|{FOLLOW_UP}"
        if cache_key not in self._graphs:
            self._graphs[cache_key] = await GraphBuilder().build_graph(ctx, self.config)
        return self._graphs[cache_key]

    async def _prepare_execution(
        self,
        turn: Turn,
        callbacks: Optional[list] = None,
        role: str = "user"
    ) -> Tuple[Dict[str, Any], Dict[str, Any], Any]:
        """
        Ensures graph compilation, runs the hooks' `before` (a pending prune is
        waited for, the job registered), and returns (config, inputs, graph).
        """
        session = turn.ctx
        prompt = turn.prompt
        session_id = session.session_id
        channel_name = session.channel_name
        job_id = session.job_id

        # Turn hints (the model's next-call thinking pick, the calls the proactive
        # team notes) are scratch state for one turn.
        session.turn_hints.clear()

        # Lazy load langgraph graph object via GraphBuilder
        graph = await self._get_graph(session)
        turn.graph = graph

        for hook in turn_hooks():
            await hook.before(turn)

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
        turn.config = config

        # The time of day rides on the user's message rather than in the system
        # prompt, where it would break the cached prefix every minute.
        content = stamp_user_message(prompt) if role == "user" else prompt
        inputs = {"messages": [{"role": role, "content": content}]}
        return config, inputs, graph

    async def _after(self, turn: Turn) -> None:
        """The hooks' `after`. One that fails is logged: the turn already ran."""
        for hook in turn_hooks():
            try:
                await hook.after(turn)
            except Exception as e:
                print(f"[Agent:{self.agent_id}] {type(hook).__name__}.after failed: {e}", file=sys.stderr)

    def _ended(self, turn: Turn) -> None:
        """The hooks' `ended`, however the turn ended. One that fails is logged."""
        for hook in turn_hooks():
            try:
                hook.ended(turn)
            except Exception as e:
                print(f"[Agent:{self.agent_id}] {type(hook).__name__}.ended failed: {e}", file=sys.stderr)

    def _parse_final_response(self, raw_reply: Any) -> AgentResponse:
        """Normalizes list content and parses the XML AgentResponse."""
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

        return AgentResponse.from_string(raw_reply)

    async def _deliver(self, session: ExecutionContext, response: AgentResponse):
        """Hands a batch turn's reply to the output the adapter supplied (foundation/output.py)."""
        if session.output is not None:
            await session.output.deliver(session, response)

    async def _handle_empty_content(self, session: ExecutionContext) -> str:
        """Emits warning for empty user prompts."""
        msg = "I cannot process empty messages. Please provide some text."
        if session.output is not None:
            try:
                await session.output.notify(msg)
            except Exception as e:
                print(
                    f"[Agent:{self.agent_id}] Error sending empty warning: {e}"
                )
        return msg

    @asynccontextmanager
    async def _execution_context(self, session: ExecutionContext):
        """Binds the execution context for the duration of the run.

        This deliberately has no job-status side effect: it is entered *before* preparation
        (so graph construction sees the right identity), and preparation registers the job.
        """
        session_token = current_execution_context.set(session)
        try:
            yield
        finally:
            current_execution_context.reset(session_token)

    @staticmethod
    def _is_corrupt_checkpoint_error(error: Exception) -> bool:
        """Detects dangling tool call exceptions resulting from interrupted/corrupted checkpoints."""
        return "tool_calls that do not have a corresponding ToolMessage" in str(error)

    def _recover_corrupt_checkpoint(self, session: ExecutionContext):
        """Rolls back the corrupt checkpoint step in SQLite storage to restore a valid state."""
        session_id = session.session_id
        SqliteCheckpointer().rollback_last_step(session_id)
        print(f"[Agent:{self.agent_id}] Rolled back corrupt checkpoint for session: {session_id}, retrying...")

    async def _handle_execution_error(self, turn: Turn, error: Exception) -> str:
        """Runs the hooks' `failed` (the job is marked as errored), formats the
        error, and tells the output when a person or a schedule started the turn."""
        session = turn.ctx
        for hook in turn_hooks():
            await hook.failed(turn, error)
        err_msg = format_error_message(error)
        print(f"[Agent:{self.agent_id}] Error executing graph: {err_msg}")
        if session.output is not None and session.source in ["discord", "scheduled"]:
            try:
                await session.output.notify(err_msg)
            except Exception as se:
                print(f"[Agent:{self.agent_id}] Error sending failure message: {se}")
        return err_msg

    async def execute(
        self,
        prompt: Union[str, list],
        session: ExecutionContext,
        callbacks: Optional[list] = None,
        role: str = "user"
    ) -> str:
        """Executes the agent graph in batch mode and returns full response text."""
        if self._is_empty_prompt(prompt):
            return await self._handle_empty_content(session)

        turn = Turn(self.agent_id, self.config, session, prompt)
        # The context is entered *before* preparation: _prepare_execution builds the graph, and
        # graph construction resolves this agent's tool roster and skills. Entering afterwards
        # meant construction ran under the caller's identity (or none at all).
        prepared = False
        try:
            async with self._execution_context(session):
                config, inputs, graph = await self._prepare_execution(turn, callbacks, role)
                prepared = True

                try:
                    print(f"Invoking graph for {self.agent_id}")
                    try:
                        result = await graph.ainvoke(inputs, config=config)
                    except Exception as e:
                        if self._is_corrupt_checkpoint_error(e):
                            self._recover_corrupt_checkpoint(session)
                            result = await graph.ainvoke(inputs, config=config)
                        else:
                            raise e
                except Exception as e:
                    return await self._handle_execution_error(turn, e)

            reply_message = result["messages"][-1]
            response = self._parse_final_response(reply_message.content)
            turn.response = response
            await self._after(turn)
            # For callers that need more than the text: a scheduled run reads
            # the speech version and its question from here (schedule_runner).
            session.turn_hints[events.RESPONSE_HINT] = response
            await self._deliver(session, response)
            return response.text
        finally:
            if prepared:
                self._ended(turn)

    async def execute_stream(
        self,
        prompt: Union[str, list],
        session: ExecutionContext,
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
            msg = await self._handle_empty_content(session)
            yield {
                "type": events.FINAL_RESPONSE,
                "text": msg,
                "poll_data": None,
                "image_paths": [],
                "video_paths": [],
                "system_memory_log": None,
                "response": None
            }
            return

        turn = Turn(self.agent_id, self.config, session, prompt)
        accumulated_tokens = []
        subagent_final_response = None
        prepared = False
        try:
            # Context entered before preparation — see the note in execute().
            async with self._execution_context(session):
                config, inputs, graph = await self._prepare_execution(turn, callbacks, role)
                prepared = True

                try:
                    print(f"Streaming graph for {self.agent_id}")
                    async for event in StreamHandler.stream_with_recovery(
                        graph=graph,
                        inputs=inputs,
                        config=config,
                        session=session,
                        recover_checkpoint_fn=self._recover_corrupt_checkpoint,
                        is_corrupt_checkpoint_fn=self._is_corrupt_checkpoint_error,
                        agent_id=self.agent_id
                    ):
                        if event.get("type") == events.TOKEN:
                            accumulated_tokens.append(event.get("content", ""))
                        elif event.get("type") == events.SUBAGENT_FINAL:
                            subagent_final_response = event.get("response")
                        yield event
                except Exception as e:
                    err_msg = await self._handle_execution_error(turn, e)
                    yield {"type": events.ERROR, "content": err_msg}
                    return

            response = subagent_final_response
            if not response:
                """Extracts final response from graph state or accumulated stream tokens and parses AgentResponse."""
                response = StreamHandler.resolve_final_response(
                    graph=graph,
                    config=config,
                    accumulated_tokens=accumulated_tokens,
                    parse_fn=self._parse_final_response
                )
                # The agent's own reply; one a delegated agent sent back is not its to log.
                turn.response = response
            await self._after(turn)

            yield {
                "type": events.FINAL_RESPONSE,
                "text": response.text,
                "poll_data": response.poll_data,
                "image_paths": response.image_paths,
                "video_paths": response.video_paths,
                "system_memory_log": response.system_memory_log,
                "response": response
            }
        finally:
            # After the final response was read from the checkpoint, or when the
            # consumer stopped early (a voice barge-in closes the stream).
            if prepared:
                self._ended(turn)
