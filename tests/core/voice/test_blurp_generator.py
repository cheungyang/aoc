import os
import pytest
from unittest.mock import patch
from core.voice.blurp_generator import BlurpGenerator, DEFAULT_BLURP

def test_blurp_generator_configured_file(tmp_path):
    fake_blurp = tmp_path / "custom_ping.wav"
    fake_blurp.write_bytes(b"RIFFdummy")
    
    config = {"audio_blurp": str(fake_blurp)}
    result = BlurpGenerator.get_blurp_audio(config)
    assert result == str(fake_blurp)

def test_blurp_generator_missing_configured_file(tmp_path):
    config = {"audio_blurp": str(tmp_path / "non_existent.wav")}
    with patch("os.path.exists", side_effect=lambda p: p == DEFAULT_BLURP):
        result = BlurpGenerator.get_blurp_audio(config)
        assert result == DEFAULT_BLURP

def test_blurp_generator_default_fallback():
    config = {}
    with patch("os.path.exists", return_value=True):
        result = BlurpGenerator.get_blurp_audio(config)
        assert result == DEFAULT_BLURP

def test_blurp_generator_no_file_found():
    config = {}
    with patch("os.path.exists", return_value=False):
        result = BlurpGenerator.get_blurp_audio(config)
        assert result is None
