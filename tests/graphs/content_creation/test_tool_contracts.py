import unittest
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from tools.remix_video import remix_video
from tools.extract_video_frames import extract_video_frames
from tools.audio_stream_probe import audio_stream_probe
from tools.generate_animation_veo3 import generate_animation_veo3
from tools.generate_image import generate_image

class TestContentCreationToolContracts(unittest.TestCase):
    """Verifies that all tool schemas accept the exact parameters used across graph nodes."""

    def test_remix_video_tool_schema_contract(self):
        schema = remix_video.args_schema
        self.assertIsNotNone(schema)
        # Test standard node parameter invocation
        payload = {
            "video_path": "path/to/raw.mp4",
            "output_path": "path/to/remix.mp4",
            "actions": [{"action": "add_text", "text": "test"}]
        }
        inst = schema(**payload)
        self.assertEqual(inst.video_path, "path/to/raw.mp4")
        self.assertEqual(inst.output_path, "path/to/remix.mp4")

        # Test alias compatibility
        alias_payload = {
            "input_video_path": "path/to/raw.mp4",
            "output_video_path": "path/to/remix.mp4",
            "actions": []
        }
        alias_inst = schema(**alias_payload)
        self.assertEqual(alias_inst.input_video_path, "path/to/raw.mp4")

    def test_extract_video_frames_tool_schema_contract(self):
        schema = extract_video_frames.args_schema
        self.assertIsNotNone(schema)
        payload = {
            "video_path": "path/to/video.mp4",
            "timestamps": [1.0, 2.5, 4.0],
            "output_dir": "path/to/frames"
        }
        inst = schema(**payload)
        self.assertEqual(inst.video_path, "path/to/video.mp4")

    def test_audio_stream_probe_tool_schema_contract(self):
        schema = audio_stream_probe.args_schema
        self.assertIsNotNone(schema)
        payload = {
            "video_path": "path/to/video.mp4"
        }
        inst = schema(**payload)
        self.assertEqual(inst.video_path, "path/to/video.mp4")

    def test_generate_animation_veo3_tool_schema_contract(self):
        schema = generate_animation_veo3.args_schema
        self.assertIsNotNone(schema)
        payload = {
            "prompt_text": "animate",
            "image_path": "path/to/image.jpg",
            "output_path": "path/to/raw.mp4",
            "duration": 6,
            "aspect_ratio": "16:9",
            "agent_id": "graph-worker"
        }
        inst = schema(**payload)
        self.assertEqual(inst.prompt_text, "animate")

    def test_generate_image_tool_schema_contract(self):
        schema = generate_image.args_schema
        self.assertIsNotNone(schema)
        payload = {
            "prompt": "generate character",
            "output_path": "path/to/image.jpg"
        }
        inst = schema(**payload)
        self.assertEqual(inst.prompt, "generate character")

if __name__ == "__main__":
    unittest.main()
