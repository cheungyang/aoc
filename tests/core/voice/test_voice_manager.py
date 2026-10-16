import asyncio
import time
import pytest
import os
from unittest.mock import AsyncMock, MagicMock, patch, ANY
import discord
from core.voice import settings as voice_settings
from core.voice.listening.utterance import Transcript, Utterance
from core.voice.voice_manager import VoiceManager
from tests.helpers import fake_audio_source

@pytest.fixture
def mock_bot_runner():
    runner = MagicMock()
    runner.agent_id = "main"
    runner.bot = MagicMock()
    runner.bot.guilds = []
    runner.bot.loop = MagicMock()
    return runner


def _spoken(text, user=None, speech_end_at=None):
    """An utterance whose transcript is already final, as the live transcriber leaves it."""
    utterance = Utterance(user=user if user is not None else MagicMock(display_name="Alva", bot=False))
    utterance.transcript = Transcript(text, "gemini_live")
    utterance.speech_end_at = speech_end_at
    return utterance


def _say(vm, text, user=None, speech_end_at=None):
    """The user said `text`: one utterance, from onset to the end of the turn."""
    utterance = _spoken(text, user, speech_end_at)
    vm.floor.utterance_opened(utterance)
    vm.floor.utterance_finished(utterance)
    return utterance


def _talk_over(vm, text, user=None):
    """The user starts talking and their words confirm an interruption, but
    they haven't finished yet. Returns the open utterance."""
    utterance = _spoken(text, user)
    vm.floor.utterance_opened(utterance)
    utterance.interim = text.lower().rstrip(".?!")
    vm.floor._on_text(utterance)
    return utterance


