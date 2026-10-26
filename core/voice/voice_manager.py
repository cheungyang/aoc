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
    turn.py     one turn, answered
    hooks       how features built on the room join in without the voice core
                knowing them: the inbox's briefings (core/voice/briefing/hook.py)

The VAD sink hands each utterance to the floor, and the floor starts and
stops turns here. A turn answers every unanswered utterance as one request,
under the current focus: exactly as an untagged message typed in the focus's
text channel would be answered, so voice and text share each agent's history.

The commands you give the room, typed or spoken, are core/runtime/commands/:
they reach it through VoiceControls (core/runtime/commands/ports.py), which
this class provides. The Discord runner, which hosts the voice room, builds
it, and forwards what Discord tells it: you moving between voice channels
(`member_moved`), and the bot being ready (`auto_join`).
"""
import asyncio
from typing import Any, Callable, List, Optional, Sequence, Tuple

import discord

from core.loaders.agents_loader import AgentsLoader
from core.runtime.foundation.output import TurnOutput
from core.voice.engines.stt import SpeechRecognizer, agent_vocabulary
from core.voice.hooks import VoiceHook
from core.voice.listening.enrollment import EnrollmentFlow
from core.voice.listening.floor import Floor
from core.voice.listening.gate_commands import gate_command, mark_from_reaction, voiceprint_command
from core.voice.listening.speaker_gate import SpeakerGate
from core.voice.listening.vad_sink import VADSink
from core.voice.room.connection import (
    VoiceConnection,
    is_channel,
    pick_voice_channel,
    voice_rooms,
)
from core.voice.room.focus import DEFAULT_LOBBY_CHANNEL, FocusDirectory, VoiceFocus
from core.voice.room.room_switch import FocusSwitcher, Say
from core.voice.speech.audio_queue import AudioStreamQueue
from core.voice.speech.reply import ReplyStreamer, TextOutputFactory
from core.voice.turn import VoiceTurn

# What the host passes for a turn's output in a text channel (foundation/output.py).
TurnOutputFactory = Callable[[Any], TurnOutput]


# `say_when_idle` waits for the pipeline to be idle: checked this often, for
# at most this long.
IDLE_POLL_S = 0.5
IDLE_MAX_WAIT_S = 300.0
# Auto-join waits this long after Discord is ready, for the guild cache to fill.
AUTO_JOIN_DELAY_S = 1.5


class VoiceManager:
    """Orchestrates one bot's voice: the parts above, and the turns between them."""

    def __init__(self, bot_runner, hooks: Sequence[VoiceHook] = (), *, text_output: TextOutputFactory,
                 turn_output: Optional[TurnOutputFactory] = None):
        """`text_output(channel)` is where a turn's reply is written in its text
        channel: the Discord runner, which hosts the voice room, passes its
        stream buffer. `turn_output(channel)` is the turn's output port
        (core/runtime/foundation/output.py), for its errors."""
        self.turn_output = turn_output
        self.agent_id = bot_runner.agent_id
        agent = AgentsLoader().get_agent(self.agent_id)
        # A copy: the loader caches agent configs, and this one is per-bot state.
        self.config = dict(agent.config.get("voice_config", {})) if agent else {}

        self.connection = VoiceConnection(bot_runner, make_sink=self._make_sink, on_joined=self._on_joined)
        self.audio_queue = AudioStreamQueue(self.connection, loop=getattr(bot_runner.bot, "loop", None))
        self.stt = SpeechRecognizer(vocabulary=self._stt_vocabulary())
        # Does it sound like you? Off until a join reads voice_config.speaker_gate.
        self.speaker_gate = SpeakerGate(transcribe=self.stt.transcribe)
        self.reply = ReplyStreamer(self.agent_id, self.config, self.audio_queue, self.connection, text_output)
        self.floor = Floor(self.stt, self.audio_queue, self.connection, turns=self, gate=self.speaker_gate)
        self.switcher = FocusSwitcher(self, self.agent_id)
        self.enrollment = EnrollmentFlow(
            self.speaker_gate,
            transcribe=self.stt.transcribe,
            say=lambda text: self.reply.say(text, self.agent_id),
            spawn=self._spawn,
            quiet=self._quiet_for_enrollment,
            log=self._log,
        )

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

    def agent(self) -> Any:
        """This bot's agent, as configured now (Concierge): what turns run as. None if it's gone."""
        return AgentsLoader().get_agent(self.agent_id)

    # --- joining and leaving ------------------------------------------------------

    async def join(self, room: Optional[str] = None, focus_channel=None) -> bool:
        """Joins `room` (default: `default_room()`) and, given a text channel,
        starts in its conversation: `!join` typed in #day-planning starts you
        with Daisy."""
        if not await self.connection.join(room or self.default_room()):
            return False
        if focus_channel is not None:
            target = self.directory().for_channel(focus_channel)
            if target is not None:
                self.set_focus(target)
        return True

    def default_room(self) -> str:
        """Where `!join` without a name and auto-join go: the first of this
        agent's voice rooms (connection.voice_rooms) that the server has."""
        agent = self.agent()
        return pick_voice_channel(self.connection.bot.guilds, voice_rooms(agent.config if agent else None))

    async def leave(self) -> None:
        await self.connection.leave()
        self._reset_focus()
        self.enrollment.reset()
        self._call_hooks("left")

    async def welcome(self) -> None:
        """You joined the voice channel: a new voice session, with the concierge.
        A hook may greet you with something other than "Concierge here." (the
        inbox's digest)."""
        directory = self.directory()
        self._reset_focus()
        self._configure_gate()
        self.enrollment.reset()
        self._focus = directory.lobby
        greetings = [line for line in self._call_hooks("joined") if line]
        await self.reply.say(greetings[0] if greetings else directory.greeting(directory.lobby), self.agent_id)

    def _on_joined(self, room) -> None:
        """The bot joined a room: nothing from the last voice session carries over."""
        self._reset_focus()
        self._configure_gate()
        self.enrollment.reset()

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

    def room(self):
        """The voice channel the bot is in, or None."""
        return self.connection.channel

    def has_voice_client(self) -> bool:
        """The bot holds a voice client: connected, or still connecting."""
        return bool(self.connection.voice_client)

    # --- following you: what the Discord runner forwards ----------------------------

    def hosts_voice(self) -> bool:
        """Whether this bot speaks in the voice room (Concierge's): voice_config
        `enabled`, read afresh. Its runner keeps the channel inbox's ✅ signal,
        answers `!inbox`, and keeps the voiceprint gate (`!enroll`,
        `!voiceprint`, `!gate`)."""
        agent = AgentsLoader().get_agent(self.agent_id)
        voice = agent.config.get("voice_config") if agent else None
        return isinstance(voice, dict) and bool(voice.get("enabled"))

    async def member_moved(self, member, before, after) -> None:
        """You moved between voice channels. Into one this bot hosts, it follows
        you and greets you there: each time you join is a new voice session,
        with the concierge in the lobby. A mute or deafen changes nothing."""
        agent = AgentsLoader().get_agent(self.agent_id)
        if not agent:
            return
        voice_config = agent.config.get("voice_config", {})
        if not voice_config.get("enabled"):
            return

        vc_targets = voice_rooms(agent.config)

        if after.channel and any(is_channel(after.channel.name, vc) for vc in vc_targets):
            if self.room() != after.channel:
                self._log(f"🏃 User {member.display_name} joined '{after.channel.name}'. Moving bot to follow user...")
                if not await self.join(after.channel.name):
                    return
            # You arrived (not a mute or deafen while already in the channel).
            if before.channel != after.channel:
                await self.welcome()

    def auto_join(self) -> None:
        """With voice_config `auto_join` on, joins `default_room()` once Discord
        is ready."""
        agent = AgentsLoader().get_agent(self.agent_id)
        if not agent:
            return
        voice_config = agent.config.get("voice_config", {})
        if not (voice_config.get("enabled") and voice_config.get("auto_join")):
            return

        async def _auto_join():
            bot = self.connection.bot
            await bot.wait_until_ready()
            await asyncio.sleep(AUTO_JOIN_DELAY_S)
            target = self.default_room()
            print(f"Agent {self.agent_id} auto-joining voice channel '{target}'...")
            await self.join(target)

        asyncio.create_task(_auto_join())

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
        For hooks; `switch_to_named` handles what you say."""
        return await self.switcher.switch_to(focus, self.directory(), say)

    async def switch_to_named(self, phrase: str, say: Say) -> bool:
        """"Switch me to …" (core/runtime/commands/spoken/switch.py): the agent
        or channel `phrase` names, or a near miss. False: it names none."""
        return await self.switcher.switch_to_named(phrase, say)

    def whereabouts(self) -> Tuple[str, str]:
        """"Where am I" (core/runtime/commands/spoken/where_am_i.py): where the
        room is, and the agent you're with, who says so."""
        focus = self.focus()
        return self.directory().whereabouts(focus), focus.agent_id

    def focus_channel(self, focus: VoiceFocus):
        """The Discord text channel `focus`'s turns are posted in, or None."""
        guild = getattr(self.connection.channel, "guild", None)
        for channel in getattr(guild, "text_channels", None) or []:
            if str(getattr(channel, "name", "")).lower() == focus.channel_name.lower():
                return channel
        return None

    def handed_off(self, event: dict):
        """The words named another agent (graphs/main's HANDOFF): the room
        follows the conversation to that agent's channel, as "switch to …"
        would, but with no greeting -- the agent's reply is the greeting.
        Returns the text channel the reply goes on in; None leaves the turn
        where it is."""
        directory = self.directory()
        focus = directory.for_channel(event.get("channel"))
        channel = self.focus_channel(focus) if focus is not None else None
        if channel is None:
            self._log(f"Hand-off to #{event.get('channel')}: no such focus or channel; the reply stays here.")
            return None
        self._log(f"🎧 Hand-off: now with {directory.who(focus)} in #{focus.channel_name}.")
        # The turn being answered is the one handing off; it must not cancel itself.
        self.set_focus(focus, interrupt=False)
        return channel

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

    async def next_agent(self, say: Say) -> bool:
        """"Next agent" (core/runtime/commands/spoken/next_agent.py): the first
        hook that knows where that is answers it. False: none does."""
        for hook in self.hooks:
            try:
                if await hook.next_agent(self, say):
                    return True
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._hook_failed(hook, "next_agent", e)
        return False

    def turn_starting(self, focus: VoiceFocus) -> None:
        """A turn is about to run in `focus`'s channel: every hook's `turn`."""
        self._call_hooks("turn", focus)

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

    # --- your voiceprint: the gate's commands, and `!enroll` ------------------------

    async def enroll(self, args: str = "", channel=None) -> str:
        """`!enroll`, typed in `channel`: starts taking your voiceprint; with
        "cancel", stops (listening/enrollment.py). Returns what to post back."""
        in_room = self.connection.is_connected() and self.someone_listening()
        return self.enrollment.command(args, channel, in_room=in_room)

    def _quiet_for_enrollment(self) -> None:
        """What you say next is a reading: any reply stops, and nothing is left unanswered."""
        if self.busy():
            self.interrupt("voiceprint enrollment")
        self.floor.clear()

    def voiceprint(self, args: str = "") -> str:
        """`!voiceprint`: your voiceprint and the gate's state. Returns what to post."""
        return voiceprint_command(self.speaker_gate, args)

    async def gate(self, args: str, channel) -> None:
        """`!gate`, typed in `channel` (`!gate review` posts there)."""
        await gate_command(self.speaker_gate, args, channel)

    def mark_review(self, message_id, emoji: str) -> bool:
        """✅ or ❌ on a `!gate review` post labels that utterance. True if it was one."""
        return mark_from_reaction(self.speaker_gate, message_id, emoji)

    # --- turns, as the floor starts and stops them -----------------------------

    def busy(self) -> bool:
        """A turn is being worked on, or its answer is still playing."""
        if self._current_task is not None and not self._current_task.done():
            return True
        return bool(self.audio_queue.is_playing())

    def is_current(self, turn_id: int) -> bool:
        """No newer turn has started, and turn `turn_id` wasn't interrupted."""
        return turn_id == self._active_turn

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

    async def _process_speech_turn(self, turn_id: int):
        """Answers every unanswered utterance as one request (turn.py), unless
        `!enroll` is taking them as readings."""
        async with self._lock:
            if turn_id != self._active_turn:
                return
            utterances = self.floor.unanswered()
            if not utterances:
                return
            enrollment = self.enrollment.active()
            if enrollment is not None:
                self.floor.forget(utterances)
                await self.enrollment.take(enrollment, utterances)
                return
            await VoiceTurn(self, turn_id, utterances).run()
