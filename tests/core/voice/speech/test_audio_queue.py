import unittest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock

import discord

from core.voice.speech.audio_queue import AudioStreamQueue
from tests.helpers import fake_audio_source


class TestAudioStreamQueue(unittest.IsolatedAsyncioTestCase):
    async def test_audio_queue_clear_stops_and_drains(self):
        connection = MagicMock()
        mock_vc = MagicMock()
        mock_vc.is_playing.return_value = True
        connection.voice_client = mock_vc

        queue = AudioStreamQueue(connection)
        
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
        connection = MagicMock()
        mock_vc = MagicMock()
        mock_vc.is_connected.return_value = True
        mock_vc.is_playing.return_value = False
        connection.voice_client = mock_vc

        # Instantiate queue passing None or another loop
        queue = AudioStreamQueue(connection, loop=None)
        self.assertEqual(queue.loop, asyncio.get_running_loop())

        with patch("os.path.exists", return_value=True), \
             patch("discord.FFmpegPCMAudio", fake_audio_source()):
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


class TestDucking(unittest.IsolatedAsyncioTestCase):
    """Stage one of barge-in: the reply keeps playing, quieter, until the user's
    words show whether they are interrupting."""

    async def _playing_queue(self):
        """A queue whose first item is playing (and stays playing) when this returns."""
        connection = MagicMock()
        connection.voice_client = MagicMock()
        connection.voice_client.is_connected.return_value = True
        connection.voice_client.is_playing.return_value = False
        played = []
        connection.voice_client.play = MagicMock(side_effect=lambda source, after=None: played.append(source))
        queue = AudioStreamQueue(connection)
        return queue, played

    async def test_duck_lowers_the_playing_item_and_unduck_restores_it(self):
        queue, played = await self._playing_queue()
        with patch("os.path.exists", return_value=True), \
             patch("discord.FFmpegPCMAudio", fake_audio_source()):
            await queue.put("/tmp/reply.mp3", auto_delete=False)
            await asyncio.sleep(0.02)
            source = played[0]
            self.assertIsInstance(source, discord.PCMVolumeTransformer)
            self.assertEqual(source.volume, 1.0)

            queue.duck(0.25)
            self.assertEqual(source.volume, 0.25)
            queue.unduck()
            self.assertEqual(source.volume, 1.0)
        await queue.stop()

    async def test_audio_that_starts_while_ducked_starts_quiet(self):
        queue, played = await self._playing_queue()
        queue.duck(0.25)
        with patch("os.path.exists", return_value=True), \
             patch("discord.FFmpegPCMAudio", fake_audio_source()):
            await queue.put("/tmp/next.mp3", auto_delete=False)
            await asyncio.sleep(0.02)
            self.assertEqual(played[0].volume, 0.25)
        await queue.stop()

    async def test_clear_resets_the_volume_for_the_next_reply(self):
        queue, _played = await self._playing_queue()
        queue.duck(0.25)
        queue.clear()
        self.assertEqual(queue._volume, 1.0)
        await queue.stop()


if __name__ == "__main__":
    unittest.main()
