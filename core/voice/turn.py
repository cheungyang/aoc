"""One voice turn: everything still unanswered, answered as one request.

The floor decides when a turn is due and VoiceManager starts it (`begin_turn`);
a `VoiceTurn` answers it:

    1. the cue that the agent heard you
    2. the transcripts, merged: they were streamed while you spoke (noise,
       with no words, is dropped)
    3. a spoken command (core/runtime/commands/spoken/): "stop" or "wait" on
       its own, for the pipeline rather than the agent; "next agent", "where
       am I", or a switch ("switch me to Daisy")
    4. the agent's reply, run exactly as an untagged message typed in the
       focus's text channel would be: on that channel's session, so voice and
       text share each agent's history

Every turn is traced (core/runtime/observability/turn_trace.py) from the newest
utterance's last voiced frame until its answer is first heard.
"""
import asyncio
from functools import partial
from typing import Any, List, Optional, Protocol

from core.runtime.commands.base import SpokenContext
from core.runtime.commands.ports import VoiceControls
from core.runtime.commands.registry import SPOKEN
from core.runtime.foundation.sessions import SessionManager
from core.runtime.observability.turn_trace import Outcome, Stage, start_turn_trace
from core.util.errors import format_error_message
from core.voice.engines.stt import SpeechRecognizer
from core.voice.foundation.utterance import Utterance
from core.voice.listening.floor import Floor
from core.voice.listening.turn_taking import merge_transcripts
from core.voice.room.focus import VoiceFocus
from core.voice.speech.reply import ReplyStreamer, TurnSpeech


class TurnRoom(VoiceControls, Protocol):
    """What a turn needs from the voice room (VoiceManager): its parts, and
    VoiceControls, which the spoken commands use."""

    agent_id: str              # this bot's agent: the voice host, whose turn it runs
    turn_output: Optional[Any]  # channel -> the turn's output port, from the host; None for none
    stt: SpeechRecognizer
    reply: ReplyStreamer
    floor: Floor

    def is_current(self, turn_id: int) -> bool:
        """No newer turn has started, and the turn wasn't interrupted."""

    def focus(self) -> VoiceFocus:
        """Where the turn goes."""

    def focus_channel(self, focus: VoiceFocus):
        """The Discord text channel `focus`'s turns are posted in, or None."""

    def agent(self) -> Any:
        """The agent the turn runs as (this bot's), or None."""

    def turn_starting(self, focus: VoiceFocus) -> None:
        """Tells the hooks a turn is about to run in `focus`'s channel."""


