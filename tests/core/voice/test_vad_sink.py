import wave
import io
import asyncio
import numpy as np
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from core.voice.vad_sink import UserVADState, VADSink

def test_user_vad_state_reset():
    state = UserVADState()
    state.is_speaking = True
    state.audio_buffer.extend(b"1234")
    state.silence_chunks = 10
    state.speech_chunks = 5
    
    state.reset_speech()
    assert state.is_speaking is False
    assert len(state.audio_buffer) == 0
    assert state.silence_chunks == 0
    assert state.speech_chunks == 0

def test_vad_sink_wants_opus():
    mock_vm = MagicMock()
    with patch("onnxruntime.InferenceSession"):
        sink = VADSink(mock_vm)
        assert sink.wants_opus() is False

def test_vad_sink_pcm_to_wav():
    mock_vm = MagicMock()
    with patch("onnxruntime.InferenceSession"):
        sink = VADSink(mock_vm)
        dummy_pcm = bytearray(3200) # 100ms of 16kHz 16-bit mono
        wav_bytes = sink._pcm_to_wav(dummy_pcm, sample_rate=16000)
        
        # Verify valid WAV format
        with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
            assert wav_file.getnchannels() == 1
            assert wav_file.getsampwidth() == 2
            assert wav_file.getframerate() == 16000
            assert wav_file.getnframes() == 1600

def test_vad_sink_write_empty_or_none():
    mock_vm = MagicMock()
    with patch("onnxruntime.InferenceSession"):
        sink = VADSink(mock_vm)
        # Should not raise exception
        sink.write(None, MagicMock(pcm=b"1234"))
        sink.write(MagicMock(id=1), MagicMock(pcm=b""))

@pytest.mark.asyncio
async def test_vad_sink_speech_detection_and_finish():
    mock_vm = MagicMock()
    mock_vm.on_speech_started = AsyncMock()
    mock_vm.on_speech_finished = AsyncMock()
    
    mock_onnx_session = MagicMock()
    # Return high speech probability (0.9), then low probability (0.1)
    call_count = 0
    def mock_run(output_names, input_feed):
        nonlocal call_count
        call_count += 1
        # First 10 calls: speech (>0.5), next 30 calls: silence (<0.35)
        prob = np.array([0.9 if call_count <= 10 else 0.1], dtype=np.float32)
        h = np.zeros((1, 1, 128), dtype=np.float32)
        c = np.zeros((1, 1, 128), dtype=np.float32)
        return prob, h, c
        
    mock_onnx_session.run.side_effect = mock_run
    
    with patch("onnxruntime.InferenceSession", return_value=mock_onnx_session):
        loop = asyncio.get_running_loop()
        sink = VADSink(mock_vm, loop=loop, silence_duration_ms=200, min_speech_ms=50)
        mock_user = MagicMock(id=42, display_name="TestUser")
        
        # Send 48kHz stereo frames (3840 bytes per 20ms frame)
        frame_bytes = np.ones(1920, dtype=np.int16).tobytes() # 960 stereo samples
        
        # Send enough frames to trigger speech start and silence end
        for _ in range(50):
            sink.write(mock_user, MagicMock(pcm=frame_bytes))
            await asyncio.sleep(0.001)
            
        assert mock_vm.on_speech_started.call_count >= 1
        assert mock_vm.on_speech_finished.call_count >= 1

def test_vad_sink_cleanup():
    mock_vm = MagicMock()
    with patch("onnxruntime.InferenceSession"):
        sink = VADSink(mock_vm)
        sink.user_states[123] = UserVADState()
        sink.cleanup()
        assert len(sink.user_states) == 0
