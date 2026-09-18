"""Tests for the voice verbalizer.

The contract these guard is narrow but load-bearing: verbalization may rephrase
freely, but it must never *lose* content, and it must never cost a model call on
plain conversational prose (the common case, where latency is visible).
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.voice.prompts import build_verbalizer_prompt
from core.voice.verbalizer import (
    Verbalizer,
    VoiceStream,
    has_structure,
    strip_xml,
)


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
        result = await v.verbalize("- eggs\n- milk")
    get_llm.assert_not_called()
    # Falls back to the sanitizer: speakable, just flatter.
    assert "eggs" in result and "milk" in result


@pytest.mark.asyncio
async def test_verbalize_structured_text_calls_the_model():
    v = Verbalizer()
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(
        content="First, eggs. After that, milk."
    ))
    with patch.object(Verbalizer, "_get_llm", return_value=llm):
        result = await v.verbalize("- eggs\n- milk")

    assert result == "First, eggs. After that, milk."
    messages = llm.ainvoke.call_args[0][0]
    assert messages[0].content == build_verbalizer_prompt()
    assert messages[1].content == "- eggs\n- milk"


@pytest.mark.asyncio
async def test_verbalize_sanitizes_model_output():
    # The prompt asks for plain speech, but a stray asterisk reaching the
    # synthesiser is read out loud, so the output is sanitized regardless.
    v = Verbalizer()
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(content="**First**, eggs \U0001F95A."))
    with patch.object(Verbalizer, "_get_llm", return_value=llm):
        result = await v.verbalize("- eggs")

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
        result = await v.verbalize("- eggs\n- milk")

    assert result == "First eggs. Then milk."


@pytest.mark.asyncio
async def test_verbalize_falls_back_when_model_raises():
    v = Verbalizer()
    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=RuntimeError("quota exceeded"))
    with patch.object(Verbalizer, "_get_llm", return_value=llm):
        result = await v.verbalize("- eggs\n- milk")

    assert "eggs" in result and "milk" in result


@pytest.mark.asyncio
async def test_verbalize_falls_back_on_timeout():
    v = Verbalizer(timeout=0.01)

    async def never_returns(*_args, **_kwargs):
        await asyncio.sleep(5)

    llm = MagicMock()
    llm.ainvoke = never_returns
    with patch.object(Verbalizer, "_get_llm", return_value=llm):
        result = await v.verbalize("- eggs\n- milk")

    assert "eggs" in result and "milk" in result


@pytest.mark.asyncio
async def test_verbalize_falls_back_on_empty_model_output():
    v = Verbalizer()
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(content="   "))
    with patch.object(Verbalizer, "_get_llm", return_value=llm):
        result = await v.verbalize("- eggs\n- milk")

    assert "eggs" in result and "milk" in result


# --------------------------------------------------------------------------
# VoiceStream
# --------------------------------------------------------------------------

class RecordingVerbalizer:
    """Stand-in that records every block it is handed."""

    def __init__(self, transform=None):
        self.blocks = []
        self._transform = transform or (lambda b: b.replace("\n", " ").strip())

    async def verbalize(self, text):
        self.blocks.append(text)
        return self._transform(text)


async def _feed(stream, text, chunk_size=7):
    """Streams text through the voice stream in small token deltas."""
    chunks = []
    for i in range(0, len(text), chunk_size):
        chunks.extend(await stream.add_token(text[i:i + chunk_size]))
    chunks.extend(await stream.flush())
    return chunks


@pytest.mark.asyncio
async def test_stream_prose_never_touches_the_verbalizer():
    verbalizer = RecordingVerbalizer()
    stream = VoiceStream(verbalizer=verbalizer)

    chunks = await _feed(stream, "I booked the table for seven. It is under your name.")

    assert verbalizer.blocks == []
    assert chunks
    assert "booked the table" in " ".join(chunks)


@pytest.mark.asyncio
async def test_stream_emits_prose_before_the_turn_ends():
    # The point of the prose fast path: audio starts while tokens are still
    # arriving, rather than after the reply is complete.
    stream = VoiceStream(verbalizer=RecordingVerbalizer())

    early = await stream.add_token("I booked the table for seven. ")
    assert early, "a complete sentence should be speakable immediately"
    assert "booked the table for seven" in early[0]


@pytest.mark.asyncio
async def test_stream_holds_a_structured_block_until_it_is_complete():
    verbalizer = RecordingVerbalizer()
    stream = VoiceStream(verbalizer=verbalizer)

    pending = await stream.add_token("- eggs\n- milk\n- bread")
    assert pending == [], "a list cannot be rewritten until it stops growing"
    assert verbalizer.blocks == []

    emitted = await stream.add_token("\n\n")
    assert verbalizer.blocks == ["- eggs\n- milk\n- bread"]
    assert emitted


@pytest.mark.asyncio
async def test_stream_flush_emits_a_trailing_structured_block():
    verbalizer = RecordingVerbalizer()
    stream = VoiceStream(verbalizer=verbalizer)

    await stream.add_token("- eggs\n- milk")
    chunks = await stream.flush()

    assert verbalizer.blocks == ["- eggs\n- milk"]
    assert "eggs" in " ".join(chunks)


@pytest.mark.asyncio
async def test_stream_breaks_a_runaway_block_at_a_line_boundary():
    # A list that never reaches a blank line still has to be spoken.
    verbalizer = RecordingVerbalizer()
    stream = VoiceStream(verbalizer=verbalizer, max_block_chars=120)

    long_list = "".join(f"- item number {i}\n" for i in range(30))
    await _feed(stream, long_list)

    assert len(verbalizer.blocks) > 1
    for block in verbalizer.blocks:
        # No bullet was cut down the middle.
        assert not block.startswith("tem"), f"bullet split mid-word: {block!r}"


@pytest.mark.asyncio
async def test_stream_handles_prose_followed_by_a_list():
    verbalizer = RecordingVerbalizer()
    stream = VoiceStream(verbalizer=verbalizer)

    chunks = await _feed(
        stream,
        "Here is what I found today.\n\n- eggs\n- milk\n",
    )

    spoken = " ".join(chunks)
    assert "Here is what I found today" in spoken
    assert "eggs" in spoken and "milk" in spoken


@pytest.mark.asyncio
async def test_stream_is_empty_for_empty_input():
    stream = VoiceStream(verbalizer=RecordingVerbalizer())
    assert await stream.add_token("") == []
    assert await stream.flush() == []


# --------------------------------------------------------------------------
# Golden: nothing may be dropped
# --------------------------------------------------------------------------

GOLDEN_INPUT = """## Weekend options

