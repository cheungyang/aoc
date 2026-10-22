"""One bot's voice: wires the listening side to the speaking side, and runs turns.

    listening/  the user's side: the VAD cuts their speech into utterances, and
                the floor decides what each one means -- a turn, an interruption
                or listening noise -- and which words are still unanswered. The
                voiceprint gate scores whether each one sounds like you (Part 4:
                `voice_config.speaker_gate`, `!enroll`)
    engines/    speech-to-text and text-to-speech
    speech/     the agent's answer, to the text channel and the speaker
    room/       joining the voice room, and the focus: which agent and text
                channel the next turn belongs to ("switch me to Daisy")
    hooks       how features built on the room join in without the voice core
                knowing them: the inbox's briefings (core/inbox/voice_briefing.py)

The VAD sink hands each utterance to the floor, and the floor starts and
stops turns here. A turn answers every unanswered utterance as one request,
under the current focus: exactly as an untagged message typed in the focus's
text channel would be answered, so voice and text share each agent's history.
"""
import asyncio
from dataclasses import dataclass
from functools import partial
from typing import Any, List, Optional, Sequence

import discord

from core.loaders.agents_loader import AgentsLoader
from core.runtime.session_manager import SessionManager
from core.runtime.turn_trace import start_turn_trace
from . import settings
from .engines.stt import SpeechRecognizer, agent_vocabulary
from .hooks import VoiceHook
from .listening.enrollment import Enrollment, saved_message
from .listening.floor import Floor
from .listening.speaker_gate import SpeakerGate
from .listening.turn_taking import bare_stop, merge_transcripts
from .listening.utterance import Utterance
from .listening.vad_sink import VADSink
from .room.connection import VoiceConnection
from .room.focus import DEFAULT_LOBBY_CHANNEL, FocusDirectory, VoiceFocus
from .room.room_switch import FocusSwitcher, Say
from .speech.audio_queue import AudioStreamQueue
from .speech.reply import ReplyStreamer, TurnSpeech


