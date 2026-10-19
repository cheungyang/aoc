"""TextSanitizer: agent markdown and XML -> words the synthesizer can say."""
from core.voice.speech.speech_util import TextSanitizer

def test_text_sanitizer_empty():
    assert TextSanitizer.sanitize("") == ""
    assert TextSanitizer.sanitize(None) == ""

def test_text_sanitizer_xml_and_custom_tags():
    raw = '<poll question="Lunch?">\n<option>Pizza</option>\n</poll>\n<job id="123">Processing</job>'
    cleaned = TextSanitizer.sanitize(raw)
    assert "<poll" not in cleaned
    assert "<job" not in cleaned
    assert "Pizza" not in cleaned # Full XML tag block stripped

def test_text_sanitizer_memory_and_vote_tags():
    raw = "<memory>User prefers dark mode.</memory>Here is the plan. <vote>yes</vote>"
    cleaned = TextSanitizer.sanitize(raw)
    assert "<memory>" not in cleaned
    assert "dark mode" not in cleaned
    assert "<vote>" not in cleaned
    assert "yes" not in cleaned
    assert cleaned == "Here is the plan."

def test_text_sanitizer_nested_and_unclosed_xml():
    raw = "<vote><choice>approve</choice></vote>Done. <memory>dangling unclosed"
    cleaned = TextSanitizer.sanitize(raw)
    assert "approve" not in cleaned
    assert "dangling" not in cleaned
    assert cleaned == "Done."

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
    raw = "Loud and clear, Sam. The system is operational. 🛎️ Ready for your next instruction! 🤖✨ <:custom:123> ✅"
    cleaned = TextSanitizer.sanitize(raw)
    assert "🛎️" not in cleaned
    assert "🤖" not in cleaned
    assert "✨" not in cleaned
    assert "✅" not in cleaned
    assert "<:custom:123>" not in cleaned
    assert cleaned == "Loud and clear, Sam. The system is operational. Ready for your next instruction!"
