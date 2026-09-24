"""Shared test helpers for establishing an ExecutionContext.

Tools no longer accept a model-supplied `agent_id`; identity is derived from the ambient
ExecutionContext. Tests that exercise a tool therefore need to run inside one.

Also holds `llm_result`, a real LLMResult builder shared by the logging-handler,
token-usage-handler and logging-util tests, and the voice fixtures
(`GOLDEN_INPUT`, `voice_session`) shared by the verbalizer and voice-stream tests.
"""
from contextlib import contextmanager

from core.runtime.execution_context import current_execution_context
from core.runtime.session_manager import SessionManager


def make_context(agent_id="test-agent", graph_id=None, source="job", channel=None, stateless=True):
    """Builds an ExecutionContext without entering it."""
    return SessionManager().get_session(
        agent_id=agent_id,
        source=source,
        channel=channel,
        stateless=stateless,
        graph_id=graph_id,
    )


@contextmanager
def execution_context(agent_id="test-agent", graph_id=None, source="job", channel=None, stateless=True):
    """Enters an ExecutionContext for the duration of the block."""
    ctx = make_context(
        agent_id=agent_id,
        graph_id=graph_id,
        source=source,
        channel=channel,
        stateless=stateless,
    )
    token = current_execution_context.set(ctx)
    try:
        yield ctx
    finally:
        current_execution_context.reset(token)


LLM_USAGE = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}


def llm_result(usage=None, response_metadata=None, llm_output=None, generation_info=None, text="reply"):
    """A real LLMResult, shaped the way langchain_google_genai returns one."""
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, LLMResult

    message = AIMessage(
        content=text,
        usage_metadata=usage,
        response_metadata=response_metadata or {},
    )
    gen = ChatGeneration(message=message, generation_info=generation_info)
    return LLMResult(generations=[[gen]], llm_output=llm_output)


USAGE = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}

# Shared by the verbalizer and voice-stream tests: a numbered list with a
# heading, the canonical "nothing may be dropped" input.
GOLDEN_INPUT = """## Weekend options

1. **Point Reyes** - 2 hour drive, $0 entry, best in the morning fog.
2. **Mount Tam** - 45 minutes, $8 parking, steep but short.
3. **Muir Woods** - 1 hour, $15 plus a $9 reservation, book ahead.
4. **Stinson Beach** - 1 hour 15, free, windy after noon.
5. **Tennessee Valley** - 40 minutes, free, flat and stroller friendly.
"""


def voice_session():
    """A voice-surface ExecutionContext for token-attribution tests."""
    return SessionManager.get_session(
        agent_id="main", source="discord", channel="general", surface="voice"
    )
