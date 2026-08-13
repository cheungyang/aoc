import pytest
from unittest.mock import MagicMock, patch
from core.voice.stt_engine import STTEngine

def test_stt_engine_caching():
    STTEngine._models.clear()
    
    mock_whisper = MagicMock()
    with patch("core.voice.stt_engine.WhisperModel", return_value=mock_whisper) as mock_class:
        engine1 = STTEngine(model_size="base.en", device="cpu", compute_type="int8")
        engine2 = STTEngine(model_size="base.en", device="cpu", compute_type="int8")
        
        assert mock_class.call_count == 1
        assert engine1.model == engine2.model

def test_stt_engine_sync_transcribe_success():
    STTEngine._models.clear()
    mock_whisper = MagicMock()
    
    seg1 = MagicMock(text="Hello")
    seg2 = MagicMock(text="world from voice")
    mock_whisper.transcribe.return_value = ([seg1, seg2], MagicMock())
    
    with patch("core.voice.stt_engine.WhisperModel", return_value=mock_whisper):
        engine = STTEngine(model_size="base.en")
        text = engine._sync_transcribe(b"RIFFfakebytes")
        assert text == "Hello world from voice"

@pytest.mark.parametrize("ignored_output", [
    "[BLANK_AUDIO]",
    "(blank audio)",
    "Thanks for watching!",
    "thank you",
    ".",
    ""
])
def test_stt_engine_sync_transcribe_filters_hallucinations(ignored_output):
    STTEngine._models.clear()
    mock_whisper = MagicMock()
    
    seg = MagicMock(text=ignored_output)
    mock_whisper.transcribe.return_value = ([seg], MagicMock())
    
    with patch("core.voice.stt_engine.WhisperModel", return_value=mock_whisper):
        engine = STTEngine(model_size="base.en")
        text = engine._sync_transcribe(b"RIFFfakebytes")
        assert text == ""

@pytest.mark.asyncio
async def test_stt_engine_async_transcribe():
    STTEngine._models.clear()
    mock_whisper = MagicMock()
    seg = MagicMock(text="Async transcription successful")
    mock_whisper.transcribe.return_value = ([seg], MagicMock())
    
    with patch("core.voice.stt_engine.WhisperModel", return_value=mock_whisper):
        engine = STTEngine(model_size="base.en")
        text = await engine.transcribe(b"RIFFfakebytes")
        assert text == "Async transcription successful"
