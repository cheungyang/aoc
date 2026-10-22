import asyncio
import time
import pytest
import os
from unittest.mock import AsyncMock, MagicMock, patch, ANY
import discord
from core.channel.response_parser import AgentResponse
from core.voice import settings as voice_settings
from core.voice.listening.utterance import Transcript, Utterance
from core.voice.speech.missing_speech import alert
from core.voice.speech.reply import ERROR_LINE
from core.voice.voice_manager import VoiceManager
from tests.helpers import FakeTTS

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


def _final(raw):
    """The final_response event for a reply the agent wrote as `raw`, parsed as production parses it."""
    response = AgentResponse.from_string(raw)
    return {"type": "final_response", "text": response.text, "response": response}


def _spoken_reply(text):
    """A reply that follows <voice_mode>: the speech, then the text."""
    return f"<speech>{text}</speech>{text}"


def test_voice_manager_init(mock_bot_runner):
    with patch("core.voice.voice_manager.AgentsLoader") as mock_loader:
        mock_agent = MagicMock()
        mock_agent.config = {"voice_config": {"audio_blurp": "assets/sounds/blurps/concierge_ping.wav"}}
        mock_loader.return_value.get_agent.return_value = mock_agent

        vm = VoiceManager(mock_bot_runner)
        assert vm.agent_id == "main"
        assert vm.config.get("audio_blurp") == "assets/sounds/blurps/concierge_ping.wav"


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
    
    # Mock Agent execution: speech for the ear, then the text reply
    raw = "<speech>You have two tasks scheduled.</speech>You have 2 tasks scheduled."

    async def fake_stream(*args, **kwargs):
        yield {"type": "token", "content": raw}
        yield _final(raw)

    mock_agent = MagicMock()
    mock_agent.execute_stream = MagicMock(side_effect=fake_stream)
    
    vm.reply.tts = FakeTTS()

    with patch("core.voice.voice_manager.AgentsLoader") as mock_loader, \
         patch("core.voice.speech.blurp_generator.BlurpGenerator.get_blurp_audio", return_value=None):
             
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
        
        # 3. Verify the speech was synthesized and the text posted, each in its own lane
        assert vm.reply.tts.said() == ["You have two tasks scheduled."]
        mock_text_channel.send.assert_any_call("You have 2 tasks scheduled.")

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

    # Nothing is spoken for the discarded turn
    assert vm.reply.tts.streams == []
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
        raw = _spoken_reply("Sam, I tried to reach Daisy...")
        yield {"type": "token", "content": raw}
        yield _final(raw)

    async def fake_stream_2(*args, **kwargs):
        raw = _spoken_reply("Here are the available agents: Daisy, Rick, Concierge.")
        yield {"type": "token", "content": raw}
        yield _final(raw)

    mock_agent = MagicMock()
    mock_agent.execute_stream = MagicMock(side_effect=[fake_stream_1(), fake_stream_2()])
    
    vm.reply.tts = FakeTTS()
    user = MagicMock(display_name="Sam", bot=False)

    with patch("core.voice.voice_manager.AgentsLoader") as mock_loader, \
         patch("core.voice.speech.blurp_generator.BlurpGenerator.get_blurp_audio", return_value=None):
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
        assert len(vm.reply.tts.said()) == 2


# ---------------------------------------------------------------------------
# Dual track: the speaker gets the <speech> block, the channel gets the text
# ---------------------------------------------------------------------------

SPEECH = ("Two ideas: Point Reyes is two hours away and free, and Mount Tam is "
          "forty-five minutes with eight dollar parking.")
STRUCTURED_REPLY = (
    "Here is what I found for you.\n"
    "\n"
    "- **Point Reyes** - 2 hours, $0 entry\n"
    "- **Mount Tam** - 45 minutes, $8 parking\n"
)
SPOKEN_STRUCTURED_REPLY = f"<speech>{SPEECH}</speech>\n{STRUCTURED_REPLY}"


QUESTION = "Where should we go this weekend?"


