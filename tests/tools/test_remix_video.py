import os
import sys
import json
import unittest
import shutil
import tempfile
import subprocess

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.remix_video import (
    remix_video,
    _parse_timestamp_sec,
    _resolve_font_file,
    _escape_drawtext_str,
    _get_ffmpeg_executable,
    _has_audio_stream,
)


class TestRemixVideo(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_remix_video_")
        self.ffmpeg_exe = _get_ffmpeg_executable()
        self.assertTrue(self.ffmpeg_exe is not None, "ffmpeg executable not found")

        # 1. Create a synthetic base video with audio
        self.video_with_audio = os.path.join(self.test_dir, "base_with_audio.mp4")
        subprocess.run([
            self.ffmpeg_exe, "-y",
            "-f", "lavfi", "-i", "testsrc=duration=4:size=320x240:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
            "-c:v", "libx264", "-c:a", "aac",
            self.video_with_audio
        ], capture_output=True, check=True)

        # 2. Create a synthetic base video without audio
        self.video_no_audio = os.path.join(self.test_dir, "base_no_audio.mp4")
        subprocess.run([
            self.ffmpeg_exe, "-y",
            "-f", "lavfi", "-i", "testsrc=duration=4:size=320x240:rate=30",
            "-c:v", "libx264",
            self.video_no_audio
        ], capture_output=True, check=True)

        # 3. Create sample audio files
        self.audio_wav = os.path.join(self.test_dir, "sample.wav")
        subprocess.run([
            self.ffmpeg_exe, "-y",
            "-f", "lavfi", "-i", "sine=frequency=880:duration=2",
            self.audio_wav
        ], capture_output=True, check=True)

        self.audio_m4a = os.path.join(self.test_dir, "sample.m4a")
        subprocess.run([
            self.ffmpeg_exe, "-y",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=1.5",
            "-c:a", "aac",
            self.audio_m4a
        ], capture_output=True, check=True)

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_parse_timestamp_sec(self):
        self.assertEqual(_parse_timestamp_sec(2.5), 2.5)
        self.assertEqual(_parse_timestamp_sec(10), 10.0)
        self.assertEqual(_parse_timestamp_sec("2.5"), 2.5)
        self.assertEqual(_parse_timestamp_sec("2500ms"), 2.5)
        self.assertEqual(_parse_timestamp_sec("3.5s"), 3.5)
        self.assertEqual(_parse_timestamp_sec("4 sec"), 4.0)
        self.assertEqual(_parse_timestamp_sec("01:23"), 83.0)
        self.assertEqual(_parse_timestamp_sec("01:02:03"), 3723.0)
        self.assertIsNone(_parse_timestamp_sec(None))
        self.assertIsNone(_parse_timestamp_sec(""))

        with self.assertRaises(ValueError):
            _parse_timestamp_sec(-1)
        with self.assertRaises(ValueError):
            _parse_timestamp_sec("-01:00")

    def test_escape_drawtext_str(self):
        escaped = _escape_drawtext_str("馬馬: 'Text' % 100 \\")
        self.assertIn("\\:", escaped)
        self.assertIn("\\%", escaped)
        self.assertIn("'\\''", escaped)

    def test_has_audio_stream(self):
        self.assertTrue(_has_audio_stream(self.ffmpeg_exe, self.video_with_audio))
        self.assertFalse(_has_audio_stream(self.ffmpeg_exe, self.video_no_audio))

    def test_add_audio_and_traditional_chinese_text(self):
        output_mp4 = os.path.join(self.test_dir, "output_remix.mp4")
        actions = [
            {
                "action": "add_audio",
                "audio_path": self.audio_wav,
                "start_time": 1.5,
                "volume": 1.8,
                "original_volume": 0.5
            },
            {
                "action": "add_text",
                "text": "馬馬",
                "start_time": "1.5s",
                "end_time": "3.5s",
                "font_size": 60,
                "font_color": "white",
                "border_color": "0x4A3B32",
                "border_width": 4,
                "x": "(w-text_w)/2",
                "y": "h*0.22"
            }
        ]

        res = remix_video.invoke({
            "video_path": self.video_with_audio,
            "actions": actions,
            "output_path": output_mp4
        })

        self.assertIn("<errors>None</errors>", res)
        self.assertIn("<payload>", res)
        self.assertTrue(os.path.exists(output_mp4))
        self.assertGreater(os.path.getsize(output_mp4), 0)
        self.assertTrue(_has_audio_stream(self.ffmpeg_exe, output_mp4))

    def test_actions_as_json_string(self):
        output_mp4 = os.path.join(self.test_dir, "output_json.mp4")
        actions = [
            {
                "action": "add_audio",
                "audio_path": self.audio_m4a,
                "start_time": 0.5,
                "volume": 1.5
            },
            {
                "action": "add_text",
                "text": "繁體中文測試",
                "start_time": 0.5,
                "end_time": 2.0,
                "font_size": 40
            }
        ]

        res = remix_video.invoke({
            "video_path": self.video_no_audio,
            "actions": json.dumps(actions),
            "output_path": output_mp4
        })

        self.assertIn("<errors>None</errors>", res)
        self.assertTrue(os.path.exists(output_mp4))
        self.assertGreater(os.path.getsize(output_mp4), 0)

    def test_subtitle_positioning_and_styling(self):
        output_mp4 = os.path.join(self.test_dir, "output_positioned.mp4")
        actions = [
            {
                "action": "add_audio",
                "audio_path": self.audio_wav,
                "start_time": "1.0s",
                "volume": 1.5
            },
            {
                "action": "add_text",
                "text": "貓咪 Top",
                "start_time": 1.0,
                "end_time": 3.0,
                "position": "top",
                "font_size": 48,
                "font_color": "yellow"
            },
            {
                "action": "add_text",
                "text": "貓咪 Bottom",
                "start_time": 1.0,
                "position": "bottom",
                "font_size": 52
            }
        ]

        res = remix_video.invoke({
            "video_path": self.video_no_audio,
            "actions": actions,
            "output_path": output_mp4
        })

        self.assertIn("<errors>None</errors>", res)
        self.assertTrue(os.path.exists(output_mp4))
        self.assertGreater(os.path.getsize(output_mp4), 0)

    def test_validation_missing_video(self):
        res = remix_video.invoke({
            "video_path": os.path.join(self.test_dir, "nonexistent.mp4"),
            "actions": [],
            "output_path": os.path.join(self.test_dir, "out.mp4")
        })
        self.assertIn("Video file not found", res)

    def test_validation_missing_audio_file(self):
        res = remix_video.invoke({
            "video_path": self.video_with_audio,
            "actions": [
                {
                    "action": "add_audio",
                    "audio_path": os.path.join(self.test_dir, "fake_audio.wav"),
                    "start_time": 0.0
                }
            ],
            "output_path": os.path.join(self.test_dir, "out.mp4")
        })
        self.assertIn("Audio file not found", res)


if __name__ == "__main__":
    unittest.main()
