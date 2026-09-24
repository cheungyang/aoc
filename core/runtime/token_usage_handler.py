import time

from core.runtime.execution_context import ExecutionContext
from core.runtime.logging_handler import LoggingHandler
from core.util.logging_util import extract_model_name


class TokenUsageHandler(LoggingHandler):
    """
    Records token usage for an LLM call made outside any agent graph.

    `LoggingHandler` writes its token row on `on_chain_end`, which a bare
    `llm.ainvoke(...)` never fires, and it appends the model's reply to the session
    transcript. Neither fits an auxiliary call such as the Verbalizer: its output is a
    rewrite of a reply already recorded, and must not become a second 'ai' message in
    the conversation history. This handler only writes the token row, on `on_llm_end`.
    """

    def __init__(self, session: ExecutionContext):
        super().__init__(session=session, human_message=None)

    def on_llm_end(self, response, **kwargs):
        if not self._is_for_this_agent(kwargs):
            return
        if self.llm_start_time:
            self.last_execution_time = round(time.time() - self.llm_start_time, 3)
            self.llm_start_time = None

        if not (response.generations and response.generations[0]):
            return
        gen = response.generations[0][0]
        message = getattr(gen, 'message', None)
        usage = getattr(message, 'usage_metadata', None) if message is not None else None
        if not usage:
            return
        usage = dict(usage)
        usage['model'] = extract_model_name(response, gen)
        self._record_token_usage(usage)
        self.last_execution_time = 0.0

    def on_chain_end(self, outputs, **kwargs):
        # Usage is written in on_llm_end; a wrapping chain must not write it twice.
        return

    def on_tool_start(self, serialized, input_str, **kwargs):
        return

    def on_tool_end(self, output, **kwargs):
        return
