import pytest
import os
from unittest.mock import AsyncMock, MagicMock, patch
import discord
from core.voice.voice_manager import VoiceManager

@pytest.fixture
def mock_bot_runner():
    runner = MagicMock()
    runner.agent_id = "main"
    runner.bot = MagicMock()
    runner.bot.guilds = []
    runner.bot.loop = MagicMock()
    return runner

def test_voice_manager_init(mock_bot_runner):
    with patch("core.voice.voice_manager.AgentsLoader") as mock_loader:
        mock_agent = MagicMock()
        mock_agent.config = {
            "voice_config": {
                "stt_model": "base.en",
                "tts_voice": "en-US-JennyNeural",
                "tts_speed": 1.0
            }
        }
        mock_loader.return_value.get_agent.return_value = mock_agent
        
        vm = VoiceManager(mock_bot_runner)
        assert vm.agent_id == "main"
        assert vm.config.get("tts_voice") == "en-US-JennyNeural"

def test_voice_manager_normalize_channel_name(mock_bot_runner):
    vm = VoiceManager(mock_bot_runner)
    assert vm.normalize_channel_name("general-voice") == "general"
    assert vm.normalize_channel_name("voice-software-dev") == "softwaredev"
    assert vm.normalize_channel_name("vc-day-planning") == "dayplanning"
    assert vm.normalize_channel_name("Weekend_Planning_VC") == "weekendplanning"

def test_voice_manager_resolve_channel_linked(mock_bot_runner):
    vm = VoiceManager(mock_bot_runner)
    linked_ch = MagicMock(name="linked_channel")
    vm.linked_text_channel = linked_ch
    
    mock_vc = MagicMock()
    assert vm.resolve_corresponding_text_channel(mock_vc) == linked_ch

def test_voice_manager_resolve_channel_matched(mock_bot_runner):
    vm = VoiceManager(mock_bot_runner)
    
    mock_guild = MagicMock()
    ch1 = MagicMock()
    ch1.name = "general"
    ch2 = MagicMock()
    ch2.name = "day-planning"
    mock_guild.text_channels = [ch1, ch2]
    
    mock_vc = MagicMock()
    mock_vc.name = "day-planning-voice"
    mock_vc.guild = mock_guild
    
    resolved = vm.resolve_corresponding_text_channel(mock_vc)
    assert resolved == ch2

@pytest.mark.asyncio
async def test_voice_manager_join_channel_success(mock_bot_runner):
    vm = VoiceManager(mock_bot_runner)
    
    mock_guild = MagicMock()
    mock_vc = MagicMock()
    mock_vc.name = "general-voice"
    mock_vc.id = 12345
    mock_vc.connect = AsyncMock()
    mock_guild.voice_channels = [mock_vc]
    mock_bot_runner.bot.guilds = [mock_guild]
    
    mock_voice_client = MagicMock()
    mock_vc.connect.return_value = mock_voice_client
    
    with patch("core.voice.voice_manager.VADSink"):
        success = await vm.join_voice_channel("general-voice")
        assert success is True
        assert vm.voice_client == mock_voice_client
        mock_voice_client.listen.assert_called_once()

@pytest.mark.asyncio
async def test_voice_manager_join_channel_not_found(mock_bot_runner):
    vm = VoiceManager(mock_bot_runner)
    mock_bot_runner.bot.guilds = []
    
    success = await vm.join_voice_channel("non_existent_vc")
    assert success is False
    assert vm.voice_client is None

@pytest.mark.asyncio
async def test_voice_manager_join_channel_timeout_graceful(mock_bot_runner):
    vm = VoiceManager(mock_bot_runner)
    
    mock_guild = MagicMock()
    mock_vc = MagicMock()
    mock_vc.name = "general-voice"
    mock_vc.id = 12345
    mock_vc.guild = mock_guild
    mock_guild.voice_client = None
    mock_vc.connect = AsyncMock(side_effect=TimeoutError())
    mock_guild.voice_channels = [mock_vc]
    mock_bot_runner.bot.guilds = [mock_guild]
    
    success = await vm.join_voice_channel("general-voice")
    assert success is False
    assert vm.voice_client is None
    assert vm.vad_sink is None

@pytest.mark.asyncio
async def test_voice_manager_join_channel_client_exception_graceful(mock_bot_runner):
    vm = VoiceManager(mock_bot_runner)
    
    mock_guild = MagicMock()
    mock_vc = MagicMock()
    mock_vc.name = "general-voice"
    mock_vc.id = 12345
    mock_vc.guild = mock_guild
    mock_guild.voice_client = None
    mock_vc.connect = AsyncMock(side_effect=discord.errors.ClientException("Already connected to a voice channel."))
    mock_guild.voice_channels = [mock_vc]
    mock_bot_runner.bot.guilds = [mock_guild]
    
    success = await vm.join_voice_channel("general-voice")
    assert success is False
    assert vm.voice_client is None
    assert vm.vad_sink is None

