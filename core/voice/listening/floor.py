"""Who has the floor: what the user's speech means while the agent is busy.

The VAD sink posts each utterance's life here -- opened, audio, paused,
resumed, finished or discarded -- on the event loop, in the order the audio
arrived. The floor streams it to the recognizer and decides what it is:

  * a new turn, when the agent is idle;
  * an interruption: barge-in is two-stage (B19). Speech over the reply first
    ducks it, and stops it only once the words confirm an interruption;
  * listening noise ("okay", "mm-hmm"), which lets the reply carry on.

It also keeps the utterances whose turn hasn't been answered yet: a turn
cancelled before its answer was heard hands its words to the next one.

Every finished utterance also goes to the voiceprint gate (speaker_gate.py)
with what it did -- started a turn, joined waiting words, stopped the reply,
or ended over it. In shadow mode the gate only scores and records it.
"""
import asyncio
import time
from typing import Dict, List, Optional, Protocol

from core.voice import settings
from core.voice.listening.gate_log import Effect
from core.voice.listening.turn_taking import confirms_barge_in, is_backchannel
from core.voice.listening.utterance import Utterance


class Turns(Protocol):
    """What the floor needs from whoever runs the turns (VoiceManager)."""

    def busy(self) -> bool:
        """A turn is being worked on, or its answer is still playing."""

    def begin_turn(self) -> None:
        """Starts a turn that answers `Floor.unanswered()`."""

    def interrupt(self, reason: str) -> None:
        """Stops the reply and abandons the turn in progress."""


class Floor:
    def __init__(self, stt, audio, connection, turns: Turns, gate: Optional[object] = None):
        self.stt = stt
        self.audio = audio  # the AudioStreamQueue: is_playing, duck, unduck
        self.connection = connection
        self.turns = turns
        self.gate = gate    # the SpeakerGate: observe(utterance, effect)
        # Utterances the user is still speaking, by id.
        self._speaking: Dict[int, Utterance] = {}
        # Utterances whose turn hasn't been answered yet, oldest first. A turn
        # cancelled before its answer was heard leaves them here, and the next
        # turn sends them together with the new words (B19).
        self._pending: List[Utterance] = []
        # The reply of the turn in progress, once it is final: a question at its
        # end makes a lone "yes" over its audio an answer, not a backchannel.
        self._reply = ""
        self._overlap_tasks = set()

    def _log(self, message: str) -> None:
        print(f"[VoiceManager:{self.connection.agent_id}] {message}")

    # --- the VAD sink's hooks ---------------------------------------------------

    def utterance_opened(self, utterance: Utterance):
        """The user started talking: transcribe as they speak, and duck the agent if it's talking."""
        if self._ignored_speaker(utterance.user):
            return
        self._speaking[utterance.id] = utterance
        if self.audio.is_playing():
            # Stage one of barge-in: lower the agent's voice, stop nothing yet.
            utterance.ducked = True
            self.audio.duck(settings.BARGE_IN_DUCK_VOLUME)
        self.stt.open(utterance, on_text=self._on_text)

    def utterance_audio(self, utterance: Utterance, pcm: bytes):
        if utterance.id in self._speaking:
            self.stt.audio(utterance, pcm)

    def utterance_paused(self, utterance: Utterance):
        if utterance.id in self._speaking:
            self.stt.pause(utterance)

    def utterance_resumed(self, utterance: Utterance):
        if utterance.id in self._speaking:
            self.stt.resume(utterance)

    def utterance_discarded(self, utterance: Utterance):
        """Too short to be speech: forget it, and give the agent its voice back."""
        if self._speaking.pop(utterance.id, None) is None:
            return
        self.stt.discard(utterance)
        self._restore_volume()

    def utterance_finished(self, utterance: Utterance):
        """The user's turn ended: start a turn, or work out what overlapping speech meant."""
        if self._speaking.pop(utterance.id, None) is None:
            return
        self.stt.finish(utterance)
        utterance.finished_at = time.monotonic()
        if utterance.confirmed or not self.turns.busy():
            self._observe(utterance, Effect.BARGE_IN if utterance.confirmed
                          else Effect.MERGE if self._waiting() else Effect.TURN)
            self._restore_volume()
            self._hand_over(utterance)
            return
        self._observe(utterance, Effect.OVERLAP)
        task = asyncio.create_task(self._resolve_overlap(utterance))
        self._overlap_tasks.add(task)
        task.add_done_callback(self._overlap_tasks.discard)

    # --- for the turn -------------------------------------------------------------

    def unanswered(self) -> List[Utterance]:
        """What the next turn answers, oldest first, minus anything too old to still matter."""
        cutoff = time.monotonic() - settings.UNANSWERED_MAX_AGE_S
        self._pending = [u for u in self._pending if u.started_at >= cutoff]
        return list(self._pending)

    def forget(self, utterances: List[Utterance]):
        self._pending = [u for u in self._pending if u not in utterances]

    def quiet(self) -> bool:
        """Nobody is mid-utterance and nothing is waiting to be answered."""
        return not self._speaking and not self._pending

    def set_reply(self, text: str):
        """The reply of the turn in progress, once final ("" when a turn starts)."""
        self._reply = text

    def clear(self):
        """Forgets what the current conversation left unanswered, and its last
        reply: the focus moved, and none of it belongs to the new one."""
        self._pending = []
        self._reply = ""

    # --- deciding -------------------------------------------------------------------

    def _ignored_speaker(self, user) -> bool:
        """The bot never reacts to its own voice, or to another bot's."""
        if user is not None and getattr(user, "bot", False) is True:
            return True
        own = self.connection.own_user()
        return own is not None and user is not None and getattr(user, "id", None) == getattr(own, "id", None)

    def _waiting(self) -> bool:
        """Earlier words are still waiting for an answer: new ones join them."""
        cutoff = time.monotonic() - settings.UNANSWERED_MAX_AGE_S
        return any(u.started_at >= cutoff for u in self._pending)

    def _observe(self, utterance: Utterance, effect: Effect):
        """Hands a finished utterance to the voiceprint gate; it never holds up the floor."""
        if self.gate is None:
            return
        try:
            self.gate.observe(utterance, effect)
        except Exception as e:
            self._log(f"The voiceprint gate failed on utterance #{utterance.id}: {type(e).__name__}: {e}")

    def _restore_volume(self):
        if not any(u.ducked for u in self._speaking.values()):
            self.audio.unduck()

    def _hand_over(self, utterance: Utterance):
        self._pending.append(utterance)
        self.turns.begin_turn()

    def _on_text(self, utterance: Utterance):
        """Stage two of barge-in: once the words confirm an interruption, stop the agent."""
        if utterance.confirmed or utterance.id not in self._speaking or not self.turns.busy():
            return
        text = utterance.text_so_far()
        if confirms_barge_in(text):
            utterance.confirmed = True
            self.turns.interrupt(f"heard {text!r}")

    async def _resolve_overlap(self, utterance: Utterance):
        """Speech that ended over a busy agent without confirming a barge-in.

        Its final words decide. Listening noise ("okay", "mm-hmm") is dropped and
        the reply carries on -- unless the reply ended on a question, when a lone
        "yes" is the answer. Anything else interrupts and becomes the next turn,
        merged with the question it cut off if that was never answered.
        """
        transcript = await self.stt.transcribe(utterance)
        text = transcript.text.strip()
        if not text or (is_backchannel(text) and not self._reply.rstrip().endswith("?")):
            self._log(f"Letting the reply continue over {text!r}.")
            self._restore_volume()
            return
        if self.turns.busy():
            self.turns.interrupt(f"heard {text!r}")
        self._hand_over(utterance)
