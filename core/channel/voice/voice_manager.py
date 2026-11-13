"""One bot's voice: wires the listening side to the speaking side, and runs turns.

    listening/  the user's side: the VAD cuts their speech into utterances, and
                the floor decides what each one means -- a turn, an interruption
                or listening noise -- and which words are still unanswered. The
                voiceprint gate scores whether each one sounds like you
                (`voice_config.speaker_gate`, `!enroll`)
    engines/    speech-to-text and text-to-speech
    speech/     the agent's answer, to the text channel and the speaker
    room/       joining the voice room, and the focus: which agent and text
                channel the next turn belongs to ("switch me to Daisy")
    turn.py     one turn, answered
    hooks       how features built on the room join in without the voice core
                knowing them: the inbox's briefings (core/channel/voice/briefing/hook.py)

The VAD sink hands each utterance to the floor, and the floor starts and
stops turns here. A turn answers every unanswered utterance as one request,
under the current focus: exactly as an untagged message typed in the focus's
text channel would be answered, so voice and text share each agent's history.

Greeting you. Every time you arrive -- you join, or the bot (re)joins a room
you're already in, after a restart or a reconnect -- the room greets you, but
only once you can hear it: your client hears nothing until it has joined the
call's end-to-end encryption (room/connection.py `until_audible`), and a
greeting started at once was heard mid-sentence or not at all. "Hello?" over
the greeting doesn't cut it off, and before anything else in the session it
gets the greeting again (core/runtime/commands/spoken/presence.py). A restart
within RESUME_FOCUS_S of your last turn puts you back where you were.

Hand-offs. When the room arrives at an agent with a hand-off waiting for it
-- the brief another agent's <switch> or <pass> parked there
(core/storage/handoff_capsules.py) -- that agent speaks first, with no words
from you: it opens with what it was handed (`arrive`), and whatever else it
left you comes after that.

The commands you give the room, typed or spoken, are core/runtime/commands/:
they reach it through VoiceControls (core/runtime/commands/ports.py), which
this class provides. The Discord runner, which hosts the voice room, builds
it, and forwards what Discord tells it: you moving between voice channels
(`member_moved`), and the bot being ready (`auto_join`).
"""
import asyncio
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import discord
import numpy as np

from core.loaders.agents_loader import AgentsLoader
from core.runtime.foundation.output import TurnOutput
from core.runtime.foundation.sessions import SessionManager
from core.runtime.history.handoff import arrival_message
from core.runtime.observability.turn_trace import Outcome, bind_turn_trace, current_turn_trace, start_turn_trace
from core.storage.handoff_capsules import HandoffCapsuleStore
from core.storage.sessions import SessionStore
from core.channel.voice.engines.stt import SpeechRecognizer, agent_vocabulary
from core.channel.voice.foundation import settings
from core.channel.voice.hooks import VoiceHook
from core.channel.voice.listening.enrollment import EnrollmentFlow
from core.channel.voice.listening.floor import Floor
from core.channel.voice.listening.gate_commands import gate_command, mark_from_reaction, voiceprint_command
from core.channel.voice.listening.speaker_gate import VOICING_FRAME, SpeakerGate, voiced_share
from core.channel.voice.listening.vad_sink import VADSink
from core.channel.voice.room.connection import (
    VoiceConnection,
    is_channel,
    pick_voice_channel,
    voice_rooms,
)
from core.channel.voice.room.focus import DEFAULT_LOBBY_CHANNEL, FocusDirectory, VoiceFocus
from core.channel.voice.room.room_switch import FocusSwitcher, Say
from core.channel.voice.speech.audio_queue import AudioStreamQueue
from core.channel.voice.speech.reply import ReplyStreamer, TextOutputFactory, TurnSpeech
from core.channel.voice.turn import VoiceTurn

# What the host passes for a turn's output in a text channel (foundation/output.py).
TurnOutputFactory = Callable[[Any], TurnOutput]


