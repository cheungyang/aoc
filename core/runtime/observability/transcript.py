import datetime
import os
import json
import re
import time
import ast
from typing import Any, Optional, Union
from langchain_core.callbacks import BaseCallbackHandler
from core.runtime.foundation.context import ExecutionContext
from core.storage.sessions import SessionStore
from core.runtime.observability.log_format import extract_model_name, format_tool_extra_str
from core.tooling.hooks import ToolCall, tool_called

_STATUS = re.compile(r"<status>\s*(ok|partial|failed)\s*</status>", re.IGNORECASE)
_ERRORS = re.compile(r"<errors>(.*?)</errors>", re.DOTALL)
# A tool's envelope opens with its status and closes with its errors
# (core/tooling/report.py); only those ends are read, since an output can be huge.
_ENVELOPE_END = 4000
# How much of a tool's reply the session log keeps: its head and its tail,
# with the size of what's left out between them. The log is read back (`!job`,
# hand-off capsules, the health report), and one Home Assistant reply was
# 150 MB. The model's own history (the checkpoint) is a separate copy, so the
# cut changes nothing it sees.
LOGGED_OUTPUT_CHARS = 64 * 1024


def tool_status(content: str) -> str:
    """A tool call's outcome from its reply: "ok", "partial" or "failed".

    An @agent_tool reply says so in <status>; a shared tool's reply has only
    <errors>, which is "None" when it worked.
    """
    text = content or ""
    status = _STATUS.search(text[:_ENVELOPE_END])
    if status:
        return status.group(1).lower()
    errors = _ERRORS.search(text[-_ENVELOPE_END:])
    if errors and errors.group(1).strip() not in ("", "None"):
        return "failed"
    return "ok"


def logged_output(content: str, limit: int = LOGGED_OUTPUT_CHARS) -> str:
    """`content` as the session log keeps it: whole, or its two ends around a
    note of how much was left out. The ends hold the envelope's status and errors."""
    if len(content) <= limit:
        return content
    half = limit // 2
    return f"{content[:half]}\n[… {len(content) - 2 * half:,} characters left out of the log …]\n{content[-half:]}"


def _tool_args(inputs: Any, input_str: Any) -> dict:
    """A tool call's arguments: LangChain's `inputs` when it passes them, else its input string, parsed."""
    if isinstance(inputs, dict):
        return inputs
    if isinstance(input_str, dict):
        return input_str
    if isinstance(input_str, str):
        for parse in (json.loads, ast.literal_eval):
            try:
                value = parse(input_str)
            except Exception:
                continue
            if isinstance(value, dict):
                return value
        return {"input": input_str}
    return {}


