"""Enrollment: which utterances count as readings, and what's posted and said."""
from types import SimpleNamespace

from core.voice.listening.enrollment import (
    ENROLL_TIMEOUT_S, MIN_CONSISTENCY, MIN_READING_MS, SENTENCES, Enrollment, saved_message,
)
from core.voice.listening.utterance import BYTES_PER_MS, Utterance


def spoken(ms, preroll_ms=500):
    u = Utterance()
    u.pcm += bytes((preroll_ms + ms + 900) * BYTES_PER_MS)
    u.speech_start, u.speech_end = preroll_ms * BYTES_PER_MS, (preroll_ms + ms) * BYTES_PER_MS
    return u


def test_a_reading_needs_enough_speech_not_just_enough_audio():
    enrollment = Enrollment()
    assert not enrollment.take(spoken(MIN_READING_MS - 100, preroll_ms=900)), "the pre-roll isn't speech"
    assert enrollment.take(spoken(MIN_READING_MS))
    assert enrollment.readings and enrollment.remaining() == len(SENTENCES) - 1


def test_it_is_done_after_one_reading_per_sentence_and_takes_no_more():
    enrollment = Enrollment(sentences=["one", "two"])
    assert enrollment.take(spoken(3000)) and not enrollment.done
    assert enrollment.take(spoken(3000)) and enrollment.done
    assert not enrollment.take(spoken(3000))
    assert len(enrollment.readings) == 2


def test_it_expires_after_a_while_without_a_word():
    now = [0.0]
    enrollment = Enrollment(clock=lambda: now[0])
    now[0] = ENROLL_TIMEOUT_S - 1
    enrollment.take(spoken(100))  # even a word too short to count keeps it alive
    now[0] += ENROLL_TIMEOUT_S - 1
    assert not enrollment.expired()
    now[0] += 2
    assert enrollment.expired()


def test_what_it_posts_and_says():
    enrollment = Enrollment(channel="chan")
    posted = enrollment.instructions()
    assert all(f"{i}. {s}" in posted for i, s in enumerate(SENTENCES, 1))
    assert "!enroll cancel" in posted
    assert enrollment.opening_line() == ("Let's take your voiceprint. Read the four sentences in the chat, "
                                         "pausing after each.")
    assert enrollment.retry_line() == "I missed that. Sentence one, please."
    enrollment.take(spoken(3000))
    assert enrollment.progress_line() == "Got it. Three more."
    assert enrollment.retry_line() == "I missed that. Sentence two, please."


def test_the_saved_message_warns_of_unlike_readings_and_an_off_gate():
    good = SimpleNamespace(readings=4, consistency=0.81)
    assert saved_message(good, "shadow") == ("🎙️ Voiceprint saved from 4 readings (consistency 0.81). "
                                             "The gate is in `shadow` mode.")
    poor = saved_message(SimpleNamespace(readings=4, consistency=MIN_CONSISTENCY - 0.1), "off")
    assert "⚠️" in poor and "`!enroll`" in poor and "set `voice_config.speaker_gate` to `shadow`" in poor
