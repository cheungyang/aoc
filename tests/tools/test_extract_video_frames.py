import os
import sys
import unittest
import shutil
import tempfile
import subprocess

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.extract_video_frames import (
    extract_video_frames,
    _parse_single_timestamp,
    _parse_all_timestamps,
    _get_ffmpeg_executable,
)
from core.util import format_tool_response


class TestExtractVideoFrames(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_extract_frames_")
        self.video_path = os.path.join(self.test_dir, "test_video.mp4")

        # Create a synthetic 3-second test video using ffmpeg or cv2
        ffmpeg_exe = _get_ffmpeg_executable()
        if ffmpeg_exe:
            subprocess.run([
                ffmpeg_exe, "-y",
                "-f", "lavfi", "-i", "testsrc=duration=3:size=320x240:rate=30",
                "-c:v", "libx264",
                self.video_path
            ], capture_output=True, check=True)
        else:
            import cv2
            import numpy as np
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            out = cv2.VideoWriter(self.video_path, fourcc, 30.0, (320, 240))
            for i in range(90):
                frame = np.zeros((240, 320, 3), dtype=np.uint8)
                cv2.putText(frame, f"Frame {i}", (20, 120), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
                out.write(frame)
            out.release()

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_parse_single_timestamp(self):
        self.assertEqual(_parse_single_timestamp(1.5), 1.5)
        self.assertEqual(_parse_single_timestamp(10), 10.0)
        self.assertEqual(_parse_single_timestamp("2.5"), 2.5)
        self.assertEqual(_parse_single_timestamp("3.5s"), 3.5)
        self.assertEqual(_parse_single_timestamp("4 sec"), 4.0)
        self.assertEqual(_parse_single_timestamp("01:23"), 83.0)
        self.assertEqual(_parse_single_timestamp("01:23.5"), 83.5)
        self.assertEqual(_parse_single_timestamp("01:02:03"), 3723.0)
        self.assertEqual(_parse_single_timestamp("01:02:03.250"), 3723.25)

        with self.assertRaises(ValueError):
            _parse_single_timestamp(-1)
        with self.assertRaises(ValueError):
            _parse_single_timestamp("-01:00")
        with self.assertRaises(ValueError):
            _parse_single_timestamp("invalid")
        with self.assertRaises(ValueError):
            _parse_single_timestamp("")

    def test_parse_all_timestamps(self):
        self.assertEqual(_parse_all_timestamps(2.0), [2.0])
        self.assertEqual(_parse_all_timestamps([1.0, "00:02", "3.5s"]), [1.0, 2.0, 3.5])
        self.assertEqual(_parse_all_timestamps("[1.0, 2.5, 3]"), [1.0, 2.5, 3.0])
        self.assertEqual(_parse_all_timestamps("1.0, 2.5, 00:03"), [1.0, 2.5, 3.0])

        with self.assertRaises(ValueError):
            _parse_all_timestamps([])
        with self.assertRaises(ValueError):
            _parse_all_timestamps("")
        with self.assertRaises(ValueError):
            _parse_all_timestamps(None)

    def test_missing_video_path(self):
        res = extract_video_frames.invoke({
            "video_path": "",
            "timestamps": 1.0,
            "output_path": os.path.join(self.test_dir, "out.jpg")
        })
        self.assertIn("Error: video_path cannot be empty", res)

    def test_nonexistent_video_path(self):
        non_existent = os.path.join(self.test_dir, "does_not_exist.mp4")
        res = extract_video_frames.invoke({
            "video_path": non_existent,
            "timestamps": 1.0,
            "output_path": os.path.join(self.test_dir, "out.jpg")
        })
        self.assertIn("Error: Video file not found", res)

    def test_missing_output_destinations(self):
        res = extract_video_frames.invoke({
            "video_path": self.video_path,
            "timestamps": 1.0
        })
        self.assertIn("Error: Either 'output_path' (for single frame) or 'output_dir' (for multiple frames) must be specified", res)

    def test_invalid_timestamps_input(self):
        res = extract_video_frames.invoke({
            "video_path": self.video_path,
            "timestamps": "invalid_timestamp",
            "output_path": os.path.join(self.test_dir, "out.jpg")
        })
        self.assertIn("Error parsing timestamps", res)

    def test_single_timestamp_with_output_path(self):
        out_file = os.path.join(self.test_dir, "single_frame.jpg")
        res = extract_video_frames.invoke({
            "video_path": self.video_path,
            "timestamps": 1.5,
            "output_path": out_file
        })
        self.assertTrue(os.path.exists(out_file))
        self.assertGreater(os.path.getsize(out_file), 0)
        self.assertEqual(
            res,
            format_tool_response("extract_video_frames", payload=os.path.abspath(out_file), errors="None")
        )

    def test_single_timestamp_with_output_dir(self):
        out_dir = os.path.join(self.test_dir, "frames_single")
        res = extract_video_frames.invoke({
            "video_path": self.video_path,
            "timestamps": "00:01.000",
            "output_dir": out_dir,
            "image_format": "png"
        })
        self.assertTrue(os.path.isdir(out_dir))
        files = os.listdir(out_dir)
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].endswith(".png"))
        self.assertIn("1_000s", files[0])
        self.assertIn(os.path.abspath(os.path.join(out_dir, files[0])), res)

    def test_multiple_timestamps_with_output_dir(self):
        out_dir = os.path.join(self.test_dir, "frames_multi")
        res = extract_video_frames.invoke({
            "video_path": self.video_path,
            "timestamps": [0.5, 1.5, "00:02.5"],
            "output_dir": out_dir,
            "image_format": "jpg"
        })
        self.assertTrue(os.path.isdir(out_dir))
        files = sorted(os.listdir(out_dir))
        self.assertEqual(len(files), 3)
        for f in files:
            self.assertTrue(f.endswith(".jpg"))
            self.assertGreater(os.path.getsize(os.path.join(out_dir, f)), 0)

        for f in files:
            self.assertIn(os.path.abspath(os.path.join(out_dir, f)), res)

    def test_multiple_timestamps_with_output_path(self):
        out_path = os.path.join(self.test_dir, "out_indexed", "capture.png")
        res = extract_video_frames.invoke({
            "video_path": self.video_path,
            "timestamps": [0.0, 1.0],
            "output_path": out_path
        })
        parent_dir = os.path.dirname(out_path)
        self.assertTrue(os.path.isdir(parent_dir))
        files = sorted(os.listdir(parent_dir))
        self.assertEqual(len(files), 2)
        for f in files:
            self.assertTrue(f.startswith("capture_"))
            self.assertTrue(f.endswith(".png"))


if __name__ == "__main__":
    unittest.main()