async def _until(condition, timeout=2.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out waiting"
        await asyncio.sleep(0.005)


def test_voice_manager_init(mock_bot_runner):
    with patch("core.voice.voice_manager.AgentsLoader") as mock_loader:
        mock_agent = MagicMock()
        mock_agent.config = {
            "voice_config": {
                "tts_voice": "en-US-JennyNeural",
                "tts_speed": 1.0
            }
        }
        mock_loader.return_value.get_agent.return_value = mock_agent
        
        vm = VoiceManager(mock_bot_runner)
        assert vm.agent_id == "main"
        assert vm.config.get("tts_voice") == "en-US-JennyNeural"


def test_stt_vocabulary_is_every_agent_name_and_spoken_id(mock_bot_runner):
    """Names like Frankie or Daisy are what a generic recognizer gets wrong."""
    with patch("core.voice.voice_manager.AgentsLoader") as mock_loader:
        loader = mock_loader.return_value
        loader.get_agent.return_value = None
        loader.list_agent_ids.return_value = ["day-planner", "main"]
        loader.get_agent_config.side_effect = lambda agent_id: {
            "day-planner": {"name": "Daisy"}, "main": {"name": "Concierge"},
        }[agent_id]
        vm = VoiceManager(mock_bot_runner)
    assert vm.stt.vocabulary == ["Daisy", "day planner", "Concierge", "main"]

def _playing_vm(mock_bot_runner):
    """A VoiceManager whose answer audio is playing."""
    vm = VoiceManager(mock_bot_runner)
    mock_vc = MagicMock()
    mock_vc.is_connected.return_value = True
    mock_vc.is_playing.return_value = True
    vm.connection.voice_client = mock_vc
    return vm, mock_vc


@pytest.mark.asyncio
async def test_speech_over_the_reply_ducks_it_and_stops_it_only_once_confirmed(mock_bot_runner):
    """B19: speech onset used to stop the reply outright, even for a cough."""
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
async def test_a_finished_utterance_runs_the_pipeline(mock_bot_runner, tmp_path):
    vm = VoiceManager(mock_bot_runner)
    
    mock_vc = MagicMock()
    mock_vc.is_connected.return_value = True
    mock_vc.is_playing.return_value = False
    
    mock_guild = MagicMock()
    mock_text_channel = MagicMock()
    mock_text_channel.name = "general"
    mock_text_channel.send = AsyncMock()
    mock_guild.text_channels = [mock_text_channel]
    
    mock_vc.channel.name = "general-voice"
    mock_vc.channel.guild = mock_guild
    vm.connection.voice_client = mock_vc
    
    # Mock Agent execution
    async def fake_stream(*args, **kwargs):
        yield {"type": "token", "content": "You have 2 tasks scheduled."}
        yield {"type": "final_response", "text": "You have 2 tasks scheduled.", "response": None}

    mock_agent = MagicMock()
    mock_agent.execute_stream = MagicMock(side_effect=fake_stream)
    
    # Mock TTS file synthesis
    fake_tts_file = tmp_path / "reply.mp3"
    fake_tts_file.write_bytes(b"dummy_mp3_data")
    vm.reply.tts_engine.synthesize_to_file = AsyncMock(return_value=str(fake_tts_file))
    
    with patch("core.voice.voice_manager.AgentsLoader") as mock_loader, \
         patch("core.voice.speech.blurp_generator.BlurpGenerator.get_blurp_audio", return_value=None), \
         patch("discord.FFmpegPCMAudio", fake_audio_source()):
             
        mock_loader.return_value.get_agent.return_value = mock_agent
        
        mock_user = MagicMock(display_name="Alice", bot=False)
        _say(vm, "Check my tasks for today", user=mock_user)
        await vm._current_task
        
        # 1. Verify Transcript posted to text channel
        mock_text_channel.send.assert_any_call("🎤 **Alice (Voice):** Check my tasks for today")
        
        # 2. Verify Agent executed with shared source='discord'
        mock_agent.execute_stream.assert_called_once_with(
            "Check my tasks for today",
            session=ANY
        )
        called_session = mock_agent.execute_stream.call_args[1]["session"]
        assert called_session.agent_id == "main"
        assert called_session.source == "discord"
        
        # 3. Verify TTS synthesized and queued
        vm.reply.tts_engine.synthesize_to_file.assert_awaited_once_with("You have 2 tasks scheduled.")

        # 4. Answered: nothing is carried into the next turn
        assert vm.floor.unanswered() == []

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

@pytest.mark.asyncio
async def test_an_interruption_while_the_agent_works_discards_its_answer(mock_bot_runner, tmp_path):
    vm, agent, _channel = _voice_turn_fixture(mock_bot_runner, tmp_path, "unused")

    # The user talks over the agent before its first token.
    async def slow_stream(*args, **kwargs):
        _talk_over(vm, "Actually, make it Friday.")
        yield {"type": "token", "content": "Old answer that should be discarded"}
        yield {"type": "final_response", "text": "Old answer that should be discarded", "response": None}
        
    agent.execute_stream = MagicMock(side_effect=slow_stream)
    
    with patch("core.voice.voice_manager.AgentsLoader") as mock_loader, \
         patch("core.voice.speech.blurp_generator.BlurpGenerator.get_blurp_audio", return_value=None):
        mock_loader.return_value.get_agent.return_value = agent
        
        first = _say(vm, "Move my dentist appointment to Thursday.")
        await asyncio.gather(vm._current_task, return_exceptions=True)

    # TTS synthesis should NOT be called for old discarded turn
    vm.reply.tts_engine.synthesize_to_file.assert_not_called()
    vm.audio_queue.put.assert_not_awaited()
    # Never heard, so the request rides along with the next turn (B19).
    assert vm.floor.unanswered() == [first]

def test_voice_manager_play_blurp_stops_existing_audio(mock_bot_runner, tmp_path):
    vm = VoiceManager(mock_bot_runner)
    mock_vc = MagicMock()
    mock_vc.is_connected.return_value = True
    mock_vc.is_playing.return_value = True
    vm.connection.voice_client = mock_vc
    
    fake_blurp = tmp_path / "blurp.wav"
    fake_blurp.write_bytes(b"RIFFblurp")
    
    with patch("core.voice.speech.blurp_generator.BlurpGenerator.get_blurp_audio", return_value=str(fake_blurp)), \
         patch("discord.FFmpegPCMAudio"):
        vm.reply.cue()
        mock_vc.stop_playing.assert_called_once()
        mock_vc.play.assert_called_once()

@pytest.mark.asyncio
async def test_voice_manager_barge_in_and_second_sentence_flow(mock_bot_runner, tmp_path):
    """
    Tests complete real-world flow:
    1. User speaks sentence 1 -> bot answers and plays the response.
    2. During playback, user starts speaking sentence 2: the reply is ducked, not stopped.
    3. Their words confirm the interruption: playback stops (without killing the reader).
    4. User finishes sentence 2.
    5. Bot replies to sentence 2 alone: sentence 1 was answered, so it isn't asked again.
    """
    vm = VoiceManager(mock_bot_runner)
    mock_vc = MagicMock()
    mock_vc.is_connected.return_value = True
    mock_vc.is_playing.return_value = False
    
    mock_guild = MagicMock()
    mock_text_channel = MagicMock(name="general")
    mock_text_channel.name = "general"
    mock_text_channel.send = AsyncMock()
    mock_guild.text_channels = [mock_text_channel]
    
    mock_vc.channel.name = "general-voice"
    mock_vc.channel.guild = mock_guild
    vm.connection.voice_client = mock_vc
    
    async def fake_stream_1(*args, **kwargs):
        yield {"type": "token", "content": "Alva, I tried to reach Daisy..."}
        yield {"type": "final_response", "text": "Alva, I tried to reach Daisy...", "response": None}

    async def fake_stream_2(*args, **kwargs):
        yield {"type": "token", "content": "Here are the available agents: Daisy, Rick, Concierge."}
        yield {"type": "final_response", "text": "Here are the available agents: Daisy, Rick, Concierge.", "response": None}

    mock_agent = MagicMock()
    mock_agent.execute_stream = MagicMock(side_effect=[fake_stream_1(), fake_stream_2()])
    
    fake_tts1 = tmp_path / "reply1.mp3"
    fake_tts1.write_bytes(b"audio1")
    fake_tts2 = tmp_path / "reply2.mp3"
    fake_tts2.write_bytes(b"audio2")
    
    vm.reply.tts_engine.synthesize_to_file = AsyncMock(side_effect=[str(fake_tts1), str(fake_tts2)])
    
    user = MagicMock(display_name="Alva", bot=False)
    
    with patch("core.voice.voice_manager.AgentsLoader") as mock_loader, \
         patch("core.voice.speech.blurp_generator.BlurpGenerator.get_blurp_audio", return_value=None), \
         patch("discord.FFmpegPCMAudio", fake_audio_source()):
        mock_loader.return_value.get_agent.return_value = mock_agent
        
        # Turn 1: "Help me ask Daisy to say hi."
        _say(vm, "Help me ask Daisy to say hi.", user=user)
        await vm._current_task
            
        assert mock_agent.execute_stream.call_count == 1
        
        # Now simulate playback active
        mock_vc.is_playing.return_value = True
        
        # Turn 2: user starts speaking over the reply...
        second = _spoken("Tell me the list of agents.", user=user)
        vm.floor.utterance_opened(second)
        mock_vc.stop_playing.assert_not_called()  # ducked only
        # ...and their words make it an interruption.
        second.interim = "tell me the list"
        vm.floor._on_text(second)
        mock_vc.stop_playing.assert_called_once() # Playback stopped!
        
        # Turn 2: User finishes speaking second sentence
        vm.floor.utterance_finished(second)
        await vm._current_task
            
        # Verify second sentence was answered, on its own
        assert mock_agent.execute_stream.call_count == 2
        assert mock_agent.execute_stream.call_args_list[1][0][0] == "Tell me the list of agents."
        assert vm.reply.tts_engine.synthesize_to_file.call_count == 2


# ---------------------------------------------------------------------------
# Text and speech diverge on a voice turn
# ---------------------------------------------------------------------------

STRUCTURED_REPLY = (
    "Here is what I found for you.\n"
    "\n"
    "- **Point Reyes** - 2 hours, $0 entry\n"
    "- **Mount Tam** - 45 minutes, $8 parking\n"
)


QUESTION = "Where should we go this weekend?"


def _voice_turn_fixture(mock_bot_runner, tmp_path, reply):
    """Wires a VoiceManager up to a mocked guild, agent, and audio queue."""
    vm = VoiceManager(mock_bot_runner)

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
        yield {"type": "final_response", "text": reply, "response": None}

    agent = MagicMock()
    agent.execute_stream = MagicMock(side_effect=fake_stream)

    tts_file = tmp_path / "reply.mp3"
    tts_file.write_bytes(b"audio")
    vm.reply.tts_engine.synthesize_to_file = AsyncMock(return_value=str(tts_file))

    # The floor and the reply share this queue, so it's stubbed in place.
    vm.audio_queue.start = MagicMock()
    vm.audio_queue.put = AsyncMock()
    vm.audio_queue.is_playing = MagicMock(return_value=False)

    return vm, agent, text_channel


@pytest.mark.asyncio
async def test_voice_turn_keeps_markdown_intact_in_the_text_channel(mock_bot_runner, tmp_path):
    """
    A voice turn previously produced audio and nothing else: the reply existed
    only as sound, so anything with a link, a price, or a name in it was
    unrecoverable once spoken. The channel now gets what the agent wrote,
    markdown and all, while only the synthesiser gets the rewritten version.
    """
    vm, agent, text_channel = _voice_turn_fixture(mock_bot_runner, tmp_path, STRUCTURED_REPLY)
    vm.reply.verbalizer.verbalize = AsyncMock(
        return_value="Point Reyes is two hours away and free. Mount Tam is forty five minutes with eight dollar parking."
    )

    with patch("core.voice.voice_manager.AgentsLoader") as mock_loader, \
         patch("core.voice.speech.blurp_generator.BlurpGenerator.get_blurp_audio", return_value=None), \
         patch("discord.FFmpegPCMAudio"):
        mock_loader.return_value.get_agent.return_value = agent
        _say(vm, QUESTION)
        await vm._current_task

    posted = "\n".join(
        call.args[0] for call in text_channel.send.call_args_list if call.args
    )
    assert "- **Point Reyes**" in posted, "markdown was flattened in the text channel"
    assert "$8 parking" in posted


@pytest.mark.asyncio
async def test_voice_turn_speaks_verbalized_text_not_markdown(mock_bot_runner, tmp_path):
    vm, agent, _channel = _voice_turn_fixture(mock_bot_runner, tmp_path, STRUCTURED_REPLY)
    vm.reply.verbalizer.verbalize = AsyncMock(
        return_value="Point Reyes is two hours away and free. Mount Tam is forty five minutes with eight dollar parking."
    )

    with patch("core.voice.voice_manager.AgentsLoader") as mock_loader, \
         patch("core.voice.speech.blurp_generator.BlurpGenerator.get_blurp_audio", return_value=None), \
         patch("discord.FFmpegPCMAudio"):
        mock_loader.return_value.get_agent.return_value = agent
        _say(vm, QUESTION)
        await vm._current_task

    # The list block reached the verbalizer...
    verbalized_inputs = "\n".join(c.args[0] for c in vm.reply.verbalizer.verbalize.call_args_list)
    assert "- **Point Reyes**" in verbalized_inputs

    # ...and nothing with a bullet or asterisk reached the synthesiser.
    spoken = [c.args[0] for c in vm.reply.tts_engine.synthesize_to_file.call_args_list]
    assert spoken, "nothing was synthesized"
    for line in spoken:
        assert "*" not in line, f"markdown leaked into speech: {line!r}"
        assert not line.lstrip().startswith("-"), f"bullet leaked into speech: {line!r}"
    assert "Point Reyes" in " ".join(spoken)


@pytest.mark.asyncio
async def test_voice_turn_prose_reply_skips_the_verbalizer(mock_bot_runner, tmp_path):
    # The common case must not pay a model round-trip before the first word.
    vm, agent, _channel = _voice_turn_fixture(
        mock_bot_runner, tmp_path, "I booked the table for seven under your name."
    )
    vm.reply.verbalizer.verbalize = AsyncMock()

    with patch("core.voice.voice_manager.AgentsLoader") as mock_loader, \
         patch("core.voice.speech.blurp_generator.BlurpGenerator.get_blurp_audio", return_value=None), \
         patch("discord.FFmpegPCMAudio"):
        mock_loader.return_value.get_agent.return_value = agent
        _say(vm, QUESTION)
        await vm._current_task

    vm.reply.verbalizer.verbalize.assert_not_awaited()
    spoken = [c.args[0] for c in vm.reply.tts_engine.synthesize_to_file.call_args_list]
    assert "I booked the table for seven under your name." in " ".join(spoken)


def test_voice_manager_verbalizer_can_be_disabled_per_agent(mock_bot_runner):
    with patch("core.voice.voice_manager.AgentsLoader") as mock_loader:
        agent = MagicMock()
        agent.config = {"voice_config": {"verbalize": False, "verbalizer_model": "custom-model"}}
        mock_loader.return_value.get_agent.return_value = agent

        vm = VoiceManager(mock_bot_runner)
        assert vm.reply.verbalizer.enabled is False
        assert vm.reply.verbalizer.model_name == "custom-model"


def test_voice_manager_verbalizer_is_on_by_default(mock_bot_runner):
    with patch("core.voice.voice_manager.AgentsLoader") as mock_loader:
        agent = MagicMock()
        agent.config = {"voice_config": {}}
        mock_loader.return_value.get_agent.return_value = agent

        vm = VoiceManager(mock_bot_runner)
        assert vm.reply.verbalizer.enabled is True


# ---------------------------------------------------------------------------
# Latency work: lanes, the transcript mirror, headers, and the turn trace
# ---------------------------------------------------------------------------

def _streaming_fixture(mock_bot_runner, tmp_path, events):
    """A voice turn whose agent yields `events`; returns (vm, agent, text_channel)."""
    vm, agent, text_channel = _voice_turn_fixture(mock_bot_runner, tmp_path, "unused")

    async def fake_stream(*_args, **_kwargs):
        for event in events:
            yield event

    agent.execute_stream = MagicMock(side_effect=fake_stream)
    return vm, agent, text_channel


async def _run_turn(vm, agent):
    with patch("core.voice.voice_manager.AgentsLoader") as mock_loader, \
         patch("core.voice.speech.blurp_generator.BlurpGenerator.get_blurp_audio", return_value=None):
        mock_loader.return_value.get_agent.return_value = agent
        _say(vm, QUESTION)
        await vm._current_task


@pytest.mark.asyncio
async def test_delegation_header_is_shown_but_not_spoken(mock_bot_runner, tmp_path):
    """B14: the '🌼 Daisy: ' attribution was read aloud before every delegated answer."""
    vm, agent, text_channel = _streaming_fixture(mock_bot_runner, tmp_path, [
        {"type": "token", "content": "🌼 Daisy: ", "agent_id": "day-planner", "is_header": True},
        {"type": "token", "content": "You have two meetings today.", "agent_id": "day-planner"},
        {"type": "final_response", "text": "You have two meetings today.", "response": None},
    ])
    await _run_turn(vm, agent)

    spoken = " ".join(c.args[0] for c in vm.reply.tts_engine.synthesize_to_file.call_args_list)
    assert "Daisy" not in spoken
    assert "You have two meetings today." in spoken
    message = text_channel.send.return_value
    shown = [c.args[0] for c in text_channel.send.call_args_list if c.args]
    shown += [c.kwargs.get("content", "") for c in message.edit.call_args_list]
    assert any(s.startswith("🌼 Daisy: You have two meetings today.") for s in shown), shown


@pytest.mark.asyncio
async def test_transcript_mirror_does_not_delay_the_agent(mock_bot_runner, tmp_path):
    """The mirror used to be awaited before the agent started; now only the text lane waits."""
    vm, agent, text_channel = _streaming_fixture(mock_bot_runner, tmp_path, [
        {"type": "token", "content": "Done, the lights are off."},
        {"type": "final_response", "text": "Done, the lights are off.", "response": None},
    ])
    release = asyncio.Event()
    sent = []
    reply_message = MagicMock(edit=AsyncMock(), delete=AsyncMock())

    async def slow_send(content=None, **_kwargs):
        if content and content.startswith("🎤"):
            await release.wait()
        sent.append(content)
        return reply_message

    text_channel.send = AsyncMock(side_effect=slow_send)

    with patch("core.voice.voice_manager.AgentsLoader") as mock_loader, \
         patch("core.voice.speech.blurp_generator.BlurpGenerator.get_blurp_audio", return_value=None):
        mock_loader.return_value.get_agent.return_value = agent
        _say(vm, QUESTION)
        for _ in range(50):
            if vm.reply.tts_engine.synthesize_to_file.await_count:
                break
            await asyncio.sleep(0.01)
        # The agent ran and speech was synthesized while the transcript post was stuck...
        agent.execute_stream.assert_called_once()
        vm.reply.tts_engine.synthesize_to_file.assert_awaited()
        assert sent == []
        release.set()
        await vm._current_task

    # ...and the channel still shows the transcript above the reply.
    assert sent[0].startswith("🎤 **Alva (Voice):**")
    assert "Done, the lights are off." in sent[1]


@pytest.mark.asyncio
async def test_slow_discord_edits_do_not_hold_up_speech(mock_bot_runner, tmp_path):
    """Text and speech run as separate lanes: a stuck channel write can't delay synthesis."""
    vm, agent, text_channel = _streaming_fixture(mock_bot_runner, tmp_path, [
        {"type": "token", "content": "First sentence is here. "},
        {"type": "token", "content": "Second sentence follows now."},
        {"type": "final_response", "text": "First sentence is here. Second sentence follows now.", "response": None},
    ])
    vm.config["post_transcript_to_chat"] = False
    release = asyncio.Event()

    async def stuck_send(*_args, **_kwargs):
        await release.wait()
        return MagicMock(edit=AsyncMock(), delete=AsyncMock())

    text_channel.send = AsyncMock(side_effect=stuck_send)

    with patch("core.voice.voice_manager.AgentsLoader") as mock_loader, \
         patch("core.voice.speech.blurp_generator.BlurpGenerator.get_blurp_audio", return_value=None):
        mock_loader.return_value.get_agent.return_value = agent
        _say(vm, QUESTION)
        for _ in range(50):
            if vm.reply.tts_engine.synthesize_to_file.await_count >= 2:
                break
            await asyncio.sleep(0.01)
        assert vm.reply.tts_engine.synthesize_to_file.await_count >= 2, "speech waited on the text channel"
        release.set()
        await vm._current_task


@pytest.mark.asyncio
async def test_turn_trace_records_stages_and_first_audio(mock_bot_runner, tmp_path):
    vm, agent, _channel = _streaming_fixture(mock_bot_runner, tmp_path, [
        {"type": "tool_start", "tool_name": "home_assistant", "tool_args": {}},
        {"type": "tool_end", "tool_name": "home_assistant", "output": "ok"},
        {"type": "token", "content": "The kitchen lights are off now."},
        {"type": "final_response", "text": "The kitchen lights are off now.", "response": None},
    ])
    vm.reply.bridge_manager.get_or_create_bridge_audio = AsyncMock(return_value=None)
    traces = []

    async def play_now(path, priority=False, auto_delete=True, on_start=None):
        if on_start:
            on_start()

    vm.audio_queue.put = AsyncMock(side_effect=play_now)
    utterance = _spoken(QUESTION, speech_end_at=time.monotonic() - 0.9)
    utterance.stream = MagicMock(timings={"final_after_end_ms": 412.4})  # the live session's timings
    with patch("core.runtime.turn_trace.TurnTrace.finish", autospec=True,
               side_effect=lambda self, session_id=None, surface="voice": traces.append((self, session_id))):
        with patch("core.voice.voice_manager.AgentsLoader") as mock_loader, \
             patch("core.voice.speech.blurp_generator.BlurpGenerator.get_blurp_audio", return_value=None):
            mock_loader.return_value.get_agent.return_value = agent
            vm.floor.utterance_opened(utterance)
            vm.floor.utterance_finished(utterance)
            await vm._current_task

    assert len(traces) == 1
    trace, session_id = traces[0]
    assert session_id == "main:discord:general"
    for stage in ("speech_end", "vad_final", "stt_done", "agent_start", "first_token",
                  "first_chunk", "tts_first_byte", "audio_start", "turn_end"):
        assert stage in trace.marks, stage
    # Anchored at the last voiced frame: the VAD's silence wait is inside the trace.
    assert trace.marks["vad_final"] >= 850
    assert [e["kind"] for e in trace.events if e["kind"].startswith("tool")] == ["tool_start", "tool_end"]
    assert trace.meta["stt"] == "gemini_live"
    assert trace.meta["stt_final_after_end_ms"] == 412


@pytest.mark.asyncio
async def test_turn_trace_waits_for_queued_audio_before_recording(mock_bot_runner, tmp_path):
    """A short reply is fully queued before playback starts; TTFA must still be captured."""
    vm, agent, _channel = _streaming_fixture(mock_bot_runner, tmp_path, [
        {"type": "token", "content": "Logged it."},
        {"type": "final_response", "text": "Logged it.", "response": None},
    ])
    starts = []
    vm.audio_queue.put = AsyncMock(side_effect=lambda path, priority=False, auto_delete=True, on_start=None: starts.append(on_start))
    traces = []
    with patch("core.runtime.turn_trace.TurnTrace.finish", autospec=True,
               side_effect=lambda self, session_id=None, surface="voice": traces.append(self)):
        await _run_turn(vm, agent)
        assert traces == [], "recorded before the answer was heard"
        starts[0]()  # playback begins
    assert len(traces) == 1 and "audio_start" in traces[0].marks
    assert traces[0].marks["audio_start"] >= traces[0].marks["turn_end"]


# ---------------------------------------------------------------------------
# Room switching by voice
# ---------------------------------------------------------------------------

def _room_fixture(mock_bot_runner, tmp_path):
    vm, agent, text_channel = _voice_turn_fixture(mock_bot_runner, tmp_path, "unused")

    general = MagicMock(id=1)
    general.name = "general-voice"
    day = MagicMock(id=2)
    day.name = "day-planning-voice"
    guild = vm.connection.voice_client.channel.guild
    guild.voice_channels = [general, day]
    general.guild = guild
    day.guild = guild
    vm.connection.voice_client.channel = general
    mock_bot_runner.get_hosted_voice_channels = MagicMock(return_value=["general-voice", "day-planning-voice"])

    loader = MagicMock()
    loader.get_agent.return_value = agent
    loader.eligible_agents_for_channel.side_effect = lambda ch, exclude_agent_id=None: {"day-planning": ["day-planner"]}.get(ch, [])
    loader.get_agent_config.side_effect = lambda agent_id: {"day-planner": {"name": "Daisy"}}.get(agent_id)
    loader.sole_eligible_agent.side_effect = lambda ch, exclude_agent_id=None: {"day-planning": "day-planner"}.get(ch)

    user = MagicMock(display_name="Alva", bot=False)
    return vm, agent, user, loader, day


@pytest.mark.asyncio
async def test_room_switch_moves_the_user_and_greets_without_a_model_call(mock_bot_runner, tmp_path):
    vm, agent, user, loader, day = _room_fixture(mock_bot_runner, tmp_path)

    async def move_to(room):
        vm.connection.voice_client.channel = room  # the runner's voice-state handler follows the user

    user.move_to = AsyncMock(side_effect=move_to)
    vm.connection.join = AsyncMock()
    with patch("core.voice.voice_manager.AgentsLoader", return_value=loader), \
         patch("core.voice.room.room_switch.AgentsLoader", return_value=loader), \
         patch("core.voice.speech.blurp_generator.BlurpGenerator.get_blurp_audio", return_value=None):
        _say(vm, "Take me to day planning.", user=user)
        await vm._current_task

    user.move_to.assert_awaited_once_with(day)
    agent.execute_stream.assert_not_called()
    vm.connection.join.assert_not_awaited()
    vm.reply.tts_engine.synthesize_to_file.assert_awaited_once_with("Daisy here.")


@pytest.mark.asyncio
async def test_room_switch_by_agent_name_joins_directly_if_the_bot_does_not_follow(mock_bot_runner, tmp_path):
    vm, agent, user, loader, day = _room_fixture(mock_bot_runner, tmp_path)
    user.move_to = AsyncMock()
    vm.connection.join = AsyncMock()
    with patch("core.voice.voice_manager.AgentsLoader", return_value=loader), \
         patch("core.voice.room.room_switch.AgentsLoader", return_value=loader), \
         patch("core.voice.room.room_switch.ROOM_FOLLOW_TIMEOUT_S", 0.05), \
         patch("core.voice.speech.blurp_generator.BlurpGenerator.get_blurp_audio", return_value=None):
        _say(vm, "Let me talk to Daisy", user=user)
        await vm._current_task

    user.move_to.assert_awaited_once_with(day)
    vm.connection.join.assert_awaited_once_with("2")
    agent.execute_stream.assert_not_called()


@pytest.mark.asyncio
async def test_a_request_that_mentions_a_room_is_still_a_normal_turn(mock_bot_runner, tmp_path):
    vm, agent, user, loader, _day = _room_fixture(mock_bot_runner, tmp_path)
    user.move_to = AsyncMock()

    async def fake_stream(*_args, **_kwargs):
        yield {"type": "final_response", "text": "Added.", "response": None}

    agent.execute_stream = MagicMock(side_effect=fake_stream)
    with patch("core.voice.voice_manager.AgentsLoader", return_value=loader), \
         patch("core.voice.room.room_switch.AgentsLoader", return_value=loader), \
         patch("core.voice.speech.blurp_generator.BlurpGenerator.get_blurp_audio", return_value=None):
        _say(vm, "Go to day planning and add a dentist task", user=user)
        await vm._current_task

    user.move_to.assert_not_awaited()
    agent.execute_stream.assert_called_once()


@pytest.mark.asyncio
async def test_room_switch_without_move_permission_says_so(mock_bot_runner, tmp_path):
    vm, agent, user, loader, _day = _room_fixture(mock_bot_runner, tmp_path)
    user.move_to = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "Missing Permissions"))
    with patch("core.voice.voice_manager.AgentsLoader", return_value=loader), \
         patch("core.voice.room.room_switch.AgentsLoader", return_value=loader), \
         patch("core.voice.speech.blurp_generator.BlurpGenerator.get_blurp_audio", return_value=None):
        _say(vm, "Switch to day planning", user=user)
        await vm._current_task

    spoken = vm.reply.tts_engine.synthesize_to_file.call_args.args[0]
    assert "Move Members" in spoken
    agent.execute_stream.assert_not_called()


