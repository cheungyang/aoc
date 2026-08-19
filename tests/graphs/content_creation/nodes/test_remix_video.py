import unittest
from unittest.mock import AsyncMock, patch, MagicMock
import tempfile
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))

import graphs.content_creation.nodes.production.remix_video as remix_video_module
from graphs.content_creation.nodes.production import remix_video_task
from tools.remix_video import remix_video

class TestRemixVideoTask(unittest.IsolatedAsyncioTestCase):

    async def test_remix_video_task_invokes_tool_with_valid_schema(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, "cat")
            os.makedirs(output_dir, exist_ok=True)
            raw_video = os.path.join(output_dir, "cat_raw_video.mp4")
            with open(raw_video, "wb") as f:
                f.write(b"RAW_VIDEO_BYTES")

            audio_file = os.path.join(output_dir, "cat.m4a")
            with open(audio_file, "wb") as f:
                f.write(b"AUDIO_BYTES")

            state = {
                "topic": "cat",
                "project_dir": temp_dir,
                "output_dir": output_dir,
                "raw_video_path": raw_video,
                "source_audio_path": audio_file
            }

            # Intercept tool call to verify schema validity with real tool validation
            captured_args = {}
            async def mock_tool_func(args):
                nonlocal captured_args
                captured_args = dict(args)
                out = args.get("output_path") or args.get("output_video_path")
                with open(out, "wb") as f:
                    f.write(b"REMIXED_OUTPUT_BYTES")
                return f"<payload>{out}</payload><errors>None</errors>"

            mock_remix = MagicMock()
            mock_remix.ainvoke = AsyncMock(side_effect=mock_tool_func)

            with patch("graphs.content_creation.nodes.production.remix_video.remix_video", mock_remix):
                res = await remix_video_task(state)

                self.assertTrue(res["video_persisted"])
                self.assertEqual(res["video_generation_error"], "")
                expected_video = os.path.join(output_dir, "cat_video.mp4")
                self.assertEqual(res["remixed_video_path"], expected_video)
                self.assertTrue(os.path.isfile(expected_video))

                # Verify captured args match tool schema
                schema = remix_video.args_schema
                if schema:
                    # Validate that captured_args can instantiate the tool's args schema without ValidationError
                    schema_inst = schema(**captured_args)
                    self.assertIsNotNone(schema_inst)

    async def test_remix_video_task_handles_missing_raw_video(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, "dog")
            os.makedirs(output_dir, exist_ok=True)

            state = {
                "topic": "dog",
                "project_dir": temp_dir,
                "output_dir": output_dir,
                "raw_video_path": os.path.join(output_dir, "nonexistent_raw.mp4")
            }

            res = await remix_video_task(state)
            self.assertFalse(res["video_persisted"])
            self.assertIn("Raw visual plate not found", res["video_generation_error"])

if __name__ == "__main__":
    unittest.main()
