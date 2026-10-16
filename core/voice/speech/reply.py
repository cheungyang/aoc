"""Everything the agent says on a voice turn.

The cue that it heard you, then the reply, streamed as two lanes. The agent
writes a <speech> block for the ear before its normal reply (`<voice_mode>`);
the router (speech_router.py) sends the block to the speaker and everything
else to the text channel. Before a tool call the agent says what it's
checking the same way, so a tool wait is never silent. A reply without the
block gets a deterministic fallback and a note under it (missing_speech.py).
Fixed lines (room switching) go through `say`. Every method that would make a
sound takes an `is_current` check, so a turn that was interrupted or
superseded goes quiet.
"""
import asyncio
import os
from typing import Callable, Optional

import discord

from core.voice.engines.tts_engine import TTSEngine
from . import missing_speech
from .audio_queue import FFMPEG_EXE
from .blurp_generator import BlurpGenerator
from .speech_router import SpeechTextRouter
from .voice_stream import VoiceStream

# How long a finished turn's trace waits for its first audio to start playing
# before it is recorded anyway (audio can queue behind earlier speech).
FIRST_AUDIO_WAIT_S = 15.0

# Said when the agent's run fails: the error itself goes to the channel only.
ERROR_LINE = "Sorry, something went wrong. The details are in the channel."

IsCurrent = Callable[[], bool]


def speech_of(response) -> Optional[str]:
    """The reply's <speech> text (AgentResponse.speech), or None."""
    speech = getattr(response, "speech", None)
    return speech if isinstance(speech, str) and speech.strip() else None


