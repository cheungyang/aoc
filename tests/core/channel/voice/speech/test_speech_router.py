"""SpeechTextRouter: the speech lane gets <speech>...</speech>, the text lane the rest."""
import random

from core.channel.voice.speech.speech_router import SpeechTextRouter

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


# A hand-over inside <speech> is never said: team.md asks for the tag outside,
# but a model may put it in, and a brief spans sentences.
HANDED_OVER = (
    "<speech>Handing you to Elephant now. "
    "<switch to=\"software-planner\">Spec the inbox scanner. Decisions: a script, not a prompt.</switch>"
    "</speech>Over to Elephant."
)


def test_a_brief_inside_speech_is_never_said_however_tokens_fall():
    rng = random.Random(7)
    expected = ("Handing you to Elephant now. \n", "Over to Elephant.")
    assert route([HANDED_OVER]) == expected
    assert route(list(HANDED_OVER)) == expected
    for _ in range(2000):
        assert route(random_split(HANDED_OVER, rng)) == expected


def test_a_self_closing_switch_inside_speech_is_dropped_too():
    assert route(["<speech>Bye. <switch to=\"x\"/></speech>"]) == ("Bye. \n", "")
    assert route(["<speech>Bye. <switch to=\"x\"/", "> See you.</speech>"]) == ("Bye.  See you.\n", "")


def test_an_unfinished_brief_ends_with_its_block():
    """An opening tag the model never closed hides the rest of its block, not the next one."""
    assert route(["<speech>One. <switch to=\"x\">brief</speech><speech>Two.</speech>"]) == ("One. \nTwo.\n", "")


def test_a_lookalike_switch_is_said():
    assert route(["<speech>The <switchboard> ", "is fine. <swi", "tching> too.</speech>"]) == (
        "The <switchboard> is fine. <switching> too.\n", "")


def test_a_switch_in_the_text_is_left_to_the_text_lane():
    assert route(["<speech>Hi.</speech><switch to=\"x\">brief</switch>"]) == (
        "Hi.\n", "<switch to=\"x\">brief</switch>")


# A pass inside <speech> is never said either: like a switch's, its brief is
# for the teammate, and spans sentences.
PASSED = (
    "<speech>Sure, I'll let Daisy know. "
    "<pass to=\"day-planner\">Book the dentist for Tuesday. Mornings only, not before nine.</pass>"
    " Anything else?</speech>Passed to Daisy."
)


def test_a_pass_inside_speech_is_never_said_however_tokens_fall():
    rng = random.Random(11)
    expected = ("Sure, I'll let Daisy know.  Anything else?\n", "Passed to Daisy.")
    assert route([PASSED]) == expected
    assert route(list(PASSED)) == expected
    for _ in range(2000):
        assert route(random_split(PASSED, rng)) == expected


def test_a_self_closing_pass_inside_speech_is_dropped_too():
    assert route(["<speech>Noted. <pass to=\"x\"/></speech>"]) == ("Noted. \n", "")
    assert route(["<speech>Noted. <pa", "ss to=\"x\"/", "> Bye.</speech>"]) == ("Noted.  Bye.\n", "")


def test_only_the_directive_that_opened_ends_its_brief():
    assert route(["<speech>One. <pass to=\"x\">eggs </switch> and milk</pass> Two.</speech>"]) == ("One.  Two.\n", "")
    assert route(["<speech>One. <switch to=\"x\">plan </pass> it</switch> Two.</speech>"]) == ("One.  Two.\n", "")


def test_a_pass_and_a_switch_in_one_block():
    assert route(["<speech>Done. <pass to=\"x\">Eggs.</pass>Bye. <switch to=\"y\"/></speech>"]) == ("Done. Bye. \n", "")


def test_an_unfinished_pass_ends_with_its_block():
    assert route(["<speech>One. <pass to=\"x\">brief</speech><speech>Two.</speech>"]) == ("One. \nTwo.\n", "")


def test_a_lookalike_pass_is_said():
    assert route(["<speech>Your <password> ", "is safe; the <pas", "sage> too. <p", "aste> it.</speech>"]) == (
        "Your <password> is safe; the <passage> too. <paste> it.\n", "")
