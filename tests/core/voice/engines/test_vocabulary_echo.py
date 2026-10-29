"""Vocabulary echoes (core/voice/engines/vocabulary_echo.py).

The positives are what the live recognizer returned in production on
2026-10-28 (the voiceprint gate's log: transcript, and the voiced audio it
came from). The negatives are things you said that week, at the length the
gate measured, and phrasings that name agents the way people do.
"""
import pytest

from core.voice.engines.vocabulary_echo import MAX_LETTERS_PER_S, vocabulary_echo

# What production biases recognition toward: each agent you can reach by name,
# and its id as spoken (core/voice/engines/stt.py `agent_vocabulary`).
VOCABULARY = [
    "Eric", "excursion planner", "Rio", "property scout", "Coco", "content creator",
    "Receipt Processor", "Butler", "home steward", "Daisy", "day planner", "Frankie",
    "fitness coach", "Grace", "goal setter", "William", "wiki gardener", "Aki", "agent designer",
    "Duckie", "note taker", "Miles", "reward travel", "Ted", "topic researcher", "Elephant",
    "software planner", "Marvin", "market scout", "Ramsay", "meal planner",
]

# Gate #158, 18:44:45: 1836 ms of audio the gate scored 0.06. Its last line
# handed the voice room from Grace to William. Recited from the vocabulary of
# the day, which still had the workers and the concierge in it.
ROSTER_2026_10_28 = (
    "Here is the list of added phrases:\n\n- Aki, agent designer\n- Butler, home steward\n"
    "- Coco, content creator\n- Concierge, main\n- Daisy, day planner\n- Duckie, note taker\n"
    "- Elephant, software planner\n- Eric, excursion planner\n- Frankie, fitness coach\n"
    "- Grace, goal setter\n- Graph Worker, graph worker\n- Graph Worker Low, graph worker low\n"
    "- Marvin, market scout\n- Miles, reward travel\n- Ramsay, meal planner\n"
    "- Receipt Processor, receipt processor\n- Rio, property scout\n- Ted, topic researcher\n"
    "- Terminator, script executor\n- William, wiki gardener"
)


def echo(text, ms=0):
    return vocabulary_echo(text, VOCABULARY, ms)


class TestRecitals:

    def test_the_roster_that_moved_the_room_to_william(self):
        assert echo(ROSTER_2026_10_28, 1836) is not None

    def test_the_roster_is_a_roster_even_without_its_preamble(self):
        """Every tell stands alone: without its first line, and without the
        audio's length, the list is still a recital."""
        body = ROSTER_2026_10_28.split("\n\n", 1)[1]
        assert echo(body).startswith("a roster of")

    def test_the_preamble_alone_is_enough(self):
        """However little of the list follows it."""
        assert echo("Here is the list of added phrases:\n\n- Aki, agent designer", 2000) == \
            "the recognizer's preamble to its list"

    def test_a_name_and_its_role(self):
        """Gate #126, 17:35:37: handed the room from Marvin to Aki."""
        assert echo("Aki, agent designer.", 900).startswith("nothing but vocabulary terms")

    @pytest.mark.parametrize("ms", [180, 324])
    def test_a_name_from_a_fraction_of_a_second(self, ms):
        """Gates #162 and #134: "Receipt Processor" is six syllables."""
        assert "letters from" in echo("Receipt Processor.", ms)

    def test_other_words_too_fast_to_have_been_said(self):
        """Gate #27, 2026-10-24: not the vocabulary, but no one says it in 180 ms."""
        assert echo("Tell your agent.", 180) is not None


class TestSpeech:

    @pytest.mark.parametrize("text, ms", [
        # what you said that week, at the voiced length the gate measured
        ("Switch to Grace.", 1008),
        ("Let's start with Grace.", 792),
        ("Start with Marvin.", 504),
        ("Let's look at our note-taking.", 828),
        ("I'll get this done.", 540),
        ("Grace.", 180),
        ("Frankie.", 324),
        ("Next agent.", 720),
        ("By the way, dropping them means marking them as obsolete instead of deleting them.", 4140),
    ])
    def test_what_you_said(self, text, ms):
        assert echo(text, ms) is None

    @pytest.mark.parametrize("text", [
        "Start with Marvin, then Grace, then William.",
        "Ted and William and Grace, what do you all think?",
        "Receipt Processor, file this receipt.",
        "Ask Ted what William said about caching.",
        "Daisy and Ted",
    ])
    def test_naming_agents_the_way_people_do(self, text):
        assert echo(text, len(text) * 80) is None  # ~12 letters a second

    def test_one_name_at_a_speed_a_person_could_say_it(self):
        """Gate #137 recited "Receipt Processor." over 792 ms: no different from
        you saying it, so it passes. The cost of not dropping your answers."""
        assert echo("Receipt Processor.", 792) is None


class TestRules:

    def test_without_the_audio_length_the_speed_is_not_checked(self):
        assert echo("Receipt Processor.", 0) is None

    def test_the_speed_limit(self):
        just_under = "a" * (MAX_LETTERS_PER_S - 1)
        assert echo(just_under, 1000) is None
        assert echo(just_under + "aa", 1000) is not None

    def test_terms_match_whole_words_in_any_case_and_spelling(self):
        assert echo("ted, TOPIC-RESEARCHER") is not None
        assert echo("Teddy, gracefully") is None

    def test_a_term_twice_is_one_term(self):
        """"Ted. Ted!" is someone calling, not the list."""
        assert echo("Ted. Ted!") is None

    def test_no_vocabulary_leaves_only_the_preamble_and_the_speed(self):
        body = ROSTER_2026_10_28.split("\n\n", 1)[1]
        assert vocabulary_echo(body, [], 0) is None
        assert vocabulary_echo(body, [], 1836) is not None

    def test_no_text_is_no_echo(self):
        assert echo("") is None
        assert echo(None) is None
