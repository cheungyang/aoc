import datetime
import os
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

    def on_tool_start(self, serialized, input_str, **kwargs):
        tool_name = serialized.get("name", "Unknown")
        print(f"Tool use: {tool_name}")
        if self.session_id:
            self.manager.append_message(self.session_id, 'system', f"Tool {tool_name}:{input_str}")

    def on_tool_end(self, output, **kwargs):
        if self.session_id:
            content = output.content if hasattr(output, 'content') else str(output)
            self.manager.append_message(self.session_id, 'system', f"Tool Output: {content}")

