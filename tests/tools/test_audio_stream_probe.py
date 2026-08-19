import unittest
from unittest.mock import patch, MagicMock
from tools.audio_stream_probe import audio_stream_probe

class TestAudioStreamProbe(unittest.TestCase):
    @patch("os.path.exists", return_value=True)
    @patch("tools.audio_stream_probe._get_ffprobe_executable", return_value="/usr/bin/ffprobe")
    @patch("subprocess.run")
    def test_audio_stream_probe_has_audio(self, mock_run, mock_ffprobe, mock_exists):
        mock_res = MagicMock()
        mock_res.stdout = "audio\n"
        mock_run.return_value = mock_res
        
        res = audio_stream_probe.invoke({"video_path": "/path/to/video.mp4"})
        self.assertIn("<payload>True</payload>", res)

    @patch("os.path.exists", return_value=True)
    @patch("tools.audio_stream_probe._get_ffprobe_executable", return_value="/usr/bin/ffprobe")
    @patch("subprocess.run")
    def test_audio_stream_probe_no_audio(self, mock_run, mock_ffprobe, mock_exists):
        mock_res = MagicMock()
        mock_res.stdout = ""
        mock_run.return_value = mock_res
        
        res = audio_stream_probe.invoke({"video_path": "/path/to/video.mp4"})
        self.assertIn("<payload>False</payload>", res)

    @patch("os.path.exists", return_value=True)
    @patch("tools.audio_stream_probe._get_ffprobe_executable", return_value=None)
    @patch("tools.audio_stream_probe._get_ffmpeg_executable", return_value="/usr/bin/ffmpeg")
    @patch("subprocess.run")
    def test_audio_stream_probe_ffmpeg_fallback_has_audio(self, mock_run, mock_ffmpeg, mock_ffprobe, mock_exists):
        mock_res = MagicMock()
        mock_res.stderr = "Input #0 ...\n  Stream #0:1: Audio: aac, 48000 Hz, stereo\n"
        mock_run.return_value = mock_res
        
        res = audio_stream_probe.invoke({"video_path": "/path/to/video.mp4"})
        self.assertIn("<payload>True</payload>", res)

    @patch("os.path.exists", return_value=True)
    @patch("tools.audio_stream_probe._get_ffprobe_executable", return_value=None)
    @patch("tools.audio_stream_probe._get_ffmpeg_executable", return_value="/usr/bin/ffmpeg")
    @patch("subprocess.run")
    def test_audio_stream_probe_ffmpeg_fallback_no_audio(self, mock_run, mock_ffmpeg, mock_ffprobe, mock_exists):
        mock_res = MagicMock()
        mock_res.stderr = "Input #0 ...\n  Stream #0:0: Video: h264\n"
        mock_run.return_value = mock_res
        
        res = audio_stream_probe.invoke({"video_path": "/path/to/video.mp4"})
        self.assertIn("<payload>False</payload>", res)

if __name__ == "__main__":
    unittest.main()