@pytest.mark.asyncio
async def test_a_room_request_leaves_an_unanswered_question_behind(mock_bot_runner, tmp_path):
    """The newest words decide a room switch; what the old room never answered
    isn't merged into it, or carried to the new room."""
    vm, agent, user, loader, day = _room_fixture(mock_bot_runner, tmp_path)
    vm.floor._pending = [_spoken("What's on my calendar?", user=user)]

    async def move_to(room):
        vm.connection.voice_client.channel = room

    user.move_to = AsyncMock(side_effect=move_to)
    with patch("core.voice.voice_manager.AgentsLoader", return_value=loader), \
         patch("core.voice.room.room_switch.AgentsLoader", return_value=loader), \
         patch("core.voice.speech.blurp_generator.BlurpGenerator.get_blurp_audio", return_value=None):
        _say(vm, "Take me to day planning.", user=user)
        await vm._current_task

    user.move_to.assert_awaited_once_with(day)
    agent.execute_stream.assert_not_called()
    assert vm.floor.unanswered() == []


# ---------------------------------------------------------------------------
# B19: unanswered words are merged into the next turn, not dropped
# ---------------------------------------------------------------------------

def _scripted_agent(agent, replies):
    """Each execute_stream call takes the next reply; None thinks until the
    turn is cancelled. Returns the prompts the agent was given."""
    prompts = []

    async def stream(prompt, session=None):
        prompts.append(prompt)
        reply = replies.pop(0)
        if reply is None:
            await asyncio.Event().wait()
        yield {"type": "token", "content": reply}
        yield {"type": "final_response", "text": reply, "response": None}

    agent.execute_stream = MagicMock(side_effect=stream)
    return prompts


