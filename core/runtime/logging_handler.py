import datetime
import os
import json
import time
import ast
from typing import Any, Optional, Union
from langchain_core.callbacks import BaseCallbackHandler
from core.knowledge.memory.sqlite_session_store import SqliteSessionStore
from core.runtime.execution_context import ExecutionContext
from core.util.logging_util import extract_model_name, format_tool_extra_str


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
        self.manager = SqliteSessionStore()
        self.llm_start_time = None
        self.last_execution_time = 0.0
        self.tool_start_times = {}

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
                from core.runtime.execution_context import try_context
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
                from core.runtime.execution_context import try_context
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

    def on_tool_end(self, output, **kwargs):
        if not self._is_for_this_agent(kwargs):
            return
        if self.session_id:
            run_id = str(kwargs.get("run_id") or "default")
            start_t = getattr(self, "tool_start_times", {}).pop(run_id, None)
            latency_str = ""
            if start_t is not None:
                latency_str = f" [{round(time.time() - start_t, 3)}s]"

            content = output.content if hasattr(output, 'content') else str(output)
            if isinstance(content, (list, dict)):
                try:
                    content = json.dumps(content)
                except Exception:
                    content = str(content)
            elif not isinstance(content, str):
                content = str(content)
            self.manager.append_message(self.session_id, 'system', f"Tool Output{latency_str}: {content}")