# `say_when_idle` waits for the pipeline to be idle: checked this often, for
# at most this long.
IDLE_POLL_S = 0.5
IDLE_MAX_WAIT_S = 300.0


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

    def __init__(self, bot_runner, hooks: Sequence[VoiceHook] = ()):
        self.agent_id = bot_runner.agent_id
        agent = AgentsLoader().get_agent(self.agent_id)
        # A copy: the loader caches agent configs, and this one is per-bot state.
        self.config = dict(agent.config.get("voice_config", {})) if agent else {}

        self.connection = VoiceConnection(bot_runner, make_sink=self._make_sink, on_joined=self._on_joined)
        self.audio_queue = AudioStreamQueue(self.connection, loop=getattr(bot_runner.bot, "loop", None))
        self.stt = SpeechRecognizer(vocabulary=self._stt_vocabulary())
        # Does it sound like you? Off until a join reads voice_config.speaker_gate.
        self.speaker_gate = SpeakerGate(transcribe=self.stt.transcribe)
        self.reply = ReplyStreamer(self.agent_id, self.config, self.audio_queue, self.connection)
        self.floor = Floor(self.stt, self.audio_queue, self.connection, turns=self, gate=self.speaker_gate)
        self.switcher = FocusSwitcher(self, self.agent_id)

        self._lock = asyncio.Lock()
        self._active_turn = 0
        self._current_task = None
        # Where the next turn goes. None means the lobby (see `focus`).
        self._focus: Optional[VoiceFocus] = None
        self._side_tasks = set()
        # Features built on the room (see hooks.py), called in this order.
        self.hooks: List[VoiceHook] = list(hooks)
        self._idle_line: Optional[asyncio.Lock] = None

    def _log(self, message: str) -> None:
        print(f"[VoiceManager:{self.agent_id}] {message}")

    def _make_sink(self) -> VADSink:
        return VADSink(self.floor, loop=self.connection.bot.loop, config=self.config,
                       owners=self.speaker_gate.owner_ids)

    def _stt_vocabulary(self) -> List[str]:
        try:
            return agent_vocabulary(AgentsLoader())
        except Exception as e:
            self._log(f"Could not list agents for the STT vocabulary: {e}")
            return []

    # --- joining and leaving ------------------------------------------------------

    async def join(self, room: Optional[str] = None, focus_channel=None) -> bool:
        """Joins `room` (default: the voice room in voice_config) and, given a
        text channel, starts in its conversation: `!join` typed in
        #day-planning starts you with Daisy."""
        if not await self.connection.join(room or self.voice_room()):
            return False
        if focus_channel is not None:
            target = self.directory().for_channel(focus_channel)
            if target is not None:
                self.set_focus(target)
        return True

    async def leave(self) -> None:
        await self.connection.leave()
        self._reset_focus()
        self.speaker_gate.enrollment = None
        self._call_hooks("left")

    async def welcome(self) -> None:
        """You joined the voice channel: a new voice session, with the concierge.
        A hook may greet you with something other than "Concierge here." (the
        inbox's digest)."""
        directory = self.directory()
        self._reset_focus()
        self._configure_gate()
        self.speaker_gate.enrollment = None
        self._focus = directory.lobby
        greetings = [line for line in self._call_hooks("joined") if line]
        await self.reply.say(greetings[0] if greetings else directory.greeting(directory.lobby), self.agent_id)

    def _on_joined(self, room) -> None:
        """The bot joined a room: nothing from the last voice session carries over."""
        self._reset_focus()
        self._configure_gate()
        self.speaker_gate.enrollment = None

    def _configure_gate(self) -> None:
        """The voiceprint gate's settings, read afresh from voice_config on every
        join: switching `speaker_gate` takes effect on your next join."""
        try:
            agent = AgentsLoader().get_agent(self.agent_id)
            config = agent.config.get("voice_config") if agent else None
        except Exception:
            config = None
        self.speaker_gate.configure(config if isinstance(config, dict) else self.config)

    def _reset_focus(self) -> None:
        """Back to the lobby (on first use), with nothing left unanswered."""
        self._focus = None
        self.floor.clear()

    def voice_room(self) -> Optional[str]:
        """The one voice channel, from voice_config (`voice_channel`), if set."""
        room = self.config.get("voice_channel")
        if isinstance(room, str) and room.strip():
            return room.strip()
        rooms = self.config.get("voice_channels")
        if isinstance(rooms, list) and rooms and isinstance(rooms[0], str):
            return rooms[0]
        return None

    # --- the focus: which conversation the room is in ------------------------------

    def directory(self) -> FocusDirectory:
        """Every focus there is, from the agent configs as they are now."""
        lobby = self.config.get("lobby_channel")
        return FocusDirectory.build(
            AgentsLoader(),
            voice_host=self.agent_id,
            lobby_channel=lobby if isinstance(lobby, str) and lobby.strip() else DEFAULT_LOBBY_CHANNEL,
        )

    def focus(self) -> VoiceFocus:
        """Where the next turn goes: the lobby (#general) until you switch."""
        if self._focus is None:
            self._focus = self.directory().lobby
        return self._focus

    def set_focus(self, focus: VoiceFocus, *, interrupt: bool = True) -> None:
        """Moves the focus. Nothing said to the old one carries over: a turn in
        flight is stopped (unless it is the turn asking: `interrupt=False`), and
        its unanswered words and last reply are forgotten."""
        previous, self._focus = self._focus, focus
        if interrupt and self.busy():
            self.interrupt(f"focus moved to {focus.label}")
        self.floor.clear()
        if previous is None or previous.key != focus.key:
            self._spawn(self._announce_focus(focus))

    async def arrive(self, focus: VoiceFocus, say: Say) -> None:
        """After a switch's greeting: whatever the hooks have for you there,
        said in the focus agent's own voice."""
        async def speak(text: str) -> None:
            await say(text, focus.agent_id)
            # A lone "yes" over the end of it is an answer, as after any reply.
            self.floor.set_reply(text)

        for hook in self.hooks:
            try:
                await hook.arrived(self, focus, speak)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._hook_failed(hook, "arrived", e)

    async def switch_to(self, focus: VoiceFocus, say: Say) -> bool:
        """Moves the focus as "switch to …" would: the greeting, then `arrive`.
        For hooks; the switcher handles what you say."""
        return await self.switcher.switch_to(focus, self.directory(), say)

    def focus_channel(self, focus: VoiceFocus):
        """The Discord text channel `focus`'s turns are posted in, or None."""
        guild = getattr(self.connection.channel, "guild", None)
        for channel in getattr(guild, "text_channels", None) or []:
            if str(getattr(channel, "name", "")).lower() == focus.channel_name.lower():
                return channel
        return None

    async def _announce_focus(self, focus: VoiceFocus) -> None:
        """Shows where the room is now, for a glance at Discord: a line in the
        voice channel's own chat (which holds no conversation, only this log),
        and the bot's status."""
        status = f"{self.directory().who(focus)} · #{focus.channel_name}"
        try:
            room = self.connection.channel
            if room is not None:
                await room.send(f"🎧 Now with {status}")
        except Exception as e:
            self._log(f"Could not post the focus change in the voice channel's chat: {e}")
        try:
            await self.connection.bot.change_presence(
                activity=discord.Activity(type=discord.ActivityType.listening, name=status))
        except Exception as e:
            self._log(f"Could not update the bot's status: {e}")

    # --- for hooks: speaking up between turns ---------------------------------------

    def say_when_idle(self, line: str) -> None:
        """Says a concierge line once nobody is talking and no turn is running."""
        self._spawn(self._say_when_idle(line))

    async def _say_when_idle(self, line: str) -> None:
        """One line at a time; dropped if you've left, or after a long wait."""
        if self._idle_line is None:
            self._idle_line = asyncio.Lock()
        async with self._idle_line:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + IDLE_MAX_WAIT_S
            while self.busy() or not self.floor.quiet():
                if loop.time() > deadline:
                    self._log(f"📣 Dropped a line after waiting {IDLE_MAX_WAIT_S:.0f}s: {line!r}")
                    return
                await asyncio.sleep(IDLE_POLL_S)
            if not self.someone_listening():
                return
            self._log(f"📣 {line}")
            await self.reply.say(line, self.agent_id)

    def someone_listening(self) -> bool:
        """A person, not a bot, is in the voice room."""
        members = getattr(self.connection.channel, "members", None) or []
        return any(not getattr(member, "bot", False) for member in members)

    # --- calling hooks ------------------------------------------------------------

    def _call_hooks(self, point: str, *args) -> List[Any]:
        """Each hook's `point(self, *args)`, in order; one that raises is skipped."""
        results = []
        for hook in self.hooks:
            try:
                results.append(getattr(hook, point)(self, *args))
            except Exception as e:
                self._hook_failed(hook, point, e)
        return results

    async def _hooks_handle(self, transcript: str, say: Say) -> bool:
        """Whether a hook answered the utterance; the first that does wins."""
        for hook in self.hooks:
            try:
                if await hook.handle(self, transcript, say):
                    return True
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._hook_failed(hook, "handle", e)
        return False

    def _hook_failed(self, hook: VoiceHook, point: str, error: Exception) -> None:
        self._log(f"Voice hook {type(hook).__name__}.{point} failed: {type(error).__name__}: {error}")

    def _spawn(self, coro) -> None:
        """Runs `coro` alongside the turn, keeping a reference until it's done."""
        try:
            task = asyncio.get_running_loop().create_task(coro)
        except RuntimeError:
            coro.close()
            return
        self._side_tasks.add(task)
        task.add_done_callback(self._side_tasks.discard)

    # --- your voiceprint: `!enroll` -----------------------------------------------

    async def enroll(self, args: str = "", channel=None) -> str:
        """`!enroll`, typed in `channel`: starts taking your voiceprint; with
        "cancel", stops. Returns what to post back."""
        gate, action = self.speaker_gate, (args or "").strip().lower()
        if action in ("cancel", "stop"):
            if gate.enrollment is None:
                return "No enrollment is in progress."
            gate.enrollment = None
            return "Enrollment cancelled."
        if action:
            return "Type `!enroll` to take your voiceprint, or `!enroll cancel` to stop."
        if not self.connection.is_connected() or not self.someone_listening():
            return "Join the voice channel first, then type `!enroll` again."
        if gate.model is None:
            return (f"The speaker model `{settings.SPEAKER_MODEL_FILE}` isn't installed: run "
                    f"`scripts/provision_assets.py --stage runtime --only speaker`.")
        enrollment = Enrollment(channel)
        gate.enrollment = enrollment
        if self.busy():
            self.interrupt("voiceprint enrollment")
        self.floor.clear()
        self._log("🎙️ Voiceprint enrollment started.")
        self._spawn(self.reply.say(enrollment.opening_line(), self.agent_id))
        return enrollment.instructions()

    def _enrolling(self) -> Optional[Enrollment]:
        """The enrollment in progress, unless it has timed out."""
        enrollment = self.speaker_gate.enrollment
        if enrollment is not None and enrollment.expired():
            self._log("🎙️ Voiceprint enrollment timed out.")
            self.speaker_gate.enrollment = enrollment = None
        return enrollment

    async def _take_readings(self, enrollment: Enrollment, utterances: List[Utterance]) -> None:
        """What you said during `!enroll`: readings for your voiceprint, not requests.
        They're taken before anything is awaited, so a reading that interrupts
        the bot's "Got it" still counts."""
        if not any([enrollment.take(u) for u in utterances]):
            # Too short for a reading: "cancel", "wait", a word or two, or noise.
            text = (await self.stt.transcribe(utterances[-1])).text.strip()
            stop = bare_stop(text)
            if stop == "cancel":
                self.speaker_gate.enrollment = None
                self._log("🎙️ Voiceprint enrollment cancelled.")
                await self._post(enrollment.channel, "Enrollment cancelled.")
                await self.reply.say("Okay, enrollment cancelled.", self.agent_id)
            elif text and stop is None:
                await self.reply.say(enrollment.retry_line(), self.agent_id)
            return
        if not enrollment.done:
            await self.reply.say(enrollment.progress_line(), self.agent_id)
            return
        self.speaker_gate.enrollment = None
        # Not part of the turn, which the next thing you say cancels.
        self._spawn(self._save_voiceprint(enrollment))

    async def _save_voiceprint(self, enrollment: Enrollment) -> None:
        try:
            voiceprint = await self.speaker_gate.enroll(enrollment.readings)
        except Exception as e:
            self._log(f"🎙️ Could not make the voiceprint: {type(e).__name__}: {e}")
            await self._post(enrollment.channel, f"⚠️ I couldn't make your voiceprint ({e}). "
                                                 f"Type `!enroll` to try again.")
            await self.reply.say("Sorry, I couldn't make your voiceprint. Type enroll to try again.", self.agent_id)
            return
        self._log(f"🎙️ Voiceprint saved: {voiceprint.readings} readings, consistency {voiceprint.consistency:.2f}.")
        await self._post(enrollment.channel, saved_message(voiceprint, self.speaker_gate.mode))
        await self.reply.say("Done. Your voiceprint is saved.", self.agent_id)

    async def _post(self, channel, text: str) -> None:
        if channel is None:
            return
        try:
            await channel.send(text)
        except Exception as e:
            self._log(f"Could not post in #{getattr(channel, 'name', channel)}: {e}")

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
            enrollment = self._enrolling()
            if enrollment is not None:
                self.floor.forget(utterances)
                await self._take_readings(enrollment, utterances)
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

                def finish_trace():
                    # The voiceprint gate scores alongside the turn: by the time
                    # its answer has been heard, each utterance has its verdict.
                    speaker = [u.gate.summary() for u in utterances if u.gate is not None]
                    if speaker:
                        trace.meta["speaker"] = speaker
                    trace.finish(session_id=turn.session_id)

                turn.speech.finish_when_heard(finish_trace, cancelled=cancelled)

    async def _answer(self, turn: _Turn):
        """The turn's transcripts, then a stop, a focus switch or the agent's reply."""
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

        # 4. A hook's own command ("next agent"), or "switch me to Daisy":
        #    either moves the focus instead of starting a turn, and leaves
        #    anything unanswered behind with the old focus.
        say = partial(self.reply.say, is_current=is_current)
        if await self._hooks_handle(newest, say):
            trace.meta["outcome"] = "hook"
            turn.answered = True
            return
        if await self.switcher.handle(newest, say=say):
            trace.meta["outcome"] = "focus_switch"
            turn.answered = True
            return

        # 5. Execute LangGraph Agent with streaming, exactly as an untagged
        #    message typed in the focus's text channel would run. Concierge
        #    hosts every channel, so the turn runs as Concierge on that
        #    channel's session, and the router in graphs/main reaches the
        #    same specialist session a typed message would: context is 100%
        #    shared with text, and the voice room keeps none of its own.
        focus = self.focus()
        agent = AgentsLoader().get_agent(self.agent_id)
        if not agent:
            turn.answered = True  # nothing will ever answer them
            return
        channel = self.focus_channel(focus)
        if channel is None:
            self._log(f"#{focus.channel_name} can't be found in Discord; the turn has nowhere to go.")
            await self.reply.say(f"I can't reach {focus.label} right now.", self.agent_id, is_current=is_current)
            turn.answered = True
            return
        self._log(f"🤖 Streaming LangGraph agent under #{getattr(channel, 'name', channel)} context...")
        self._call_hooks("turn", focus)

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
            is_current=is_current, on_final=self.floor.set_reply, speaker=focus.agent_id,
        )