class LoggingHandler(BaseCallbackHandler):
    def __init__(
        self,
        session: ExecutionContext,
        human_message: Optional[Union[str, list]] = None,
        role: str = "user",
    ):
        self.session = session
        self.session_id = session.session_id
        self.agent_id = session.agent_id
        self.role = role
        self.human_message = human_message
        self.manager = SessionStore()
        self.llm_start_time = None
        self.last_execution_time = 0.0
        self.tool_start_times = {}
        # run_id -> (tool, args) of the calls under way, announced to the tool
        # hooks (core/tooling/hooks.py) when they finish.
        self._calls = {}

    def _is_for_this_agent(self, kwargs: dict) -> bool:
        """
        Determines whether this callback event belongs to this handler's agent/session.
        Prevents parent agent handlers (e.g. main) from logging or recording
        events dispatched by child agents (e.g. subagents invoked via agent_call).
        """
        metadata = kwargs.get("metadata") or {}
        event_agent = metadata.get("agent_id")
        if not event_agent:
            try:
                from core.runtime.foundation.context import try_context
                sess = try_context()
                event_agent = sess.agent_id if sess else None
            except Exception:
                pass
        if self.agent_id and event_agent and event_agent != self.agent_id:
            return False
        return True

    def on_llm_start(self, serialized, prompts, **kwargs):
        if not self._is_for_this_agent(kwargs):
            return
        self.llm_start_time = time.time()
        if self.session_id and self.role and self.human_message is not None:
            msg = self.human_message
            if isinstance(msg, (list, dict)):
                try:
                    msg = json.dumps(msg)
                except Exception:
                    msg = str(msg)
            elif not isinstance(msg, str):
                msg = str(msg)
            self.manager.append_message(self.session_id, self.role, msg)
            self.human_message = None

    def on_llm_end(self, response, **kwargs):
        if not self._is_for_this_agent(kwargs):
            return
        if hasattr(self, 'llm_start_time') and self.llm_start_time:
            self.last_execution_time = round(time.time() - self.llm_start_time, 3)
            self.llm_start_time = None

        if response.generations and response.generations[0]:
             if self.session_id:
                  gen = response.generations[0][0]
                  ai_response = gen.text
                  if not ai_response and hasattr(gen, 'message') and gen.message:
                      ai_response = gen.message.content
                  if isinstance(ai_response, (list, dict)):
                      try:
                          ai_response = json.dumps(ai_response)
                      except Exception:
                          ai_response = str(ai_response)
                  elif not isinstance(ai_response, str):
                      ai_response = str(ai_response) if ai_response is not None else ""
                  self.manager.append_message(self.session_id, 'ai', ai_response)
        
        # Extract token usage
        self.last_token_usage = None
        if response.generations and response.generations[0]:
            gen = response.generations[0][0]
            if hasattr(gen, 'message') and hasattr(gen.message, 'usage_metadata') and gen.message.usage_metadata:
                self.last_token_usage = dict(gen.message.usage_metadata)
                self.last_token_usage['model'] = extract_model_name(response, gen)

    def _surface(self) -> str:
        try:
            return self.session.get_surface()
        except Exception:
            return "text"

    def _record_token_usage(self, usage: dict):
        input_token = usage.get('input_tokens', 0)
        output_token = usage.get('output_tokens', 0)
        model = usage.get('model', 'unknown')
        execution_time = getattr(self, 'last_execution_time', 0.0) or 0.0

        # Calculate cached token %
        cached_tokens = 0
        if 'input_token_details' in usage:
            cached_tokens = usage['input_token_details'].get('cache_read', 0)
        elif 'cache_read' in usage: # fallback
            cached_tokens = usage.get('cache_read', 0)

        cached_percent = 0
        if input_token > 0:
            cached_percent = (cached_tokens / input_token) * 100

        if self.session_id:
            self.manager.append_token_usage(
                self.session_id, model, input_token, output_token, cached_percent, execution_time,
                surface=self._surface(),
            )

    def on_chain_end(self, outputs, **kwargs):
        if not self._is_for_this_agent(kwargs):
            return
        if hasattr(self, 'last_token_usage') and self.last_token_usage:
            self._record_token_usage(self.last_token_usage)
            
            # Clear it so we don't log it again unless a new LLM call happens
            self.last_token_usage = None
            self.last_execution_time = 0.0

    def on_tool_start(self, serialized, input_str, **kwargs):
        if not self._is_for_this_agent(kwargs):
            return
        tool_name = serialized.get("name", "Unknown") if isinstance(serialized, dict) else "Unknown"
        run_id = str(kwargs.get("run_id") or "default")
        if not hasattr(self, "tool_start_times"):
            self.tool_start_times = {}
        self.tool_start_times[run_id] = time.time()
        
        agent_id = self.agent_id
        if not agent_id:
            try:
                from core.runtime.foundation.context import try_context
                sess = try_context()
                agent_id = sess.agent_id if sess else None
            except Exception:
                pass

        if not agent_id:
            try:
                dict_val = None
                if isinstance(input_str, dict):
                    dict_val = input_str
                elif isinstance(input_str, str):
                    try:
                        dict_val = json.loads(input_str)
                    except Exception:
                        try:
                            dict_val = ast.literal_eval(input_str)
                        except Exception:
                            pass
                if isinstance(dict_val, dict):
                    if tool_name == "agent_call":
                        agent_id = dict_val.get("caller")
                    else:
                        agent_id = dict_val.get("agent_id")
            except Exception:
                pass

        agent_prefix = f"[Agent:{agent_id}] " if agent_id else ""
        extra_str = format_tool_extra_str(input_str, tool_name=tool_name)
        
        print(f"{agent_prefix}Tool use: {tool_name}{extra_str}")
        if self.session_id:
            self.manager.append_message(self.session_id, 'system', f"Tool {tool_name}{extra_str}:{input_str}")
        self._calls[run_id] = (tool_name, _tool_args(kwargs.get("inputs"), input_str))

    def on_tool_end(self, output, **kwargs):
        if not self._is_for_this_agent(kwargs):
            return
        run_id = str(kwargs.get("run_id") or "default")
        content = output.content if hasattr(output, 'content') else str(output)
        if isinstance(content, (list, dict)):
            try:
                content = json.dumps(content)
            except Exception:
                content = str(content)
        elif not isinstance(content, str):
            content = str(content)
        self._announce(run_id, tool_status(content))
        if self.session_id:
            start_t = getattr(self, "tool_start_times", {}).pop(run_id, None)
            latency_str = ""
            if start_t is not None:
                latency_str = f" [{round(time.time() - start_t, 3)}s]"
            self.manager.append_message(self.session_id, 'system', f"Tool Output{latency_str}: {logged_output(content)}")

    def on_tool_error(self, error, **kwargs):
        """A tool that raised: announced as failed. Its error reaches the model as the call's reply."""
        if not self._is_for_this_agent(kwargs):
            return
        self._announce(str(kwargs.get("run_id") or "default"), "failed")

    def _announce(self, run_id: str, status: str) -> None:
        """Tells the tool hooks (core/tooling/hooks.py) that a call of this agent's finished."""
        started = self._calls.pop(run_id, None)
        if started is not None:
            tool, args = started
            tool_called(ToolCall(tool=tool, args=args, status=status, session=self.session))
