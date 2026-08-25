import unittest
import os
from unittest.mock import patch, MagicMock, AsyncMock
from core.voice.bridge_manager import BridgeManager

class TestBridgeManager(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bm = BridgeManager()

    def test_get_bridge_phrase_agent_call(self):
        phrase = self.bm.get_bridge_phrase("main", "agent_call", {"agent_id": "day-planner"})
        self.assertEqual(phrase, "Checking in with Daisy now...")

        phrase_meal = self.bm.get_bridge_phrase("main", "agent_call", {"agent_id": "meal-planner"})
        self.assertEqual(phrase_meal, "Checking in with Ramsay now...")

        phrase_researcher = self.bm.get_bridge_phrase("main", "agent_call", {"agent_id": "topic-researcher"})
        self.assertEqual(phrase_researcher, "Checking in with Ted now...")

    def test_get_bridge_phrase_graph_call(self):
        phrase = self.bm.get_bridge_phrase("main", "graph_call", {"graph_name": "coding"})
        self.assertEqual(phrase, "Running the Coding workflow now...")

    def test_get_bridge_phrase_search(self):
        phrase = self.bm.get_bridge_phrase("main", "web_search", {})
        self.assertEqual(phrase, "Searching online records for you...")

        phrase_kb = self.bm.get_bridge_phrase("main", "vector_search", {})
        self.assertEqual(phrase_kb, "Checking our knowledge base on that...")

    def test_get_bridge_phrase_fallback(self):
        phrase = self.bm.get_bridge_phrase("main", "custom_tool", {})
        self.assertEqual(phrase, "Looking into that for you...")

    async def test_get_or_create_bridge_audio_cached(self):
        mock_tts = MagicMock()
        mock_tts.synthesize_to_file = AsyncMock(return_value="/tmp/test_bridge.mp3")

        with patch("os.path.exists", return_value=True), patch("os.path.getsize", return_value=1024):
            path = await self.bm.get_or_create_bridge_audio(
                "main", "agent_call", {"agent_id": "day-planner"}, mock_tts
            )
            self.assertIn("main_agent_day-planner.mp3", path)
            # If cached on disk, TTS should not be called
            mock_tts.synthesize_to_file.assert_not_called()

if __name__ == "__main__":
    unittest.main()