class VoiceTurn:
    """One turn: its utterances, its trace, and whether it was answered."""

    def __init__(self, room: TurnRoom, turn_id: int, utterances: List[Utterance]):
        self.room = room
        self.id = turn_id
        self.utterances = utterances
        newest = utterances[-1]
        self.trace = start_turn_trace(room.agent_id, turn_id, speech_end_at=newest.speech_end_at)
        if newest.finished_at is not None:
            self.trace.mark(Stage.VAD_FINAL, at=newest.finished_at)
        self.speech = TurnSpeech(self.trace)
        self.answered = False
        self._held = False  # "wait": this utterance dropped, the earlier words kept
        self.session_id: Optional[str] = None

    def _log(self, message: str) -> None:
        print(f"[VoiceManager:{self.room.agent_id}] {message}")

    def is_current(self) -> bool:
        return self.room.is_current(self.id)

    def hold(self) -> None:
        """"Wait" (core/runtime/commands/spoken/wait.py): drops this utterance,
        but keeps the words said before it for what's said next."""
        self._held = True
        self.room.floor.forget(self.utterances[-1:])

    async def run(self) -> None:
        """Answers the turn; its trace is recorded once the answer is first heard."""
        room, trace, utterances = self.room, self.trace, self.utterances
        cancelled = False
        room.floor.set_reply("")
        try:
            await self._answer()
        except asyncio.CancelledError:
            cancelled = True
            trace.meta["outcome"] = Outcome.CANCELLED
        except Exception as e:
            trace.meta["outcome"] = Outcome.ERROR
            self.answered = True
            self._log(f"Error in voice pipeline: {format_error_message(e)}")
        finally:
            # Answered, or heard at least in part: done with these words.
            # Otherwise they ride along with the next turn (B19).
            if self.answered or self.speech.heard:
                room.floor.forget(utterances)
            if not self.is_current() and not cancelled:
                trace.meta.setdefault("outcome", Outcome.SUPERSEDED)
            trace.mark(Stage.TURN_END)

            def finish_trace():
                # The voiceprint gate scores alongside the turn: by the time
                # its answer has been heard, each utterance has its verdict.
                speaker = [u.gate.summary() for u in utterances if u.gate is not None]
                if speaker:
                    trace.meta["speaker"] = speaker
                trace.finish(session_id=self.session_id)

            self.speech.finish_when_heard(finish_trace, cancelled=cancelled)

    async def _answer(self) -> None:
        """The turn's transcripts, then a spoken command or the agent's reply."""
        room, trace, utterances = self.room, self.trace, self.utterances
        is_current = self.is_current

        # 1. Instantly play static acoustic blurp
        self._log("🔔 Playing signature blurp...")
        room.reply.cue()

        # 2. The transcripts were streamed while the user spoke, so they
        #    are usually final already.
        transcripts = [await room.stt.transcribe(u) for u in utterances]
        transcript = merge_transcripts(t.text for t in transcripts)
        newest = transcripts[-1].text.strip()
        trace.mark(Stage.STT_DONE)
        trace.meta["stt"] = transcripts[-1].provider
        final_ms = (getattr(utterances[-1].stream, "timings", None) or {}).get("final_after_end_ms")
        if isinstance(final_ms, (int, float)):
            trace.meta["stt_final_after_end_ms"] = round(final_ms)
        if len(utterances) > 1:
            trace.meta["merged_utterances"] = len(utterances)
        if not newest:
            # Noise, not words. Anything older stays unanswered.
            self._log("Transcription empty. Ignoring.")
            trace.meta["outcome"] = Outcome.NO_TRANSCRIPT
            room.floor.forget(utterances[-1:])
            return
        if not is_current():
            return

        user = utterances[-1].user
        author = getattr(user, "display_name", str(user)) if user else "Speaker"
        merged = f" ({len(utterances)} utterances merged)" if len(utterances) > 1 else ""
        self._log(f"📝 Transcribed prompt from {author}{merged}: '{transcript}'")

        # 3. A spoken command. "Stop" or "wait" on its own is for the
        #    pipeline, not the agent: the reply has already stopped, and
        #    "wait" keeps the unanswered words for what the user says next.
        #    "Next agent" or "switch me to Daisy" moves the focus instead of
        #    starting a turn, and leaves anything unanswered with the old one.
        say = partial(room.reply.say, is_current=is_current)
        command = await SPOKEN.answer(newest, SpokenContext(voice=room, heard=newest, say=say, turn=self,
                                                            log=self._log))
        if command is not None:
            trace.meta["outcome"] = command.outcome
            self.answered = not self._held
            return

        # 4. Execute LangGraph Agent with streaming, exactly as an untagged
        #    message typed in the focus's text channel would run. Concierge
        #    hosts every channel, so the turn runs as Concierge on that
        #    channel's session, and the router in graphs/main reaches the
        #    same specialist session a typed message would: context is 100%
        #    shared with text, and the voice room keeps none of its own.
        focus = room.focus()
        agent = room.agent()
        if not agent:
            self.answered = True  # nothing will ever answer them
            return
        channel = room.focus_channel(focus)
        if channel is None:
            self._log(f"#{focus.channel_name} can't be found in Discord; the turn has nowhere to go.")
            await room.reply.say(f"I can't reach {focus.label} right now.", room.agent_id, is_current=is_current)
            self.answered = True
            return
        self._log(f"🤖 Streaming LangGraph agent under #{getattr(channel, 'name', channel)} context...")
        room.turn_starting(focus)

        # Same session as the text channel (so context is shared), but
        # tagged as voice so token rows can tell spoken turns from typed
        # ones, and so the agent gets <voice_mode>.
        session = SessionManager().get_session(
            agent_id=room.agent_id,
            source="discord",
            channel=channel,
            surface="voice",
            output=room.turn_output(channel) if room.turn_output and channel is not None else None,
        )
        self.session_id = session.session_id
        self.answered = await room.reply.stream(
            agent, transcript, author, session, channel, self.speech,
            is_current=is_current, on_final=room.floor.set_reply, speaker=focus.agent_id,
        )
