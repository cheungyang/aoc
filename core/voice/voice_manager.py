"""One bot's voice: wires the listening side to the speaking side, and runs turns.

    listening/  the user's side: the VAD cuts their speech into utterances, and
                the floor decides what each one means -- a turn, an interruption
                or listening noise -- and which words are still unanswered
    engines/    speech-to-text and text-to-speech
    speech/     the agent's answer, to the text channel and the speaker
    room/       joining rooms, the text channel a room talks into, and
                "take me to day planning"

The VAD sink hands each utterance to the floor, and the floor starts and
stops turns here. A turn answers every unanswered utterance as one request.
"""
import asyncio
from dataclasses import dataclass
from functools import partial
from typing import Any, List, Optional

from core.loaders.agents_loader import AgentsLoader
from core.runtime.session_manager import SessionManager
from core.runtime.turn_trace import start_turn_trace
from .engines.stt import SpeechRecognizer, agent_vocabulary
from .listening.floor import Floor
from .listening.turn_taking import bare_stop, merge_transcripts
from .listening.utterance import Utterance
from .listening.vad_sink import VADSink
from .room.connection import VoiceConnection
from .room.room_switch import RoomSwitcher
from .speech.audio_queue import AudioStreamQueue
from .speech.reply import ReplyStreamer, TurnSpeech


@dataclass
class _Turn:
    """One turn's state, shared by the steps that answer it."""
    id: int
    utterances: List[Utterance]
    trace: Any
    speech: TurnSpeech
    answered: bool = False
    session_id: Optional[str] = None


