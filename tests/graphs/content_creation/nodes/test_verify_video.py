import unittest
from unittest.mock import AsyncMock, patch, MagicMock
import tempfile
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))

import graphs.content_creation.nodes.production.verify_video as verify_video_module
from graphs.content_creation.nodes.production import verify_video_task

class TestVerifyVideo(unittest.IsolatedAsyncioTestCase):

    async def test_verify_video_approves_when_audio_and_frames_present(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, "cat")
            os.makedirs(output_dir, exist_ok=True)
            video_path = os.path.join(output_dir, "cat_video.mp4")
            with open(video_path, "wb") as f:
                f.write(b"REMIXED_VIDEO_BYTES")

            mock_frames = MagicMock()
            mock_frames.ainvoke = AsyncMock(return_value="<payload>frame1.png\nframe2.png</payload><errors>None</errors>")
            
            mock_probe = MagicMock()
            mock_probe.ainvoke = AsyncMock(return_value="<payload>True</payload><errors>None</errors>")

            with patch.object(verify_video_module, "extract_video_frames", mock_frames), \
                 patch.object(verify_video_module, "audio_stream_probe", mock_probe):
                
                state = {
                    "topic": "cat",
                    "project_dir": temp_dir,
                    "output_dir": output_dir,
                    "remixed_video_path": video_path
                }
                res = await verify_video_task(state)

                self.assertTrue(res["video_qc_passed"])
                self.assertTrue(res["audio_verified"])
                self.assertEqual(res["extracted_frames_path"], ["frame1.png", "frame2.png"])

    async def test_verify_video_rejects_when_audio_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, "cat")
            os.makedirs(output_dir, exist_ok=True)
            video_path = os.path.join(output_dir, "cat_video.mp4")
            with open(video_path, "wb") as f:
                f.write(b"REMIXED_VIDEO_BYTES")

            mock_frames = MagicMock()
            mock_frames.ainvoke = AsyncMock(return_value="<payload>frame1.png</payload><errors>None</errors>")
            
            mock_probe = MagicMock()
            mock_probe.ainvoke = AsyncMock(return_value="<payload>False</payload><errors>None</errors>")

            with patch.object(verify_video_module, "extract_video_frames", mock_frames), \
                 patch.object(verify_video_module, "audio_stream_probe", mock_probe):
                
                state = {
                    "topic": "cat",
                    "project_dir": temp_dir,
                    "output_dir": output_dir,
                    "remixed_video_path": video_path
                }
                res = await verify_video_task(state)

                self.assertFalse(res["video_qc_passed"])
                self.assertFalse(res["audio_verified"])
                self.assertEqual(res["video_qc_rejection_target"], "remix")

    async def test_verify_video_handles_dict_and_list_fallbacks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, "cat")
            os.makedirs(output_dir, exist_ok=True)
            video_path = os.path.join(output_dir, "cat_video.mp4")
            with open(video_path, "wb") as f:
                f.write(b"REMIXED_VIDEO_BYTES")

            mock_frames = MagicMock()
            mock_frames.ainvoke = AsyncMock(return_value=["frame1.png", "frame2.png"])
            
            mock_probe = MagicMock()
            mock_probe.ainvoke = AsyncMock(return_value={"has_audio": True})

            with patch.object(verify_video_module, "extract_video_frames", mock_frames), \
                 patch.object(verify_video_module, "audio_stream_probe", mock_probe):
                
                state = {
                    "topic": "cat",
                    "project_dir": temp_dir,
                    "output_dir": output_dir,
                    "remixed_video_path": video_path
                }
                res = await verify_video_task(state)

                self.assertTrue(res["video_qc_passed"])
                self.assertTrue(res["audio_verified"])
                self.assertEqual(res["extracted_frames_path"], ["frame1.png", "frame2.png"])

if __name__ == "__main__":
    unittest.main()