def _voice_turn_fixture(mock_bot_runner, tmp_path, reply):
    """Wires a VoiceManager up to a mocked guild, agent, and audio queue.
    The agent writes `reply`, streamed as one token."""
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
        yield _final(reply)

    agent = MagicMock()
    agent.execute_stream = MagicMock(side_effect=fake_stream)

    vm.reply.tts = FakeTTS()

    # The floor and the reply share this queue, so it's stubbed in place.
    vm.audio_queue.start = MagicMock()
    vm.audio_queue.put = AsyncMock()
    vm.audio_queue.is_playing = MagicMock(return_value=False)

    return vm, agent, text_channel


def _posted(text_channel):
    """Everything the text channel showed: its sends and its edits."""
    message = text_channel.send.return_value
    shown = [c.args[0] for c in text_channel.send.call_args_list if c.args]
    shown += [c.kwargs.get("content", "") for c in message.edit.call_args_list]
    return "\n".join(shown)


def _synthesized(vm):
    """What was spoken: one string per stretch of speech."""
    return vm.reply.tts.said()


def _chunks(vm):
    """How many chunks went to TTS, across stretches."""
    return sum(len(stream.chunks) for stream in vm.reply.tts.streams)


def _sent(text_channel):
    """The messages the text channel was sent, in order (edits not included)."""
    return [c.args[0] for c in text_channel.send.call_args_list if c.args]


def _notes(text_channel):
    return [m for m in _sent(text_channel) if m.startswith("⚠️")]


def _play_at_once(vm):
    """Queued audio starts playing at once, so the trace records at the turn's end."""
    async def play_now(speaker, on_start=None):
        if on_start:
            on_start()

    vm.audio_queue.put = AsyncMock(side_effect=play_now)


@pytest.mark.asyncio
async def test_voice_turn_keeps_markdown_intact_in_the_text_channel(mock_bot_runner, tmp_path):
    """
    A voice turn previously produced audio and nothing else: the reply existed
    only as sound, so anything with a link, a price, or a name in it was
    unrecoverable once spoken. The channel now gets what the agent wrote,
    markdown and all, and never its <speech> block.
    """
    vm, agent, text_channel = _voice_turn_fixture(mock_bot_runner, tmp_path, SPOKEN_STRUCTURED_REPLY)
    await _run_turn(vm, agent)

    posted = _posted(text_channel)
    assert "- **Point Reyes**" in posted, "markdown was flattened in the text channel"
    assert "$8 parking" in posted
    assert "speech>" not in posted and "forty-five" not in posted


@pytest.mark.asyncio
async def test_voice_turn_speaks_the_speech_block_not_the_markdown(mock_bot_runner, tmp_path):
    vm, agent, _channel = _voice_turn_fixture(mock_bot_runner, tmp_path, SPOKEN_STRUCTURED_REPLY)
    await _run_turn(vm, agent)

    assert _synthesized(vm) == [SPEECH]
    # What was heard decides whether a lone "yes" next is an answer.
    assert vm.floor._reply == SPEECH


@pytest.mark.asyncio
async def test_the_preamble_is_spoken_while_the_tool_runs(mock_bot_runner, tmp_path):
    """Bridge phrases are gone: the agent says what it's checking in the same
    response as the tool call, so the wait isn't silent."""
    vm, agent, text_channel = _voice_turn_fixture(mock_bot_runner, tmp_path, "unused")
    streams = vm.reply.tts.streams
    answer = "<speech>You're free after four.</speech>You're free after 16:00."

    async def stream(*_args, **_kwargs):
        yield {"type": "token", "content": "<speech>Checking your calendar.</speech>"}
        yield {"type": "tool_start", "tool_name": "gog", "tool_args": {}}
        await _until(lambda: streams and streams[0].finished)  # said in full while the tool runs
        yield {"type": "tool_end", "tool_name": "gog", "output": "free"}
        yield {"type": "token", "content": answer}
        yield _final(answer)  # the final message is the answer's

    agent.execute_stream = MagicMock(side_effect=stream)
    await _run_turn(vm, agent)

    assert _synthesized(vm) == ["Checking your calendar.", "You're free after four."]
    posted = _posted(text_channel)
    assert "You're free after 16:00." in posted and "Checking" not in posted