@pytest.mark.asyncio
async def test_voice_manager_join_channel_stabilization_failure_graceful(mock_bot_runner):
    vm = VoiceManager(mock_bot_runner)
    
    mock_guild = MagicMock()
    mock_vc = MagicMock()
    mock_vc.name = "general-voice"
    mock_vc.id = 12345
    mock_vc.guild = mock_guild
    mock_guild.voice_client = None
    
    mock_voice_client = MagicMock()
    mock_voice_client.is_connected.return_value = False
    mock_voice_client.disconnect = AsyncMock()
    mock_vc.connect = AsyncMock(return_value=mock_voice_client)
    mock_guild.voice_channels = [mock_vc]
    mock_bot_runner.bot.guilds = [mock_guild]
    
    with patch("asyncio.sleep", AsyncMock()):
        success = await vm.join_voice_channel("general-voice")
        assert success is False
        assert vm.voice_client is None
        assert vm.vad_sink is None

@pytest.mark.asyncio
async def test_voice_manager_join_channel_pre_cleans_existing_guild_vc(mock_bot_runner):
    vm = VoiceManager(mock_bot_runner)
    
    mock_guild = MagicMock()
    mock_existing_vc = MagicMock()
    mock_existing_vc.disconnect = AsyncMock()
    mock_guild.voice_client = mock_existing_vc
    
    mock_vc = MagicMock()
    mock_vc.name = "general-voice"
    mock_vc.id = 12345
    mock_vc.guild = mock_guild
    
    mock_new_voice_client = MagicMock()
    mock_new_voice_client.is_connected.return_value = True
    mock_vc.connect = AsyncMock(return_value=mock_new_voice_client)
    mock_guild.voice_channels = [mock_vc]
    mock_bot_runner.bot.guilds = [mock_guild]
    
    with patch("core.voice.voice_manager.VADSink"):
        success = await vm.join_voice_channel("general-voice")
        assert success is True
        mock_existing_vc.disconnect.assert_awaited_once()

@pytest.mark.asyncio
async def test_voice_manager_leave_channel(mock_bot_runner):
    vm = VoiceManager(mock_bot_runner)
    mock_vc = MagicMock()
    mock_vc.is_connected.return_value = True
    mock_vc.disconnect = AsyncMock()
    vm.voice_client = mock_vc
    vm.linked_text_channel = MagicMock()
    
    await vm.leave_voice_channel()
    mock_vc.disconnect.assert_awaited_once()
    assert vm.voice_client is None
    assert vm.linked_text_channel is None

@pytest.mark.asyncio
async def test_voice_manager_barge_in(mock_bot_runner):
    vm = VoiceManager(mock_bot_runner)
    mock_vc = MagicMock()
    mock_vc.is_playing.return_value = True
    vm.voice_client = mock_vc
    
    await vm.on_speech_started(MagicMock())
    mock_vc.stop_playing.assert_called_once()

@pytest.mark.asyncio
async def test_voice_manager_on_speech_finished_pipeline(mock_bot_runner, tmp_path):
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
    vm.voice_client = mock_vc
    
    # Mock STT transcription
    vm.stt_engine.transcribe = AsyncMock(return_value="Check my tasks for today")
    
    # Mock Agent execution
    mock_agent = MagicMock()
    mock_agent.execute = AsyncMock(return_value="You have 2 tasks scheduled.")
    
    # Mock TTS file synthesis
    fake_tts_file = tmp_path / "reply.mp3"
    fake_tts_file.write_bytes(b"dummy_mp3_data")
    vm.tts_engine.synthesize_to_file = AsyncMock(return_value=str(fake_tts_file))
    
    with patch("core.voice.voice_manager.AgentsLoader") as mock_loader, \
         patch("core.voice.blurp_generator.BlurpGenerator.get_blurp_audio", return_value=None), \
         patch("discord.FFmpegPCMAudio") as mock_ffmpeg:
             
        mock_loader.return_value.get_agent.return_value = mock_agent
        
        mock_user = MagicMock(display_name="Alice")
        await vm.on_speech_finished(mock_user, b"fake_wav_bytes")
        if vm._current_task:
            await vm._current_task
        
        # 1. Verify STT called
        vm.stt_engine.transcribe.assert_awaited_once_with(b"fake_wav_bytes")
        
        # 2. Verify Transcript posted to text channel
        mock_text_channel.send.assert_any_call("🎤 **Alice (Voice):** Check my tasks for today")
        
        # 3. Verify Agent executed with shared source='discord'
        mock_agent.execute.assert_awaited_once_with(
            content="Check my tasks for today",
            source="discord",
            channel=mock_text_channel
        )
        
        # 4. Verify TTS synthesized and played
        vm.tts_engine.synthesize_to_file.assert_awaited_once_with("You have 2 tasks scheduled.")
        mock_vc.play.assert_called_once()

@pytest.mark.asyncio
async def test_voice_manager_ignores_bot_speech(mock_bot_runner):
    vm = VoiceManager(mock_bot_runner)
    mock_vc = MagicMock()
    mock_vc.is_playing.return_value = True
    vm.voice_client = mock_vc
    
    bot_user = MagicMock(bot=True)
    await vm.on_speech_started(bot_user)
    # Should NOT stop playback because user is a bot
    mock_vc.stop.assert_not_called()
    
    await vm.on_speech_finished(bot_user, b"fake_bytes")
    assert mock_vc.play.call_count == 0

