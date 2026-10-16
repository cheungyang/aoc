"""VoiceConnection: joining and leaving rooms, and the text channel a room talks into."""
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from core.voice.room.connection import VoiceConnection, normalize_channel_name


@pytest.fixture
def mock_bot_runner():
    runner = MagicMock()
    runner.agent_id = "main"
    runner.bot = MagicMock()
    runner.bot.guilds = []
    return runner


def _connection(runner, channel_hosts=()):
    return VoiceConnection(runner, make_sink=MagicMock(), channel_hosts=lambda: list(channel_hosts))


def test_normalize_channel_name():
    assert normalize_channel_name("general-voice") == "general"
    assert normalize_channel_name("voice-software-dev") == "softwaredev"
    assert normalize_channel_name("vc-day-planning") == "dayplanning"
    assert normalize_channel_name("Weekend_Planning_VC") == "weekendplanning"


def test_resolve_channel_linked(mock_bot_runner):
    connection = _connection(mock_bot_runner)
    linked_ch = MagicMock(name="linked_channel")
    connection.linked_text_channel = linked_ch

    mock_vc = MagicMock()
    assert connection.resolve_text_channel(mock_vc) == linked_ch


def test_resolve_channel_matched(mock_bot_runner):
    connection = _connection(mock_bot_runner)

    mock_guild = MagicMock()
    ch1 = MagicMock()
    ch1.name = "general"
    ch2 = MagicMock()
    ch2.name = "day-planning"
    mock_guild.text_channels = [ch1, ch2]

    mock_vc = MagicMock()
    mock_vc.name = "day-planning-voice"
    mock_vc.guild = mock_guild

    resolved = connection.resolve_text_channel(mock_vc)
    assert resolved == ch2


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
async def test_leave_channel(mock_bot_runner):
    connection = _connection(mock_bot_runner)
    mock_vc = MagicMock()
    mock_vc.is_connected.return_value = True
    mock_vc.disconnect = AsyncMock()
    connection.voice_client = mock_vc
    connection.linked_text_channel = MagicMock()

    await connection.leave()
    mock_vc.disconnect.assert_awaited_once()
    assert connection.voice_client is None
    assert connection.linked_text_channel is None


@pytest.mark.asyncio
async def test_joining_without_a_text_channel_clears_a_stale_join_link(mock_bot_runner):
    """B20: a `!join` link survived room moves, so a new room kept talking into the old channel."""
    connection = _connection(mock_bot_runner)
    connection.linked_text_channel = MagicMock(name="old-link")
    mock_bot_runner.bot.guilds = []  # the target won't be found; the link must be gone regardless
    await connection.join("day-planning-voice")
    assert connection.linked_text_channel is None

    explicit = MagicMock(name="explicit")
    await connection.join("day-planning-voice", text_channel=explicit)
    assert connection.linked_text_channel is explicit
