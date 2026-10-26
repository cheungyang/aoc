"""The voice connection: which voice channel the bot uses, and joining and leaving it."""
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from core.voice.room.connection import (
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
    """B20's successor: a new voice session must not keep the last one's
    conversation, so the voice manager hears about every join."""
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


def test_pick_voice_channel_takes_the_first_target_the_guild_has():
    voice = MagicMock(id=123)
    voice.name = "day-planning-voice"
    guilds = [MagicMock(voice_channels=[voice])]
    assert pick_voice_channel(guilds, ["general-voice", "day-planning-voice"]) == "day-planning-voice"
    assert pick_voice_channel(guilds, ["general-voice", "123"]) == "123"
    assert pick_voice_channel(guilds, ["Day Planning"]) == "Day Planning"  # normalized
    assert pick_voice_channel([], ["general-voice", "other"]) == "general-voice"
