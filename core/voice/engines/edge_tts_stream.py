"""The fallback voice: edge-tts, one request per chunk, decoded to PCM by FFmpeg.

It speaks every stream when Chirp can't run on this host, and takes over a
Chirp stream that fails before its first audio. edge-tts has no streaming
input, so each chunk is a request of its own after the one before it: slower
to start, but played the same way. An agent keeps its voice's gender
(EDGE_FALLBACK_VOICES), not the voice itself.
"""
import asyncio

from core.voice import settings


class EdgeStream:
    """One stretch of speech through edge-tts. Every method is safe from any thread."""

    def __init__(self, voice, speaker):
        self.voice = voice
        self.speaker = speaker
        self._loop = asyncio.get_running_loop()
        self._texts: asyncio.Queue = asyncio.Queue()
        self._task = self._loop.create_task(self._run())

    def send(self, text: str) -> None:
        if text:
            self._loop.call_soon_threadsafe(self._texts.put_nowait, text)

    def finish(self) -> None:
        self._loop.call_soon_threadsafe(self._texts.put_nowait, None)

    def cancel(self) -> None:
        self._loop.call_soon_threadsafe(self._task.cancel)

    async def _run(self) -> None:
        try:
            while (text := await self._texts.get()) is not None:
                try:
                    pcm = await synthesize(text, self.voice)
                except Exception as e:
                    print(f"[TTS] edge-tts failed: {e}")
                    continue
                self.speaker.write(pcm)
        except asyncio.CancelledError:
            pass
        finally:
            self.speaker.finish()


async def synthesize(text: str, voice) -> bytes:
    """`text` in the fallback voice for `voice`, as 48 kHz mono PCM."""
    import edge_tts

    mp3 = bytearray()
    rate = f"{round((voice.rate - 1) * 100):+d}%"
    communicate = edge_tts.Communicate(text, voice=settings.EDGE_FALLBACK_VOICES[voice.gender], rate=rate)
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            mp3 += chunk["data"]
    if not mp3:
        return b""
    ffmpeg = await asyncio.create_subprocess_exec(
        settings.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
        "-f", "s16le", "-ac", "1", "-ar", str(settings.TTS_SAMPLE_RATE), "pipe:1",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    pcm, err = await ffmpeg.communicate(bytes(mp3))
    if ffmpeg.returncode:
        raise RuntimeError(f"ffmpeg exited {ffmpeg.returncode}: {err.decode(errors='replace')[:200]}")
    return pcm
