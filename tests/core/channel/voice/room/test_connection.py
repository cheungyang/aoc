"""The voice connection: which voice channel the bot uses, joining and leaving
it, and whether you can hear it yet."""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from core.channel.voice.room.connection import (
    DEFAULT_ROOM, VoiceConnection, normalize_channel_name, pick_voice_channel, voice_rooms,
)


def _connection(runner, on_joined=None):
    return VoiceConnection(runner, make_sink=MagicMock(), on_joined=on_joined)


def test_normalize_channel_name():
    assert normalize_channel_name("general-voice") == "general"
    assert normalize_channel_name("voice-software-dev") == "softwaredev"
    assert normalize_channel_name("vc-day-planning") == "dayplanning"
    assert normalize_channel_name("Weekend_Planning_VC") == "weekendplanning"


def _guild_with_room(runner, connect):
    """One guild with a 'general-voice' room whose connect() is `connect`."""
    mock_guild = MagicMock()
    mock_guild.voice_client = None
    mock_vc = MagicMock()
    mock_vc.name = "general-voice"
    mock_vc.id = 12345
    mock_vc.guild = mock_guild
    mock_vc.connect = connect
    mock_guild.voice_channels = [mock_vc]
    runner.bot.guilds = [mock_guild]
    return mock_guild


@pytest.mark.asyncio
async def test_join_channel_success(mock_bot_runner):
    connection = _connection(mock_bot_runner)
    mock_voice_client = MagicMock()
    _guild_with_room(mock_bot_runner, AsyncMock(return_value=mock_voice_client))

    success = await connection.join("general-voice")
    assert success is True
    assert connection.voice_client == mock_voice_client
    mock_voice_client.listen.assert_called_once_with(connection.sink)


@pytest.mark.asyncio
async def test_join_channel_not_found(mock_bot_runner):
    connection = _connection(mock_bot_runner)

    success = await connection.join("non_existent_vc")
    assert success is False
    assert connection.voice_client is None


@pytest.mark.asyncio
async def test_join_channel_timeout_graceful(mock_bot_runner):
    connection = _connection(mock_bot_runner)
    _guild_with_room(mock_bot_runner, AsyncMock(side_effect=TimeoutError()))

    success = await connection.join("general-voice")
    assert success is False
    assert connection.voice_client is None
    assert connection.sink is None


@pytest.mark.asyncio
async def test_join_channel_client_exception_graceful(mock_bot_runner):
    connection = _connection(mock_bot_runner)
    error = discord.errors.ClientException("Already connected to a voice channel.")
    _guild_with_room(mock_bot_runner, AsyncMock(side_effect=error))

    success = await connection.join("general-voice")
    assert success is False
    assert connection.voice_client is None
    assert connection.sink is None


@pytest.mark.asyncio
async def test_join_channel_stabilization_failure_graceful(mock_bot_runner):
    connection = _connection(mock_bot_runner)
    mock_voice_client = MagicMock()
    mock_voice_client.is_connected.return_value = False
    mock_voice_client.disconnect = AsyncMock()
    _guild_with_room(mock_bot_runner, AsyncMock(return_value=mock_voice_client))

    with patch("asyncio.sleep", AsyncMock()):
        success = await connection.join("general-voice")
        assert success is False
        assert connection.voice_client is None
        assert connection.sink is None


@pytest.mark.asyncio
async def test_join_channel_pre_cleans_existing_guild_vc(mock_bot_runner):
    connection = _connection(mock_bot_runner)
    mock_new_voice_client = MagicMock()
    mock_new_voice_client.is_connected.return_value = True
    mock_guild = _guild_with_room(mock_bot_runner, AsyncMock(return_value=mock_new_voice_client))
    mock_existing_vc = MagicMock()
    mock_existing_vc.disconnect = AsyncMock()
    mock_guild.voice_client = mock_existing_vc

    success = await connection.join("general-voice")
    assert success is True
    mock_existing_vc.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_join_warns_when_libopus_cannot_load(mock_bot_runner, capsys):
    """Without libopus, voice_recv's reader dies on the first packet of speech
    and the bot sits in the room deaf (the Docker image lost it with chromium)."""
    connection = _connection(mock_bot_runner)
    _guild_with_room(mock_bot_runner, AsyncMock(return_value=MagicMock()))

    with patch.object(discord.opus.Decoder, "get_opus_version", side_effect=discord.opus.OpusNotLoaded()):
        assert await connection.join("general-voice") is True
    assert "libopus could not be loaded" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_join_says_nothing_about_libopus_when_it_loads(mock_bot_runner, capsys):
    connection = _connection(mock_bot_runner)
    _guild_with_room(mock_bot_runner, AsyncMock(return_value=MagicMock()))

    with patch.object(discord.opus.Decoder, "get_opus_version", return_value="libopus 1.5.2"):
        assert await connection.join("general-voice") is True
    assert "libopus" not in capsys.readouterr().out