1. **Point Reyes** - 2 hour drive, $0 entry, best in the morning fog.
2. **Mount Tam** - 45 minutes, $8 parking, steep but short.
3. **Muir Woods** - 1 hour, $15 plus a $9 reservation, book ahead.
4. **Stinson Beach** - 1 hour 15, free, windy after noon.
5. **Tennessee Valley** - 40 minutes, free, flat and stroller friendly.
"""

GOLDEN_SPOKEN = (
    "Here are your weekend options. "
    "First, Point Reyes, a two hour drive with no entry fee, best in the morning fog. "
    "Second, Mount Tam, forty five minutes away, eight dollars for parking, steep but short. "
    "Third, Muir Woods, an hour out, fifteen dollars plus a nine dollar reservation, so book ahead. "
    "Fourth, Stinson Beach, an hour and fifteen minutes, free, though it gets windy after noon. "
    "Fifth, Tennessee Valley, forty minutes, free, flat and stroller friendly."
)


@pytest.mark.asyncio
async def test_golden_stream_preserves_every_item():
    # Guards the plumbing rather than the model: given a faithful rewrite, the
    # chunking must not lose a clause on the way to the synthesiser.
    stream = VoiceStream(verbalizer=RecordingVerbalizer(lambda _: GOLDEN_SPOKEN))

    chunks = await _feed(stream, GOLDEN_INPUT)
    spoken = " ".join(chunks)

    for place in ("Point Reyes", "Mount Tam", "Muir Woods",
                  "Stinson Beach", "Tennessee Valley"):
        assert place in spoken, f"{place} was dropped"
    for detail in ("morning fog", "steep but short", "book ahead",
                   "windy after noon", "stroller friendly"):
        assert detail in spoken, f"{detail} was dropped"


@pytest.mark.asyncio
async def test_golden_stream_preserves_item_order():
    stream = VoiceStream(verbalizer=RecordingVerbalizer(lambda _: GOLDEN_SPOKEN))

    chunks = await _feed(stream, GOLDEN_INPUT)
    spoken = " ".join(chunks)

    positions = [
        spoken.index(place)
        for place in ("Point Reyes", "Mount Tam", "Muir Woods",
                      "Stinson Beach", "Tennessee Valley")
    ]
    assert positions == sorted(positions), "items were reordered"


@pytest.mark.asyncio
async def test_golden_fallback_preserves_every_item():
    # Even with the model unavailable, the sanitizer path must not drop a row.
    verbalizer = Verbalizer()
    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=RuntimeError("model down"))
    with patch.object(Verbalizer, "_get_llm", return_value=llm):
        stream = VoiceStream(verbalizer=verbalizer)
        chunks = await _feed(stream, GOLDEN_INPUT)

    spoken = " ".join(chunks)
    for place in ("Point Reyes", "Mount Tam", "Muir Woods",
                  "Stinson Beach", "Tennessee Valley"):
        assert place in spoken, f"{place} was dropped by the fallback"


# --------------------------------------------------------------------------
# XML stripping
# --------------------------------------------------------------------------

def test_strip_xml_removes_memory_and_vote_blocks():
    text = "<memory>User prefers concise answers.</memory>Here is the plan."
    assert strip_xml(text) == "Here is the plan."

    text = "<vote>yes</vote>The vote was recorded."
    assert strip_xml(text) == "The vote was recorded."

    text = "<memory>Note 1</memory><vote>Note 2</vote>All clear."
    assert strip_xml(text) == "All clear."


def test_strip_xml_removes_nested_and_multiline_blocks():
    text = "<vote><choice>yes</choice><reason>fast</reason></vote>Done."
    assert strip_xml(text) == "Done."

    text = "<memory>\n- User likes tea\n- User likes coffee\n</memory>\nHere is your tea."
    assert strip_xml(text).strip() == "Here is your tea."


def test_strip_xml_removes_unclosed_or_dangling_tags():
    assert strip_xml("<memory>unclosed tag without closing") == ""
    assert strip_xml("Here is content.<memory>unclosed") == "Here is content."


def test_strip_xml_preserves_plain_text_and_comparisons():
    assert strip_xml("The price is < 50 dollars.") == "The price is < 50 dollars."
    assert strip_xml("Sure, I booked the table.") == "Sure, I booked the table."


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
        result = await v.verbalize("<memory>User likes grocery lists</memory>\n<vote>yes</vote>\n- eggs\n- milk")

    assert result == "First, eggs. After that, milk."
    messages = llm.ainvoke.call_args[0][0]
    # Verify the XML block was stripped before being passed to the model
    assert "<memory>" not in messages[1].content
    assert "User likes grocery lists" not in messages[1].content
    assert "<vote>" not in messages[1].content
    assert "- eggs\n- milk" in messages[1].content


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


@pytest.mark.asyncio
async def test_stream_strips_memory_and_vote_blocks():
    stream = VoiceStream(verbalizer=RecordingVerbalizer())
    chunks = await _feed(
        stream,
        "<memory>Remember user preferences.</memory> I booked the table for seven. It is under your name.",
    )
    spoken = " ".join(chunks)
    assert "Remember user preferences" not in spoken
    assert "booked the table for seven" in spoken


@pytest.mark.asyncio
async def test_stream_holds_in_flight_xml_tokens():
    stream = VoiceStream(verbalizer=RecordingVerbalizer())
    # Add tokens where XML block is split across chunks
    tokens = ["<mem", "ory>User likes pizza.</mem", "ory> I booked ", "the table for seven. "]
    chunks = []
    for t in tokens:
        chunks.extend(await stream.add_token(t))
    chunks.extend(await stream.flush())

    spoken = " ".join(chunks)
    assert "pizza" not in spoken.lower()
    assert "booked the table for seven" in spoken


@pytest.mark.asyncio
async def test_stream_only_xml_emits_nothing():
    stream = VoiceStream(verbalizer=RecordingVerbalizer())
    chunks = await _feed(stream, "<memory>Internal agent notes</memory><vote>approve</vote>")
    assert chunks == []