class VoiceManager:
    """Orchestrates one bot's voice: the parts above, and the turns between them."""

    def __init__(self, bot_runner):
        self.agent_id = bot_runner.agent_id
        agent = AgentsLoader().get_agent(self.agent_id)
        # A copy: the loader caches agent configs, and this one is per-bot state.
        self.config = dict(agent.config.get("voice_config", {})) if agent else {}

        self.connection = VoiceConnection(bot_runner, make_sink=self._make_sink, channel_hosts=self._channel_hosts)
        self.audio_queue = AudioStreamQueue(self.connection, loop=getattr(bot_runner.bot, "loop", None))
        self.stt = SpeechRecognizer(vocabulary=self._stt_vocabulary())
        self.reply = ReplyStreamer(self.agent_id, self.config, self.audio_queue, self.connection)
        self.floor = Floor(self.stt, self.audio_queue, self.connection, turns=self)
        self.rooms = RoomSwitcher(bot_runner, self.connection)

        self._lock = asyncio.Lock()
        self._active_turn = 0
        self._current_task = None

    def _log(self, message: str) -> None:
        print(f"[VoiceManager:{self.agent_id}] {message}")

    def _make_sink(self) -> VADSink:
        return VADSink(self.floor, loop=self.connection.bot.loop, config=self.config)

    def _channel_hosts(self) -> List[str]:
        agent = AgentsLoader().get_agent(self.agent_id)
        return agent.config.get("channel_hosts", []) if agent else []

    def _stt_vocabulary(self) -> List[str]:
        try:
            return agent_vocabulary(AgentsLoader())
        except Exception as e:
            self._log(f"Could not list agents for the STT vocabulary: {e}")
            return []

    # --- turns, as the floor starts and stops them -----------------------------

    def busy(self) -> bool:
        """A turn is being worked on, or its answer is still playing."""
        if self._current_task is not None and not self._current_task.done():
            return True
        return bool(self.audio_queue.is_playing())

    def begin_turn(self):
        """Starts a turn that answers everything still unanswered."""
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()
        self._active_turn += 1
        self._current_task = asyncio.create_task(self._process_speech_turn(self._active_turn))

    def interrupt(self, reason: str):
        """Stops the reply and abandons the turn in progress."""
        self._log(f"🛑 Barge-in ({reason}): stopping the reply.")
        self._active_turn += 1
        self.audio_queue.clear()
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()

    # --- answering a turn -------------------------------------------------------

    async def _process_speech_turn(self, turn_id: int):
        """Answers every unanswered utterance as one request, traced from the
        newest one's last voiced frame."""
        async with self._lock:
            if turn_id != self._active_turn:
                return
            utterances = self.floor.unanswered()
            if not utterances:
                return
            newest = utterances[-1]
            trace = start_turn_trace(self.agent_id, turn_id, speech_end_at=newest.speech_end_at)
            if newest.finished_at is not None:
                trace.mark("vad_final", at=newest.finished_at)
            turn = _Turn(turn_id, utterances, trace, TurnSpeech(trace))
            cancelled = False
            self.floor.set_reply("")
            try:
                await self._answer(turn)
            except asyncio.CancelledError:
                cancelled = True
                trace.meta["outcome"] = "cancelled"
            except Exception as e:
                from core.util import format_error_message
                trace.meta["outcome"] = "error"
                turn.answered = True
                self._log(f"Error in voice pipeline: {format_error_message(e)}")
            finally:
                # Answered, or heard at least in part: done with these words.
                # Otherwise they ride along with the next turn (B19).
                if turn.answered or turn.speech.heard:
                    self.floor.forget(utterances)
                if turn_id != self._active_turn and not cancelled:
                    trace.meta.setdefault("outcome", "superseded")
                trace.mark("turn_end")
                turn.speech.finish_when_heard(lambda: trace.finish(session_id=turn.session_id), cancelled=cancelled)

    async def _answer(self, turn: _Turn):
        """The turn's transcripts, then a stop, a room switch or the agent's reply."""
        trace, utterances = turn.trace, turn.utterances

        def is_current() -> bool:
            return turn.id == self._active_turn

        # 1. Instantly play static acoustic blurp
        self._log("🔔 Playing signature blurp...")
        self.reply.cue()

        # 2. The transcripts were streamed while the user spoke, so they
        #    are usually final already.
        transcripts = [await self.stt.transcribe(u) for u in utterances]
        transcript = merge_transcripts(t.text for t in transcripts)
        newest = transcripts[-1].text.strip()
        trace.mark("stt_done")
        trace.meta["stt"] = transcripts[-1].provider
        final_ms = (getattr(utterances[-1].stream, "timings", None) or {}).get("final_after_end_ms")
        if isinstance(final_ms, (int, float)):
            trace.meta["stt_final_after_end_ms"] = round(final_ms)
        if len(utterances) > 1:
            trace.meta["merged_utterances"] = len(utterances)
        if not newest:
            # Noise, not words. Anything older stays unanswered.
            self._log("Transcription empty. Ignoring.")
            trace.meta["outcome"] = "no_transcript"
            self.floor.forget(utterances[-1:])
            return
        if not is_current():
            return

        # 3. "Stop" or "wait" on its own is for the pipeline, not the agent:
        #    the reply has already stopped. "Wait" keeps the unanswered
        #    words for what the user says next; "stop" drops them.
        stop = bare_stop(newest)
        if stop is not None:
            self._log(f"✋ Heard {newest!r}: stopping, "
                      + ("keeping the request for what comes next." if stop == "hold" else "request dropped."))
            trace.meta["outcome"] = f"stop_{stop}"
            if stop == "hold":
                self.floor.forget(utterances[-1:])
            else:
                turn.answered = True
            return

        user = utterances[-1].user
        author = getattr(user, "display_name", str(user)) if user else "Speaker"
        merged = f" ({len(utterances)} utterances merged)" if len(utterances) > 1 else ""
        self._log(f"📝 Transcribed prompt from {author}{merged}: '{transcript}'")

        # 4. "Take me to day planning" moves rooms instead of starting a
        #    turn, and leaves anything unanswered behind with the old room.
        if await self.rooms.switch(user, newest, say=partial(self.reply.say, is_current=is_current)):
            trace.meta["outcome"] = "room_switch"
            turn.answered = True
            return

        # 5. Execute LangGraph Agent with streaming, in the text channel this
        #    room talks into, so context is 100% shared with text.
        agent = AgentsLoader().get_agent(self.agent_id)
        if not agent:
            turn.answered = True  # nothing will ever answer them
            return
        channel = self.connection.text_channel()
        self._log(f"🤖 Streaming LangGraph agent under #{getattr(channel, 'name', channel)} context...")

        # Same session as the text channel (so context is shared), but
        # tagged as voice so token rows can tell spoken turns from typed
        # ones, and so the agent gets <voice_mode>.
        session = SessionManager().get_session(
            agent_id=self.agent_id,
            source="discord",
            channel=channel,
            surface="voice",
        )
        turn.session_id = session.session_id
        turn.answered = await self.reply.stream(
            agent, transcript, author, session, channel, turn.speech,
            is_current=is_current, on_final=self.floor.set_reply,
        )
