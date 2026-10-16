"""A voice reply without <speech>: the fallback, and the note posted under it."""
from core.voice.speech.missing_speech import REST_IN_CHANNEL, alert, fallback_speech
from tests.helpers import GOLDEN_INPUT


def test_the_fallback_is_the_first_sentence_and_where_the_rest_is():
    reply = "You have two meetings today. Standup is at nine. The dentist is at three."
    assert fallback_speech(reply) == f"You have two meetings today. {REST_IN_CHANNEL}"


def test_a_one_sentence_reply_is_spoken_whole():
    assert fallback_speech("Done, the lights are off.") == "Done, the lights are off."


def test_the_fallback_speaks_words_not_markup():
    spoken = fallback_speech(GOLDEN_INPUT)
    assert spoken.endswith(REST_IN_CHANNEL)
    assert not any(mark in spoken for mark in ("#", "*", "<"))
    assert "Weekend options" in spoken


def test_no_text_no_fallback():
    assert fallback_speech("") == ""
    assert fallback_speech("<poll><question>Q?</question></poll>") == ""


def test_the_note_names_the_agent_that_replied():
    assert alert("day-planner") == "⚠️ `day-planner` replied without a `<speech>` block; the voice read the fallback."
