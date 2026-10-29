"""VoiceManager, the voice room: joining and following you, barge-in over the
reply, the focus ("switch me to Daisy"), briefings and hooks.

A turn's own tests are in test_turn.py and the voiceprint's in test_voiceprint.py;
the doubles they share are in voice_room.py.
"""
import asyncio
import time
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from core.runtime.observability.turn_trace import Outcome
from core.voice.foundation import settings as voice_settings
from core.voice.foundation.utterance import Utterance
from core.voice.listening.speaker_gate import Assessment, Verdict
from core.voice.voice_manager import VoiceManager
from tests.core.voice.voice_room import (
    TEXT_OUTPUT, _capture_sessions, _focused, _room_fixture, _say, _spoken, _turn,
)


def test_voice_manager_init(mock_bot_runner):
    with patch("core.voice.voice_manager.AgentsLoader") as mock_loader:
        mock_agent = MagicMock()
        mock_agent.config = {"voice_config": {"audio_blurp": "assets/sounds/blurps/concierge_ping.wav"}}
        mock_loader.return_value.get_agent.return_value = mock_agent

        vm = VoiceManager(mock_bot_runner, text_output=TEXT_OUTPUT)
        assert vm.agent_id == "main"
        assert vm.config.get("audio_blurp") == "assets/sounds/blurps/concierge_ping.wav"


def test_stt_vocabulary_is_the_agents_you_can_reach_by_name(mock_bot_runner):
    """Names like Frankie or Daisy are what a generic recognizer gets wrong. The
    concierge's id ("main") and a worker are only more for it to recite back
    over noise: neither has a channel a name leads to."""
    configs = {
        "day-planner": {"name": "Daisy", "channels": ["day-planning", "general"]},
        "main": {"name": "Concierge", "channels": ["*"], "channel_hosts": ["general", "day-planning"]},
        "graph-worker": {"name": "Graph Worker", "channels": ["*"]},
    }
    with patch("core.voice.voice_manager.AgentsLoader") as mock_loader:
        loader = mock_loader.return_value
        loader.get_agent.return_value = None
        loader.list_agent_ids.return_value = list(configs)
        loader.get_agent_config.side_effect = configs.get
        loader.hosts_channel.side_effect = lambda agent_id, channel: channel in configs[agent_id].get(
            "channel_hosts", [])
        loader.eligible_agents_for_channel.side_effect = lambda channel, exclude_agent_id=None: sorted(
            a for a, c in configs.items() if a != exclude_agent_id and channel in c["channels"] and "*" not in c["channels"])
        vm = VoiceManager(mock_bot_runner, text_output=TEXT_OUTPUT)
    assert vm.stt.vocabulary == ["Daisy", "day planner"]


@contextmanager
def _hosting(voice_config, channel_hosts=("general", "day-planning")):
    """The agent config the room reads afresh, while the block runs."""
    agent = MagicMock()
    agent.config = {"channel_hosts": list(channel_hosts), "voice_config": voice_config}
    with patch("core.voice.voice_manager.AgentsLoader") as loader_class:
        loader_class.return_value.get_agent.return_value = agent
        yield agent


AOC_VOICE = {"enabled": True, "voice_channel": "aoc-voice"}


def _following_vm(mock_bot_runner, joined=True):
    """A room whose agent hosts the one voice channel, aoc-voice; joining and the greeting stubbed."""
    vm = VoiceManager(mock_bot_runner, text_output=TEXT_OUTPUT)
    vm.join = AsyncMock(return_value=joined)
    vm.welcome = AsyncMock()
    room = MagicMock()
    room.name = "aoc-voice"
    return vm, room


@pytest.mark.asyncio
async def test_you_joining_its_voice_channel_brings_the_bot_and_a_greeting(mock_bot_runner):
    """You join the voice channel: the bot follows you there and greets you
    (a new voice session, in the lobby)."""
    vm, room = _following_vm(mock_bot_runner)
    with _hosting(AOC_VOICE):
        await vm.member_moved(MagicMock(bot=False, display_name="Sam"), MagicMock(channel=None), MagicMock(channel=room))
    vm.join.assert_awaited_once_with("aoc-voice")
    vm.welcome.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_mute_where_you_are_is_not_an_arrival(mock_bot_runner):
    """Muting or deafening in the channel you're in keeps the conversation going."""
    vm, room = _following_vm(mock_bot_runner)
    vm.connection.voice_client = MagicMock(channel=room)
    with _hosting(AOC_VOICE):
        await vm.member_moved(MagicMock(bot=False, display_name="Sam"), MagicMock(channel=room), MagicMock(channel=room))
    vm.join.assert_not_awaited()
    vm.welcome.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_greeting_if_the_bot_cannot_follow_you(mock_bot_runner):
    vm, room = _following_vm(mock_bot_runner, joined=False)
    with _hosting(AOC_VOICE):
        await vm.member_moved(MagicMock(bot=False, display_name="Sam"), MagicMock(channel=None), MagicMock(channel=room))
    vm.welcome.assert_not_awaited()


@pytest.mark.asyncio
async def test_other_voice_channels_and_disabled_voice_are_ignored(mock_bot_runner):
    vm, room = _following_vm(mock_bot_runner)
    other = MagicMock()
    other.name = "day-planning-voice"
    with _hosting(AOC_VOICE):
        await vm.member_moved(MagicMock(bot=False, display_name="Sam"), MagicMock(channel=None), MagicMock(channel=other))
    with _hosting({"enabled": False, "voice_channel": "aoc-voice"}):
        await vm.member_moved(MagicMock(bot=False, display_name="Sam"), MagicMock(channel=None), MagicMock(channel=room))
    vm.join.assert_not_awaited()
    vm.welcome.assert_not_awaited()


async def _auto_join(vm, voice_config, channel_hosts):
    """Runs auto-join as Discord would: once ready, after the guild cache settles."""
    vm.connection.bot.wait_until_ready = AsyncMock()
    with _hosting(voice_config, channel_hosts), \
            patch.object(asyncio, "create_task") as create_task, patch("asyncio.sleep", AsyncMock()):
        vm.auto_join()
        create_task.assert_called_once()
        await create_task.call_args[0][0]


@pytest.mark.asyncio
async def test_auto_join_goes_to_the_first_hosted_voice_channel(mock_bot_runner):
    vm = VoiceManager(mock_bot_runner, text_output=TEXT_OUTPUT)
    vm.join = AsyncMock(return_value=True)
    await _auto_join(vm, {"enabled": True, "auto_join": True}, channel_hosts=["general"])
    vm.join.assert_called_with("general-voice")


@pytest.mark.asyncio
async def test_auto_join_picks_a_hosted_voice_channel_the_guild_has(mock_bot_runner):
    vm = VoiceManager(mock_bot_runner, text_output=TEXT_OUTPUT)
    vm.join = AsyncMock(return_value=True)
    voice_channel = MagicMock()
    voice_channel.name = "day-planning-voice"
    mock_bot_runner.bot.guilds = [MagicMock(voice_channels=[voice_channel])]
    await _auto_join(vm, {"enabled": True, "auto_join": True}, channel_hosts=["general", "day-planning"])
    vm.join.assert_called_with("day-planning-voice")


