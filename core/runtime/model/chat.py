"""The chat model a turn runs on: which model (the agent's tier, or a per-call
override), where it runs (the provider), and the retry wrapper.

GraphBuilder asks for one each time it compiles an agent's graph. The providers
other than Gemini load their client only when used: they are optional
dependencies.
"""
from typing import Any

from core.config.models import resolve_model
from core.config.settings import Config
from core.runtime.model.retry import RetryingChatGoogleGenerativeAI


def chat_model(ctx: Any, agent_config: dict) -> Any:
    """The model `ctx`'s turns run on, for an agent configured as `agent_config`."""
    provider = agent_config.get("provider", "google")
    model_name = resolve_model(ctx.model or agent_config.get("model"), provider=provider)

    if provider == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(model=model_name)
    if provider == "local":
        # An on-device server speaking the OpenAI wire format. ChatOpenAI is
        # a protocol client here, not a route to the hosted API.
        from langchain_openai import ChatOpenAI

        cfg = Config()
        return ChatOpenAI(
            model=model_name,
            base_url=cfg.local_llm_base_url,
            # Load-bearing, despite the server ignoring it. Omitting the
            # argument makes ChatOpenAI fall back to OPENAI_API_KEY from the
            # environment -- which .env exports -- so a machine holding a
            # real key would post that paid credential to an
            # unauthenticated local socket. A constant makes that
            # impossible. The client also rejects an empty string, so this
            # cannot simply be "".
            api_key="not-needed",
            timeout=cfg.local_llm_timeout,
            # The client's default of 2 assumes an elastic hosted service.
            # This server handles one request at a time, so a retry does not
            # find spare capacity -- it joins the back of the queue every
            # other agent is already waiting in.
            max_retries=0,
            # Sends stream_options.include_usage, without which the usage
            # metadata LoggingHandler records is absent under streaming.
            stream_usage=True,
        )
    # 503s are retried (and resumed mid-stream) by the subclass; the SDK
    # keeps the other retriable codes. See core/runtime/model/retry.py.
    return RetryingChatGoogleGenerativeAI(model=model_name, max_retries=Config().llm_max_retries)
