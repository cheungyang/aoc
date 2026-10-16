"""PCMSpeaker: TTS audio in as it arrives, 20 ms Discord frames out."""
import array
import asyncio
import time
from unittest.mock import MagicMock

import pytest

from core.voice.speech import pcm_speaker
from core.voice.speech.pcm_speaker import FRAME_BYTES, SILENCE, PCMSpeaker

ONE_FRAME = 960  # mono samples in 20 ms at 48 kHz


def _mono(*samples):
    return array.array("h", samples).tobytes()


@pytest.mark.asyncio
async def test_mono_in_stereo_frames_out():
    speaker = PCMSpeaker(asyncio.get_running_loop())
    speaker.write(_mono(*range(ONE_FRAME)))
    frame = speaker.read()
    assert len(frame) == FRAME_BYTES
    assert array.array("h", frame)[:6].tolist() == [0, 0, 1, 1, 2, 2]


@pytest.mark.asyncio
async def test_a_late_chunk_is_a_gap_not_the_end():
    speaker = PCMSpeaker(asyncio.get_running_loop())
    speaker.write(_mono(*[7] * ONE_FRAME))
    assert speaker.read() != SILENCE
    assert speaker.read() == SILENCE  # nothing has arrived yet: playback goes on, quietly
    assert speaker.underruns == 1
    speaker.write(_mono(*[7] * (ONE_FRAME // 2)))
    speaker.finish()
    last = speaker.read()
    assert len(last) == FRAME_BYTES and last.endswith(bytes(FRAME_BYTES // 2)), "padded to a whole frame"
    assert speaker.read() == b"", "finished and drained: playback ends"


@pytest.mark.asyncio
async def test_a_chunk_that_splits_a_sample_is_rejoined():
    speaker = PCMSpeaker(asyncio.get_running_loop())
    data = _mono(1, 2, *[0] * (ONE_FRAME - 2))
    speaker.write(data[:3])
    speaker.write(data[3:])
    assert array.array("h", speaker.read())[:4].tolist() == [1, 1, 2, 2]


@pytest.mark.asyncio
async def test_ready_waits_for_the_first_audio_and_reports_it_once():
    heard = []
    speaker = PCMSpeaker(asyncio.get_running_loop(), on_audio=lambda: heard.append("audio"))
    waiting = asyncio.create_task(speaker.ready())
    await asyncio.sleep(0.01)
    assert not waiting.done()
    speaker.write(bytes(1920))
    speaker.write(bytes(1920))
    assert await asyncio.wait_for(waiting, 1) is True
    assert heard == ["audio"]


@pytest.mark.asyncio
async def test_a_stream_that_ends_silent_has_nothing_to_play():
    speaker = PCMSpeaker(asyncio.get_running_loop())
    speaker.finish()
    assert await asyncio.wait_for(speaker.ready(), 1) is False


@pytest.mark.asyncio
async def test_cleanup_stops_the_stream_feeding_it():
    speaker = PCMSpeaker(asyncio.get_running_loop())
    speaker.stream = MagicMock()
    speaker.write(bytes(1920))
    speaker.cleanup()  # a barge-in, or playback over
    speaker.stream.cancel.assert_called_once()
    assert speaker.read() == b""
    speaker.write(bytes(1920))  # audio still on its way is dropped
    assert speaker.read() == b""


@pytest.mark.asyncio
async def test_a_stalled_stream_ends_playback(monkeypatch):
    speaker = PCMSpeaker(asyncio.get_running_loop())
    speaker.write(bytes(FRAME_BYTES // 2))
    speaker.read()
    monkeypatch.setattr(pcm_speaker, "STALL_S", 0.0)
    time.sleep(0.001)
    assert speaker.read() == b""