@pytest.mark.asyncio
async def test_join_without_a_name_follows_and_auto_join_share_one_list(mock_bot_runner):
    """`!join` with no name goes where auto-join would, and both go to a room
    the bot would also follow you into."""
    vm, room = _following_vm(mock_bot_runner)
    voice_channel = MagicMock()
    voice_channel.name = "day-planning-voice"
    mock_bot_runner.bot.guilds = [MagicMock(voice_channels=[voice_channel])]
    vm.connection.join = AsyncMock(return_value=True)
    with _hosting({"enabled": True}, channel_hosts=["general", "day-planning"]):
        assert vm.default_room() == "day-planning-voice"
        await VoiceManager.join(vm)
        await vm.member_moved(MagicMock(bot=False, display_name="Sam"), MagicMock(channel=None),
                              MagicMock(channel=voice_channel))
    vm.connection.join.assert_awaited_once_with("day-planning-voice")
    vm.join.assert_awaited_once_with("day-planning-voice")  # followed you in


def test_auto_join_needs_voice_and_auto_join_on(mock_bot_runner):
    vm = VoiceManager(mock_bot_runner, text_output=TEXT_OUTPUT)
    for voice_config in ({"enabled": True}, {"auto_join": True}):
        with _hosting(voice_config), patch.object(asyncio, "create_task") as create_task:
            vm.auto_join()
        create_task.assert_not_called()


def test_hosts_voice_reads_voice_config_afresh(mock_bot_runner):
    vm = VoiceManager(mock_bot_runner, text_output=TEXT_OUTPUT)
    with _hosting({"enabled": True}):
        assert vm.hosts_voice()
    with _hosting({"enabled": False}):
        assert not vm.hosts_voice()
    with patch("core.voice.voice_manager.AgentsLoader") as loader_class:
        loader_class.return_value.get_agent.return_value = None
        assert not vm.hosts_voice()


def _playing_vm(mock_bot_runner):
    """A VoiceManager whose answer audio is playing."""
    vm = VoiceManager(mock_bot_runner, text_output=TEXT_OUTPUT)
    mock_vc = MagicMock()
    mock_vc.is_connected.return_value = True
    mock_vc.is_playing.return_value = True
    vm.connection.voice_client = mock_vc
    return vm, mock_vc


@pytest.mark.asyncio
async def test_speech_over_the_reply_ducks_it_and_stops_it_only_once_confirmed(mock_bot_runner):
    """Speech onset used to stop the reply outright, even for a cough."""
    vm, mock_vc = _playing_vm(mock_bot_runner)
    utterance = _spoken("Actually, make it Friday.")
    vm.floor.utterance_opened(utterance)
    assert vm.audio_queue._volume == voice_settings.BARGE_IN_DUCK_VOLUME
    mock_vc.stop_playing.assert_not_called()

    utterance.interim = "actually"  # one word: not an interruption yet
    vm.floor._on_text(utterance)
    mock_vc.stop_playing.assert_not_called()

    utterance.interim = "actually make it Friday"
    vm.floor._on_text(utterance)
    mock_vc.stop_playing.assert_called_once()
    assert utterance.confirmed
    assert vm.audio_queue._volume == 1.0, "the next reply must not start ducked"


@pytest.mark.asyncio
async def test_a_backchannel_over_the_reply_lets_it_continue(mock_bot_runner):
    vm, mock_vc = _playing_vm(mock_bot_runner)
    vm._process_speech_turn = AsyncMock()
    utterance = _spoken("Mm-hmm.")
    vm.floor.utterance_opened(utterance)
    utterance.interim = "mm-hmm"
    vm.floor._on_text(utterance)
    vm.floor.utterance_finished(utterance)
    await asyncio.gather(*list(vm.floor._overlap_tasks))

    mock_vc.stop_playing.assert_not_called()
    vm._process_speech_turn.assert_not_called()
    assert vm.audio_queue._volume == 1.0


class _Recited:
    """A live session that came back with `text`, whatever was said."""

    def __init__(self, text):
        self.text = text

    def finish(self):
        pass

    async def result(self):
        return self.text


@pytest.mark.asyncio
async def test_the_recognizer_reciting_its_vocabulary_over_the_reply_lets_it_continue(mock_bot_runner):
    """2026-10-28, 18:46: "Receipt Processor." came back from 180 ms of sound
    over William's reply, cut it off, and rode into the next turn as your
    words. A recital isn't words: no barge-in, no turn (stt.py `echo`)."""
    vm, mock_vc = _playing_vm(mock_bot_runner)
    vm._process_speech_turn = AsyncMock()
    vm.stt.vocabulary = ["Receipt Processor"]
    utterance = Utterance(user=MagicMock(display_name="Sam", bot=False))
    utterance.pcm += bytes(32 * 180)  # 180 ms of sound
    vm.floor.utterance_opened(utterance)
    utterance.stream = _Recited("Receipt Processor.")

    utterance.interim = "Receipt Processor."  # two words: enough to confirm a barge-in
    vm.floor._on_text(utterance)
    vm.floor.utterance_finished(utterance)
    await asyncio.gather(*list(vm.floor._overlap_tasks))

    assert not utterance.confirmed
    mock_vc.stop_playing.assert_not_called()
    vm._process_speech_turn.assert_not_called()
    assert vm.audio_queue._volume == 1.0


@pytest.mark.asyncio
async def test_yes_to_a_closing_question_is_an_answer_not_a_backchannel(mock_bot_runner):
    vm, mock_vc = _playing_vm(mock_bot_runner)
    vm._process_speech_turn = AsyncMock()
    vm.floor.set_reply("I found two slots. Want me to book the earlier one?")
    utterance = _say(vm, "Yes.")
    await asyncio.gather(*list(vm.floor._overlap_tasks))
    await vm._current_task

    mock_vc.stop_playing.assert_called_once()
    vm._process_speech_turn.assert_awaited_once()
    assert vm.floor.unanswered() == [utterance]


@pytest.mark.asyncio
async def test_a_discarded_blip_gives_the_reply_its_volume_back(mock_bot_runner):
    vm, mock_vc = _playing_vm(mock_bot_runner)
    utterance = _spoken("")
    vm.floor.utterance_opened(utterance)
    assert vm.audio_queue._volume < 1.0
    vm.floor.utterance_discarded(utterance)
    assert vm.audio_queue._volume == 1.0
    mock_vc.stop_playing.assert_not_called()


