import datetime
from langchain_core.callbacks import BaseCallbackHandler

class DebugLogHandler(BaseCallbackHandler):
    def __init__(self, log_file="debug_log.log"):
        self.log_file = log_file
        
    def _log(self, message):
        timestamp = datetime.datetime.now().isoformat()
        with open(self.log_file, "a") as f:
            f.write(f"[{timestamp}] {message}\n")

    def on_llm_start(self, serialized, prompts, **kwargs):
        self._log(f"--- LLM START ---")
        for i, prompt in enumerate(prompts):
            self._log(f"Prompt {i}:\n{prompt}")

    def on_llm_end(self, response, **kwargs):
        self._log(f"--- LLM END ---")
        if response.generations:
             for i, gen in enumerate(response.generations[0]):
                  self._log(f"Generation {i}:\n{gen.text}")

    def on_tool_start(self, serialized, input_str, **kwargs):
        tool_name = serialized.get("name", "Unknown")
        self._log(f"--- TOOL START --- Tool: {tool_name} | Params: {input_str}")

    def on_tool_end(self, output, **kwargs):
        self._log(f"--- TOOL END --- Tool Output (truncated 200 chars): {str(output)[:200]}")
