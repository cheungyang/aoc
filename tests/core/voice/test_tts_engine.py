import os
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from core.voice.tts_engine import TextSanitizer, TTSEngine

def test_text_sanitizer_empty():
    assert TextSanitizer.sanitize("") == ""
    assert TextSanitizer.sanitize(None) == ""

def test_text_sanitizer_xml_and_custom_tags():
    raw = '<poll question="Lunch?">\n<option>Pizza</option>\n</poll>\n<job id="123">Processing</job>'
    cleaned = TextSanitizer.sanitize(raw)
    assert "<poll" not in cleaned
    assert "<job" not in cleaned
    assert "Pizza" not in cleaned # Full XML tag block stripped

def test_text_sanitizer_code_blocks():
    raw = "Here is the solution:\n```python\ndef add(a, b):\n    return a + b\n```\nLet me know!"
    cleaned = TextSanitizer.sanitize(raw)
    assert "```" not in cleaned
    assert "def add" not in cleaned
    assert "I have provided the code in the chat." in cleaned
    assert "Let me know!" in cleaned

def test_text_sanitizer_inline_code_and_formatting():
    raw = "Use `git status` to check **changes** and _branch_ name with ~~old~~ text."
    cleaned = TextSanitizer.sanitize(raw)
    assert "`" not in cleaned
    assert "**" not in cleaned
    assert "~~" not in cleaned
    assert "git status" in cleaned
    assert "changes" in cleaned
    assert "branch" in cleaned

def test_text_sanitizer_links_and_urls():
    raw = "Visit [OpenAI](https://openai.com) or check https://github.com/langchain directly."
    cleaned = TextSanitizer.sanitize(raw)
    assert "https://" not in cleaned
    assert "OpenAI" in cleaned
    assert "[" not in cleaned

def test_text_sanitizer_headers_lists_and_quotes():
    raw = "### Tasks for Today\n> Note this carefully\n- Task 1\n* Task 2\n1. Task 3"
    cleaned = TextSanitizer.sanitize(raw)
    assert "#" not in cleaned
    assert ">" not in cleaned
    assert "Tasks for Today" in cleaned
    assert "Task 1" in cleaned
    assert "Task 2" in cleaned
    assert "Task 3" in cleaned

def test_text_sanitizer_removes_emojis():
    raw = "Loud and clear, Alva. The system is operational. 🛎️ Ready for your next instruction! 🤖✨ <:custom:123> ✅"
    cleaned = TextSanitizer.sanitize(raw)
    assert "🛎️" not in cleaned
    assert "🤖" not in cleaned
    assert "✨" not in cleaned
    assert "✅" not in cleaned
    assert "<:custom:123>" not in cleaned
    assert cleaned == "Loud and clear, Alva. The system is operational. Ready for your next instruction!"

@pytest.mark.asyncio
async def test_tts_engine_synthesize_empty():
    engine = TTSEngine()
    result = await engine.synthesize_to_file("")
    assert result == ""

@pytest.mark.asyncio
async def test_tts_engine_synthesize_success(tmp_path):
    engine = TTSEngine(default_voice="en-US-JennyNeural", default_speed=1.1)
    
    mock_comm = MagicMock()
    mock_comm.save = AsyncMock()
    
    with patch("edge_tts.Communicate", return_value=mock_comm) as mock_class:
        file_path = await engine.synthesize_to_file("Hello there!")
        assert file_path != ""
        assert file_path.endswith(".mp3")
        mock_class.assert_called_once_with("Hello there!", voice="en-US-JennyNeural", rate="+10%")
        mock_comm.save.assert_awaited_once_with(file_path)
        
        if os.path.exists(file_path):
            os.unlink(file_path)

@pytest.mark.asyncio
async def test_tts_engine_synthesize_error():
    engine = TTSEngine()
    
    with patch("edge_tts.Communicate", side_effect=RuntimeError("TTS failure")):
        file_path = await engine.synthesize_to_file("Hello there!")
        assert file_path == ""