def _mirrored(text_channel):
    return [c.args[0] for c in text_channel.send.call_args_list if c.args and c.args[0].startswith("🎤")]


@pytest.mark.asyncio
async def test_a_split_utterance_keeps_both_halves(mock_bot_runner, tmp_path):
    """The user pauses long enough to end the turn, then goes on. The first half
    used to be dropped; now its turn is abandoned unheard, and the next turn
    answers both halves as one request."""
    vm, agent, text_channel = _voice_turn_fixture(mock_bot_runner, tmp_path, "unused")
    prompts = _scripted_agent(agent, [None, "Added milk and eggs."])
    with patch("core.voice.voice_manager.AgentsLoader") as mock_loader, \
         patch("core.voice.speech.blurp_generator.BlurpGenerator.get_blurp_audio", return_value=None):
        mock_loader.return_value.get_agent.return_value = agent
        _say(vm, "Add milk to the shopping list.")
        await _until(lambda: prompts)  # the agent is working on the first half
        rest = _talk_over(vm, "And eggs.")  # two words over a busy agent
        vm.floor.utterance_finished(rest)
        await vm._current_task

    assert prompts == ["Add milk to the shopping list.", "Add milk to the shopping list. And eggs."]
    assert "🎤 **Alva (Voice):** Add milk to the shopping list. And eggs." in _mirrored(text_channel)
    assert vm.floor.unanswered() == []


