"""Tests for the speech notation pass: XML, hashtags and priority symbols."""
import pytest

from core.voice.speech_util import speak_priority_symbols, strip_hashtags, strip_xml
from core.voice.verbalizer import has_structure


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


# --------------------------------------------------------------------------
# Task notation: hashtags and priority symbols
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("Read this #a/read today.", "Read this today."),
    ("Buy milk #shopping", "Buy milk"),
    ("Filed under #project/home/kitchen now.", "Filed under now."),
    ("Two tags #a/read #b/write here.", "Two tags here."),
    ("Tagged #work-stuff and #home_stuff.", "Tagged and."),
])
def test_strip_hashtags_removes_tags_and_tidies_spacing(text, expected):
    assert strip_hashtags(text) == expected


@pytest.mark.parametrize("text", [
    "# Heading",                          # markdown header, not a tag
    "###### Deep heading",
    "The build uses C# and F#.",
    "See issue #4 for the details.",
    "Open https://example.com/page#section for more.",
    "Press the # key twice.",
])
def test_strip_hashtags_leaves_non_tags_alone(text):
    """A hash is only a tag when it opens a word. Everything else here is
    content a listener needs."""
    assert strip_hashtags(text) == text


def test_strip_hashtags_preserves_line_structure():
    """`has_structure` reads line beginnings; flattening here would route a
    list down the prose path and lose the rewriting."""
    cleaned = strip_hashtags("- eggs #shopping\n- milk #shopping")
    assert cleaned == "- eggs\n- milk"
    assert has_structure(cleaned) is True


def test_strip_hashtags_drops_a_tag_only_line_entirely():
    """Leaving the line blank would read as a block boundary downstream and
    split the list in two."""
    assert strip_hashtags("- eggs\n#shopping\n- milk") == "- eggs\n- milk"
    assert strip_hashtags("- eggs\n- #shopping\n- milk") == "- eggs\n- milk"


def test_strip_hashtags_keeps_genuinely_blank_lines():
    """Only lines emptied *by tag removal* are dropped; a real paragraph
    break is a block boundary the stream relies on."""
    assert strip_hashtags("First para.\n\nSecond para.") == "First para.\n\nSecond para."


def test_strip_hashtags_empty():
    assert strip_hashtags("") == ""


@pytest.mark.parametrize("symbol,words", [
    ("\U0001F53A", "highest priority"),
    ("\u23EB", "high priority"),
    ("\U0001F53C", "medium priority"),
    ("\U0001F53D", "low priority"),
    ("\u23EC", "lowest priority"),
])
def test_speak_priority_symbols_covers_every_level(symbol, words):
    assert speak_priority_symbols(f"Call the dentist {symbol}") == (
        f"Call the dentist, {words}"
    )


def test_speak_priority_symbols_reads_a_leading_symbol_as_a_label():
    assert speak_priority_symbols("\u23EB Call the dentist") == (
        "high priority: Call the dentist"
    )


def test_speak_priority_symbols_handles_a_symbol_after_a_bullet():
    assert speak_priority_symbols("- \u23EB Call the dentist") == (
        "- high priority: Call the dentist"
    )


def test_speak_priority_symbols_does_not_double_up_punctuation():
    assert speak_priority_symbols("Call the dentist. \u23EB") == (
        "Call the dentist. high priority"
    )


def test_speak_priority_symbols_leaves_plain_text_alone():
    text = "Call the dentist tomorrow."
    assert speak_priority_symbols(text) == text
