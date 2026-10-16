"""Turn-taking rules: adaptive end of turn, barge-in confirmation, backchannels, stops."""
import pytest

from core.voice import settings
from core.voice.listening.turn_taking import (
    bare_stop,
    confirms_barge_in,
    end_of_turn_ms,
    is_backchannel,
    is_stop_request,
    merge_transcripts,
    words,
)


def test_words_lowercases_and_splits_hyphens():
    assert words("Mm-hmm, OK.") == ["mm", "hmm", "ok"]
    assert words("") == []
    assert words(None) == []


class TestEndOfTurn:
    def test_a_finished_sentence_ends_quickly(self):
        assert end_of_turn_ms("Add milk to the shopping list.") == settings.END_OF_TURN_COMPLETE_MS
        assert end_of_turn_ms("What's on my calendar today?") == settings.END_OF_TURN_COMPLETE_MS

    def test_no_words_yet_keeps_the_default_wait(self):
        assert end_of_turn_ms("") == settings.END_OF_TURN_DEFAULT_MS
        assert end_of_turn_ms("   ") == settings.END_OF_TURN_DEFAULT_MS

    @pytest.mark.parametrize("text", [
        "Add milk and",
        "Remind me to call the",
        "I need to, um",
        "Move the dentist appointment to",
        "Book a table for",
    ])
    def test_a_trailing_continuation_waits_longer(self, text):
        assert end_of_turn_ms(text) == settings.END_OF_TURN_CONTINUING_MS

    def test_a_trailing_continuation_wins_over_punctuation(self):
        # Interims sometimes punctuate a word that is plainly mid-sentence.
        assert end_of_turn_ms("Add milk and.") == settings.END_OF_TURN_CONTINUING_MS

    def test_unpunctuated_words_keep_the_default_wait(self):
        assert end_of_turn_ms("add milk to the shopping list") == settings.END_OF_TURN_DEFAULT_MS

    def test_a_short_complete_sentence_is_often_a_lead_in(self):
        # "Hey Daisy." usually comes before the request, not instead of it.
        assert end_of_turn_ms("Hey Daisy.") == settings.END_OF_TURN_DEFAULT_MS

    def test_a_short_stop_ends_quickly(self):
        assert end_of_turn_ms("Stop.") == settings.END_OF_TURN_COMPLETE_MS

    def test_the_waits_are_ordered(self):
        assert (settings.END_OF_TURN_COMPLETE_MS < settings.END_OF_TURN_DEFAULT_MS
                < settings.END_OF_TURN_CONTINUING_MS)


class TestBackchannel:
    @pytest.mark.parametrize("text", [
        "Okay.", "Mm-hmm.", "Yeah.", "Yeah, sure.", "Got it.", "Uh-huh.", "Right.",
        "Thank you.", "OK cool", "I see.",
    ])
    def test_listening_noises(self, text):
        assert is_backchannel(text)

    @pytest.mark.parametrize("text", [
        "", "Okay, add it to Friday.", "No.", "Tomorrow.", "Yeah but what about Friday",
        "Okay okay okay okay",
    ])
    def test_not_listening_noises(self, text):
        assert not is_backchannel(text)


class TestBargeIn:
    @pytest.mark.parametrize("text", ["stop", "Wait", "hold on", "Never mind", "okay stop"])
    def test_a_stop_phrase_interrupts_however_short(self, text):
        assert is_stop_request(text)
        assert confirms_barge_in(text)

    def test_a_stop_phrase_late_in_a_sentence_is_not_a_stop_request(self):
        assert not is_stop_request("I want to know when the bus will stop")

    def test_two_real_words_interrupt(self):
        assert confirms_barge_in("Actually Friday")
        assert confirms_barge_in("And eggs")

    def test_one_word_does_not_interrupt_yet(self):
        assert not confirms_barge_in("Actually")
        assert not confirms_barge_in("")

    def test_backchannels_never_interrupt(self):
        assert not confirms_barge_in("Yeah sure")
        assert not confirms_barge_in("Mm-hmm")
        assert not confirms_barge_in("Got it")


class TestBareStop:
    @pytest.mark.parametrize("text", ["Stop.", "Stop, stop.", "Okay, stop talking.", "Never mind.",
                                      "Cancel that.", "No, no.", "Forget it.", "Enough."])
    def test_stop_drops_the_request(self, text):
        assert bare_stop(text) == "cancel"

    @pytest.mark.parametrize("text", ["Wait.", "Hold on.", "Wait a second.", "Hang on a sec.",
                                      "Hold up, please."])
    def test_wait_keeps_the_request(self, text):
        assert bare_stop(text) == "hold"

    @pytest.mark.parametrize("text", ["Wait, make it Friday.", "Stop the timer.", "Wait, what?",
                                      "Hold on, what about Thursday?", "Okay.", ""])
    def test_anything_more_is_a_request(self, text):
        assert bare_stop(text) is None


def test_merge_transcripts_joins_in_order_and_skips_empty_parts():
    assert merge_transcripts(["Add milk to the list.", "", "  ", "And eggs."]) == \
        "Add milk to the list. And eggs."
    assert merge_transcripts([]) == ""
