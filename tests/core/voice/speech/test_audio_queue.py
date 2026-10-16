"""AudioStreamQueue: a turn's speakers, played in order; barge-in clears them."""
import asyncio
import unittest
from unittest.mock import MagicMock

import discord

from core.voice.speech.audio_queue import AudioStreamQueue
from core.voice.speech.pcm_speaker import PCMSpeaker

FRAME_OF_AUDIO = bytes(1920)  # 20 ms of 48 kHz mono


def _speaker(audio=True, finished=True):
    speaker = PCMSpeaker(asyncio.get_running_loop())
    speaker.stream = MagicMock()
    if audio:
        speaker.write(FRAME_OF_AUDIO)
    if finished:
        speaker.finish()
    return speaker


def _connection(playing=False):
    connection = MagicMock()
    vc = MagicMock()
    vc.is_connected.return_value = True
    vc.is_playing.return_value = playing
    connection.voice_client = vc
    return connection, vc


class TestAudioStreamQueue(unittest.IsolatedAsyncioTestCase):
    async def test_speakers_play_in_order_each_once_its_audio_is_in(self):
        connection, vc = _connection()
        played, started = [], []

        def play(source, after=None):
            played.append(source.original)
            after(None)  # finishes at once

        vc.play = MagicMock(side_effect=play)
        queue = AudioStreamQueue(connection)
        first, second = _speaker(audio=False, finished=False), _speaker()
        await queue.put(first, on_start=lambda: started.append("first"))
        await queue.put(second, on_start=lambda: started.append("second"))
        await asyncio.sleep(0.02)
        self.assertEqual(played, [], "nothing plays before its audio arrives, and the second waits its turn")
        self.assertTrue(queue.is_playing(), "speech on its way counts as playing")

        first.write(FRAME_OF_AUDIO)
        first.finish()
        await asyncio.sleep(0.02)
        self.assertEqual(played, [first, second])
        self.assertEqual(started, ["first", "second"])
        await queue.stop()

    async def test_speech_that_never_makes_a_sound_is_skipped(self):
        connection, vc = _connection()
        queue = AudioStreamQueue(connection)
        await queue.put(_speaker(audio=False))  # its TTS failed
        await asyncio.sleep(0.02)
        vc.play.assert_not_called()
        self.assertFalse(queue.is_playing())
        await queue.stop()

    async def test_clear_stops_playback_and_the_synthesis_behind_queued_speech(self):
        connection, vc = _connection(playing=True)
        queue = AudioStreamQueue(connection)
        waiting = [_speaker(audio=False, finished=False) for _ in range(3)]
        for speaker in waiting:
            await queue.put(speaker)
        await asyncio.sleep(0.01)  # the first is off the queue, waiting for its audio

        queue.clear()
        self.assertTrue(queue._queue.empty())
        self.assertTrue(vc.stop_playing.called or vc.stop.called)
        for speaker in waiting:
            speaker.stream.cancel.assert_called()
        waiting[0].write(FRAME_OF_AUDIO)  # audio that was already on its way
        await asyncio.sleep(0.02)
        vc.play.assert_not_called()
        await queue.stop()

    async def test_audio_queue_loop_shift_resilience(self):
        """Tests that AudioStreamQueue created outside running loop safely adopts active loop."""
        connection, vc = _connection()
        queue = AudioStreamQueue(connection, loop=None)
        self.assertEqual(queue.loop, asyncio.get_running_loop())
        vc.play = MagicMock(side_effect=lambda source, after=None: after(None))

        await queue.put(_speaker())
        await asyncio.sleep(0.05)

        vc.play.assert_called_once()
        self.assertTrue(queue._queue.empty())
        await queue.stop()


class TestDucking(unittest.IsolatedAsyncioTestCase):
    """Stage one of barge-in: the reply keeps playing, quieter, until the user's
    words show whether they are interrupting."""

    async def _playing_queue(self):
        """A queue whose first item plays (and stays playing) once it's queued."""
        connection, vc = _connection()
        played = []
        vc.play = MagicMock(side_effect=lambda source, after=None: played.append(source))
        return AudioStreamQueue(connection), played

    async def test_duck_lowers_the_playing_item_and_unduck_restores_it(self):
        queue, played = await self._playing_queue()
        await queue.put(_speaker())
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
        await queue.put(_speaker())
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
