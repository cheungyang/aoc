import datetime
import os
import json
import time
import ast
from typing import Any
from langchain_core.callbacks import BaseCallbackHandler
from core.knowledge.memory.sqlite_session_store import SqliteSessionStore

def format_tool_extra_str(input_str: Any) -> str:
    """
    Format extra string info for tool start logging.
    - For filesystem instructions list: [action "ls" on {directory1}, "read" on {file2}]
    - For other tools with action/path/skill_id: [action: create, path: /tmp, skill_id: skill_123]
    """
    if input_str is None:
        return ""

    try:
        input_dict = None
        if isinstance(input_str, dict):
            input_dict = input_str
        elif isinstance(input_str, str):
            try:
                input_dict = json.loads(input_str)
            except Exception:
                try:
                    input_dict = ast.literal_eval(input_str)
                except Exception:
                    input_dict = None

        if not isinstance(input_dict, dict):
            return ""

        instructions = input_dict.get("instructions")
        if isinstance(instructions, str):
            try:
                instructions = json.loads(instructions)
            except Exception:
                try:
                    instructions = ast.literal_eval(instructions)
                except Exception:
                    pass

        if isinstance(instructions, list) and instructions:
            action_items = []
            for inst in instructions:
                if isinstance(inst, dict):
                    act = inst.get("action")
                    p = inst.get("path")
                    if act and p:
                        action_items.append(f'"{act}" on {p}')
                    elif act:
                        action_items.append(f'"{act}"')
                    elif p:
                        action_items.append(f'on {p}')
            if action_items:
                joined = ", ".join(action_items)
                return f" [action {joined}]"

        action = input_dict.get("action")
        path = input_dict.get("path")
        skill_id = input_dict.get("skill_id")

        extra_info = []
        if action:
            extra_info.append(f"action: {action}")
        if path:
            extra_info.append(f"path: {path}")
        if skill_id:
            extra_info.append(f"skill_id: {skill_id}")
        if extra_info:
            return f" [{', '.join(extra_info)}]"
    except Exception:
        pass

    return ""


class LoggingHandler(BaseCallbackHandler):
    def __init__(self, session_id=None, role=None, human_message=None):
        self.session_id = session_id
        self.role = role
        self.human_message = human_message
        self.manager = SqliteSessionStore()
        self.llm_start_time = None
        self.last_execution_time = 0.0
        self.tool_start_times = {}
        
    def on_llm_start(self, serialized, prompts, **kwargs):
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
                # Add model name if available
                if hasattr(response, 'llm_output') and response.llm_output:
                    self.last_token_usage['model'] = response.llm_output.get('model_name', 'unknown')

    def on_chain_end(self, outputs, **kwargs):
        if hasattr(self, 'last_token_usage') and self.last_token_usage:
            usage = self.last_token_usage
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
                self.manager.append_token_usage(self.session_id, model, input_token, output_token, cached_percent, execution_time)
            
            # Clear it so we don't log it again unless a new LLM call happens
            self.last_token_usage = None
            self.last_execution_time = 0.0

    def on_tool_start(self, serialized, input_str, **kwargs):
        tool_name = serialized.get("name", "Unknown")
        run_id = str(kwargs.get("run_id") or "default")
        if not hasattr(self, "tool_start_times"):
            self.tool_start_times = {}
        self.tool_start_times[run_id] = time.time()
        
        extra_str = format_tool_extra_str(input_str)
        
        print(f"Tool use: {tool_name}{extra_str}")
        if self.session_id:
            self.manager.append_message(self.session_id, 'system', f"Tool {tool_name}{extra_str}:{input_str}")

    def on_tool_end(self, output, **kwargs):
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
