import unittest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from core.voice.audio_queue import AudioStreamQueue

class TestAudioStreamQueue(unittest.IsolatedAsyncioTestCase):
    async def test_audio_queue_clear_stops_and_drains(self):
        mock_vm = MagicMock()
        mock_vc = MagicMock()
        mock_vc.is_playing.return_value = True
        mock_vm.voice_client = mock_vc

        queue = AudioStreamQueue(mock_vm)
        
        with patch("os.path.exists", return_value=True):
            await queue.put("/tmp/file1.mp3", auto_delete=False)
            await queue.put("/tmp/file2.mp3", auto_delete=False)

            self.assertFalse(queue._queue.empty())
            queue.clear()
            self.assertTrue(queue._queue.empty())
            self.assertTrue(mock_vc.stop.called or mock_vc.stop_playing.called)

        await queue.stop()

    async def test_audio_queue_loop_shift_resilience(self):
        """Tests that AudioStreamQueue created outside running loop safely adopts active loop."""
        mock_vm = MagicMock()
        mock_vc = MagicMock()
        mock_vc.is_connected.return_value = True
        mock_vc.is_playing.return_value = False
        mock_vm.voice_client = mock_vc

        # Instantiate queue passing None or another loop
        queue = AudioStreamQueue(mock_vm, loop=None)
        self.assertEqual(queue.loop, asyncio.get_running_loop())

        with patch("os.path.exists", return_value=True), \
             patch("discord.FFmpegPCMAudio") as mock_ffmpeg:
            def fake_play(source, after=None):
                if after:
                    after(None)
            mock_vc.play = MagicMock(side_effect=fake_play)

            await queue.put("/tmp/test.mp3", auto_delete=False)
            # Give playback loop time to execute
            await asyncio.sleep(0.05)

            mock_vc.play.assert_called_once()
            self.assertTrue(queue._queue.empty())

        await queue.stop()

if __name__ == "__main__":
    unittest.main()