class TurnSpeech:
    """What a turn's speech lane has done so far, and when its audio was first heard."""

    def __init__(self, trace):
        self.trace = trace
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

        Speech and text diverge here, deliberately. The speaker gets the
        <speech> block, which the agent wrote for the ear; the channel gets
        everything else -- what the agent wrote for the eye, markdown and all.
        They run as two concurrent lanes so neither waits on the other: a slow
        Discord edit doesn't delay the next sentence's synthesis, and
        synthesis doesn't delay reading the agent's stream. This loop only
        routes events and never blocks on I/O.

        Returns whether the agent answered: False if a newer turn took over
        first, leaving the words unanswered. `on_final` gets what the user
        hears: the reply's speech, or its text when it came without any.
        """
        from core.channel.discord.stream_buffer import DiscordStreamBuffer

        # Mirror the user's words to the shared text channel without holding
        # up the agent: only the text lane waits for it, so the transcript
        # still lands above the reply.
        mirror_task = None
        if self.config.get("post_transcript_to_chat", True) and channel:
            mirror_task = asyncio.create_task(self._post(channel, f"🎤 **{author} (Voice):** {transcript}"))

        trace = speech.trace
        router = SpeechTextRouter()
        text_buffer = DiscordStreamBuffer(channel, edit_interval=1.5)
        text_lane: asyncio.Queue = asyncio.Queue()
        speech_lane: asyncio.Queue = asyncio.Queue()

        def route(said: str, shown: str):
            if shown:
                text_lane.put_nowait(("token", shown))
            if said:
                speech_lane.put_nowait(("speech", said))

        # Ensure audio queue is started
        self.audio.start()
        text_task = asyncio.create_task(self._run_text_lane(channel, text_buffer, text_lane, mirror_task))
        speech_task = asyncio.create_task(self._run_speech_lane(speech_lane, speech, is_current))
        speaker = self.agent_id  # whose reply is heard: a delegate's, once it streams
        replied, response, final_text, failed = False, None, "", False
        try:
            trace.mark("agent_start")
            # Stream agent execution events (tokens, tool calls, and the final response)
            async for event in agent.execute_stream(transcript, session=session):
                if not is_current():
                    self._log("Discarding streaming event due to newer voice turn.")
                    return False

                event_type = event.get("type")
                if event_type == "tool_start":
                    trace.event("tool_start", tool=event.get("tool_name", ""))

                elif event_type == "tool_end":
                    trace.event("tool_end", tool=event.get("tool_name", ""))

                elif event_type == "token":
                    token = event.get("content", "")
                    if not token:
                        continue
                    if event.get("is_header") or event.get("is_error"):
                        # The "🌼 Daisy: " attribution of a delegated reply, and a
                        # delegate's error, are for the channel: read aloud, one is
                        # noise before every answer and the other a stack of jargon.
                        text_lane.put_nowait(("token", token))
                        failed = failed or bool(event.get("is_error"))
                        continue
                    trace.mark("first_token")
                    speaker = event.get("agent_id") or speaker
                    route(*router.feed(token))

                elif event_type == "final_response":
                    route(*router.flush())
                    replied = True
                    response = event.get("response")
                    final_text = event.get("text", "") or ""
                    on_final(speech_of(response) or final_text)
                    text_lane.put_nowait(("final", event))

                elif event_type == "error":
                    failed = True
                    self._log(f"Agent stream error: {event.get('content')}")

            route(*router.flush())
            # Only a model's reply is held to <speech>: not a failed run, nor a
            # turn that never reached a model (a system command).
            closing = ERROR_LINE if failed else ""
            if replied and not failed and self._missing_speech(speaker, response, trace):
                closing = missing_speech.fallback_speech(final_text)
                text_lane.put_nowait(("note", missing_speech.alert(speaker)))
            speech_lane.put_nowait(("end", closing))
            text_lane.put_nowait(None)
            await asyncio.gather(text_task, speech_task)
            return True
        finally:
            for task in (text_task, speech_task):
                if not task.done():
                    task.cancel()

    def _missing_speech(self, speaker: str, response, trace) -> bool:
        """Records on the trace whether the reply came with <speech>; True if it didn't."""
        missing = speech_of(response) is None
        trace.meta["speech"] = "missing" if missing else "ok"
        trace.meta["speaker"] = speaker
        if missing:
            self._log(f"⚠️ {speaker} replied without a <speech> block; speaking the fallback.")
        return missing

    async def _post(self, channel, text: str):
        """Posts a line of the pipeline's own (the transcript, a note) to the channel."""
        try:
            await channel.send(text)
        except Exception as e:
            self._log(f"Error posting to the channel: {e}")

    async def _run_text_lane(self, channel, buffer, lane: asyncio.Queue, mirror_task):
        """Writes the agent's reply to the text channel as it streams, then any note under it."""
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
            elif kind == "note" and channel:
                await self._post(channel, payload)

    async def _run_speech_lane(self, lane: asyncio.Queue, speech: TurnSpeech, is_current: IsCurrent):
        """Speaks the <speech> text sentence by sentence, while the text lane runs."""
        voice_stream = VoiceStream()
        while True:
            kind, payload = await lane.get()
            if not is_current():
                return
            if kind == "speech":
                for sentence in voice_stream.add(payload):
                    await self._speak(sentence, speech, is_current)
            elif kind == "end":
                for sentence in voice_stream.flush():
                    await self._speak(sentence, speech, is_current, label="final chunk")
                if payload:
                    await self._speak(payload, speech, is_current, label="closing line")
                return

    async def _speak(self, sentence: str, speech: TurnSpeech, is_current: IsCurrent, label: str = "chunk"):
        """Synthesizes one chunk and queues it for playback."""
        if not is_current():
            return
        speech.trace.mark("first_chunk")
        self._log(f"🔊 Synthesizing {label}: '{sentence[:60]}...'")
        tts_file = await self.tts_engine.synthesize_to_file(sentence)
        if tts_file:
            speech.trace.mark("tts_first_byte")
        if tts_file and is_current() and self.connection.is_connected():
            speech.queued_audio = True
            await self.audio.put(tts_file, auto_delete=True, on_start=speech.on_audio_start)