@pytest.mark.asyncio
async def test_leave_channel(mock_bot_runner):
    connection = _connection(mock_bot_runner)
    mock_vc = MagicMock()
    mock_vc.is_connected.return_value = True
    mock_vc.disconnect = AsyncMock()
    connection.voice_client = mock_vc

    await connection.leave()
    mock_vc.disconnect.assert_awaited_once()
    assert connection.voice_client is None


@pytest.mark.asyncio
async def test_a_successful_join_is_reported_with_its_room(mock_bot_runner):
    """A new voice session must not keep the last one's conversation, so the
    voice manager hears about every join."""
    joined = []
    connection = _connection(mock_bot_runner, on_joined=joined.append)
    mock_guild = _guild_with_room(mock_bot_runner, AsyncMock(return_value=MagicMock()))

    assert await connection.join("general-voice") is True
    assert joined == [mock_guild.voice_channels[0]]


@pytest.mark.asyncio
async def test_a_failed_join_is_not_reported(mock_bot_runner):
    joined = []
    connection = _connection(mock_bot_runner, on_joined=joined.append)
    _guild_with_room(mock_bot_runner, AsyncMock(side_effect=TimeoutError()))

    assert await connection.join("general-voice") is False
    assert await connection.join("non_existent_vc") is False
    assert joined == []


# --- which voice channel ------------------------------------------------------------------

def test_the_named_rooms_come_first_voice_channel_leading():
    hosts = {"channel_hosts": ["general"]}
    named = {"voice_channel": " aoc-voice ", "voice_channels": ["other", "aoc-voice", 123]}
    assert voice_rooms({**hosts, "voice_config": named}) == ["aoc-voice", "other", "123"]
    assert voice_rooms({**hosts, "voice_config": {"voice_channels": ["first", "second"]}}) == ["first", "second"]


def test_without_a_named_room_the_hosted_channels_have_one_each():
    config = {"channel_hosts": ["general", "day-planning", "agent-management"],
              "voice_config": {"enabled": True, "voice_channel": "  "}}
    assert voice_rooms(config) == ["general-voice", "day-planning-voice", "agent-management-voice"]


def test_with_nothing_to_go_on_the_default_room():
    assert voice_rooms({"voice_config": {"enabled": True}}) == [DEFAULT_ROOM]
    assert voice_rooms(None) == [DEFAULT_ROOM]
    assert DEFAULT_ROOM == "general-voice"


def test_the_wildcard_names_no_room():
    """The Concierge hosts "*" and names its room in voice_config; without one, never "*-voice"."""
    assert voice_rooms({"channel_hosts": ["*"], "voice_config": {"enabled": True}}) == [DEFAULT_ROOM]
    assert voice_rooms({"channel_hosts": ["*", "general"]}) == ["general-voice"]


def test_pick_voice_channel_takes_the_first_target_the_guild_has():
    voice = MagicMock(id=123)
    voice.name = "day-planning-voice"
    guilds = [MagicMock(voice_channels=[voice])]
    assert pick_voice_channel(guilds, ["general-voice", "day-planning-voice"]) == "day-planning-voice"
    assert pick_voice_channel(guilds, ["general-voice", "123"]) == "123"
    assert pick_voice_channel(guilds, ["Day Planning"]) == "Day Planning"  # normalized
    assert pick_voice_channel([], ["general-voice", "other"]) == "general-voice"


# --- already in the room ----------------------------------------------------------------

def _in(connection, name="aoc-voice", room_id=777, connected=True):
    voice_client = MagicMock()
    voice_client.is_connected.return_value = connected
    voice_client.channel.name = name
    voice_client.channel.id = room_id
    connection.voice_client = voice_client
    return voice_client


def test_in_room_by_name_or_id_only_while_connected(mock_bot_runner):
    """Auto-join runs on every on_ready; it must know the bot is there already."""
    connection = _connection(mock_bot_runner)
    assert connection.in_room("aoc-voice") is False  # no voice client
    _in(connection)
    assert connection.in_room("aoc-voice") is True
    assert connection.in_room("vc-aoc") is True  # normalized
    assert connection.in_room("777") is True
    assert connection.in_room("general-voice") is False
    assert connection.in_room("") is False
    _in(connection, connected=False)
    assert connection.in_room("aoc-voice") is False


# --- whether you can hear the bot yet ---------------------------------------------------
#
# The state is discord.py's VoiceConnectionState (voice_client._connection):
# dave_protocol_version, dave_session (davey.DaveSession: ready,
# get_user_ids() -> List[str]) and dave_pending_transitions (Dict[int, int]).

YOU = 42


def _dave(connection, version=1, ready=True, members=(str(YOU),), pending=None, session=True):
    voice_client = _in(connection)
    dave_session = MagicMock(ready=ready)
    dave_session.get_user_ids.return_value = list(members)
    voice_client._connection = SimpleNamespace(
        dave_protocol_version=version, dave_session=dave_session if session else None,
        dave_pending_transitions=dict(pending or {}))
    return voice_client._connection


def test_with_no_e2ee_session_it_cannot_tell(mock_bot_runner):
    connection = _connection(mock_bot_runner)
    assert connection.audible_to(YOU) is None  # no voice client
    _in(connection)
    assert connection.audible_to(YOU) is None  # a MagicMock's protocol version is no number
    _dave(connection, version=0)
    assert connection.audible_to(YOU) is None  # the call isn't end-to-end encrypted


