"""How spoken words are compared."""
from core.runtime.commands.spoken.words import alias_key, clean, normalize, words


def test_words_lowercases_and_splits_hyphens():
    assert words("Mm-hmm, OK.") == ["mm", "hmm", "ok"]
    assert words("") == []
    assert words(None) == []


def test_clean_keeps_apostrophes_and_single_spaces():
    assert clean("  Take me to Daisy\u2019s   room, please! ") == "take me to daisy's room please"
    assert clean(None) == ""


def test_normalize_drops_possessives_and_apostrophes():
    assert normalize("Daisy's room") == "daisy room"
    assert normalize("don't") == "dont"


def test_alias_key_drops_the_words_around_a_name():
    assert alias_key("the Day-Planning room, please!") == "day planning"
    assert alias_key("The concierge") == "concierge"
