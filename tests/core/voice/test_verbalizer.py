"""Tests for the voice verbalizer.

The contract these guard is narrow but load-bearing: verbalization may rephrase
freely, but it must never *lose* content, and it must never cost a model call on
plain conversational prose (the common case, where latency is visible).
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.voice.prompts import build_verbalizer_prompt
from core.voice.tts_engine import TextSanitizer
from core.voice.verbalizer import Verbalizer, has_structure
from tests.helpers import GOLDEN_INPUT, voice_session


# --------------------------------------------------------------------------
# has_structure
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "- first item\n- second item",
    "* starred item",
    "+ plus item",
    "1. first\n2. second",
    "3) third",
    "# Heading",
    "###### Deep heading",
    "| Name | Cost |\n| --- | --- |",
    "```python\nprint(1)\n```",
    "This is **important** to note.",
    "> quoted line",
    "Intro line\n- then a bullet",
])
def test_has_structure_detects_visual_layout(text):
    assert has_structure(text) is True


@pytest.mark.parametrize("text", [
    "",
    "Sure, I booked the table for seven.",
    "It rained yesterday. It will rain again tomorrow.",
    "The cost is 12.50 dollars, which is under budget.",
    "I checked 3 sites and none of them had availability.",
])
def test_has_structure_ignores_plain_prose(text):
    assert has_structure(text) is False


def test_has_structure_does_not_fire_on_hyphenated_words():
    # A mid-sentence hyphen is not a bullet; only a line-leading one is.
    assert has_structure("It is a well-known trade-off.") is False


# --------------------------------------------------------------------------
# Verbalizer
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_verbalize_empty_returns_empty():
    v = Verbalizer()
    assert await v.verbalize("") == ""
    assert await v.verbalize("   \n  ") == ""


@pytest.mark.asyncio
async def test_verbalize_prose_skips_the_model():
    v = Verbalizer()
    with patch.object(Verbalizer, "_get_llm") as get_llm:
        result = await v.verbalize("I booked the table for seven.")
    get_llm.assert_not_called()
    assert result == "I booked the table for seven."


@pytest.mark.asyncio
async def test_verbalize_prose_is_still_sanitized():
    v = Verbalizer()
    result = await v.verbalize("Done \u2705 see https://example.com for details.")
    assert "\u2705" not in result
    assert "https://" not in result


@pytest.mark.asyncio
async def test_verbalize_disabled_skips_the_model_even_with_structure():
    v = Verbalizer(enabled=False)
    with patch.object(Verbalizer, "_get_llm") as get_llm:
        result = await v.verbalize(NESTED_LIST)
    get_llm.assert_not_called()
    # Falls back to the sanitizer: speakable, just flatter.
    assert "eggs" in result and "milk" in result


# A nested list is structure the deterministic fast path declines, so these
# inputs still reach the model. Flat lists no longer do; see the fast-path
# tests below.
NESTED_LIST = "- eggs\n  - free range\n- milk"


@pytest.mark.asyncio
async def test_verbalize_structured_text_calls_the_model():
    v = Verbalizer()
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(
        content="First, eggs. After that, milk."
    ))
    with patch.object(Verbalizer, "_get_llm", return_value=llm):
        result = await v.verbalize(NESTED_LIST)

    assert result == "First, eggs. After that, milk."
    messages = llm.ainvoke.call_args[0][0]
    assert messages[0].content == build_verbalizer_prompt()
    assert messages[1].content == NESTED_LIST


@pytest.mark.asyncio
async def test_verbalize_sanitizes_model_output():
    # The prompt asks for plain speech, but a stray asterisk reaching the
    # synthesiser is read out loud, so the output is sanitized regardless.
    v = Verbalizer()
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(content="**First**, eggs \U0001F95A."))
    with patch.object(Verbalizer, "_get_llm", return_value=llm):
        result = await v.verbalize(NESTED_LIST)

    assert "*" not in result
    assert "\U0001F95A" not in result
    assert "First" in result and "eggs" in result


@pytest.mark.asyncio
async def test_verbalize_joins_multipart_content():
    v = Verbalizer()
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(
        content=[{"text": "First eggs. "}, {"text": "Then milk."}]
    ))
    with patch.object(Verbalizer, "_get_llm", return_value=llm):
        result = await v.verbalize(NESTED_LIST)

    assert result == "First eggs. Then milk."


@pytest.mark.asyncio
async def test_verbalize_falls_back_when_model_raises():
    v = Verbalizer()
    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=RuntimeError("quota exceeded"))
    with patch.object(Verbalizer, "_get_llm", return_value=llm):
        result = await v.verbalize(NESTED_LIST)

    llm.ainvoke.assert_called_once()
    assert "eggs" in result and "milk" in result


@pytest.mark.asyncio
async def test_verbalize_falls_back_on_timeout():
    v = Verbalizer(timeout=0.01)

    async def never_returns(*_args, **_kwargs):
        await asyncio.sleep(5)

    llm = MagicMock()
    llm.ainvoke = never_returns
    with patch.object(Verbalizer, "_get_llm", return_value=llm):
        result = await v.verbalize(NESTED_LIST)

    assert "eggs" in result and "milk" in result


@pytest.mark.asyncio
async def test_verbalize_falls_back_on_empty_model_output():
    v = Verbalizer()
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(content="   "))
    with patch.object(Verbalizer, "_get_llm", return_value=llm):
        result = await v.verbalize(NESTED_LIST)

    assert "eggs" in result and "milk" in result


# --------------------------------------------------------------------------
# XML handling
# --------------------------------------------------------------------------

def test_has_structure_ignores_structure_only_in_xml():
    # If the only bullets/headers are inside <memory> or <vote>, it should not trigger structuring
    text = "<memory>\n- note 1\n- note 2\n</memory>\nI will remember that."
    assert has_structure(text) is False


def test_has_structure_detects_structure_outside_xml():
    text = "<memory>User likes tea</memory>\n- First\n- Second"
    assert has_structure(text) is True


@pytest.mark.asyncio
async def test_verbalize_strips_xml_before_model_call():
    v = Verbalizer()
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(
        content="First, eggs. After that, milk."
    ))
    with patch.object(Verbalizer, "_get_llm", return_value=llm):
        result = await v.verbalize("<memory>User likes grocery lists</memory>\n<vote>yes</vote>\n" + NESTED_LIST)

    assert result == "First, eggs. After that, milk."
    messages = llm.ainvoke.call_args[0][0]
    # Verify the XML block was stripped before being passed to the model
    assert "<memory>" not in messages[1].content
    assert "User likes grocery lists" not in messages[1].content
    assert "<vote>" not in messages[1].content
    assert NESTED_LIST in messages[1].content


@pytest.mark.asyncio
async def test_verbalize_only_xml_returns_empty_without_model_call():
    v = Verbalizer()
    with patch.object(Verbalizer, "_get_llm") as get_llm:
        assert await v.verbalize("<memory>User likes dark mode</memory>") == ""
        assert await v.verbalize("<vote>yes</vote>") == ""
        assert await v.verbalize("<memory>\n- Item 1\n- Item 2\n</memory>") == ""
    get_llm.assert_not_called()


@pytest.mark.asyncio
async def test_verbalize_prose_with_xml_skips_the_model():
    v = Verbalizer()
    with patch.object(Verbalizer, "_get_llm") as get_llm:
        result = await v.verbalize("<memory>User lives in Seattle</memory>I booked the table for seven.")
    get_llm.assert_not_called()
    assert result == "I booked the table for seven."
    assert "Seattle" not in result


# --------------------------------------------------------------------------
# Task notation
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_verbalize_prose_speaks_priority_the_sanitizer_would_delete():
    """The sanitizer classes these symbols as emoji and removes them. On the
    prose path there is no model to convert them first, so without the
    notation pass the level vanishes silently."""
    v = Verbalizer()
    with patch.object(Verbalizer, "_get_llm") as get_llm:
        result = await v.verbalize("Call the dentist \u23EB")
    get_llm.assert_not_called()
    assert result == "Call the dentist, high priority"


@pytest.mark.asyncio
async def test_verbalize_prose_drops_hashtags_without_a_model_call():
    v = Verbalizer()
    with patch.object(Verbalizer, "_get_llm") as get_llm:
        result = await v.verbalize("Read the briefing #a/read today.")
    get_llm.assert_not_called()
    assert result == "Read the briefing today."


@pytest.mark.asyncio
async def test_verbalize_normalises_notation_before_the_model_sees_it():
    """The model is handed words, not markers, so nothing depends on it
    getting a formatting rule right."""
    v = Verbalizer()
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(content="First, eggs."))
    with patch.object(Verbalizer, "_get_llm", return_value=llm):
        await v.verbalize("- eggs #shopping \u23EB\n  - free range\n- milk #shopping")

    sent = llm.ainvoke.call_args[0][0][1].content
    assert "#" not in sent
    assert "\u23EB" not in sent
    assert "high priority" in sent
    assert "eggs" in sent and "milk" in sent


@pytest.mark.asyncio
async def test_verbalize_tag_only_text_returns_empty_without_a_model_call():
    v = Verbalizer()
    with patch.object(Verbalizer, "_get_llm") as get_llm:
        result = await v.verbalize("#a/read #project/home")
    get_llm.assert_not_called()
    assert result == ""


@pytest.mark.asyncio
async def test_fallback_keeps_priority_on_a_structured_task_list():
    """Model disabled: the sanitizer runs alone, and the levels still have
    to survive it."""
    v = Verbalizer(enabled=False)
    result = await v.verbalize(
        "- \U0001F53A Renew the passport #admin\n"
        "- \u23EC Sort the garage #home"
    )
    assert "highest priority" in result
    assert "lowest priority" in result
    assert "Renew the passport" in result
    assert "Sort the garage" in result
    assert "#" not in result


# --------------------------------------------------------------------------
# Deterministic fast path for simple structure
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "- eggs\n- milk",
    "## Weekend\n1. Point Reyes\n2. Mount Tam",
    "Remember this is **urgent**.",
    "| Day | Forecast |\n| --- | --- |\n| Monday | Sunny |",
])
@pytest.mark.asyncio
async def test_verbalize_simple_structure_skips_the_model(text):
    v = Verbalizer()
    with patch.object(Verbalizer, "_get_llm") as get_llm:
        result = await v.verbalize(text)
    get_llm.assert_not_called()
    assert result
    assert "*" not in result and "|" not in result and "#" not in result


@pytest.mark.asyncio
async def test_verbalize_fast_path_keeps_priority_words():
    v = Verbalizer()
    with patch.object(Verbalizer, "_get_llm") as get_llm:
        result = await v.verbalize("- \u23EB Call the dentist #admin\n- Buy milk")
    get_llm.assert_not_called()
    assert result == "high priority: Call the dentist. Buy milk."


@pytest.mark.asyncio
async def test_golden_input_takes_the_fast_path_and_keeps_every_item():
    v = Verbalizer()
    with patch.object(Verbalizer, "_get_llm") as get_llm:
        spoken = await v.verbalize(GOLDEN_INPUT)
    get_llm.assert_not_called()
    assert spoken.startswith("Weekend options. First, Point Reyes")
    for detail in ("morning fog", "steep but short", "book ahead",
                   "windy after noon", "stroller friendly"):
        assert detail in spoken


# --------------------------------------------------------------------------
# Token attribution for the model call
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_verbalize_model_call_carries_token_logging_callbacks():
    from core.runtime.token_usage_handler import TokenUsageHandler

    session = voice_session()
    v = Verbalizer()
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(content="Eggs, then milk."))
    with patch.object(Verbalizer, "_get_llm", return_value=llm):
        await v.verbalize(NESTED_LIST, session=session)

    config = llm.ainvoke.call_args.kwargs["config"]
    handlers = config["callbacks"]
    assert len(handlers) == 1 and isinstance(handlers[0], TokenUsageHandler)
    assert handlers[0].session is session
    assert config["metadata"]["agent_id"] == "main"
    assert config["metadata"]["surface"] == "voice"


@pytest.mark.asyncio
async def test_verbalize_falls_back_to_the_ambient_context_for_attribution():
    from core.runtime.execution_context import current_execution_context

    session = voice_session()
    v = Verbalizer()
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(content="Eggs, then milk."))
    token = current_execution_context.set(session)
    try:
        with patch.object(Verbalizer, "_get_llm", return_value=llm):
            await v.verbalize(NESTED_LIST)
    finally:
        current_execution_context.reset(token)

    handler = llm.ainvoke.call_args.kwargs["config"]["callbacks"][0]
    assert handler.session is session


@pytest.mark.asyncio
async def test_verbalize_without_any_context_still_calls_the_model():
    v = Verbalizer()
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(content="Eggs, then milk."))
    with patch.object(Verbalizer, "_get_llm", return_value=llm):
        result = await v.verbalize(NESTED_LIST)
    assert result == "Eggs, then milk."
    assert "callbacks" not in llm.ainvoke.call_args.kwargs["config"]