def test_audible_once_the_group_holds_you_with_no_key_change_pending(mock_bot_runner):
    connection = _connection(mock_bot_runner)
    _dave(connection)
    assert connection.audible_to(YOU) is True
    assert connection.audible_to(str(YOU)) is True
    assert connection.audible_to(7) is False  # someone the group doesn't hold yet


@pytest.mark.parametrize("state", [
    {"session": False},          # no MLS session yet
    {"ready": False},            # the bot's own group isn't ready: it can't encrypt
    {"pending": {3: 1}},         # a welcome or commit is still to be executed
    {"members": ("99",)},        # your client hasn't joined the group
], ids=["no-session", "not-ready", "transition-pending", "not-a-member"])
def test_not_audible_before_your_client_has_the_keys(mock_bot_runner, state):
    connection = _connection(mock_bot_runner)
    _dave(connection, **state)
    assert connection.audible_to(YOU) is False


@pytest.fixture
def quick(monkeypatch):
    """until_audible looks every 5 ms, and assumes you hear it after 50 ms of no signal."""
    from core.channel.voice.foundation import settings
    from core.channel.voice.room import connection as module
    monkeypatch.setattr(module, "AUDIBLE_POLL_S", 0.005)
    monkeypatch.setattr(settings, "GREET_NO_SIGNAL_WAIT_S", 0.05)


@pytest.mark.asyncio
async def test_until_audible_waits_for_the_key_transition(mock_bot_runner, quick):
    """The greeting used to start ~0.4 s after you joined, while your client was
    still joining the E2EE group: you heard it mid-sentence or not at all."""
    connection = _connection(mock_bot_runner)
    state = _dave(connection, pending={5: 1}, members=())

    async def your_client_joins():
        await asyncio.sleep(0.03)
        state.dave_session.get_user_ids.return_value = [str(YOU)]
        await asyncio.sleep(0.03)
        state.dave_pending_transitions.clear()  # the transition executes

    joining = asyncio.create_task(your_client_joins())
    started = time.monotonic()
    assert await connection.until_audible(YOU, started, timeout=2.0) == "e2ee"
    assert time.monotonic() - started >= 0.05
    await joining


@pytest.mark.asyncio
async def test_until_audible_gives_up_after_the_timeout(mock_bot_runner, quick):
    connection = _connection(mock_bot_runner)
    _dave(connection, ready=False)
    assert await connection.until_audible(YOU, time.monotonic(), timeout=0.05) == "timeout"


@pytest.mark.asyncio
async def test_until_audible_without_e2ee_waits_for_your_client_to_connect(mock_bot_runner, quick):
    """No E2EE to ask: voice_recv's op 11 (`voice_member_connect`) says your
    client reached the voice server."""
    connection = _connection(mock_bot_runner)
    _in(connection)
    connection._watch_members()

    async def your_client_connects():
        await asyncio.sleep(0.02)
        await connection._member_connected(SimpleNamespace(id=YOU))

    connecting = asyncio.create_task(your_client_connects())
    assert await connection.until_audible(YOU, time.monotonic(), timeout=2.0) == "connected"
    await connecting


@pytest.mark.asyncio
async def test_until_audible_with_no_signal_at_all_assumes_after_a_moment(mock_bot_runner, quick):
    connection = _connection(mock_bot_runner)
    _in(connection)
    started = time.monotonic()
    assert await connection.until_audible(YOU, started, timeout=2.0) == "assumed"
    assert time.monotonic() - started >= 0.05


@pytest.mark.asyncio
async def test_a_connect_from_an_earlier_visit_does_not_count(mock_bot_runner, quick):
    connection = _connection(mock_bot_runner)
    _in(connection)
    connection._connected_at[YOU] = time.monotonic() - 2.0  # before you joined this time
    assert await connection.until_audible(YOU, time.monotonic(), timeout=2.0) == "assumed"


@pytest.mark.asyncio
async def test_until_audible_stops_when_the_bot_is_gone(mock_bot_runner, quick):
    connection = _connection(mock_bot_runner)
    assert await connection.until_audible(YOU, time.monotonic(), timeout=2.0) == "gone"


@pytest.mark.asyncio
async def test_joining_watches_members_connect_and_disconnect(mock_bot_runner):
    connection = _connection(mock_bot_runner)
    voice_client = MagicMock()
    _guild_with_room(mock_bot_runner, AsyncMock(return_value=voice_client))
    connection._connected_at[YOU] = 1.0  # from the last voice session

    assert await connection.join("general-voice") is True
    assert connection._connected_at == {}
    names = [c.kwargs["name"] for c in voice_client.add_listener.call_args_list]
    assert names == ["on_voice_member_connect", "on_voice_member_disconnect"]

    connected, disconnected = (c.args[0] for c in voice_client.add_listener.call_args_list)
    await connected(SimpleNamespace(id=YOU))
    assert YOU in connection._connected_at
    await disconnected(SimpleNamespace(id=YOU))
    assert YOU not in connection._connected_at
