"""Everything the agent says on a voice turn.

The cue that it heard you, the reply -- streamed to the text channel and to
the speaker as two lanes -- floor-holding bridge phrases while tools run, and
fixed lines (room switching). Every method that would make a sound takes an
`is_current` check, so a turn that was interrupted or superseded goes quiet.
"""
import asyncio
import os
from typing import Callable

import discord

from core.util.models import DEFAULT_VERBALIZER_MODEL
from core.voice.engines.tts_engine import TTSEngine
from .audio_queue import FFMPEG_EXE
from .blurp_generator import BlurpGenerator
from .bridge_manager import BridgeManager
from .sentence_chunker import SentenceChunker
from .verbalizer import Verbalizer
from .voice_stream import VoiceStream

# How long a finished turn's trace waits for its first audio to start playing
# before it is recorded anyway (audio can queue behind a bridge phrase).
FIRST_AUDIO_WAIT_S = 15.0

IsCurrent = Callable[[], bool]


class TurnSpeech:
    """What a turn's speech lane has done so far, and when its audio was first heard."""

    def __init__(self, trace):
        self.trace = trace
        self.emitted = False        # anything spoken (or a bridge played) this turn
        self.queued_audio = False   # answer audio handed to the audio queue
        self.heard = False          # the first answer audio started playing
        self._finish = None
        self._timer = None

    def on_audio_start(self):
        """Audio-queue callback: the listener starts hearing the answer now."""
        if self.heard:
            return
        self.heard = True
        self.trace.mark("audio_start")
        if self._finish is not None:
            self._run_finish()

    def finish_when_heard(self, finish, cancelled: bool):
        """Runs `finish` now, or once the queued answer audio starts playing.

        Short replies are often fully queued before playback begins, and time
        to first audio is the number the trace exists for, so recording waits
        for it -- bounded, because a barge-in can clear audio that never plays.
        """
        if cancelled or self.heard or not self.queued_audio:
            finish()
            return
        self._finish = finish
        try:
            self._timer = asyncio.get_running_loop().call_later(FIRST_AUDIO_WAIT_S, self._run_finish)
        except RuntimeError:
            self._run_finish()

    def _run_finish(self):
        finish, self._finish = self._finish, None
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if finish is not None:
            finish()


