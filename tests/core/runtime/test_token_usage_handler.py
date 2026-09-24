import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.runtime.session_manager import SessionManager
from core.runtime.token_usage_handler import TokenUsageHandler
from tests.helpers import LLM_USAGE as USAGE, llm_result as _llm_result


class TestTokenUsageHandler(unittest.TestCase):
    """For out-of-graph calls (the Verbalizer): token row only, written on llm_end."""

    def setUp(self):
        self.session = SessionManager.get_session(
            agent_id="main", source="discord", channel="general", surface="voice"
        )

    def test_records_usage_on_llm_end_without_a_chain(self):
        handler = TokenUsageHandler(session=self.session)
        handler.manager = MagicMock()
        handler.on_llm_start(None, ["prompt"])
        handler.on_llm_end(_llm_result(
            usage={"input_tokens": 200, "output_tokens": 40, "total_tokens": 240,
                   "input_token_details": {"cache_read": 50}},
            response_metadata={"model_name": "gemini-flash-lite"},
        ))
        handler.manager.append_token_usage.assert_called_once()
        args = handler.manager.append_token_usage.call_args
        self.assertEqual(args[0][:5], ("main:discord:general", "gemini-flash-lite", 200, 40, 25.0))
        self.assertEqual(args.kwargs["surface"], "voice")

    def test_never_writes_messages_to_the_transcript(self):
        handler = TokenUsageHandler(session=self.session)
        handler.manager = MagicMock()
        handler.on_llm_start(None, ["prompt"])
        handler.on_llm_end(_llm_result(usage=USAGE, text="rewritten for speech"))
        handler.on_chain_end({})
        handler.on_tool_start({"name": "x"}, "y")
        handler.on_tool_end("z")
        handler.manager.append_message.assert_not_called()
        self.assertEqual(handler.manager.append_token_usage.call_count, 1)

    def test_no_usage_no_row(self):
        handler = TokenUsageHandler(session=self.session)
        handler.manager = MagicMock()
        handler.on_llm_end(_llm_result(usage=None))
        handler.manager.append_token_usage.assert_not_called()

    def test_real_chat_model_invocation_is_recorded(self):
        """Wired through langchain's callback manager, as the Verbalizer does it."""
        import asyncio
        from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
        from langchain_core.messages import AIMessage

        class UsageFake(GenericFakeChatModel):
            def _generate(self, *args, **kwargs):
                result = super()._generate(*args, **kwargs)
                msg = result.generations[0].message
                msg.usage_metadata = dict(USAGE)
                msg.response_metadata = {"model_name": "fake-verbalizer"}
                return result

        llm = UsageFake(messages=iter([AIMessage(content="spoken")]))
        handler = TokenUsageHandler(session=self.session)
        handler.manager = MagicMock()
        asyncio.run(llm.ainvoke("- a\n  - b", config={"callbacks": [handler]}))
        handler.manager.append_token_usage.assert_called_once()
        args = handler.manager.append_token_usage.call_args
        self.assertEqual(args[0][1], "fake-verbalizer")
        self.assertEqual(args.kwargs["surface"], "voice")
        handler.manager.append_message.assert_not_called()


if __name__ == "__main__":
    unittest.main()