@pytest.mark.asyncio
async def test_a_reply_without_speech_speaks_the_fallback_and_notes_it_under_the_reply(mock_bot_runner, tmp_path):
    reply = "You have two meetings today. Standup is at nine. The dentist is at three."
    vm, agent, text_channel = _voice_turn_fixture(mock_bot_runner, tmp_path, reply)
    _play_at_once(vm)
    traces = []
    with patch("core.runtime.turn_trace.TurnTrace.finish", autospec=True,
               side_effect=lambda self, session_id=None, surface="voice": traces.append(self)):
        await _run_turn(vm, agent)

    assert _synthesized(vm) == ["You have two meetings today. The rest is in the channel."]
    assert (traces[0].meta["speech"], traces[0].meta["speaker"]) == ("missing", "main")
    assert reply in _posted(text_channel)
    assert _sent(text_channel)[-1] == alert("main"), "the note goes under the reply, in the same channel"


@pytest.mark.asyncio
async def test_every_miss_is_noted_against_the_agent_that_replied(mock_bot_runner, tmp_path):
    vm, agent, text_channel = _voice_turn_fixture(mock_bot_runner, tmp_path, "unused")

    async def stream(*_args, **_kwargs):
        yield {"type": "token", "content": "🌼 Daisy: ", "agent_id": "day-planner", "is_header": True}
        yield {"type": "token", "content": "You have two meetings today.", "agent_id": "day-planner"}
        yield _final("You have two meetings today.")

    agent.execute_stream = MagicMock(side_effect=stream)
    await _run_turn(vm, agent)
    await _run_turn(vm, agent)

    assert _notes(text_channel) == [alert("day-planner")] * 2


@pytest.mark.asyncio
async def test_a_spoken_reply_gets_no_note(mock_bot_runner, tmp_path):
    vm, agent, text_channel = _voice_turn_fixture(mock_bot_runner, tmp_path, _spoken_reply("Logged your run."))
    for _ in range(3):
        await _run_turn(vm, agent)
    assert _notes(text_channel) == []


@pytest.mark.asyncio
async def test_a_turn_without_a_model_reply_is_not_held_to_speech(mock_bot_runner, tmp_path):
    """A system command ("[new]") ends the stream before any model runs."""
    vm, agent, _channel = _voice_turn_fixture(mock_bot_runner, tmp_path, "unused")

    async def handled_by_a_command(*_args, **_kwargs):
        return
        yield  # an async generator that yields nothing

    agent.execute_stream = MagicMock(side_effect=handled_by_a_command)
    await _run_turn(vm, agent)
    assert _notes(_channel) == []
    assert _synthesized(vm) == []


@pytest.mark.asyncio
async def test_a_delegates_error_is_shown_and_never_read_out(mock_bot_runner, tmp_path):
    """The replay caught a mid-stream failure landing inside an open <speech>
    block: the error would have been read aloud. It goes to the channel; the
    speaker says one plain line, and a failed run isn't held to <speech>."""
    vm, agent, text_channel = _streaming_fixture(mock_bot_runner, tmp_path, [
        {"type": "token", "content": "🛎️ Butler: ", "agent_id": "home-steward", "is_header": True},
        {"type": "token", "content": "<speech>Checking the thermostat.", "agent_id": "home-steward"},
        {"type": "token", "content": "\n[Error: 503 UNAVAILABLE]", "agent_id": "home-steward", "is_error": True},
        _final("Checking the thermostat."),
    ])
    await _run_turn(vm, agent)

    assert _synthesized(vm) == ["Checking the thermostat.", ERROR_LINE]
    assert "[Error: 503 UNAVAILABLE]" in _posted(text_channel)
    assert _notes(text_channel) == []


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
        {"type": "token", "content": _spoken_reply("You have two meetings today."), "agent_id": "day-planner"},
        _final(_spoken_reply("You have two meetings today.")),
    ])
    await _run_turn(vm, agent)

    spoken = " ".join(_synthesized(vm))
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
        {"type": "token", "content": _spoken_reply("Done, the lights are off.")},
        _final(_spoken_reply("Done, the lights are off.")),
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
            if _chunks(vm):
                break
            await asyncio.sleep(0.01)
        # The agent ran and speech was synthesized while the transcript post was stuck...
        agent.execute_stream.assert_called_once()
        assert _chunks(vm)
        assert sent == []
        release.set()
        await vm._current_task

    # ...and the channel still shows the transcript above the reply.
    assert sent[0].startswith("🎤 **Sam (Voice):**")
    assert "Done, the lights are off." in sent[1]