class ReplyStreamer:
    def __init__(self, agent_id: str, config: dict, audio, connection):
        self.agent_id = agent_id
        self.config = config
        self.audio = audio  # the AudioStreamQueue
        self.connection = connection
        self.tts_engine = TTSEngine(
            default_voice=config.get("tts_voice", "en-US-JennyNeural"),
            default_speed=float(config.get("tts_speed", 1.0)),
        )
        # Rewrites structured agent output for the ear. Disabling it falls the
        # pipeline back to TextSanitizer, which is still speakable -- just
        # flatter -- so this is a safe switch to flip if latency matters more
        # than phrasing on a given agent.
        self.verbalizer = Verbalizer(
            model_name=config.get("verbalizer_model", DEFAULT_VERBALIZER_MODEL),
            enabled=config.get("verbalize", True),
        )
        self.bridge_manager = BridgeManager()

    def _log(self, message: str) -> None:
        print(f"[VoiceManager:{self.agent_id}] {message}")

    def cue(self):
        """Plays the agent's static blurp cue immediately."""
        vc = self.connection.voice_client
        if not vc or not vc.is_connected():
            return
        try:
            if vc.is_playing():
                self.audio.clear()
            blurp_file = BlurpGenerator.get_blurp_audio(self.config)
            if blurp_file and os.path.exists(blurp_file):
                vc.play(discord.FFmpegPCMAudio(blurp_file, executable=FFMPEG_EXE))
        except Exception as e:
            self._log(f"Error playing static blurp: {e}")

    async def say(self, text: str, is_current: IsCurrent = lambda: True):
        """Speaks a fixed line (room switching) outside an agent reply."""
        if not is_current():
            return
        tts_file = await self.tts_engine.synthesize_to_file(text)
        if tts_file and self.connection.is_connected():
            self.audio.start()
            await self.audio.put(tts_file, auto_delete=True)

    async def stream(self, agent, transcript: str, author: str, session, channel, speech: TurnSpeech,
                     is_current: IsCurrent, on_final: Callable[[str], None]) -> bool:
        """Streams the agent and fans its events out to a text lane and a speech lane.

        Speech and text diverge here, deliberately. The synthesiser gets text
        restructured for the ear; the channel gets what the agent actually
        wrote, markdown and all. They run as two concurrent lanes so neither
        waits on the other: a slow Discord edit no longer delays the next
        sentence's synthesis, and synthesis no longer delays reading the
        agent's stream. This loop only routes events and never blocks on I/O.

        Returns whether the agent answered: False if a newer turn took over
        first, leaving the words unanswered. `on_final` gets the reply's text.
        """
        from core.channel.discord.stream_buffer import DiscordStreamBuffer

        # Mirror the user's words to the shared text channel without holding
        # up the agent: only the text lane waits for it, so the transcript
        # still lands above the reply.
        mirror_task = None
        if self.config.get("post_transcript_to_chat", True) and channel:
            mirror_task = asyncio.create_task(self._post_transcript(channel, author, transcript))

        trace = speech.trace
        voice_stream = VoiceStream(verbalizer=self.verbalizer, session=session)
        text_buffer = DiscordStreamBuffer(channel, edit_interval=1.5)
        text_lane: asyncio.Queue = asyncio.Queue()
        speech_lane: asyncio.Queue = asyncio.Queue()

        # Ensure audio queue is started
        self.audio.start()
        text_task = asyncio.create_task(self._run_text_lane(text_buffer, text_lane, mirror_task))
        speech_task = asyncio.create_task(self._run_speech_lane(voice_stream, speech_lane, session, speech, is_current))
        final_text = ""
        try:
            trace.mark("agent_start")
            # Stream agent execution events (tokens, tool bridge phrases, and final audio)
            async for event in agent.execute_stream(transcript, session=session):
                if not is_current():
                    self._log("Discarding streaming event due to newer voice turn.")
                    return False

                event_type = event.get("type")
                if event_type == "tool_start":
                    trace.event("tool_start", tool=event.get("tool_name", ""))
                    speech_lane.put_nowait(("bridge", event))

                elif event_type == "tool_end":
                    trace.event("tool_end", tool=event.get("tool_name", ""))

                elif event_type == "token":
                    token_delta = event.get("content", "")
                    if not token_delta:
                        continue
                    text_lane.put_nowait(("token", token_delta))
                    # The "🌼 Daisy: " attribution of a delegated reply is for
                    # the channel; read aloud it is noise before every answer.
                    if not event.get("is_header"):
                        trace.mark("first_token")
                        speech_lane.put_nowait(("token", token_delta))

                elif event_type == "final_response":
                    final_text = event.get("text", "") or ""
                    on_final(final_text)
                    text_lane.put_nowait(("final", event))

                elif event_type == "error":
                    self._log(f"Agent stream error: {event.get('content')}")

            speech_lane.put_nowait(("end", final_text))
            text_lane.put_nowait(None)
            await asyncio.gather(text_task, speech_task)
            return True
        finally:
            for task in (text_task, speech_task):
                if not task.done():
                    task.cancel()

    async def _post_transcript(self, channel, author: str, transcript: str):
        try:
            await channel.send(f"🎤 **{author} (Voice):** {transcript}")
        except Exception as te:
            self._log(f"Error posting transcript: {te}")

    async def _run_text_lane(self, buffer, lane: asyncio.Queue, mirror_task):
        """Writes the agent's reply to the text channel as it streams."""
        if mirror_task is not None:
            # The transcript goes above the reply; only this lane waits for it.
            try:
                await mirror_task
            except Exception:
                pass
        while True:
            item = await lane.get()
            if item is None:
                return
            kind, payload = item
            if kind == "token":
                await buffer.append_token(payload)
            elif kind == "final":
                await buffer.finalize(
                    final_text=payload.get("text", "") or "",
                    response=payload.get("response")
                )

    async def _run_speech_lane(self, voice_stream, lane: asyncio.Queue, session, speech: TurnSpeech,
                               is_current: IsCurrent):
        """Turns the agent's tokens into speech, in order, while the text lane runs."""
        while True:
            kind, payload = await lane.get()
            if not is_current():
                return
            if kind == "token":
                for sentence in await voice_stream.add_token(payload):
                    await self._speak(sentence, speech, is_current)
            elif kind == "bridge":
                if not speech.emitted:
                    await self._play_bridge(payload, speech, is_current)
            elif kind == "end":
                # Flush remaining text from the voice stream
                for sentence in await voice_stream.flush():
                    await self._speak(sentence, speech, is_current, label="final chunk")

                # Fallback for short direct answers (e.g. "Done.") that never streamed a token
                final_text = payload
                if not speech.emitted and final_text:
                    spoken = await self.verbalizer.verbalize(final_text, session=session)
                    for sentence in SentenceChunker().split_into_sentences(spoken):
                        await self._speak(sentence, speech, is_current, label="direct response")
                return

    async def _speak(self, sentence: str, speech: TurnSpeech, is_current: IsCurrent, label: str = "chunk"):
        """Synthesizes one chunk and queues it for playback."""
        if not is_current():
            return
        speech.emitted = True
        speech.trace.mark("first_chunk")
        self._log(f"🔊 Synthesizing {label}: '{sentence[:60]}...'")
        tts_file = await self.tts_engine.synthesize_to_file(sentence)
        if tts_file:
            speech.trace.mark("tts_first_byte")
        if tts_file and is_current() and self.connection.is_connected():
            speech.queued_audio = True
            await self.audio.put(tts_file, auto_delete=True, on_start=speech.on_audio_start)

    async def _play_bridge(self, event, speech: TurnSpeech, is_current: IsCurrent):
        """Plays a floor-holding phrase while the first tool runs, if nothing was said yet."""
        tool_name = event.get("tool_name", "")
        bridge_audio = await self.bridge_manager.get_or_create_bridge_audio(
            self.agent_id, tool_name, event.get("tool_args", {}), self.tts_engine,
            voice=self.config.get("tts_voice"), speed=float(self.config.get("tts_speed", 1.0)),
        )
        if bridge_audio and is_current() and self.connection.is_connected():
            speech.emitted = True
            self._log(f"🌉 Dual-track bridge: Playing floor-holding audio for '{tool_name}'...")
            await self.audio.put(bridge_audio, priority=False, auto_delete=False)