# `say_when_idle` waits for the pipeline to be idle: checked this often, for
# at most this long.
IDLE_POLL_S = 0.5
IDLE_MAX_WAIT_S = 300.0
# Auto-join waits this long after Discord is ready, for the guild cache to fill.
AUTO_JOIN_DELAY_S = 1.5
# "Hanging up": the goodbye, said in the voice of the agent you're with. The
# bot waits for it to finish playing -- checked this often, for at most this
# long -- before it disconnects you and leaves.
HANG_UP_LINE = "Talk soon. Bye!"
HANG_UP_POLL_S = 0.1
HANG_UP_WAIT_S = 6.0
# Said in the voice channel's chat when Discord won't let the bot disconnect you.
NO_MOVE_MEMBERS = ("📴 Hung up, but I couldn't disconnect you: I need the **Move Members** permission "
                   "in this voice channel.")
# Where the room keeps its last focus across a restart (`rejoined`), in the
# voice data dir beside memory.db.
FOCUS_FILE = "room_focus.json"

# Where the session's greeting is (`_Greeting.state`).
WAITING = "waiting"    # for you to be able to hear it
PLAYING = "playing"
HEARD = "heard"        # played to the end
CUT = "cut"            # cut off, or dropped before it played
SKIPPED = "skipped"    # you spoke first: said, it would only talk over you


