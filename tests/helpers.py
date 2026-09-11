"""Shared test helpers for establishing an ExecutionContext.

Tools no longer accept a model-supplied `agent_id`; identity is derived from the ambient
ExecutionContext. Tests that exercise a tool therefore need to run inside one.
"""
from contextlib import contextmanager

from core.agent.execution_context import current_execution_context
from core.agent.session_manager import SessionManager


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