@pytest.mark.asyncio
async def test_a_one_word_continuation_is_merged_once_it_is_heard(mock_bot_runner, tmp_path):
    """One word doesn't confirm an interruption while it is spoken; once it is
    final and isn't a backchannel, it interrupts and is merged."""
    vm, agent, _channel = _voice_turn_fixture(mock_bot_runner, tmp_path, "unused")
    prompts = _scripted_agent(agent, [None, "I'll remind you tomorrow."])
    with patch("core.voice.voice_manager.AgentsLoader") as mock_loader, \
         patch("core.voice.speech.blurp_generator.BlurpGenerator.get_blurp_audio", return_value=None):
        mock_loader.return_value.get_agent.return_value = agent
        _say(vm, "Remind me to call mom")
        await _until(lambda: prompts)
        _say(vm, "Tomorrow.")
        await asyncio.gather(*list(vm.floor._overlap_tasks))
        await vm._current_task

    assert prompts[-1] == "Remind me to call mom Tomorrow."


@pytest.mark.asyncio
async def test_words_whose_answer_was_heard_are_not_asked_again(mock_bot_runner, tmp_path):
    vm, agent, _channel = _voice_turn_fixture(mock_bot_runner, tmp_path, "unused")
    prompts = []

    async def stream(prompt, session=None):
        prompts.append(prompt)
        if len(prompts) == 1:
            yield {"type": "token", "content": "It will rain after four this afternoon. "}
            yield {"type": "token", "content": "Bring an umbrella"}
            await asyncio.Event().wait()  # still talking when interrupted
        yield {"type": "token", "content": "Sunny all week."}
        yield {"type": "final_response", "text": "Sunny all week.", "response": None}

    agent.execute_stream = MagicMock(side_effect=stream)
    started = []

    async def play_now(path, priority=False, auto_delete=True, on_start=None):
        started.append(path)
        if on_start:
            on_start()

    vm.audio_queue.put = AsyncMock(side_effect=play_now)
    with patch("core.voice.voice_manager.AgentsLoader") as mock_loader, \
         patch("core.voice.speech.blurp_generator.BlurpGenerator.get_blurp_audio", return_value=None):
        mock_loader.return_value.get_agent.return_value = agent
        _say(vm, "What's the weather today?")
        await _until(lambda: started)  # the first sentence is playing
        vm.audio_queue.is_playing.return_value = True
        rest = _talk_over(vm, "And the rest of the week?")
        vm.floor.utterance_finished(rest)
        await vm._current_task

    assert prompts == ["What's the weather today?", "And the rest of the week?"]
    assert vm.floor.unanswered() == []


