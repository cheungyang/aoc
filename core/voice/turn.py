"""One voice turn: everything still unanswered, answered as one request.

The floor decides when a turn is due and VoiceManager starts it (`begin_turn`);
a `VoiceTurn` answers it:

    1. the cue that the agent heard you
    2. the transcripts, merged: they were streamed while you spoke
    3. noise, or "stop" / "wait" on its own: for the pipeline, not the agent
    4. a hook's own command ("next agent"), or a switch ("switch me to Daisy")
    5. the agent's reply, run exactly as an untagged message typed in the
       focus's text channel would be: on that channel's session, so voice and
       text share each agent's history

Every turn is traced (core/runtime/observability/turn_trace.py) from the newest
utterance's last voiced frame until its answer is first heard.
"""
import asyncio
from functools import partial
from typing import Any, List, Optional, Protocol

from core.runtime.foundation.sessions import SessionManager
from core.runtime.observability.turn_trace import Outcome, Stage, start_turn_trace
from core.util.error_util import format_error_message
from core.voice.engines.stt import SpeechRecognizer
from core.voice.foundation.utterance import Utterance
from core.voice.listening.floor import Floor
from core.voice.listening.turn_taking import bare_stop, merge_transcripts
from core.voice.room.focus import VoiceFocus
from core.voice.room.room_switch import FocusSwitcher, Say
from core.voice.speech.reply import ReplyStreamer, TurnSpeech


class TurnRoom(Protocol):
    """What a turn needs from the voice room (VoiceManager)."""

    agent_id: str              # this bot's agent: the voice host, whose turn it runs
    stt: SpeechRecognizer
    reply: ReplyStreamer
    floor: Floor
    switcher: FocusSwitcher

    def is_current(self, turn_id: int) -> bool:
        """No newer turn has started, and the turn wasn't interrupted."""

    def focus(self) -> VoiceFocus:
        """Where the turn goes."""

    def focus_channel(self, focus: VoiceFocus):
        """The Discord text channel `focus`'s turns are posted in, or None."""

    def agent(self) -> Any:
        """The agent the turn runs as (this bot's), or None."""

    async def offer_to_hooks(self, transcript: str, say: Say) -> bool:
        """Whether a hook answered the utterance."""

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
        self.session_id: Optional[str] = None

    def _log(self, message: str) -> None:
        print(f"[VoiceManager:{self.room.agent_id}] {message}")

    def is_current(self) -> bool:
        return self.room.is_current(self.id)

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
        """The turn's transcripts, then a stop, a focus switch or the agent's reply."""
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

        # 3. "Stop" or "wait" on its own is for the pipeline, not the agent:
        #    the reply has already stopped. "Wait" keeps the unanswered
        #    words for what the user says next; "stop" drops them.
        stop = bare_stop(newest)
        if stop is not None:
            self._log(f"✋ Heard {newest!r}: stopping, "
                      + ("keeping the request for what comes next." if stop == "hold" else "request dropped."))
            trace.meta["outcome"] = Outcome.STOP_HOLD if stop == "hold" else Outcome.STOP_CANCEL
            if stop == "hold":
                room.floor.forget(utterances[-1:])
            else:
                self.answered = True
            return

        user = utterances[-1].user
        author = getattr(user, "display_name", str(user)) if user else "Speaker"
        merged = f" ({len(utterances)} utterances merged)" if len(utterances) > 1 else ""
        self._log(f"📝 Transcribed prompt from {author}{merged}: '{transcript}'")

        # 4. A hook's own command ("next agent"), or "switch me to Daisy":
        #    either moves the focus instead of starting a turn, and leaves
        #    anything unanswered behind with the old focus.
        say = partial(room.reply.say, is_current=is_current)
        if await room.offer_to_hooks(newest, say):
            trace.meta["outcome"] = Outcome.HOOK
            self.answered = True
            return
        if await room.switcher.handle(newest, say=say):
            trace.meta["outcome"] = Outcome.FOCUS_SWITCH
            self.answered = True
            return

        # 5. Execute LangGraph Agent with streaming, exactly as an untagged
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
        )
        self.session_id = session.session_id
        self.answered = await room.reply.stream(
            agent, transcript, author, session, channel, self.speech,
            is_current=is_current, on_final=room.floor.set_reply, speaker=focus.agent_id,
        )
