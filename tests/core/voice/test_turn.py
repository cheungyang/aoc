"""A voice turn (core/voice/turn.py), run through the room: the pipeline, the
two lanes (the speaker gets the <speech> block, the channel the text), latency
and the turn trace, and unanswered words merged into the next turn."""
import asyncio
import time
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from core.voice.foundation import settings as voice_settings
from core.voice.speech.missing_speech import alert
from core.voice.speech.reply import ERROR_LINE
from core.voice.voice_manager import VoiceManager
from tests.core.voice.voice_room import (
    QUESTION, TEXT_OUTPUT, _final, _play_at_once, _say, _spoken, _spoken_reply, _streaming_fixture,
    _voice_turn_fixture,
)
from tests.helpers import FakeTTS


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


@pytest.mark.asyncio
async def test_a_finished_utterance_runs_the_pipeline(mock_bot_runner, tmp_path):
    vm = VoiceManager(mock_bot_runner, text_output=TEXT_OUTPUT)
    
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
    # Never heard, so the request rides along with the next turn.
    assert vm.floor.unanswered() == [first]


def test_voice_manager_play_blurp_stops_existing_audio(mock_bot_runner, tmp_path):
    vm = VoiceManager(mock_bot_runner, text_output=TEXT_OUTPUT)
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
    vm = VoiceManager(mock_bot_runner, text_output=TEXT_OUTPUT)
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
    with patch("core.runtime.observability.turn_trace.TurnTrace.finish", autospec=True,
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
    """A stream that ends before any model runs has nothing to hold to speech."""
    vm, agent, _channel = _voice_turn_fixture(mock_bot_runner, tmp_path, "unused")

    async def nothing_streamed(*_args, **_kwargs):
        return
        yield  # an async generator that yields nothing

    agent.execute_stream = MagicMock(side_effect=nothing_streamed)
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
# Latency: lanes, the transcript mirror, headers, and the turn trace
# ---------------------------------------------------------------------------

async def _run_turn(vm, agent):
    with patch("core.voice.voice_manager.AgentsLoader") as mock_loader, \
         patch("core.voice.speech.blurp_generator.BlurpGenerator.get_blurp_audio", return_value=None):
        mock_loader.return_value.get_agent.return_value = agent
        _say(vm, QUESTION)
        await vm._current_task


@pytest.mark.asyncio
async def test_delegation_header_is_shown_but_not_spoken(mock_bot_runner, tmp_path):
    """The '🌼 Daisy: ' attribution used to be read aloud before every delegated answer."""
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
    with patch("core.runtime.observability.turn_trace.TurnTrace.finish", autospec=True,
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
    with patch("core.runtime.observability.turn_trace.TurnTrace.finish", autospec=True,
               side_effect=lambda self, session_id=None, surface="voice": traces.append(self)):
        await _run_turn(vm, agent)
        assert traces == [], "recorded before the answer was heard"
        starts[0]()  # playback begins
    assert len(traces) == 1 and "audio_start" in traces[0].marks
    assert traces[0].marks["audio_start"] >= traces[0].marks["turn_end"]


# ---------------------------------------------------------------------------
# Unanswered words are merged into the next turn, not dropped
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
