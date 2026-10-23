"""Doubles for the voice room's tests (test_voice_manager.py, test_turn.py and
test_voiceprint.py): what you say, what the agent writes, and a room wired to
mocked Discord channels and a fake TTS. The runner a room is built with is the
`mock_bot_runner` fixture, in conftest.py."""
import asyncio
from functools import partial
from unittest.mock import AsyncMock, MagicMock, patch

from core.channel.discord.stream_buffer import DiscordStreamBuffer
from core.channel.response_parser import AgentResponse
from core.voice.foundation.utterance import Transcript, Utterance
from core.voice.voice_manager import VoiceManager
from tests.helpers import FakeTTS


# The voice room's text lane, as the Discord runner builds it.
TEXT_OUTPUT = partial(DiscordStreamBuffer, edit_interval=1.5)


def _spoken(text, user=None, speech_end_at=None):
    """An utterance whose transcript is already final, as the live transcriber leaves it."""
    utterance = Utterance(user=user if user is not None else MagicMock(display_name="Sam", bot=False))
    utterance.transcript = Transcript(text, "gemini_live")
    utterance.speech_end_at = speech_end_at
    return utterance


def _say(vm, text, user=None, speech_end_at=None):
    """The user said `text`: one utterance, from onset to the end of the turn."""
    utterance = _spoken(text, user, speech_end_at)
    vm.floor.utterance_opened(utterance)
    vm.floor.utterance_finished(utterance)
    return utterance


def _final(raw):
    """The final_response event for a reply the agent wrote as `raw`, parsed as production parses it."""
    response = AgentResponse.from_string(raw)
    return {"type": "final_response", "text": response.text, "response": response}


def _spoken_reply(text):
    """A reply that follows <voice_mode>: the speech, then the text."""
    return f"<speech>{text}</speech>{text}"


QUESTION = "Where should we go this weekend?"


def _voice_turn_fixture(mock_bot_runner, tmp_path, reply):
    """Wires a VoiceManager up to a mocked guild, agent, and audio queue.
    The agent writes `reply`, streamed as one token."""
    vm = VoiceManager(mock_bot_runner, text_output=TEXT_OUTPUT)

    mock_vc = MagicMock()
    mock_vc.is_connected.return_value = True
    mock_vc.is_playing.return_value = False

    sent_message = MagicMock()
    sent_message.edit = AsyncMock()
    sent_message.delete = AsyncMock()

    text_channel = MagicMock()
    text_channel.name = "general"
    text_channel.send = AsyncMock(return_value=sent_message)

    guild = MagicMock()
    guild.text_channels = [text_channel]
    mock_vc.channel.name = "general-voice"
    mock_vc.channel.guild = guild
    vm.connection.voice_client = mock_vc

    async def fake_stream(*_args, **_kwargs):
        yield {"type": "token", "content": reply}
        yield _final(reply)

    agent = MagicMock()
    agent.execute_stream = MagicMock(side_effect=fake_stream)

    vm.reply.tts = FakeTTS()

    # The floor and the reply share this queue, so it's stubbed in place.
    vm.audio_queue.start = MagicMock()
    vm.audio_queue.put = AsyncMock()
    vm.audio_queue.is_playing = MagicMock(return_value=False)

    return vm, agent, text_channel


def _play_at_once(vm):
    """Queued audio starts playing at once, so the trace records at the turn's end."""
    async def play_now(speaker, on_start=None):
        if on_start:
            on_start()

    vm.audio_queue.put = AsyncMock(side_effect=play_now)


def _streaming_fixture(mock_bot_runner, tmp_path, events):
    """A voice turn whose agent yields `events`; returns (vm, agent, text_channel)."""
    vm, agent, text_channel = _voice_turn_fixture(mock_bot_runner, tmp_path, "unused")

    async def fake_stream(*_args, **_kwargs):
        for event in events:
            yield event

    agent.execute_stream = MagicMock(side_effect=fake_stream)
    return vm, agent, text_channel


class _Loader:
    """What the voice manager and its focus directory ask of AgentsLoader.
    Concierge hosts every channel, Aki's included: no agent has a bot of its own."""

    CONFIGS = {
        "main": {"name": "Concierge", "channels": ["*"], "tools": {"agent_call": {}},
                 "channel_hosts": ["general", "day-planning", "market-scout", "fitness-coach", "agent-management"]},
        "day-planner": {"name": "Daisy", "channels": ["day-planning", "general"]},
        "market-scout": {"name": "Marvin", "channels": ["market-scout", "general"]},
        "fitness-coach": {"name": "Frankie", "channels": ["fitness-coach"],
                          "description": "Curates and tracks home workouts."},
        "agent-designer": {"name": "Aki", "channels": ["agent-management", "general"]},
    }

    def __init__(self, agents):
        self.agents = agents  # agent id -> agent double

    def list_agent_ids(self):
        return list(self.CONFIGS)

    def get_agent_config(self, agent_id):
        return self.CONFIGS.get(agent_id)

    def get_agent(self, agent_id):
        return self.agents.get(agent_id)

    def hosts_channel(self, agent_id, channel):
        return channel in (self.CONFIGS.get(agent_id) or {}).get("channel_hosts", [])

    def eligible_agents_for_channel(self, channel, exclude_agent_id=None):
        return sorted(a for a, c in self.CONFIGS.items()
                      if a != exclude_agent_id and "*" not in c["channels"] and channel in c["channels"])


def _text_channel(name):
    channel = MagicMock()
    channel.name = name
    message = MagicMock()
    message.edit = AsyncMock()
    message.delete = AsyncMock()
    channel.send = AsyncMock(return_value=message)
    return channel


def _room_fixture(mock_bot_runner, tmp_path):
    """The bot in the one voice room, 'aoc-voice', whose own chat only logs
    switches. The lobby is #general; the model behind "Do you mean …?" is a
    mock that finds nothing close unless a test says otherwise."""
    vm, main_agent, general = _voice_turn_fixture(mock_bot_runner, tmp_path, "unused")
    vm.config.update({"voice_channel": "aoc-voice"})
    room = vm.connection.voice_client.channel
    room.name = "aoc-voice"
    room.send = AsyncMock()
    channels = {"general": general}
    for name in ("day-planning", "market-scout", "fitness-coach", "agent-management"):
        channels[name] = _text_channel(name)
    room.guild.text_channels = list(channels.values())
    channels["room"] = room
    vm.connection.bot.change_presence = AsyncMock()
    vm.switcher._resolve = AsyncMock(return_value=None)
    loader = _Loader({"main": main_agent})
    return vm, main_agent, loader, channels


def _capture_sessions(agent, reply="Done."):
    """The agent answers `reply` every time; returns the (prompt, session) of each call."""
    calls = []

    async def stream(prompt, session=None):
        calls.append((prompt, session))
        yield {"type": "token", "content": _spoken_reply(reply)}
        yield _final(_spoken_reply(reply))

    agent.execute_stream = MagicMock(side_effect=stream)
    return calls


async def _turn(vm, text, user=None):
    _say(vm, text, user=user)
    await vm._current_task
    await asyncio.gather(*list(vm._side_tasks))


def _focused(loader, *patches):
    """The patches every focus test runs under."""
    from contextlib import ExitStack
    stack = ExitStack()
    stack.enter_context(patch("core.voice.voice_manager.AgentsLoader", return_value=loader))
    stack.enter_context(patch("core.voice.speech.blurp_generator.BlurpGenerator.get_blurp_audio", return_value=None))
    for extra in patches:
        stack.enter_context(extra)
    return stack
