"""SpeechTextRouter: the speech lane gets <speech>...</speech>, the text lane the rest."""
import random

from core.voice.speech.speech_router import SpeechTextRouter

REPLY = (
    "<speech>Checking your calendar.</speech>"
    "<speech>You have two meetings: standup at nine, and the dentist at three.</speech>\n\n"
    "**Today**\n- 9:00 Standup\n- 15:00 Dentist (bring the <form>)\n"
    "<system_memory_log>\n- [09:00:00] [MEMORY] Read the calendar.\n</system_memory_log>"
)
SPEECH = "Checking your calendar.\nYou have two meetings: standup at nine, and the dentist at three.\n"
TEXT = (
    "\n\n**Today**\n- 9:00 Standup\n- 15:00 Dentist (bring the <form>)\n"
    "<system_memory_log>\n- [09:00:00] [MEMORY] Read the calendar.\n</system_memory_log>"
)


def route(tokens):
    router, speech, text = SpeechTextRouter(), [], []
    for token in tokens:
        said, shown = router.feed(token)
        speech.append(said)
        text.append(shown)
    said, shown = router.flush()
    return "".join(speech + [said]), "".join(text + [shown])


def random_split(text, rng):
    tokens, i = [], 0
    while i < len(text):
        n = rng.randint(1, 12)
        tokens.append(text[i:i + n])
        i += n
    return tokens


def test_whole_reply_in_one_token():
    assert route([REPLY]) == (SPEECH, TEXT)


def test_two_thousand_random_token_splits():
    """However the model's tokens fall -- one character at a time, or a tag
    split across three tokens -- the lanes come out the same."""
    rng = random.Random(4)
    for _ in range(2000):
        assert route(random_split(REPLY, rng)) == (SPEECH, TEXT)


def test_one_character_at_a_time():
    assert route(list(REPLY)) == (SPEECH, TEXT)


def test_a_split_tag_is_held_until_the_next_token_decides_it():
    router = SpeechTextRouter()
    assert router.feed("Hi <spe") == ("", "Hi ")
    assert router.feed("ech>Hello") == ("Hello", "")
    assert router.feed("</spee") == ("", "")
    assert router.feed("ch> there") == ("\n", " there")  # the block's end, for the voice stream


def test_a_lookalike_is_released_as_text():
    router = SpeechTextRouter()
    assert router.feed("Use <spe") == ("", "Use ")
    assert router.feed("cial> care") == ("", "<special> care")


def test_flush_drops_a_cut_off_closing_tag_and_releases_text():
    router = SpeechTextRouter()
    router.feed("<speech>Almost </spee")  # a reply cut off mid-tag by an error
    assert router.flush() == ("", "")
    router = SpeechTextRouter()
    router.feed("Text ends <spe")
    assert router.flush() == ("", "<spe")


def test_an_unclosed_block_keeps_streaming_to_speech():
    assert route(["<speech>The kitchen light ", "is on."]) == ("The kitchen light is on.", "")


def test_tags_are_matched_case_insensitively():
    assert route(["<SPEECH>Loud.</Speech>Quiet."]) == ("Loud.\n", "Quiet.")


def test_a_reply_without_speech_is_all_text():
    assert route(["Just text, ", "no tags < here."]) == ("", "Just text, no tags < here.")