@pytest.mark.asyncio
async def test_voice_manager_interruption_turn_invalidation(mock_bot_runner, tmp_path):
    vm = VoiceManager(mock_bot_runner)
    mock_vc = MagicMock()
    mock_vc.is_connected.return_value = True
    vm.voice_client = mock_vc
    
    # Simulate an interruption occurring while agent is executing
    async def slow_execute(*args, **kwargs):
        # Trigger interruption by simulating user speaking again
        await vm.on_speech_started(MagicMock(bot=False))
        return "Old answer that should be discarded"
        
    mock_agent = MagicMock()
    mock_agent.execute = AsyncMock(side_effect=slow_execute)
    
    vm.stt_engine.transcribe = AsyncMock(return_value="Initial question")
    vm.tts_engine.synthesize_to_file = AsyncMock()
    
    with patch("core.voice.voice_manager.AgentsLoader") as mock_loader, \
         patch("core.voice.blurp_generator.BlurpGenerator.get_blurp_audio", return_value=None):
        mock_loader.return_value.get_agent.return_value = mock_agent
        
        await vm.on_speech_finished(MagicMock(bot=False), b"fake_wav")
        # TTS synthesis should NOT be called for old discarded turn
        vm.tts_engine.synthesize_to_file.assert_not_called()
        mock_vc.play.assert_not_called()

def test_voice_manager_play_blurp_stops_existing_audio(mock_bot_runner, tmp_path):
    vm = VoiceManager(mock_bot_runner)
    mock_vc = MagicMock()
    mock_vc.is_connected.return_value = True
    mock_vc.is_playing.return_value = True
    vm.voice_client = mock_vc
    
    fake_blurp = tmp_path / "blurp.wav"
    fake_blurp.write_bytes(b"RIFFblurp")
    
    with patch("core.voice.blurp_generator.BlurpGenerator.get_blurp_audio", return_value=str(fake_blurp)), \
         patch("discord.FFmpegPCMAudio"):
        vm._play_blurp()
        mock_vc.stop_playing.assert_called_once()
        mock_vc.play.assert_called_once()

@pytest.mark.asyncio
async def test_voice_manager_barge_in_and_second_sentence_flow(mock_bot_runner, tmp_path):
    """
    Tests complete real-world flow:
    1. User speaks sentence 1 -> bot transcribes and plays response.
    2. During playback, user interrupts by speaking sentence 2.
    3. Bot stops playback immediately (Barge-in without killing reader).
    4. User finishes sentence 2.
    5. Bot transcribes sentence 2 and replies to sentence 2!
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
    vm.voice_client = mock_vc
    
    # 1. First turn: user asks "Help me ask Daisy to say hi."
    vm.stt_engine.transcribe = AsyncMock(side_effect=["Help me ask Daisy to say hi.", "Tell me the list of agents."])
    
    mock_agent = MagicMock()
    mock_agent.execute = AsyncMock(side_effect=[
        "Alva, I tried to reach Daisy...",
        "Here are the available agents: Daisy, Rick, Concierge."
    ])
    
    fake_tts1 = tmp_path / "reply1.mp3"
    fake_tts1.write_bytes(b"audio1")
    fake_tts2 = tmp_path / "reply2.mp3"
    fake_tts2.write_bytes(b"audio2")
    
    vm.tts_engine.synthesize_to_file = AsyncMock(side_effect=[str(fake_tts1), str(fake_tts2)])
    
    user = MagicMock(display_name="Alva", bot=False)
    
    with patch("core.voice.voice_manager.AgentsLoader") as mock_loader, \
         patch("core.voice.blurp_generator.BlurpGenerator.get_blurp_audio", return_value=None), \
         patch("discord.FFmpegPCMAudio"):
        mock_loader.return_value.get_agent.return_value = mock_agent
        
        # Turn 1 finishes speaking
        await vm.on_speech_finished(user, b"wav1")
        if vm._current_task:
            await vm._current_task
            
        assert mock_agent.execute.call_count == 1
        assert mock_vc.play.call_count == 1
        
        # Now simulate playback active
        mock_vc.is_playing.return_value = True
        
        # Turn 2: User starts speaking (Interruption / Barge-in)
        await vm.on_speech_started(user)
        mock_vc.stop_playing.assert_called_once() # Playback stopped!
        
        # Turn 2: User finishes speaking second sentence
        await vm.on_speech_finished(user, b"wav2")
        if vm._current_task:
            await vm._current_task
            
        # Verify second sentence was transcribed and answered!
        assert vm.stt_engine.transcribe.call_count == 2
        assert mock_agent.execute.call_count == 2
        assert mock_agent.execute.call_args_list[1][1]["content"] == "Tell me the list of agents."
        assert vm.tts_engine.synthesize_to_file.call_count == 2