@pytest.mark.asyncio
async def test_voice_manager_ignores_bot_speech(mock_bot_runner):
    """
    The bot must never react to its own (or another bot's) voice: no ducking,
    no barge-in, no new turn. Note interrupt() calls audio_queue.clear(),
    which halts playback via vc.stop_playing().
    """
    vm, mock_vc = _playing_vm(mock_bot_runner)

    # Stub the turn pipeline so we can observe whether a turn is started at all.
    vm._process_speech_turn = AsyncMock()

    bot_speech = _spoken("Here is the weather for today.", user=MagicMock(bot=True, display_name="OtherBot"))
    vm.floor.utterance_opened(bot_speech)
    assert vm.audio_queue._volume == 1.0
    bot_speech.interim = "here is the weather for today"
    vm.floor._on_text(bot_speech)
    vm.floor.utterance_finished(bot_speech)
    mock_vc.stop_playing.assert_not_called()
    assert vm._active_turn == 0
    assert vm._current_task is None and not vm.floor._overlap_tasks

    # Control: the same words from a person DO duck, barge in and start a
    # turn, so the assertions above pin the bot guard, not dead code.
    human = _spoken("Here is the weather for today.", user=MagicMock(bot=False, display_name="Alice"))
    vm.floor.utterance_opened(human)
    assert vm.audio_queue._volume == voice_settings.BARGE_IN_DUCK_VOLUME
    human.interim = "here is the weather for today"
    vm.floor._on_text(human)
    mock_vc.stop_playing.assert_called_once()

    vm.floor.utterance_finished(human)
    assert vm._current_task is not None
    await vm._current_task
    vm._process_speech_turn.assert_awaited_once_with(vm._active_turn)


# ---------------------------------------------------------------------------
# One voice room: "switch me to Daisy" moves the focus, not the user
# ---------------------------------------------------------------------------

# The voices the real agent configs give them (agent_voice reads those).
CONCIERGE_VOICE = "en-US-Chirp3-HD-Aoede"
DAISY_VOICE = "en-US-Chirp3-HD-Zephyr"
FRANKIE_VOICE = "en-US-Chirp3-HD-Pulcherrima"


def _voices(vm):
    return [stream.voice.name for stream in vm.reply.tts.streams]


@pytest.mark.asyncio
async def test_a_switch_moves_the_focus_and_greets_without_a_model_call(mock_bot_runner, tmp_path):
    vm, main_agent, loader, channels = _room_fixture(mock_bot_runner, tmp_path)
    with _focused(loader):
        assert vm.focus().is_lobby and vm.focus().channel_name == "general", "a voice session starts in #general"
        await _turn(vm, "Switch me to Daisy.")

    main_agent.execute_stream.assert_not_called()
    vm.switcher._resolve.assert_not_awaited()
    assert vm.focus().key == "day-planning"
    assert vm.reply.tts.said() == ["Daisy here."]
    assert _voices(vm) == [DAISY_VOICE], "greeted in Daisy's own voice"
    channels["room"].send.assert_awaited_once_with("🎧 Now with Daisy · #day-planning")
    activity = vm.connection.bot.change_presence.await_args.kwargs["activity"]
    assert activity.name == "Daisy · #day-planning"


@pytest.mark.asyncio
async def test_after_a_switch_turns_run_exactly_as_typed_in_the_focus_channel(mock_bot_runner, tmp_path):
    """The turn runs as Concierge, on #day-planning's discord session; graphs/main
    then reaches Daisy's own session, as for a typed message."""
    vm, main_agent, loader, channels = _room_fixture(mock_bot_runner, tmp_path)
    calls = _capture_sessions(main_agent)
    with _focused(loader):
        await _turn(vm, "Switch me to Daisy.")
        await _turn(vm, "What's on my calendar tomorrow?")

    (prompt, session), = calls
    assert prompt == "What's on my calendar tomorrow?"
    assert session.session_id == "main:discord:day-planning"
    assert session.source == "discord" and session.get_surface() == "voice"
    assert session.channel_obj is channels["day-planning"]
    channels["day-planning"].send.assert_any_call("🎤 **Sam (Voice):** What's on my calendar tomorrow?")
    channels["general"].send.assert_not_called()
    assert _voices(vm)[-1] == DAISY_VOICE, "until a delegate's tokens say otherwise, it's Daisy you hear"


@pytest.mark.asyncio
async def test_take_me_to_concierge_continues_generals_conversation(mock_bot_runner, tmp_path):
    """The lobby is #general, not a conversation of the voice room's own."""
    vm, main_agent, loader, channels = _room_fixture(mock_bot_runner, tmp_path)
    calls = _capture_sessions(main_agent)
    with _focused(loader):
        await _turn(vm, "Take me to Marvin.")
        await _turn(vm, "Take me to concierge.")
        await _turn(vm, "What agents do I have?")

    assert vm.focus().is_lobby
    assert vm.reply.tts.said()[:2] == ["Marvin here.", "Concierge here."]
    assert _voices(vm)[1] == CONCIERGE_VOICE
    (_prompt, session), = calls
    assert session.session_id == "main:discord:general"
    assert session.channel_obj is channels["general"]
    channels["room"].send.assert_any_await("🎧 Now with the concierge · #general")


HANDOFF = {"type": "handoff", "agent_id": "day-planner", "name": "Daisy", "emoji": "🌼", "channel": "day-planning",
           "pointer": "→ 🌼 Daisy has this in #day-planning", "request": "Daisy, we're in Tahoe this weekend"}


def _handing_off(agent, reply="Got it, I'll plan around Tahoe."):
    """The turn's words named Daisy (graphs/main): the stream says so, then
    carries her reply, as stream_delegate relays it. Returns the prompts asked."""
    from tests.core.voice.voice_room import _final, _spoken_reply

    calls = []

    async def stream(prompt, session=None):
        calls.append((prompt, session))
        if len(calls) == 1:
            yield HANDOFF
            yield {"type": "token", "content": "🌼 Daisy: ", "agent_id": "day-planner", "is_header": True}
        yield {"type": "token", "content": _spoken_reply(reply), "agent_id": "day-planner"}
        yield _final(_spoken_reply(reply))

    agent.execute_stream = MagicMock(side_effect=stream)
    return calls


def _shown(channel):
    message = channel.send.return_value
    return [c.args[0] for c in channel.send.call_args_list if c.args] + \
           [c.kwargs.get("content", "") for c in message.edit.call_args_list]


