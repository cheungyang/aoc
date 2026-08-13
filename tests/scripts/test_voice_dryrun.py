import os
import sys
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from scripts.voice_dryrun import main

@pytest.mark.asyncio
async def test_voice_dryrun_with_provided_file(tmp_path):
    fake_wav = tmp_path / "custom.wav"
    fake_wav.write_bytes(b"RIFFdummydata")
    
    with patch("sys.argv", ["voice_dryrun.py", str(fake_wav)]), \
         patch("core.voice.stt_engine.STTEngine.transcribe", AsyncMock(return_value="Testing 1 2 3")), \
         patch("core.loaders.agents_loader.AgentsLoader.get_agent") as mock_get_agent, \
         patch("core.voice.tts_engine.TTSEngine.synthesize_to_file", AsyncMock(return_value=str(tmp_path / "out.mp3"))):
        
        mock_agent = MagicMock()
        mock_agent.execute = AsyncMock(return_value="Loud and clear.")
        mock_get_agent.return_value = mock_agent
        
        await main()
        mock_agent.execute.assert_awaited_once_with(content="Testing 1 2 3", source="voice_dryrun")

@pytest.mark.asyncio
async def test_voice_dryrun_synthetic_generation(tmp_path):
    with patch("sys.argv", ["voice_dryrun.py"]), \
         patch("core.voice.tts_engine.TTSEngine.synthesize_to_file", AsyncMock(return_value=str(tmp_path / "synth.mp3"))), \
         patch("subprocess.run") as mock_subproc, \
         patch("builtins.open", MagicMock()) as mock_open, \
         patch("core.voice.stt_engine.STTEngine.transcribe", AsyncMock(return_value="Testing testing 1 2 3")), \
         patch("core.loaders.agents_loader.AgentsLoader.get_agent") as mock_get_agent:
        
        mock_open.return_value.__enter__.return_value.read.return_value = b"RIFFsyntheticbytes"
        mock_agent = MagicMock()
        mock_agent.execute = AsyncMock(return_value="Hello there.")
        mock_get_agent.return_value = mock_agent
        
        await main()
        mock_agent.execute.assert_awaited_once_with(content="Testing testing 1 2 3", source="voice_dryrun")

@pytest.mark.asyncio
async def test_voice_dryrun_empty_transcription(tmp_path):
    fake_wav = tmp_path / "silence.wav"
    fake_wav.write_bytes(b"RIFFsilence")
    
    with patch("sys.argv", ["voice_dryrun.py", str(fake_wav)]), \
         patch("core.voice.stt_engine.STTEngine.transcribe", AsyncMock(return_value="")), \
         patch("core.loaders.agents_loader.AgentsLoader.get_agent") as mock_get_agent:
        
        await main()
        mock_get_agent.assert_not_called()
