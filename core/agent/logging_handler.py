import datetime
import os
import json
import time
from langchain_core.callbacks import BaseCallbackHandler
from core.memory.flat_file_session_store import FlatFileSessionStore

class LoggingHandler(BaseCallbackHandler):
    def __init__(self, session_id=None, role=None, human_message=None):
        self.session_id = session_id
        self.role = role
        self.human_message = human_message
        self.manager = FlatFileSessionStore()
        
    def on_llm_start(self, serialized, prompts, **kwargs):
        if self.session_id and self.role and self.human_message:
            self.manager.append_message(self.session_id, self.role, self.human_message)

    def on_llm_end(self, response, **kwargs):
        if response.generations:
             if self.session_id:
                  ai_response = response.generations[0][0].text
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
                self.manager.append_token_usage(self.session_id, model, input_token, output_token, cached_percent)
            
            # Clear it so we don't log it again unless a new LLM call happens
            self.last_token_usage = None

    def on_tool_start(self, serialized, input_str, **kwargs):
        tool_name = serialized.get("name", "Unknown")
        
        action = None
        path = None
        skill_id = None
        try:
            import ast
            input_dict = ast.literal_eval(input_str)
            if isinstance(input_dict, dict):
                action = input_dict.get("action")
                path = input_dict.get("path")
                skill_id = input_dict.get("skill_id")
        except Exception:
            pass

        extra_info = []
        if action:
            extra_info.append(f"action: {action}")
        if path:
            extra_info.append(f"path: {path}")
        if skill_id:
            extra_info.append(f"skill_id: {skill_id}")
        extra_str = f" [{', '.join(extra_info)}]" if extra_info else ""
        
        print(f"Tool use: {tool_name}{extra_str}")
        if self.session_id:
            self.manager.append_message(self.session_id, 'system', f"Tool {tool_name}{extra_str}:{input_str}")

    def on_tool_end(self, output, **kwargs):
        if self.session_id:
            content = output.content if hasattr(output, 'content') else str(output)
            self.manager.append_message(self.session_id, 'system', f"Tool Output: {content}")

