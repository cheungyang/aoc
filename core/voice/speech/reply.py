"""Everything the agent says on a voice turn.

The cue that it heard you, then the reply, streamed as two lanes. The agent
writes a <speech> block for the ear before its normal reply (`<voice_mode>`);
the router (speech_router.py) sends the block to the speaker and everything
else to the text channel. Before a tool call the agent says what it's
checking the same way, so a tool wait is never silent. Speech is synthesized
while it's being written: each stretch -- a <speech> block, in the voice of
the agent that wrote it -- is one TTS stream feeding one speaker in the audio
queue (engines/tts.py, pcm_speaker.py). A reply without the block gets a
deterministic fallback and a note under it (missing_speech.py). Fixed lines
(switching who you're talking to) go through `say`. Every method that would make a sound takes
an `is_current` check, so a turn that was interrupted or superseded goes
quiet.
"""
import asyncio
import os
from typing import Callable, Dict, List, Optional

import discord

from core.voice import settings
from core.voice.engines.tts import TTSEngine, Voice, agent_voice
from . import missing_speech
from .blurp_generator import BlurpGenerator
from .pcm_speaker import PCMSpeaker
from .speech_router import BLOCK_END, SpeechTextRouter
from .speech_util import speakable
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
        self.tts = TTSEngine()

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
                vc.play(discord.FFmpegPCMAudio(blurp_file, executable=settings.ffmpeg_exe()))
        except Exception as e:
            self._log(f"Error playing static blurp: {e}")

    async def say(self, text: str, agent_id: Optional[str] = None, is_current: IsCurrent = lambda: True):
        """Speaks a fixed line (a focus switch) outside an agent reply, in
        `agent_id`'s voice: this bot's own agent by default."""
        line = speakable(text)
        if not line or not is_current() or not self.connection.is_connected():
            return
        stream = await self._open(agent_voice(agent_id or self.agent_id))
        stream.send(line)
        stream.finish()

    async def _open(self, voice: Voice, speech: Optional[TurnSpeech] = None):
        """Starts a stretch of speech in `voice`: a TTS stream into a speaker queued to play."""
        on_audio = (lambda: speech.trace.mark("tts_first_byte")) if speech else None
        speaker = PCMSpeaker(asyncio.get_running_loop(), on_audio=on_audio)
        speaker.stream = await self.tts.open(voice, speaker)
        if speech is not None:
            speech.queued_audio = True
        self.audio.start()
        await self.audio.put(speaker, on_start=speech.on_audio_start if speech else None)
        return speaker.stream

    async def stream(self, agent, transcript: str, author: str, session, channel, speech: TurnSpeech,
                     is_current: IsCurrent, on_final: Callable[[str], None],
                     speaker: Optional[str] = None) -> bool:
        """Streams the agent and fans its events out to a text lane and a speech lane.

        Speech and text diverge here, deliberately. The speaker gets the
        <speech> block, which the agent wrote for the ear; the channel gets
        everything else -- what the agent wrote for the eye, markdown and all.
        They run as two concurrent lanes so neither waits on the other: a slow
        Discord edit doesn't delay the next sentence's synthesis, and
        synthesis doesn't delay reading the agent's stream. This loop only
        routes events and never blocks on I/O.

        `speaker` is the agent you're talking to (the focus's): whose voice is
        heard until a delegate's tokens say otherwise, and who says the error
        line. Defaults to this bot's own agent.

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
                speech_lane.put_nowait(("speech", said, speaker))

        # Ensure audio queue is started
        self.audio.start()
        text_task = asyncio.create_task(self._run_text_lane(channel, text_buffer, text_lane, mirror_task))
        speech_task = asyncio.create_task(self._run_speech_lane(speech_lane, speech, is_current))
        you_asked = speaker or self.agent_id  # the agent you're talking to
        speaker = you_asked  # whose reply is heard: a delegate's, once it streams
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
            closing, said_by = (ERROR_LINE, you_asked) if failed else ("", speaker)
            if replied and not failed and self._missing_speech(speaker, response, trace):
                closing = missing_speech.fallback_speech(final_text)
                text_lane.put_nowait(("note", missing_speech.alert(speaker)))
            speech_lane.put_nowait(("end", closing, said_by))
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
        """Speaks the <speech> text as it's written, each agent in its own voice.

        A stretch of speech -- up to the end of a <speech> block, or until
        another agent speaks -- is one TTS stream, fed a chunk at a time
        (voice_stream.py), and one speaker in the audio queue.
        """
        chunks, stretch = VoiceStream(), _Stretch(self, speech)
        voices: Dict[Optional[str], Voice] = {}
        last = None  # the voice the buffered text is in
        try:
            while True:
                kind, text, agent_id = await lane.get()
                if not is_current():
                    stretch.cancel()
                    return
                if agent_id not in voices:
                    voices[agent_id] = agent_voice(agent_id)
                voice = voices[agent_id]
                if kind == "speech":
                    last = voice
                    *closed, rest = text.split(BLOCK_END)
                    for block_end in closed:  # a <speech> block closed: say it all now
                        await stretch.say(chunks.add(block_end + BLOCK_END), voice)
                        stretch.end()
                    await stretch.say(chunks.add(rest), voice)
                else:  # "end": the rest, then any closing line
                    await stretch.say(chunks.flush(), last or voice)
                    if text:
                        await stretch.say([text], voice)
                    stretch.end()
                    return
        except BaseException:
            stretch.cancel()
            raise


class _Stretch:
    """The stretch of speech being fed: one voice, one TTS stream, one speaker."""

    def __init__(self, reply: ReplyStreamer, speech: TurnSpeech):
        self._reply = reply
        self._speech = speech
        self.stream = None
        self.voice: Optional[Voice] = None

    async def say(self, chunks: List[str], voice: Voice) -> None:
        for chunk in chunks:
            if self.stream is not None and voice != self.voice:
                self.end()  # another agent speaks: in its own voice, after this one
            if self.stream is None:
                self.stream = await self._reply._open(voice, self._speech)
                self.voice = voice
            self._speech.trace.mark("first_chunk")
            self._reply._log(f"🔊 {voice.name.rsplit('-', 1)[-1]}: '{chunk[:60]}'")
            self.stream.send(chunk + " ")

    def end(self) -> None:
        """No more text for this stretch; its audio plays out."""
        if self.stream is not None:
            self.stream.finish()
            self.stream = None

    def cancel(self) -> None:
        if self.stream is not None:
            self.stream.cancel()
            self.stream = None
