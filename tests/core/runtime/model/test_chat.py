"""The chat model a turn runs on (core/runtime/model/chat.py)."""
import unittest
from unittest.mock import MagicMock, patch

from core.config.models import FLASH_LITE, PRO, resolve_model
from core.config.settings import Config
from core.runtime.foundation.sessions import SessionManager
from core.runtime.model.chat import chat_model


def _ctx(model=None):
    return SessionManager.get_session(agent_id="a", source="job", model=model)


class TestChatModel(unittest.TestCase):

    def tearDown(self):
        Config().reset()

    @patch("core.runtime.model.chat.RetryingChatGoogleGenerativeAI")
    def test_gemini_by_default_with_the_retry_wrapper(self, gemini):
        Config().llm_max_retries = 3
        self.assertIs(chat_model(_ctx(), {"model": "FLASH_LITE"}), gemini.return_value)
        gemini.assert_called_once_with(model=FLASH_LITE, max_retries=3)

    @patch("core.runtime.model.chat.RetryingChatGoogleGenerativeAI")
    def test_a_per_call_model_wins_over_the_agents(self, gemini):
        chat_model(_ctx(model="PRO"), {"model": "FLASH_LITE"})
        self.assertEqual(gemini.call_args.kwargs["model"], PRO)

    def test_the_local_server_never_gets_a_real_key(self):
        openai = MagicMock()
        Config().local_llm_base_url = "http://localhost:9379/v1"
        with patch.dict("sys.modules", {"langchain_openai": openai}):
            model = chat_model(_ctx(), {"provider": "local", "model": "FLASH_LITE"})
        self.assertIs(model, openai.ChatOpenAI.return_value)
        kwargs = openai.ChatOpenAI.call_args.kwargs
        self.assertEqual(kwargs["model"], resolve_model("FLASH_LITE", provider="local"))
        self.assertEqual(kwargs["base_url"], "http://localhost:9379/v1")
        self.assertEqual(kwargs["api_key"], "not-needed")
        self.assertEqual((kwargs["max_retries"], kwargs["stream_usage"]), (0, True))


if __name__ == "__main__":
    unittest.main()