@pytest.mark.asyncio
async def test_a_spoken_hand_off_moves_the_room_to_the_named_agents_channel(mock_bot_runner, tmp_path):
    vm, main_agent, loader, channels = _room_fixture(mock_bot_runner, tmp_path)
    channels["day-planning"].mention = "<#42>"
    calls = _handing_off(main_agent)
    with _focused(loader):
        await _turn(vm, "Daisy, we're in Tahoe this weekend")
        await _turn(vm, "Anything indoors if it rains?")

    assert vm.focus().key == "day-planning", "the room follows the conversation"
    general, daisy = _shown(channels["general"]), _shown(channels["day-planning"])
    assert general == ["🎤 **Sam (Voice):** Daisy, we're in Tahoe this weekend",
                       "→ 🌼 Daisy has this in <#42>"], "the lobby keeps only the pointer"
    assert daisy[0] == "↪️ 🎤 **Sam** in #general: Daisy, we're in Tahoe this weekend"
    assert any(s.startswith("🌼 Daisy: Got it, I'll plan around Tahoe.") for s in daisy), daisy
    assert vm.reply.tts.said()[0] == "Got it, I'll plan around Tahoe."
    assert _voices(vm)[0] == DAISY_VOICE, "heard in Daisy's voice, with no greeting first"
    channels["room"].send.assert_any_await("🎧 Now with Daisy · #day-planning")
    (_first, _), (_prompt, session) = calls
    assert session.session_id == "main:discord:day-planning", "the next turn is Daisy's channel's"


@pytest.mark.asyncio
async def test_a_hand_off_to_a_channel_the_room_cant_find_stays_put(mock_bot_runner, tmp_path):
    vm, main_agent, loader, channels = _room_fixture(mock_bot_runner, tmp_path)
    _handing_off(main_agent)
    channels["room"].guild.text_channels = [channels["general"]]
    with _focused(loader):
        await _turn(vm, "Daisy, we're in Tahoe this weekend")

    assert vm.focus().is_lobby
    assert any(s.startswith("🌼 Daisy: Got it") for s in _shown(channels["general"]))


@pytest.mark.asyncio
async def test_who_are_you_is_answered_by_the_agent_you_are_with(mock_bot_runner, tmp_path):
    """An ordinary turn in Daisy's channel: she introduces herself as she would there."""
    vm, main_agent, loader, _channels = _room_fixture(mock_bot_runner, tmp_path)
    calls = _capture_sessions(main_agent, reply="I'm Daisy, your day planner.")
    with _focused(loader):
        await _turn(vm, "Switch me to Daisy.")
        await _turn(vm, "Who are you?")

    (prompt, session), = calls
    assert prompt == "Who are you?"
    assert session.session_id == "main:discord:day-planning"
    assert vm.reply.tts.said() == ["Daisy here.", "I'm Daisy, your day planner."]
    vm.switcher._resolve.assert_not_awaited()


@pytest.mark.asyncio
async def test_aki_is_reached_through_concierge_like_any_agent(mock_bot_runner, tmp_path):
    """No special case: #agent-management is Concierge's, handed to Aki on
    the session a message typed there reaches."""
    vm, main_agent, loader, channels = _room_fixture(mock_bot_runner, tmp_path)
    calls = _capture_sessions(main_agent, reply="Sure.")
    with _focused(loader):
        await _turn(vm, "Let me talk to Aki.")
        await _turn(vm, "Design an agent for my reading list.")

    assert vm.reply.tts.said()[0] == "Aki here."
    (_prompt, session), = calls
    assert session.session_id == "main:discord:agent-management"
    assert session.channel_obj is channels["agent-management"]


@pytest.mark.asyncio
async def test_nothing_said_to_one_agent_reaches_the_next(mock_bot_runner, tmp_path):
    """Unanswered words and a closing question belong to the old focus."""
    vm, main_agent, loader, _channels = _room_fixture(mock_bot_runner, tmp_path)
    calls = _capture_sessions(main_agent)
    with _focused(loader):
        await _turn(vm, "Switch me to Daisy.")
        vm.floor._pending = [_spoken("What's on my calendar?")]  # Daisy never answered it
        vm.floor.set_reply("Want me to book the earlier slot?")
        await _turn(vm, "Take me to Marvin.")
        assert vm.floor.unanswered() == [] and vm.floor._reply == ""
        await _turn(vm, "How is the market today?")

    assert [prompt for prompt, _session in calls] == ["How is the market today?"]
    assert calls[0][1].session_id == "main:discord:market-scout"


@pytest.mark.asyncio
async def test_a_request_that_mentions_a_channel_is_still_a_normal_turn(mock_bot_runner, tmp_path):
    """Switching and asking in one sentence is out of scope: no switch, no question."""
    vm, main_agent, loader, _channels = _room_fixture(mock_bot_runner, tmp_path)
    calls = _capture_sessions(main_agent)
    with _focused(loader):
        await _turn(vm, "Go to day planning and add a dentist task")

    assert vm.focus().is_lobby
    assert [prompt for prompt, _session in calls] == ["Go to day planning and add a dentist task"]
    vm.switcher._resolve.assert_not_awaited()


@pytest.mark.asyncio
async def test_where_am_i_and_already_there_are_answered_without_a_model_call(mock_bot_runner, tmp_path):
    vm, main_agent, loader, _channels = _room_fixture(mock_bot_runner, tmp_path)
    with _focused(loader):
        await _turn(vm, "Switch me to Daisy.")
        await _turn(vm, "Where am I?")
        await _turn(vm, "Switch me to Daisy.")

    main_agent.execute_stream.assert_not_called()
    assert vm.reply.tts.said() == ["Daisy here.", "You're in day planning, with Daisy.", "You're already with Daisy."]


@pytest.mark.asyncio
async def test_a_near_miss_tells_you_the_words_and_they_switch(mock_bot_runner, tmp_path):
    vm, main_agent, loader, _channels = _room_fixture(mock_bot_runner, tmp_path)
    vm.switcher._resolve = AsyncMock(return_value="fitness-coach")
    with _focused(loader):
        await _turn(vm, "Take me to exercise.")
        assert vm.focus().is_lobby, "nothing switches until you say the words"
        await _turn(vm, "Switch to Frankie.")

    main_agent.execute_stream.assert_not_called()
    assert vm.focus().key == "fitness-coach"
    assert vm.reply.tts.said() == ["Do you mean fitness coach Frankie? Say 'switch to Frankie' to get there.",
                                   "Frankie here."]
    assert _voices(vm) == [CONCIERGE_VOICE, FRANKIE_VOICE], "the concierge suggests; Frankie greets"


@pytest.mark.asyncio
async def test_a_yes_after_do_you_mean_is_an_ordinary_request(mock_bot_runner, tmp_path):
    """No yes/no mode: nothing waits for an answer."""
    vm, main_agent, loader, _channels = _room_fixture(mock_bot_runner, tmp_path)
    vm.switcher._resolve = AsyncMock(return_value="fitness-coach")
    calls = _capture_sessions(main_agent)
    with _focused(loader):
        await _turn(vm, "Switch me to Daisy.")
        await _turn(vm, "Take me to exercise.")
        await _turn(vm, "Yes.")

    assert vm.focus().key == "day-planning"
    assert [prompt for prompt, _session in calls] == ["Yes."]
    assert calls[0][1].session_id == "main:discord:day-planning"


