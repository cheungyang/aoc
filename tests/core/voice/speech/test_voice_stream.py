"""VoiceStream: the turn's <speech> text, cut into sentences as it streams."""
from core.voice.speech.voice_stream import VoiceStream


def feed(stream, text, chunk_size=7):
    """Streams `text` in small pieces; returns the sentences in order, flush included."""
    out = []
    for i in range(0, len(text), chunk_size):
        out += stream.add(text[i:i + chunk_size])
    return out + stream.flush()


def test_sentences_are_emitted_before_the_speech_ends():
    stream = VoiceStream()
    first = stream.add("You have two meetings today. The first one is ")
    assert first == ["You have two meetings today."]
    assert stream.flush() == ["The first one is"]


def test_every_word_is_spoken_in_order():
    speech = ("There are five options. Point Reyes is a two hour drive and free. Mount Tam is forty-five "
              "minutes with eight dollar parking. Muir Woods is an hour away. Stinson Beach is windy after "
              "noon. Tennessee Valley is flat and stroller friendly.")
    spoken = " ".join(feed(VoiceStream(), speech))
    assert spoken == speech


def test_a_closed_block_is_spoken_at_once_however_short():
    """The router ends each <speech> block with a line break. A short preamble
    is heard while its tool runs, and never runs into the next block."""
    stream = VoiceStream()
    assert stream.add("One moment") == []
    assert stream.add("\n") == ["One moment"]
    assert stream.add("Checking.\nYou're free after four.\n") == ["Checking.", "You're free after four."]
    assert stream.flush() == []


def test_a_runaway_sentence_is_cut_at_a_clause():
    speech = "First " + "and then another thing, " * 10 + "done."
    chunks = feed(VoiceStream(max_chars=80), speech)
    assert len(chunks) > 1
    assert all(len(c) <= 140 for c in chunks)
    assert " ".join(chunks).replace("  ", " ").startswith("First and then another thing,")


def test_notation_is_spoken_as_words():
    """The agent writes for the ear, but a stray tag, symbol or asterisk is
    still never read out as one."""
    chunks = feed(VoiceStream(), "Call the dentist \u23EB and buy milk #shopping. It's **today**.")
    spoken = " ".join(chunks)
    assert "high priority" in spoken
    assert "#" not in spoken and "*" not in spoken
    assert "today" in spoken


def test_nothing_in_nothing_out():
    stream = VoiceStream()
    assert stream.add("") == []
    assert stream.flush() == []
