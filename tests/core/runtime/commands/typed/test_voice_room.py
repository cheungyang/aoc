"""The voice room's typed commands: !join, !leave, !enroll, !voiceprint and
!gate. Which bot answers, and what each says, through VoiceControls."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.runtime.commands.base import TypedContext
from core.runtime.commands.registry import TYPED
from core.runtime.commands.typed.enroll import EnrollCommand
from core.runtime.commands.typed.gate import GateCommand
from core.runtime.commands.typed.join import JoinCommand
from core.runtime.commands.typed.leave import LeaveCommand
from core.runtime.commands.typed.voiceprint import VoiceprintCommand

VOICE_ROOM = ["join", "leave", "inbox", "enroll", "voiceprint", "gate"]


def _room(hosts_voice=True, joined=True):
    """A stand-in voice room: VoiceControls, as the commands use it."""
    room = MagicMock()
    room.hosts_voice.return_value = hosts_voice
    room.join = AsyncMock(return_value=joined)
    room.leave = AsyncMock()
    room.enroll = AsyncMock(return_value="🎙️ **Voiceprint enrollment.**")
    room.voiceprint.return_value = "The gate is off."
    room.gate = AsyncMock()
    room.room.return_value.name = "aoc-voice"
    return room


def _typed(room, author_voice=None):
    """Typed in #day-planning, by someone in `author_voice` (or no voice channel)."""
    channel = MagicMock(send=AsyncMock())
    channel.name = "day-planning"
    return TypedContext(channel=channel, author=MagicMock(voice=author_voice), voice=room), channel


def test_only_the_voice_host_answers_inbox_enroll_voiceprint_and_gate():
    for hosts in (True, False):
        ctx, _channel = _typed(_room(hosts_voice=hosts))
        answers = {c.name: c.available(ctx) for c in TYPED if c.name in VOICE_ROOM}
        assert answers == {"join": True, "leave": True, "inbox": hosts, "enroll": hosts, "voiceprint": hosts,
                           "gate": hosts}


def test_no_voice_room_no_voice_commands():
    ctx = TypedContext(voice=None)
    assert not any(c.available(ctx) for c in TYPED if c.name in VOICE_ROOM)


@pytest.mark.asyncio
async def test_join_follows_you_into_your_voice_channel_and_says_where_you_are_talking():
    room = _room()
    room.focus.return_value = MagicMock(is_lobby=False, channel_name="day-planning")
    ctx, channel = _typed(room, author_voice=MagicMock(channel=MagicMock(id=1234)))
    await JoinCommand().run(ctx, "")
    room.join.assert_awaited_once_with("1234", focus_channel=channel)
    channel.send.assert_awaited_once_with("Connected to voice channel: `aoc-voice`, talking in #day-planning 🎙️")


@pytest.mark.asyncio
async def test_join_by_name_into_the_lobby_or_not_at_all():
    room = _room()
    room.focus.return_value = MagicMock(is_lobby=True)
    ctx, channel = _typed(room)
    await JoinCommand().run(ctx, "aoc-voice")
    room.join.assert_awaited_once_with("aoc-voice", focus_channel=channel)
    channel.send.assert_awaited_once_with("Connected to voice channel: `aoc-voice`, talking in the lobby 🎙️")

    room = _room(joined=False)
    ctx, channel = _typed(room, author_voice=None)
    await JoinCommand().run(ctx, "")
    room.join.assert_awaited_once_with(None, focus_channel=channel)
    channel.send.assert_awaited_once_with("Could not join voice channel.")


@pytest.mark.asyncio
async def test_leave():
    room = _room()
    ctx, channel = _typed(room)
    await LeaveCommand().run(ctx, "")
    room.leave.assert_awaited_once()
    channel.send.assert_awaited_once_with("Disconnected from voice channel. 👋")


@pytest.mark.asyncio
async def test_enroll_voiceprint_and_gate_hand_over_to_the_room():
    room = _room()
    ctx, channel = _typed(room)
    await EnrollCommand().run(ctx, "cancel")
    room.enroll.assert_awaited_once_with("cancel", channel)
    await VoiceprintCommand().run(ctx, "reset")
    room.voiceprint.assert_called_once_with("reset")
    await GateCommand().run(ctx, "review all")
    room.gate.assert_awaited_once_with("review all", channel)
    assert [c.args[0] for c in channel.send.await_args_list] == ["🎙️ **Voiceprint enrollment.**", "The gate is off."]


@pytest.mark.asyncio
async def test_typed_as_bang_name_with_its_arguments():
    room = _room()
    ctx, channel = _typed(room)
    assert isinstance(await TYPED.answer("!gate review all", ctx), GateCommand)
    room.gate.assert_awaited_once_with("review all", channel)
