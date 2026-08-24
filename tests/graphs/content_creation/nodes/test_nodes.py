import unittest
import os
import shutil
import tempfile
import sys
from unittest.mock import patch, AsyncMock
from langchain_core.messages import HumanMessage, AIMessage

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))

from graphs.content_creation.nodes.ingestion import ask_for_audio_node, ingest_audio_node as receive_audio_node

class MockResponse:
    def __init__(self, data=b"FAKE_AUDIO_DATA", status=200):
        self.status = status
        self._data = data
    async def read(self):
        return self._data
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        pass

class MockSession:
    def __init__(self, response=None):
        self.response = response or MockResponse()
    def get(self, url):
        return self.response
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        pass

class TestAudioNodes(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.project_path = os.path.join(self.test_dir, "project")
        os.makedirs(self.project_path, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    async def test_ask_for_audio_node(self):
        state = {"topic": "cat"}
        res = await ask_for_audio_node(state)
        self.assertIn("messages", res)
        self.assertEqual(len(res["messages"]), 1)
        self.assertTrue(isinstance(res["messages"][0], AIMessage))
        self.assertIn("Please upload the audio clip", res["messages"][0].content)

    @patch("graphs.content_creation.nodes.ingestion.ingest_audio_node.aiohttp.ClientSession")
    async def test_receive_audio_from_discord_attachment_markdown(self, mock_session_cls):
        mock_session_cls.return_value = MockSession(MockResponse(data=b"FAKE_AUDIO_DATA_M4A"))

        state = {
            "topic": "dog",
            "project_path": self.project_path,
            "output_path": os.path.join(self.project_path, "dog"),
            "messages": [
                HumanMessage(content="[Attached file: Dog_1.m4a](https://cdn.discordapp.com/attachments/123/456/Dog_1.m4a?ex=abc)")
            ]
        }

        res = await receive_audio_node(state)
        self.assertIn("source_audio_path", res)
        expected_path = os.path.join(self.project_path, "dog", "Dog_1.m4a")
        self.assertEqual(res["source_audio_path"], expected_path)
        self.assertTrue(os.path.isfile(expected_path))
        with open(expected_path, "rb") as f:
            self.assertEqual(f.read(), b"FAKE_AUDIO_DATA_M4A")

    @patch("graphs.content_creation.nodes.ingestion.ingest_audio_node.aiohttp.ClientSession")
    async def test_receive_audio_from_raw_discord_url(self, mock_session_cls):
        mock_session_cls.return_value = MockSession(MockResponse(data=b"RAW_URL_AUDIO_BYTES"))

        raw_url = "https://cdn.discordapp.com/attachments/1537392166207356968/1538895661733257318/Cat_1.m4a?ex=6a8457c5&is=6a830645&hm=b6748b1f15746e9855e68ba9166d92ef8f753cbd4aabae54d1b25e902586497d&"
        state = {
            "topic": "cat",
            "project_path": self.project_path,
            "output_path": os.path.join(self.project_path, "cat"),
            "query": raw_url,
            "messages": [HumanMessage(content=raw_url)]
        }

        res = await receive_audio_node(state)
        self.assertIn("source_audio_path", res)
        expected_path = os.path.join(self.project_path, "cat", "Cat_1.m4a")
        self.assertEqual(res["source_audio_path"], expected_path)
        self.assertTrue(os.path.isfile(expected_path))

    @patch("graphs.content_creation.nodes.ingestion.ingest_audio_node.aiohttp.ClientSession")
    async def test_receive_audio_from_query_key_value(self, mock_session_cls):
        mock_session_cls.return_value = MockSession(MockResponse(data=b"HORSE_AUDIO_DATA"))

        query = "topic: horse, audio_file: https://cdn.discordapp.com/attachments/1/2/horse_audio.wav"
        state = {
            "topic": "horse",
            "project_path": self.project_path,
            "output_path": os.path.join(self.project_path, "horse"),
            "query": query
        }

        res = await receive_audio_node(state)
        self.assertIn("source_audio_path", res)
        expected_path = os.path.join(self.project_path, "horse", "horse_audio.wav")
        self.assertEqual(res["source_audio_path"], expected_path)
        self.assertTrue(os.path.isfile(expected_path))

    async def test_receive_audio_from_local_file(self):
        local_file = os.path.join(self.test_dir, "local_sample.m4a")
        with open(local_file, "wb") as f:
            f.write(b"SAMPLE_LOCAL_M4A")

        state = {
            "topic": "local",
            "project_path": self.project_path,
            "output_path": os.path.join(self.project_path, "local"),
            "messages": [HumanMessage(content=local_file)]
        }

        res = await receive_audio_node(state)
        self.assertEqual(res.get("source_audio_path"), local_file)

    async def test_receive_audio_from_existing_directory_file(self):
        out_dir = os.path.join(self.project_path, "dog")
        os.makedirs(out_dir, exist_ok=True)
        existing_audio = os.path.join(out_dir, "dog_sound.wav")
        with open(existing_audio, "wb") as f:
            f.write(b"EXISTING_WAV")

        state = {
            "topic": "dog",
            "project_path": self.project_path,
            "output_path": out_dir,
            "messages": []
        }

        res = await receive_audio_node(state)
        self.assertEqual(res.get("source_audio_path"), existing_audio)

    async def test_receive_audio_missing_paths_returns_error(self):
        state = {
            "topic": "puppy",
            "messages": [
                HumanMessage(content="[Attached file: Puppy_Audio.m4a](https://cdn.discordapp.com/attachments/1/2/Puppy_Audio.m4a)")
            ]
        }

        res = await receive_audio_node(state)
        self.assertIn("error_message", res)
        self.assertIn("Missing required project/output path", res["error_message"])

    async def test_receive_audio_missing_returns_empty(self):
        state = {
            "topic": "bird",
            "project_path": self.project_path,
            "output_path": os.path.join(self.project_path, "bird"),
            "messages": [HumanMessage(content="create content for bird")]
        }

        res = await receive_audio_node(state)
        self.assertNotIn("source_audio_path", res)


if __name__ == "__main__":
    unittest.main()