@pytest.mark.asyncio
async def test_anything_else_after_do_you_mean_runs_as_a_normal_turn(mock_bot_runner, tmp_path):
    vm, main_agent, loader, _channels = _room_fixture(mock_bot_runner, tmp_path)
    vm.switcher._resolve = AsyncMock(return_value="fitness-coach")
    calls = _capture_sessions(main_agent)
    with _focused(loader):
        await _turn(vm, "Take me to exercise.")
        await _turn(vm, "What's the weather like?")
        await _turn(vm, "Yes.")  # just a "yes" to the concierge

    assert vm.focus().is_lobby
    assert [prompt for prompt, _session in calls] == ["What's the weather like?", "Yes."]


@pytest.mark.asyncio
async def test_never_mind_after_do_you_mean_switches_nothing(mock_bot_runner, tmp_path):
    vm, main_agent, loader, _channels = _room_fixture(mock_bot_runner, tmp_path)
    vm.switcher._resolve = AsyncMock(return_value="fitness-coach")
    calls = _capture_sessions(main_agent)
    with _focused(loader):
        await _turn(vm, "Take me to exercise.")
        await _turn(vm, "Never mind.")
        await _turn(vm, "Yes.")

    assert vm.focus().is_lobby
    assert [prompt for prompt, _session in calls] == ["Yes."]


@pytest.mark.asyncio
async def test_nothing_close_makes_it_an_ordinary_request(mock_bot_runner, tmp_path):
    vm, main_agent, loader, _channels = _room_fixture(mock_bot_runner, tmp_path)
    calls = _capture_sessions(main_agent)
    with _focused(loader):
        await _turn(vm, "Take me to the airport on Friday.")

    vm.switcher._resolve.assert_awaited_once()
    assert vm.focus().is_lobby
    assert [prompt for prompt, _session in calls] == ["Take me to the airport on Friday."]
    assert calls[0][1].session_id == "main:discord:general"


@pytest.mark.asyncio
async def test_a_channel_missing_from_discord_is_reported_not_switched_to(mock_bot_runner, tmp_path):
    vm, main_agent, loader, channels = _room_fixture(mock_bot_runner, tmp_path)
    channels["room"].guild.text_channels = [channels["general"], channels["day-planning"]]  # #market-scout is gone
    with _focused(loader):
        await _turn(vm, "Take me to Marvin.")

    assert vm.focus().is_lobby
    assert vm.reply.tts.said() == ["I can't reach market scout right now."]
    main_agent.execute_stream.assert_not_called()


@pytest.mark.asyncio
async def test_a_new_voice_session_starts_in_the_lobby_again(mock_bot_runner, tmp_path):
    """Each join is a new voice session: it must not keep talking into the last conversation."""
    vm, _main_agent, loader, channels = _room_fixture(mock_bot_runner, tmp_path)
    with _focused(loader):
        await _turn(vm, "Switch me to Daisy.")
        vm._on_joined(channels["room"])
        assert vm.focus().is_lobby


@pytest.mark.asyncio
async def test_joining_the_voice_channel_greets_you_in_the_lobby(mock_bot_runner, tmp_path):
    vm, _main_agent, loader, _channels = _room_fixture(mock_bot_runner, tmp_path)
    with _focused(loader):
        await _turn(vm, "Switch me to Daisy.")
        vm.floor._pending = [_spoken("What's on my calendar?")]
        await vm.welcome()

    assert vm.focus().is_lobby
    assert vm.floor.unanswered() == []
    assert vm.reply.tts.said()[-1] == "Concierge here."
    assert _voices(vm)[-1] == CONCIERGE_VOICE


@pytest.mark.asyncio
async def test_join_typed_in_a_text_channel_starts_there(mock_bot_runner, tmp_path):
    vm, _main_agent, loader, channels = _room_fixture(mock_bot_runner, tmp_path)
    vm.connection.join = AsyncMock(return_value=True)
    with _focused(loader):
        assert await vm.join(None, focus_channel=channels["day-planning"]) is True

    vm.connection.join.assert_awaited_once_with("aoc-voice")
    assert vm.focus().key == "day-planning"


# ---------------------------------------------------------------------------
# A name on its own, and "go back"
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_name_on_its_own_switches_without_a_model_call(mock_bot_runner, tmp_path):
    """The prod misses: "Grace." in the lobby got "Who's it for?", and "Daisy."
    said to Marvin was answered by Marvin."""
    vm, main_agent, loader, _channels = _room_fixture(mock_bot_runner, tmp_path)
    with _focused(loader):
        await _turn(vm, "Daisy.")
        await _turn(vm, "Let's start with Marvin.")

    main_agent.execute_stream.assert_not_called()
    vm.switcher._resolve.assert_not_awaited()
    assert vm.focus().key == "market-scout"
    assert vm.reply.tts.said() == ["Daisy here.", "Marvin here."]


def _recording_traces(traces):
    return patch("core.runtime.observability.turn_trace.TurnTrace.finish", autospec=True,
                 side_effect=lambda self, session_id=None, surface="voice": traces.append(self))


@pytest.mark.asyncio
async def test_a_bare_name_made_of_noise_moves_nothing_and_reaches_no_agent(mock_bot_runner, tmp_path):
    """"Receipt Processor." at 17:44 was 0.21 voiced: noise the recognizer gave
    an agent's name. It's dropped, and the trace says it had no words."""
    vm, main_agent, loader, _channels = _room_fixture(mock_bot_runner, tmp_path)
    noise = _spoken("Frankie.")
    noise.gate = Assessment(Verdict.UNDECIDED, None, None, voiced_share=0.21)
    traces = []
    with _focused(loader, _recording_traces(traces)):
        vm.floor.utterance_opened(noise)
        vm.floor.utterance_finished(noise)
        await vm._current_task

    assert vm.focus().is_lobby
    main_agent.execute_stream.assert_not_called()
    assert vm.reply.tts.said() == []
    assert traces[0].meta["outcome"] == Outcome.NO_TRANSCRIPT
    assert vm.floor.unanswered() == []


@pytest.mark.asyncio
async def test_leaving_a_conversation_on_a_bare_name_says_how_to_go_back(mock_bot_runner, tmp_path):
    """"Frankie." at 23:33 was a mishearing in the middle of talking to Grace:
    the switch costs one phrase to undo, and no model call either way."""
    vm, main_agent, loader, _channels = _room_fixture(mock_bot_runner, tmp_path)
    calls = _capture_sessions(main_agent)
    with _focused(loader):
        await _turn(vm, "Switch me to Daisy.")
        await _turn(vm, "What's on my calendar?")
        await _turn(vm, "Frankie.")
        assert vm.focus().key == "fitness-coach"
        await _turn(vm, "Go back.")

    assert vm.focus().key == "day-planning"
    assert vm.reply.tts.said() == ["Daisy here.", "Done.", "Frankie here. Say 'go back' for Daisy.", "Daisy here."]
    assert [prompt for prompt, _session in calls] == ["What's on my calendar?"]