@dataclass
class _Greeting:
    """This voice session's greeting, and where it is."""
    line: str
    state: str = WAITING
    event: str = "welcome"  # "welcome" when you joined, "rejoin" when the bot did
    how: Optional[str] = None
    waited_ms: Optional[int] = None


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
        # The focus before the last move, by key, and what has happened since:
        # where "go back" returns, and whether it still may.
        self._previous_key: Optional[str] = None
        self._moved_at = 0.0
        self._turns_since_move = 0
        # When each focus last had a turn (time.monotonic()): whether a move
        # away from it leaves a conversation.
        self._last_turn_at: Dict[str, float] = {}
        # How to undo a hand-off this turn made out of a conversation, said
        # once the named agent's reply is (see `handed_off`).
        self._undo_hint: Optional[str] = None
        self._side_tasks = set()
        # Features built on the room (see hooks.py), called in this order.
        self.hooks: List[VoiceHook] = list(hooks)
        self._idle_line: Optional[asyncio.Lock] = None
        # "Hanging up" is under way: speech starts no turn until the next join.
        self._hanging_up = False
        # This voice session's greeting (`welcome`), and whether you've said
        # anything since but "hello?": until you have, "hello?" gets it again.
        self._greeting: Optional[_Greeting] = None
        self._floor_taken = False

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
        """Leaves the voice room (`!leave`, or hanging up): nothing of this
        voice session carries over, not even across a restart."""
        await self.connection.leave()
        self._reset_focus()
        self.enrollment.reset()
        self._hanging_up = False
        self._forget_focus()
        self._call_hooks("left")

    async def hang_up(self, say: Say, member=None) -> bool:
        """"Hanging up" (core/runtime/commands/spoken/hang_up.py): ends the
        voice session as a phone call ends. The agent you're with says
        goodbye; once that has played, `member` -- who said it -- is
        disconnected from the voice channel, and the bot leaves. From the
        words on, speech starts no turn. It runs alongside the turn that heard
        it, so a barge-in, which cancels that turn, can't stop it halfway."""
        if not self._hanging_up:
            self._hanging_up = True
            self._spawn(self._hang_up(say, member))
        return True

    async def _hang_up(self, say: Say, member) -> None:
        try:
            try:
                await say(HANG_UP_LINE, self.focus().agent_id)
                await self._until_said(HANG_UP_WAIT_S)
            except Exception as e:
                self._log(f"📴 The goodbye failed ({type(e).__name__}: {e}); hanging up all the same.")
            await self._disconnect_member(member)
        finally:
            await self.leave()

    async def _until_said(self, timeout: float) -> None:
        """Until nothing is playing or queued, for at most `timeout` seconds:
        `say` returns once its line is queued, not heard. It takes two quiet
        checks in a row, since the queue is empty for a moment as the cue
        gives way to the line (speech/audio_queue.py `_play`)."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        quiet = 0
        while loop.time() < deadline:
            quiet = 0 if self.audio_queue.is_playing() else quiet + 1
            if quiet >= 2:
                return
            await asyncio.sleep(HANG_UP_POLL_S)

    async def _disconnect_member(self, member) -> None:
        """Takes `member` out of the voice channel, as a call ends on both
        sides: only a person, and only from this room. Discord allows it with
        the Move Members permission; without it, the voice channel's chat says
        so, and the bot leaves all the same."""
        room = self.room()
        where = getattr(getattr(member, "voice", None), "channel", None)
        if member is None or getattr(member, "bot", False) or room is None or where != room:
            return
        who = getattr(member, "display_name", member)
        try:
            await member.move_to(None)
        except discord.Forbidden:
            self._log(f"📴 Could not disconnect {who}: the bot lacks the Move Members permission.")
            await self._note_in_room(NO_MOVE_MEMBERS)
            return
        except Exception as e:
            self._log(f"📴 Could not disconnect {who}: {type(e).__name__}: {e}")
            return
        self._log(f"📴 Disconnected {who} from '{getattr(room, 'name', room)}'.")
        await self._note_in_room("📴 Hung up.")

    async def _note_in_room(self, line: str) -> None:
        """A line in the voice channel's own chat, the room's log (see `_announce_focus`)."""
        try:
            room = self.connection.channel
            if room is not None:
                await room.send(line)
        except Exception as e:
            self._log(f"Could not post in the voice channel's chat: {e}")

    async def welcome(self, member=None, since: Optional[float] = None) -> None:
        """You joined the voice channel: a new voice session, with the concierge.
        A hook may greet you with something other than "Concierge here." (the
        inbox's digest). Given who joined (`member`, at `since`, a
        time.monotonic()), the greeting waits until they can hear it
        (`_greet`); the session itself starts at once."""
        directory = self.directory()
        self._reset_focus()
        self._configure_gate()
        self.enrollment.reset()
        self._focus = directory.lobby
        greetings = [line for line in self._call_hooks("joined") if line]
        await self._greet(greetings[0] if greetings else directory.greeting(directory.lobby), member, since)

    async def _greet(self, line: str, member=None, since: Optional[float] = None, *, event: str = "welcome",
                     agent_id: Optional[str] = None) -> None:
        """Says the session's greeting, `line`, in `agent_id`'s voice (the
        concierge's by default), once `member` can hear it: the bot's E2EE
        group holds them and no key change is pending
        (room/connection.py `until_audible`), then GREET_SETTLE_S more for a
        phone's audio route. Given no member, at once. Said with a short
        silence ahead of it. Not said if you've spoken first (`took_floor`),
        or a newer session has begun meanwhile."""
        greeting = self._greeting = _Greeting(line, event=event)
        if member is not None:
            since = time.monotonic() if since is None else since
            greeting.how = await self.connection.until_audible(getattr(member, "id", None), since)
            if greeting.how != "gone":
                await asyncio.sleep(settings.GREET_SETTLE_S)
            greeting.waited_ms = round((time.monotonic() - since) * 1000)
            self._log(f"👋 {getattr(member, 'display_name', 'You')} can hear the room "
                      f"({greeting.how}, {greeting.waited_ms} ms after joining).")
        if self._greeting is not greeting:
            return  # a newer session's greeting took over
        if self._floor_taken or self._hanging_up or not self.connection.is_connected():
            self._log("👋 You spoke first: the greeting is moot." if self._floor_taken
                      else "👋 No one to greet any more.")
            self._greeting_done(greeting, SKIPPED)
            return
        await self._say_greeting(greeting, self.reply.say, agent_id or self.agent_id)

    async def _say_greeting(self, greeting: _Greeting, say: Say, agent_id: Optional[str] = None) -> None:
        """Plays `greeting`, noting when it ends whether it played to the end."""
        greeting.state = PLAYING
        await say(greeting.line, agent_id or self.agent_id, lead_in_ms=settings.GREETING_LEAD_IN_MS,
                  on_end=lambda completed: self._greeting_done(greeting, HEARD if completed else CUT))

    def _greeting_done(self, greeting: _Greeting, state: str) -> None:
        """`greeting` is over: heard to the end, cut off, or skipped. Recorded
        in the lobby's session (`_room_event`), so memory.db shows each join,
        how long the room waited for you to hear it, and how it went."""
        if greeting.state not in (WAITING, PLAYING):
            return  # over already: only a replay (`presence`) plays it again
        greeting.state = state
        self._room_event(self.directory().lobby if greeting.event == "welcome" else self.focus(), {
            "kind": greeting.event, "how": greeting.how, "waited_ms": greeting.waited_ms,
            "chars": len(greeting.line), "state": state,
        })

    def _on_joined(self, room) -> None:
        """The bot joined a room: nothing from the last voice session carries over."""
        self._reset_focus()
        self._configure_gate()
        self.enrollment.reset()
        self._hanging_up = False

    async def rejoined(self) -> None:
        """The bot (re)joined its room on its own -- at startup, after a
        restart or a reconnect, or taking voice over from another instance --
        and you may be in there already. Discord says nothing of a member who
        was there first, so nothing greeted you, and the restarts in the prod
        logs left you talking into the lobby unawares. Now the room greets you
        once you can hear it. Back within RESUME_FOCUS_S of your last turn, it
        puts you back where you were, in that agent's voice: "I'm back. You're
        in day planning, with Daisy."; otherwise it's a new session, with the
        concierge's greeting."""
        listener = self._listener()
        if listener is None:
            return
        directory = self.directory()
        resumed = directory.get(self._remembered_focus())
        if resumed is None or resumed.is_lobby:
            await self.welcome(listener, since=time.monotonic())
            return
        self._log(f"↩️ Back in the room within {settings.RESUME_FOCUS_S}s of your last turn: "
                  f"resuming #{resumed.channel_name}.")
        self._configure_gate()
        self.set_focus(resumed, interrupt=False)
        await self._greet(f"I'm back. {directory.whereabouts(resumed)}", listener, time.monotonic(),
                          event="rejoin", agent_id=resumed.agent_id)

    def _listener(self):
        """The first person, not a bot, in the voice room; None if there's none."""
        members = getattr(self.connection.channel, "members", None) or []
        return next((member for member in members if not getattr(member, "bot", False)), None)

    def _focus_file(self) -> str:
        return os.path.join(settings.voice_data_dir(), FOCUS_FILE)

    def _remember_focus(self, focus: VoiceFocus) -> None:
        """Keeps where the room is, and when, across a restart (`rejoined`)."""
        try:
            path = self._focus_file()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path + ".tmp", "w") as f:
                json.dump({"focus": focus.key, "at": time.time()}, f)
            os.replace(path + ".tmp", path)
        except Exception as e:
            self._log(f"Could not keep the room's focus: {e}")

    def _remembered_focus(self) -> Optional[str]:
        """The focus kept before a restart, if it was in use within RESUME_FOCUS_S."""
        try:
            with open(self._focus_file()) as f:
                kept = json.load(f)
        except (OSError, ValueError):
            return None
        at, key = kept.get("at"), kept.get("focus")
        if not isinstance(at, (int, float)) or not isinstance(key, str):
            return None
        return key if 0 <= time.time() - at <= settings.RESUME_FOCUS_S else None

    def _forget_focus(self) -> None:
        try:
            os.remove(self._focus_file())
        except OSError:
            pass

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
        """Back to the lobby (on first use), with nothing left unanswered,
        nowhere to go back to, and no greeting yet."""
        self._focus = None
        self.floor.clear()
        self._previous_key = None
        self._turns_since_move = 0
        self._last_turn_at.clear()
        self._undo_hint = None
        self._greeting = None
        self._floor_taken = False

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
        you and greets you there, once you can hear it: each time you join is
        a new voice session, with the concierge in the lobby. A mute or deafen
        changes nothing."""
        arrived = time.monotonic()  # Discord's word that you joined; hearing the bot comes later
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
                await self.welcome(member, since=arrived)

    def auto_join(self) -> None:
        """With voice_config `auto_join` on, joins `default_room()` once Discord
        is ready, and greets whoever is in there already (`rejoined`). Discord
        calls this again whenever its gateway session is renewed; a bot still
        in the room stays as it is, conversation and all."""
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
            if self.connection.in_room(target):
                self._log(f"Already in '{target}': the voice session carries on.")
                return
            print(f"Agent {self.agent_id} auto-joining voice channel '{target}'...")
            if await self.join(target):
                await self.rejoined()

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
        its unanswered words and last reply are forgotten. The old focus is
        where "go back" returns. Where the room is now is kept across a
        restart (`rejoined`), and the move is a room event in memory.db."""
        previous, self._focus = self._focus, focus
        if interrupt and self.busy():
            self.interrupt(f"focus moved to {focus.label}")
        self.floor.clear()
        if previous is None or previous.key != focus.key:
            if previous is not None:
                self._previous_key = previous.key
                self._moved_at = time.monotonic()
                self._turns_since_move = 0
            self._remember_focus(focus)
            self._room_event(focus, {"kind": "focus", "from": previous.key if previous else None, "to": focus.key})
            self._spawn(self._announce_focus(focus))

    async def arrive(self, focus: VoiceFocus, say: Say, *, handoff: Any = None,
                     undo_hint: Optional[str] = None) -> None:
        """After a switch's greeting. With a hand-off waiting there
        (`waiting_handoff`), its agent opens with it, no words from you
        needed (`_open_with_handoff`); what else it left you is handed to it
        for after the hand-off, not played first. Otherwise, whatever the
        hooks have for you there, said in the focus agent's own voice.
        `undo_hint`, how to go back, is said after the opening: the switcher
        puts it in the greeting itself when there's no hand-off."""
        if handoff is not None and await self._open_with_handoff(focus, handoff, say):
            if undo_hint and self.focus().key == focus.key:  # unless the opening moved you on again
                await say(undo_hint, focus.agent_id)
            return
        if undo_hint:
            await say(undo_hint, focus.agent_id)

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

    def waiting_handoff(self, focus: VoiceFocus) -> Any:
        """The hand-off waiting for `focus`'s agent in its channel, if there is
        one: the brief an agent's <switch> or <pass> parked there, with the
        conversation it came from (core/storage/handoff_capsules.py). Looked
        at, not taken: the agent's first turn there takes it (graphs/main).
        None in the lobby, and in a channel the concierge answers itself."""
        if focus is None or focus.is_lobby or focus.agent_id == self.agent_id:
            return None
        try:
            return HandoffCapsuleStore().waiting(focus.agent_id, focus.channel_name)
        except Exception as e:
            self._log(f"Could not look for a hand-off waiting in #{focus.channel_name}: {e}")
            return None

    async def _waiting_note(self, focus: VoiceFocus) -> Optional[str]:
        """What a hook has waiting for you in `focus`, for its agent to bring
        up after the hand-off it opens with (VoiceHook.handed_over); None."""
        for hook in self.hooks:
            try:
                note = await hook.handed_over(self, focus)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._hook_failed(hook, "handed_over", e)
                continue
            if isinstance(note, str) and note.strip():
                return note.strip()
        return None

    async def _open_with_handoff(self, focus: VoiceFocus, handoff: Any, say: Say) -> bool:
        """The arrival turn: `focus`'s agent speaks first, opening with the
        hand-off that waits for it. Handed over, an agent used to stay silent
        until you spoke -- its brief rode on your next message -- and you heard
        "Daisy here." and her unrelated morning post; in the prod logs you
        asked every time, \"did you get what Eric sent you?\".

        It runs as a turn of its own, on the channel's session, as a typed
        message there would: graphs/main attaches the parked hand-off to this
        turn's message (core/runtime/history/handoff.py `arrival_message`),
        which team.md tells the agent how to open with. It runs inside the
        turn that moved you, so a barge-in stops it as it would that turn.
        False when it can't run (no agent, no channel, a newer turn): the
        room then arrives as it otherwise would."""
        agent, channel = self.agent(), self.focus_channel(focus)
        turn_id = self._active_turn
        if agent is None or channel is None or not self.is_current(turn_id):
            return False
        directory = self.directory()
        sender = directory.names.get(getattr(handoff, "from_agent", "")) or getattr(handoff, "from_agent", "")
        note = await self._waiting_note(focus)
        self._log(f"↪️ {directory.who(focus)} opens with the hand-off from {sender}"
                  + (", then what it left you." if note else "."))
        outer = current_turn_trace()
        trace = start_turn_trace(self.agent_id, turn_id, kind="arrival", handed_by=getattr(handoff, "from_agent", ""),
                                 via=getattr(handoff, "via", ""))
        speech = TurnSpeech(trace)
        session = None
        answered, cancelled = False, False
        try:
            session = SessionManager().get_session(
                agent_id=self.agent_id, source="discord", channel=channel, surface="voice",
                output=self.turn_output(channel) if self.turn_output else None,
            )
            answered = await self.reply.stream(
                agent, arrival_message(note), sender, session, channel, speech,
                is_current=lambda: self.is_current(turn_id), on_final=self.floor.set_reply,
                speaker=focus.agent_id, on_handoff=self.handed_off,
                on_switch=lambda teammate: self.switch_to_agent(teammate, say),
                heading=self._handover_heading(handoff, directory),
            )
        except asyncio.CancelledError:
            cancelled = True
            trace.meta["outcome"] = Outcome.CANCELLED
            raise
        except Exception as e:
            trace.meta["outcome"] = Outcome.ERROR
            self._log(f"The hand-off's arrival failed: {type(e).__name__}: {e}")
        finally:
            if answered:
                # A conversation there now: a move away says how to come back.
                self._last_turn_at[focus.key] = time.monotonic()
            session_id = session.session_id if session is not None else None
            speech.finish_when_heard(lambda: trace.finish(session_id=session_id), cancelled=cancelled)
            bind_turn_trace(outer)
        return answered

    def _handover_heading(self, handoff: Any, directory: FocusDirectory) -> str:
        """What the channel shows above the agent's opening: who handed you over, from where."""
        from_agent = getattr(handoff, "from_agent", "") or ""
        config = AgentsLoader().get_agent_config(from_agent) or {}
        name = directory.names.get(from_agent) or config.get("name") or from_agent
        where = getattr(handoff, "from_channel", "") or ""
        how = "Passed along" if getattr(handoff, "via", "") == "pass" else "Handed over"
        return f"↪️ {how} from {config.get('emoji', '🤖')} {name}" + (f" in #{where}" if where else "")

    async def switch_to(self, focus: VoiceFocus, say: Say) -> bool:
        """Moves the focus as "switch to …" would: the greeting, then `arrive`.
        For hooks; `switch_to_named` handles what you say."""
        return await self.switcher.switch_to(focus, self.directory(), say)

    async def switch_to_named(self, phrase: str, say: Say, *, asked: bool = True, bare: bool = False,
                              turn: Any = None) -> Optional[str]:
        """"Switch me to …", or a name on its own (core/runtime/commands/spoken/switch.py):
        the agent or channel `phrase` names, or, `asked` for by a switch
        phrase, a near miss. A `bare` name must sound like a voice (`turn`'s
        newest utterance). A move that leaves a conversation says how to come
        back. Returns the turn's outcome, or None: the words are a request for
        the agent."""
        return await self.switcher.switch_to_named(
            phrase, say, asked=asked, bare=bare, noise=lambda: self._not_a_voice(turn),
            live=self._conversation_live)

    async def switch_to_agent(self, agent: str, say: Say) -> bool:
        """An agent's reply handed you to `agent`, a teammate's id or name
        (<switch to="..."/>, core/channel/voice/speech/reply.py): the room moves to its
        channel, as "switch to ..." would, saying how to come back. False: it
        has no channel to go to."""
        return await self.switcher.switch_to_agent(agent, say, live=self._conversation_live)

    async def go_back(self, say: Say) -> bool:
        """"Go back" (core/runtime/commands/spoken/go_back.py): to the focus
        before the last move. Only until you've talked in the new place, or
        within GO_BACK_WINDOW_S of the move; after that the words are a request
        for the agent, and "back to Grace" names where to go."""
        if self._previous_key is None:
            return False
        if self._turns_since_move and time.monotonic() - self._moved_at > settings.GO_BACK_WINDOW_S:
            return False
        directory = self.directory()
        target = directory.get(self._previous_key)
        if target is None:
            return False
        self._log(f"↩️ Going back to #{target.channel_name}.")
        return await self.switcher.switch_to(target, directory, say)

    def _conversation_live(self, focus: VoiceFocus) -> bool:
        """Whether you were talking in `focus` a moment ago: one of its
        agent's turns ran within LIVE_CONVERSATION_S. Never the lobby."""
        if focus.is_lobby:
            return False
        last = self._last_turn_at.get(focus.key)
        return last is not None and time.monotonic() - last <= settings.LIVE_CONVERSATION_S

    @staticmethod
    def _not_a_voice(turn: Any) -> Optional[str]:
        """Why `turn`'s newest utterance doesn't sound like a voice speaking
        -- too little of it has a pitch -- or None. None too when there's no
        audio to tell from. The voiceprint gate's figure is used when it has
        scored the utterance already."""
        utterances = getattr(turn, "utterances", None)
        if not isinstance(utterances, (list, tuple)) or not utterances:
            return None
        newest = utterances[-1]
        share = getattr(getattr(newest, "gate", None), "voiced_share", None)
        if not isinstance(share, (int, float)):
            pcm = newest.speech_pcm() if hasattr(newest, "speech_pcm") else b""
            if len(pcm) < 3 * VOICING_FRAME * 2:  # under 90 ms of 16-bit audio: too little to tell
                return None
            share = voiced_share(np.frombuffer(pcm, dtype=np.int16))
        return f"{share:.2f} voiced" if share < settings.BARE_NAME_MIN_VOICED else None

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
        would, but with no greeting -- the agent's reply is the greeting. A
        move out of a conversation says how to undo it once the reply is said
        (`say_undo_hint`). Returns the text channel the reply goes on in;
        None leaves the turn where it is."""
        directory = self.directory()
        focus = directory.for_channel(event.get("channel"))
        channel = self.focus_channel(focus) if focus is not None else None
        if channel is None:
            self._log(f"Hand-off to #{event.get('channel')}: no such focus or channel; the reply stays here.")
            return None
        here = self.focus()
        self._log(f"🎧 Hand-off: now with {directory.who(focus)} in #{focus.channel_name}.")
        # The turn being answered is the one handing off; it must not cancel itself.
        self.set_focus(focus, interrupt=False)
        # The turn was said where you were, so out of any agent's channel this
        # is a move out of a conversation; out of the lobby, never.
        if here.key != focus.key and self._conversation_live(here):
            self._undo_hint = directory.go_back_hint(here)
        return channel

    async def say_undo_hint(self, say: Say) -> None:
        """After a reply that handed you to another agent out of a
        conversation: how to undo the move, "Say 'go back' for Daisy.", in
        the voice of the agent you're with now. After the reply, so the
        answer isn't kept waiting."""
        hint, self._undo_hint = self._undo_hint, None
        if hint:
            await say(hint, self.focus().agent_id)

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
        """One line at a time; dropped if you've left or are hanging up, or after a long wait."""
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
            if self._hanging_up or not self.someone_listening():
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
        """A turn is about to run in `focus`'s channel: every hook's `turn`.
        It is also a conversation there, now; and once you've talked in the
        new place, "go back" soon stops being a command. The room keeps
        where it is, across a restart (`rejoined`)."""
        self._turns_since_move += 1
        self._last_turn_at[focus.key] = time.monotonic()
        self._undo_hint = None  # one a cancelled turn never said is stale now
        self._floor_taken = True
        self._remember_focus(focus)
        self._call_hooks("turn", focus)

    # --- the greeting, as the floor and the turns ask about it ----------------------

    def took_floor(self) -> None:
        """You said something other than "hello?" (core/channel/voice/turn.py):
        the session is under way, so a greeting still waiting for you to hear
        it would only talk over you, and "hello?" is words for the agent now."""
        self._floor_taken = True

    def greeting_playing(self) -> bool:
        """The session's greeting is what's playing (listening/floor.py): a
        \"hello?\" over it is you not sure you're heard, and doesn't stop it."""
        greeting = self._greeting
        return greeting is not None and greeting.state == PLAYING and bool(self.audio_queue.is_playing())

    async def presence(self, say: Say) -> bool:
        """"Hello?", "can you hear me?" (core/runtime/commands/spoken/presence.py)
        before anything else in the session: the greeting may never have
        reached you, so it's said again. Still waiting for you to be able to
        hear it, it's on its way, and the words need nothing more. False once
        you've said anything else: the words are for the agent you're with."""
        greeting = self._greeting
        if greeting is None or self._floor_taken:
            return False
        if greeting.state == WAITING or self.greeting_playing():
            self._log("👋 'Hello?' while the greeting is on its way.")
            return True
        self._log("👋 'Hello?' before anything else: the greeting again.")
        await self._say_greeting(greeting, say, self.focus().agent_id if greeting.event == "rejoin" else None)
        return True

    def _room_event(self, focus: VoiceFocus, event: Dict[str, Any]) -> None:
        """Records something the room did on its own in `focus`'s session
        (SessionStore.append_room_event): best effort, it never stops the room."""
        try:
            session = SessionManager.get_session(agent_id=self.agent_id, source="discord",
                                                 channel=focus.channel_name, surface="voice")
            SessionStore().append_room_event(session.session_id, event)
        except Exception as e:
            self._log(f"Could not record the room's {event.get('kind')}: {e}")

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
        """Starts a turn that answers everything still unanswered; none once
        you're hanging up (`hang_up`): a "bye!" after it is for no one."""
        if self._hanging_up:
            return
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
