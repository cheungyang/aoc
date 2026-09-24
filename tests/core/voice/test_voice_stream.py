"""Tests for VoiceStream: cadence, block boundaries, and that nothing is dropped."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.voice.verbalizer import Verbalizer
from core.voice.voice_stream import VoiceStream
from tests.helpers import GOLDEN_INPUT, voice_session


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
# XML and notation
# --------------------------------------------------------------------------

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


@pytest.mark.asyncio
async def test_stream_prose_handles_notation_on_the_fast_path():
    """The prose branch never calls `verbalize`, so it needs its own pass."""
    stream = VoiceStream(verbalizer=RecordingVerbalizer())
    chunks = await _feed(
        stream,
        "Call the dentist \u23EB. Then read the briefing #a/read today.",
    )
    spoken = " ".join(chunks)
    assert "high priority" in spoken
    assert "#" not in spoken
    assert "a/read" not in spoken
    assert "briefing" in spoken


# --------------------------------------------------------------------------
# Token attribution
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stream_hands_its_session_to_the_verbalizer():
    session = voice_session()
    verbalizer = MagicMock()
    verbalizer.verbalize = AsyncMock(return_value="Eggs.")
    stream = VoiceStream(verbalizer=verbalizer, session=session)

    await stream.add_token("- eggs\n- milk")
    await stream.flush()

    verbalizer.verbalize.assert_awaited_once_with("- eggs\n- milk", session=session)