@pytest.mark.asyncio
async def test_the_trace_tells_a_switch_from_the_go_back_that_undoes_it(mock_bot_runner, tmp_path):
    """Both end as FOCUS_SWITCH; the command named in the trace is what lets
    the rate of switches undone be counted."""
    vm, _main_agent, loader, _channels = _room_fixture(mock_bot_runner, tmp_path)
    traces = []
    with _focused(loader, _recording_traces(traces)):
        await _turn(vm, "Frankie.")
        await _turn(vm, "Go back.")

    assert [(trace.meta["outcome"], trace.meta["command"]) for trace in traces] == [
        (Outcome.FOCUS_SWITCH, "switch"), (Outcome.FOCUS_SWITCH, "go back")]


@pytest.mark.asyncio
async def test_go_back_still_works_soon_after_talking_in_the_new_place(mock_bot_runner, tmp_path):
    vm, main_agent, loader, _channels = _room_fixture(mock_bot_runner, tmp_path)
    calls = _capture_sessions(main_agent)
    with _focused(loader):
        await _turn(vm, "Switch me to Daisy.")
        await _turn(vm, "What's on my calendar?")
        await _turn(vm, "Go back.")

    assert vm.focus().is_lobby
    assert [prompt for prompt, _session in calls] == ["What's on my calendar?"]


@pytest.mark.asyncio
async def test_go_back_is_a_request_once_you_have_been_talking_there_a_while(mock_bot_runner, tmp_path):
    """By then "go back" is more likely about what you're discussing."""
    vm, main_agent, loader, _channels = _room_fixture(mock_bot_runner, tmp_path)
    calls = _capture_sessions(main_agent)
    with _focused(loader):
        await _turn(vm, "Switch me to Daisy.")
        await _turn(vm, "What's on my calendar?")
        vm._moved_at -= voice_settings.GO_BACK_WINDOW_S + 1
        await _turn(vm, "Go back.")

    assert vm.focus().key == "day-planning"
    assert [prompt for prompt, _session in calls] == ["What's on my calendar?", "Go back."]


@pytest.mark.asyncio
async def test_go_back_undoes_a_hand_off(mock_bot_runner, tmp_path):
    """A hand-off's own reply is the named agent's, not you talking there."""
    vm, main_agent, loader, _channels = _room_fixture(mock_bot_runner, tmp_path)
    calls = _handing_off(main_agent)
    with _focused(loader):
        await _turn(vm, "Daisy, we're in Tahoe this weekend")
        assert vm.focus().key == "day-planning"
        assert vm.reply.tts.said() == ["Got it, I'll plan around Tahoe."], "out of the lobby: no hint"
        await _turn(vm, "Go back.")

    assert vm.focus().is_lobby
    assert vm.reply.tts.said()[-1] == "Concierge here."
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_a_hand_off_out_of_a_conversation_says_how_to_go_back(mock_bot_runner, tmp_path):
    """Like any move: after the named agent's reply, in its voice, so the
    answer isn't kept waiting."""
    vm, main_agent, loader, _channels = _room_fixture(mock_bot_runner, tmp_path)
    _handing_off(main_agent)
    with _focused(loader):
        await _turn(vm, "Switch me to Frankie.")
        await _turn(vm, "Daisy, we're in Tahoe this weekend")
        assert vm.focus().key == "day-planning"
        await _turn(vm, "Go back.")

    assert vm.reply.tts.said() == ["Frankie here.", "Got it, I'll plan around Tahoe.", "Say 'go back' for Frankie.",
                                   "Frankie here."]
    assert _voices(vm)[1:3] == [DAISY_VOICE, DAISY_VOICE]
    assert vm.focus().key == "fitness-coach"


@pytest.mark.asyncio
async def test_a_new_voice_session_has_nowhere_to_go_back_to(mock_bot_runner, tmp_path):
    vm, main_agent, loader, channels = _room_fixture(mock_bot_runner, tmp_path)
    calls = _capture_sessions(main_agent)
    with _focused(loader):
        await _turn(vm, "Switch me to Daisy.")
        vm._on_joined(channels["room"])
        await _turn(vm, "Go back.")

    assert vm.focus().is_lobby
    assert [prompt for prompt, _session in calls] == ["Go back."]


def _replying(agent, raw):
    """The agent answers `raw`, as written, every time."""
    from tests.core.voice.voice_room import _final

    async def stream(prompt, session=None):
        yield {"type": "token", "content": raw}
        yield _final(raw)

    agent.execute_stream = MagicMock(side_effect=stream)


@pytest.mark.asyncio
async def test_an_agent_can_hand_you_to_a_teammate(mock_bot_runner, tmp_path):
    """Asked to switch, Ted said he had no permission to move you. Now an agent
    ends its reply with <switch to="..."/>, and once the reply is said, the
    room follows and the teammate greets you, saying how to come back."""
    vm, main_agent, loader, channels = _room_fixture(mock_bot_runner, tmp_path)
    _replying(main_agent, "<speech>Frankie runs your workouts. Passing you over.</speech>"
                          'Frankie runs your workouts. <switch to="fitness-coach"/>')
    pointer = patch("core.channel.discord.stream_buffer.switch_pointer", return_value="→ Over to Frankie")
    with _focused(loader, pointer):
        await _turn(vm, "Switch me to Daisy.")
        await _turn(vm, "Who handles my workouts?")
        assert vm.focus().key == "fitness-coach"
        await _turn(vm, "Go back.")

    assert vm.reply.tts.said() == ["Daisy here.", "Frankie runs your workouts. Passing you over.",
                                   "Frankie here. Say 'go back' for Daisy.", "Daisy here."]
    shown = _shown(channels["day-planning"])
    assert not any("<switch" in line for line in shown), shown
    assert "→ Over to Frankie" in shown
    assert vm.focus().key == "day-planning", "and \"go back\" undoes it"


@pytest.mark.asyncio
async def test_a_reply_handing_you_to_no_one_moves_nothing(mock_bot_runner, tmp_path):
    vm, main_agent, loader, _channels = _room_fixture(mock_bot_runner, tmp_path)
    _replying(main_agent, '<speech>Let me get the right person.</speech>One moment. <switch to="nobody"/>')
    with _focused(loader):
        await _turn(vm, "Switch me to Daisy.")
        await _turn(vm, "Who handles my taxes?")

    assert vm.focus().key == "day-planning"
    assert vm.reply.tts.said() == ["Daisy here.", "Let me get the right person."]


def _turn_with(samples):
    utterance = Utterance()
    utterance.pcm += np.asarray(samples, dtype=np.int16).tobytes()
    return SimpleNamespace(utterances=[utterance])


def test_a_voice_is_told_from_noise_by_its_pitch():
    t = np.arange(8000) / 16000  # half a second
    voice = 8000 * np.sin(2 * np.pi * 150 * t)
    noise = np.random.default_rng(7).normal(0, 4000, len(t))
    assert VoiceManager._not_a_voice(_turn_with(voice)) is None
    assert VoiceManager._not_a_voice(_turn_with(noise)).endswith(" voiced")