@pytest.mark.asyncio
async def test_slow_discord_edits_do_not_hold_up_speech(mock_bot_runner, tmp_path):
    """Text and speech run as separate lanes: a stuck channel write can't delay synthesis."""
    vm, agent, text_channel = _streaming_fixture(mock_bot_runner, tmp_path, [
        {"type": "token", "content": "<speech>First sentence is here. "},
        {"type": "token", "content": "Second sentence follows now.</speech>"},
        {"type": "token", "content": "First sentence is here. Second sentence follows now."},
        _final(_spoken_reply("First sentence is here. Second sentence follows now.")),
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
            if _chunks(vm) >= 2:
                break
            await asyncio.sleep(0.01)
        assert _chunks(vm) >= 2, "speech waited on the text channel"
        release.set()
        await vm._current_task


@pytest.mark.asyncio
async def test_turn_trace_records_stages_and_first_audio(mock_bot_runner, tmp_path):
    vm, agent, _channel = _streaming_fixture(mock_bot_runner, tmp_path, [
        {"type": "tool_start", "tool_name": "home_assistant", "tool_args": {}},
        {"type": "tool_end", "tool_name": "home_assistant", "output": "ok"},
        {"type": "token", "content": _spoken_reply("The kitchen lights are off now.")},
        _final(_spoken_reply("The kitchen lights are off now.")),
    ])
    traces = []
    _play_at_once(vm)
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
    assert trace.meta["speech"] == "ok"


@pytest.mark.asyncio
async def test_turn_trace_waits_for_queued_audio_before_recording(mock_bot_runner, tmp_path):
    """A short reply is fully queued before playback starts; TTFA must still be captured."""
    vm, agent, _channel = _streaming_fixture(mock_bot_runner, tmp_path, [
        {"type": "token", "content": _spoken_reply("Logged it.")},
        _final(_spoken_reply("Logged it.")),
    ])
    starts = []
    vm.audio_queue.put = AsyncMock(side_effect=lambda speaker, on_start=None: starts.append(on_start))
    traces = []
    with patch("core.runtime.turn_trace.TurnTrace.finish", autospec=True,
               side_effect=lambda self, session_id=None, surface="voice": traces.append(self)):
        await _run_turn(vm, agent)
        assert traces == [], "recorded before the answer was heard"
        starts[0]()  # playback begins
    assert len(traces) == 1 and "audio_start" in traces[0].marks
    assert traces[0].marks["audio_start"] >= traces[0].marks["turn_end"]


# ---------------------------------------------------------------------------
# One voice room: "switch me to Daisy" moves the focus, not the user
# ---------------------------------------------------------------------------

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


# The voices the real agent configs give them (agent_voice reads those).
CONCIERGE_VOICE = "en-US-Chirp3-HD-Aoede"
DAISY_VOICE = "en-US-Chirp3-HD-Zephyr"
FRANKIE_VOICE = "en-US-Chirp3-HD-Pulcherrima"


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


def _voices(vm):
    return [stream.voice.name for stream in vm.reply.tts.streams]


def _focused(loader, *patches):
    """The patches every focus test runs under."""
    from contextlib import ExitStack
    stack = ExitStack()
    stack.enter_context(patch("core.voice.voice_manager.AgentsLoader", return_value=loader))
    stack.enter_context(patch("core.voice.speech.blurp_generator.BlurpGenerator.get_blurp_audio", return_value=None))
    for extra in patches:
        stack.enter_context(extra)
    return stack


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
    """I1: Concierge, on #day-planning's discord session; graphs/main then
    reaches Daisy's own session, as for a typed message."""
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
    """D1: the lobby is #general, not a conversation of the voice room's own."""
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
    """I3: unanswered words and a closing question belong to the old focus."""
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
    """B20's successor: a join must not keep talking into the last conversation."""
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
# Part 3: voice briefings from the channel inbox
# ---------------------------------------------------------------------------

DAISY_SPEECH = "Yesterday you noted the afternoon slipped away. How's your energy this morning, one to ten?"
DAISY_QUESTION = "How's your energy this morning, one to ten?"


def _inbox_row(channel, agent_id, **kw):
    from core.inbox.model import InboxRow, Source
    base = dict(channel=channel, agent_id=agent_id, posted_at=time.time() - 3600, source=Source.SCHEDULE)
    base.update(kw)
    return InboxRow(**base)


def _briefed_room(mock_bot_runner, tmp_path, *rows, listening=True):
    """The room fixture with the inbox's briefing hook plugged in (as the
    Discord runner does), `rows` in the channel inbox, and you in the room."""
    from core.inbox.store import InboxStore
    from core.inbox.voice_briefing import VoiceBriefing
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
    from core.inbox.store import InboxStore
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
    from core.inbox.store import InboxStore
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
    from core.inbox.model import Source
    from core.inbox.speech_writer import SpeechWriter
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
    from core.inbox.model import Source
    from core.inbox.speech_writer import SpeechWriter
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

    def __init__(self, greeting=None, handles=(), says_on_arrival=None):
        self.greeting, self.handles, self.says_on_arrival = greeting, set(handles), says_on_arrival
        self.calls = []

    def joined(self, room):
        self.calls.append(("joined",))
        return self.greeting

    def left(self, room):
        self.calls.append(("left",))

    async def handle(self, room, transcript, say):
        self.calls.append(("handle", transcript))
        if transcript.rstrip(".") in self.handles:
            await say("Handled.", "main")
            return True
        return False

    async def arrived(self, room, focus, speak):
        self.calls.append(("arrived", focus.key))
        if self.says_on_arrival:
            await speak(self.says_on_arrival)

    def turn(self, room, focus):
        self.calls.append(("turn", focus.key))


class _BrokenHook(_RecordingHook):
    def joined(self, room):
        raise RuntimeError("joined broke")

    async def handle(self, room, transcript, say):
        raise RuntimeError("handle broke")

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
async def test_hooks_hear_an_utterance_before_the_switcher_and_the_agent(mock_bot_runner, tmp_path):
    vm, main_agent, loader, _channels = _room_fixture(mock_bot_runner, tmp_path)
    hook = _RecordingHook(handles={"Take me to the next agent"})
    vm.hooks = [hook]
    with _focused(loader):
        await _turn(vm, "Take me to the next agent.")

    vm.switcher._resolve.assert_not_awaited()  # the switcher never saw it
    main_agent.execute_stream.assert_not_called()
    assert vm.reply.tts.said() == ["Handled."]
    assert ("handle", "Take me to the next agent.") in hook.calls


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
        await _turn(vm, "What's on my calendar?")
        await vm.leave()

    assert vm.reply.tts.said()[:2] == ["Concierge here.", "Daisy here."]
    assert [prompt for prompt, _session in calls] == ["What's on my calendar?"]


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
        yield {"type": "token", "content": _spoken_reply(reply)}
        yield _final(_spoken_reply(reply))

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
    assert "🎤 **Sam (Voice):** Add milk to the shopping list. And eggs." in _mirrored(text_channel)
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
            yield {"type": "token", "content": "<speech>It will rain after four this afternoon. "}
            yield {"type": "token", "content": "Bring an umbrella"}
            await asyncio.Event().wait()  # still talking when interrupted
        yield {"type": "token", "content": _spoken_reply("Sunny all week.")}
        yield _final(_spoken_reply("Sunny all week."))

    agent.execute_stream = MagicMock(side_effect=stream)
    started = []

    async def play_now(speaker, on_start=None):
        started.append(speaker)
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