@pytest.mark.asyncio
async def test_stop_stops_the_agent_without_becoming_a_request(mock_bot_runner, tmp_path):
    vm, agent, text_channel = _voice_turn_fixture(mock_bot_runner, tmp_path, "unused")
    prompts = _scripted_agent(agent, [None])
    with patch("core.voice.voice_manager.AgentsLoader") as mock_loader, \
         patch("core.voice.speech.blurp_generator.BlurpGenerator.get_blurp_audio", return_value=None):
        mock_loader.return_value.get_agent.return_value = agent
        _say(vm, "Read me all my emails.")
        await _until(lambda: prompts)
        stop = _talk_over(vm, "Stop.")
        vm.floor.utterance_finished(stop)
        await vm._current_task

    assert prompts == ["Read me all my emails."], "'stop' must not be sent to the agent"
    assert not any("Stop." in line for line in _mirrored(text_channel))
    assert vm.floor.unanswered() == [], "'stop' drops the request it cut off"


@pytest.mark.asyncio
async def test_wait_keeps_the_request_for_what_comes_next(mock_bot_runner, tmp_path):
    vm, agent, _channel = _voice_turn_fixture(mock_bot_runner, tmp_path, "unused")
    prompts = _scripted_agent(agent, [None, "Moved it to Friday."])
    with patch("core.voice.voice_manager.AgentsLoader") as mock_loader, \
         patch("core.voice.speech.blurp_generator.BlurpGenerator.get_blurp_audio", return_value=None):
        mock_loader.return_value.get_agent.return_value = agent
        first = _say(vm, "Move the dentist to Thursday.")
        await _until(lambda: prompts)
        hold = _talk_over(vm, "Wait.")
        vm.floor.utterance_finished(hold)
        await vm._current_task
        assert prompts == ["Move the dentist to Thursday."]
        assert vm.floor.unanswered() == [first]

        _say(vm, "Make it Friday.")
        await vm._current_task

    assert prompts[-1] == "Move the dentist to Thursday. Make it Friday."