def test_the_gates_figure_is_used_and_no_audio_tells_nothing():
    scored = _spoken("Frankie.")
    scored.gate = Assessment(Verdict.UNDECIDED, None, None, voiced_share=0.21)
    assert VoiceManager._not_a_voice(SimpleNamespace(utterances=[scored])) == "0.21 voiced"
    assert VoiceManager._not_a_voice(SimpleNamespace(utterances=[_spoken("Frankie.")])) is None
    assert VoiceManager._not_a_voice(_turn_with(np.zeros(100))) is None, "too little audio to tell"
    assert VoiceManager._not_a_voice(None) is None


# ---------------------------------------------------------------------------
# Voice briefings from the channel inbox
# ---------------------------------------------------------------------------

DAISY_SPEECH = "Yesterday you noted the afternoon slipped away. How's your energy this morning, one to ten?"
DAISY_QUESTION = "How's your energy this morning, one to ten?"


def _inbox_row(channel, agent_id, **kw):
    from core.storage.channel_inbox import InboxRow, Source
    base = dict(channel=channel, agent_id=agent_id, posted_at=time.time() - 3600, source=Source.SCHEDULE)
    base.update(kw)
    return InboxRow(**base)


def _briefed_room(mock_bot_runner, tmp_path, *rows, listening=True):
    """The room fixture with the inbox's briefing hook plugged in (as the
    Discord runner does), `rows` in the channel inbox, and you in the room."""
    from core.voice.briefing.hook import VoiceBriefing
    from core.storage.channel_inbox import InboxStore
    vm, main_agent, loader, channels = _room_fixture(mock_bot_runner, tmp_path)
    for row in rows:
        InboxStore().put(row)
    channels["room"].members = [MagicMock(bot=False)] if listening else []
    vm.hooks = [VoiceBriefing(hour=lambda: 8)]
    return vm, main_agent, loader, channels


def _briefing(vm):
    return vm.hooks[0]


async def _settle(vm):
    """Lets the speech writer and any announcement finish."""
    for _ in range(3):
        writers = [task for _, task in _briefing(vm).writer._tasks.values()]
        await asyncio.gather(*writers, *list(vm._side_tasks))
        await asyncio.sleep(0)


def _status(channel):
    from core.storage.channel_inbox import InboxStore
    return InboxStore().get(channel).status.value


@pytest.mark.asyncio
async def test_joining_with_unfinished_channels_greets_you_with_the_digest(mock_bot_runner, tmp_path):
    vm, _main_agent, loader, _channels = _briefed_room(
        mock_bot_runner, tmp_path,
        _inbox_row("day-planning", "day-planner", label="your morning check-in", speech=DAISY_SPEECH,
                   question=DAISY_QUESTION),
        _inbox_row("market-scout", "market-scout", label="the market scan", speech="Markets are flat."),
    )
    with _focused(loader):
        await vm.welcome()
        await _settle(vm)

    assert vm.reply.tts.said() == [
        "Morning. Two agents have something for you. Daisy is waiting for your answer. "
        "Marvin has the market scan. Start with Daisy? Say 'start with Daisy' to begin."
    ]
    assert _voices(vm) == [CONCIERGE_VOICE]


@pytest.mark.asyncio
async def test_next_agent_plays_what_the_agent_left_then_only_its_question(mock_bot_runner, tmp_path):
    vm, main_agent, loader, _channels = _briefed_room(
        mock_bot_runner, tmp_path,
        _inbox_row("day-planning", "day-planner", label="your morning check-in", speech=DAISY_SPEECH,
                   question=DAISY_QUESTION),
    )
    with _focused(loader):
        await vm.welcome()
        await _turn(vm, "Next agent.")
        assert vm.focus().key == "day-planning"
        assert _status("day-planning") == "AWAITING_REPLY"
        await _turn(vm, "Take me to concierge.")
        await _turn(vm, "Switch me to Daisy.")

    main_agent.execute_stream.assert_not_called()
    assert vm.reply.tts.said()[1:] == ["Daisy here.", DAISY_SPEECH, "Concierge here.", "Daisy here.", DAISY_QUESTION]
    assert _voices(vm)[1:3] == [DAISY_VOICE, DAISY_VOICE], "the agent speaks its own post"


@pytest.mark.asyncio
async def test_answering_by_voice_finishes_the_channels_row(mock_bot_runner, tmp_path):
    from core.storage.channel_inbox import InboxStore
    vm, main_agent, loader, _channels = _briefed_room(
        mock_bot_runner, tmp_path,
        _inbox_row("day-planning", "day-planner", label="your morning check-in", speech=DAISY_SPEECH,
                   question=DAISY_QUESTION),
    )
    calls = _capture_sessions(main_agent, reply="Good. What are your intentions for today?")
    with _focused(loader):
        await vm.welcome()
        await _turn(vm, "Start with Daisy.")
        await _turn(vm, "About a seven.")

    assert [prompt for prompt, _session in calls] == ["About a seven."]
    row = InboxStore().get("day-planning")
    assert (row.status.value, row.status_via.value) == ("DONE", "VOICE")


@pytest.mark.asyncio
async def test_next_agent_with_nothing_left_says_so(mock_bot_runner, tmp_path):
    vm, main_agent, loader, _channels = _briefed_room(mock_bot_runner, tmp_path)
    with _focused(loader):
        await vm.welcome()
        await _turn(vm, "Next agent.")

    assert vm.reply.tts.said() == ["Concierge here.", "That's everything."]
    main_agent.execute_stream.assert_not_called()


@pytest.mark.asyncio
async def test_a_reply_judged_after_you_join_is_announced_once_the_pipeline_is_idle(mock_bot_runner, tmp_path):
    from core.storage.channel_inbox import Source
    from core.voice.briefing.speech_writer import SpeechWriter
    vm, _main_agent, loader, _channels = _briefed_room(
        mock_bot_runner, tmp_path,
        _inbox_row("market-scout", "market-scout", source=Source.REPLY, label="a question for you",
                   excerpt="Should I add NVDA to your watchlist?"),
    )

    async def write(row, name):
        return "Should I add NVDA to your watchlist?", "Should I add NVDA to your watchlist?"

    _briefing(vm).writer = SpeechWriter(write=write)
    with _focused(loader):
        await vm.welcome()
        await _settle(vm)

    # Unjudged, so not in the digest; announced once the speech writer kept it.
    assert vm.reply.tts.said() == ["Concierge here.",
                                   "Marvin has a question for you. Say 'switch to Marvin' to hear it."]
    assert _voices(vm) == [CONCIERGE_VOICE, CONCIERGE_VOICE]


@pytest.mark.asyncio
async def test_no_announcement_when_nobody_is_listening(mock_bot_runner, tmp_path):
    from core.storage.channel_inbox import Source
    from core.voice.briefing.speech_writer import SpeechWriter
    vm, _main_agent, loader, _channels = _briefed_room(
        mock_bot_runner, tmp_path,
        _inbox_row("market-scout", "market-scout", source=Source.LATE_REPLY, label="a reply for you",
                   excerpt="Done: the scan is in."),
        listening=False,
    )

    async def write(row, name):
        return "The scan is in.", None

    _briefing(vm).writer = SpeechWriter(write=write)
    with _focused(loader):
        await vm.welcome()
        await _settle(vm)

    assert vm.reply.tts.said() == ["Concierge here."]


# ---------------------------------------------------------------------------
# Hooks: how features built on the room join in (core/voice/hooks.py)
# ---------------------------------------------------------------------------

class _RecordingHook:
    """A hook that records every call, and does what the test sets."""

    def __init__(self, greeting=None, knows_next_agent=False, says_on_arrival=None):
        self.greeting, self.knows_next_agent, self.says_on_arrival = greeting, knows_next_agent, says_on_arrival
        self.calls = []

    def joined(self, room):
        self.calls.append(("joined",))
        return self.greeting

    def left(self, room):
        self.calls.append(("left",))

    async def next_agent(self, room, say):
        self.calls.append(("next_agent",))
        if self.knows_next_agent:
            await say("Handled.", "main")
        return self.knows_next_agent

    async def arrived(self, room, focus, speak):
        self.calls.append(("arrived", focus.key))
        if self.says_on_arrival:
            await speak(self.says_on_arrival)

    def turn(self, room, focus):
        self.calls.append(("turn", focus.key))


class _BrokenHook(_RecordingHook):
    def joined(self, room):
        raise RuntimeError("joined broke")

    async def next_agent(self, room, say):
        raise RuntimeError("next_agent broke")

    async def arrived(self, room, focus, speak):
        raise RuntimeError("arrived broke")

    def turn(self, room, focus):
        raise RuntimeError("turn broke")


@pytest.mark.asyncio
async def test_a_hook_may_greet_you_instead_of_the_concierge(mock_bot_runner, tmp_path):
    vm, _agent, loader, _channels = _room_fixture(mock_bot_runner, tmp_path)
    vm.hooks = [_RecordingHook(greeting=None), _RecordingHook(greeting="Morning. Two agents have something for you.")]
    with _focused(loader):
        await vm.welcome()
    assert vm.reply.tts.said() == ["Morning. Two agents have something for you."]
    assert [h.calls for h in vm.hooks] == [[("joined",)], [("joined",)]]


@pytest.mark.asyncio
async def test_a_hook_answers_next_agent_before_the_switcher_and_the_agent(mock_bot_runner, tmp_path):
    vm, main_agent, loader, _channels = _room_fixture(mock_bot_runner, tmp_path)
    hook = _RecordingHook(knows_next_agent=True)
    vm.hooks = [hook]
    with _focused(loader):
        await _turn(vm, "Take me to the next agent.")

    vm.switcher._resolve.assert_not_awaited()  # the switcher never saw it
    main_agent.execute_stream.assert_not_called()
    assert vm.reply.tts.said() == ["Handled."]
    assert ("next_agent",) in hook.calls


@pytest.mark.asyncio
async def test_next_agent_goes_to_the_agent_when_no_hook_answers_it(mock_bot_runner, tmp_path):
    vm, main_agent, loader, _channels = _room_fixture(mock_bot_runner, tmp_path)
    calls = _capture_sessions(main_agent)
    hook = _RecordingHook()
    vm.hooks = [hook]
    with _focused(loader):
        await _turn(vm, "Next agent.")

    assert ("next_agent",) in hook.calls
    assert [prompt for prompt, _session in calls] == ["Next agent."]


@pytest.mark.asyncio
async def test_arriving_lets_hooks_speak_in_the_agents_voice(mock_bot_runner, tmp_path):
    vm, _agent, loader, _channels = _room_fixture(mock_bot_runner, tmp_path)
    hook = _RecordingHook(says_on_arrival="How's your energy this morning, one to ten?")
    vm.hooks = [hook]
    with _focused(loader):
        await _turn(vm, "Switch me to Daisy.")

    assert vm.reply.tts.said() == ["Daisy here.", "How's your energy this morning, one to ten?"]
    assert _voices(vm) == [DAISY_VOICE, DAISY_VOICE]
    assert ("arrived", "day-planning") in hook.calls
    # A lone "yes" over the end of what she said counts as an answer.
    assert vm.floor._reply == "How's your energy this morning, one to ten?"


@pytest.mark.asyncio
async def test_a_turn_tells_hooks_which_channel_it_runs_in(mock_bot_runner, tmp_path):
    vm, main_agent, loader, _channels = _room_fixture(mock_bot_runner, tmp_path)
    _capture_sessions(main_agent)
    hook = _RecordingHook()
    vm.hooks = [hook]
    with _focused(loader):
        await _turn(vm, "Switch me to Daisy.")
        await _turn(vm, "What's on my calendar?")
    assert hook.calls[-1] == ("turn", "day-planning")


@pytest.mark.asyncio
async def test_a_broken_hook_is_skipped_and_the_room_carries_on(mock_bot_runner, tmp_path):
    vm, main_agent, loader, _channels = _room_fixture(mock_bot_runner, tmp_path)
    calls = _capture_sessions(main_agent)
    vm.hooks = [_BrokenHook()]
    with _focused(loader):
        await vm.welcome()
        await _turn(vm, "Switch me to Daisy.")
        await _turn(vm, "Next agent.")
        await _turn(vm, "What's on my calendar?")
        await vm.leave()

    assert vm.reply.tts.said()[:2] == ["Concierge here.", "Daisy here."]
    assert [prompt for prompt, _session in calls] == ["Next agent.", "What's on my calendar?"]


@pytest.mark.asyncio
async def test_say_when_idle_waits_for_the_turn_and_needs_a_listener(mock_bot_runner, tmp_path):
    vm, _agent, loader, channels = _room_fixture(mock_bot_runner, tmp_path)
    channels["room"].members = [MagicMock(bot=False)]
    vm.audio_queue.is_playing = MagicMock(return_value=True)  # a reply is playing
    with _focused(loader), patch("core.voice.voice_manager.IDLE_POLL_S", 0.001):
        vm.say_when_idle("Marvin has a question for you. Say 'switch to Marvin' to hear it.")
        await asyncio.sleep(0.01)
        assert vm.reply.tts.said() == [], "not over a reply"
        vm.audio_queue.is_playing.return_value = False
        await asyncio.gather(*list(vm._side_tasks))
        assert vm.reply.tts.said() == ["Marvin has a question for you. Say 'switch to Marvin' to hear it."]

        channels["room"].members = []  # you left
        vm.say_when_idle("Ted has a reply for you.")
        await asyncio.gather(*list(vm._side_tasks))
    assert len(vm.reply.tts.said()) == 1