@pytest.mark.asyncio
async def test_never_mind_drops_the_request(mock_bot_runner, tmp_path):
    vm, agent, _channel = _voice_turn_fixture(mock_bot_runner, tmp_path, "unused")
    prompts = _scripted_agent(agent, [None, "Sunny."])
    with patch("core.voice.voice_manager.AgentsLoader") as mock_loader, \
         patch("core.voice.speech.blurp_generator.BlurpGenerator.get_blurp_audio", return_value=None):
        mock_loader.return_value.get_agent.return_value = agent
        _say(vm, "Book a table for two.")
        await _until(lambda: prompts)
        cancel = _talk_over(vm, "Never mind.")
        vm.floor.utterance_finished(cancel)
        await vm._current_task
        assert vm.floor.unanswered() == []

        _say(vm, "What's the weather?")
        await vm._current_task

    assert prompts == ["Book a table for two.", "What's the weather?"]


@pytest.mark.asyncio
async def test_noise_does_not_resend_an_unanswered_request(mock_bot_runner, tmp_path):
    vm, agent, _channel = _voice_turn_fixture(mock_bot_runner, tmp_path, "unused")
    prompts = _scripted_agent(agent, [None])
    with patch("core.voice.voice_manager.AgentsLoader") as mock_loader, \
         patch("core.voice.speech.blurp_generator.BlurpGenerator.get_blurp_audio", return_value=None):
        mock_loader.return_value.get_agent.return_value = agent
        first = _say(vm, "Move the dentist to Thursday.")
        await _until(lambda: prompts)
        hold = _talk_over(vm, "Hold on.")
        vm.floor.utterance_finished(hold)
        await vm._current_task

        _say(vm, "")  # a cough: speech to the VAD, no words to the transcriber
        await vm._current_task

    assert prompts == ["Move the dentist to Thursday."]
    assert vm.floor.unanswered() == [first]


@pytest.mark.asyncio
async def test_unanswered_words_expire(mock_bot_runner, tmp_path):
    vm, agent, _channel = _voice_turn_fixture(mock_bot_runner, tmp_path, "unused")
    prompts = _scripted_agent(agent, ["Hello."])
    stale = _spoken("What did I have for lunch?")
    stale.started_at -= voice_settings.UNANSWERED_MAX_AGE_S + 1
    vm.floor._pending = [stale]
    with patch("core.voice.voice_manager.AgentsLoader") as mock_loader, \
         patch("core.voice.speech.blurp_generator.BlurpGenerator.get_blurp_audio", return_value=None):
        mock_loader.return_value.get_agent.return_value = agent
        _say(vm, "Good morning.")
        await vm._current_task

    assert prompts == ["Good morning."]
    assert vm.floor.unanswered() == []
