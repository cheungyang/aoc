"""Intent parsing is the graph's spend gate, so it is tested as one.

Several assertions here are deliberately inverted relative to the previous
suite: messages that used to be classified as approvals or revisions are now
required to be `unclear`. That is the point of the change -- the old
classifier's confident answers were how unattended money got spent.
"""

import unittest

from graphs.content_creation.utils.classifiers import (
    ABORT,
    APPROVE,
    GATE1,
    GATE2,
    RETRY,
    REVISE,
    STATUS,
    UNCLEAR,
    Intent,
    extract_remix_parameters,
    gate_menu,
    parse_intent,
)


class TestApproval(unittest.TestCase):
    """Approval is whole-message equality. Nothing else counts."""

    def test_exact_approvals(self):
        for message in [
            "approve", "Approve", "APPROVED", "approve!", " lgtm ", "ship it",
            "looks good", "yes", "ok", "perfect", "go ahead", "👍",
        ]:
            with self.subTest(message=message):
                for gate in (GATE1, GATE2):
                    self.assertEqual(parse_intent(message, gate).action, APPROVE)

    def test_negation_is_not_approval(self):
        """The headline regression.

        The old classifier matched approval phrases with `endswith`, before
        checking for negation, so every one of these was 'approved' and the
        graph moved on to spend money on the next stage.
        """
        for message in [
            "not good", "not great", "no", "nope", "bad", "reject",
        ]:
            with self.subTest(message=message):
                for gate in (GATE1, GATE2):
                    intent = parse_intent(message, gate)
                    self.assertEqual(intent.action, UNCLEAR)
                    self.assertFalse(intent.may_spend)

    def test_rejection_explains_itself(self):
        intent = parse_intent("not good", GATE1)
        self.assertIn("rejection", intent.reason.lower())

    def test_approval_word_inside_a_sentence_is_not_approval(self):
        """'I approve of the image but the plot is wrong' is not an approval."""
        for message in [
            "approve the image but not the plot",
            "looks good except the hat",
            "yes but change the camera",
            "ok, but the mouth is wrong",
        ]:
            with self.subTest(message=message):
                self.assertEqual(parse_intent(message, GATE1).action, UNCLEAR)


class TestControl(unittest.TestCase):
    def test_control_verbs(self):
        self.assertEqual(parse_intent("abort", GATE1).action, ABORT)
        self.assertEqual(parse_intent("cancel", GATE2).action, ABORT)
        self.assertEqual(parse_intent("stop", GATE1).action, ABORT)
        self.assertEqual(parse_intent("retry", GATE1).action, RETRY)
        self.assertEqual(parse_intent("status", GATE2).action, STATUS)

    def test_abort_and_status_never_spend(self):
        for message in ("abort", "status"):
            self.assertFalse(parse_intent(message, GATE1).may_spend)


class TestExplicitRevision(unittest.TestCase):
    def test_gate1_targets(self):
        intent = parse_intent("revise image: make the hat red", GATE1)
        self.assertEqual(intent.action, REVISE)
        self.assertEqual(intent.target, "image")
        self.assertEqual(intent.instruction, "make the hat red")
        self.assertEqual(intent.decision(GATE1), "revise_image")

        intent = parse_intent("revise plot: push in more slowly", GATE1)
        self.assertEqual(intent.target, "plot")
        self.assertEqual(intent.decision(GATE1), "revise_plot")

    def test_gate2_targets(self):
        cases = {
            "revise video: re-render the animation": ("video", "revise_video"),
            "revise audio: start at 4s": ("remix", "revise_remix"),
            "revise copy: shorter hook": ("copy", "revise_copy"),
        }
        for message, (target, decision) in cases.items():
            with self.subTest(message=message):
                intent = parse_intent(message, GATE2)
                self.assertEqual(intent.target, target)
                self.assertEqual(intent.decision(GATE2), decision)

    def test_target_must_belong_to_the_gate(self):
        """Gate 2 cannot revise the image, and says so instead of guessing.

        The old classifier had no image bucket at gate 2, so 'the image is
        wrong' fell through to its default and regenerated the *copy*.
        """
        intent = parse_intent("revise image: the hat is wrong", GATE2)
        self.assertEqual(intent.action, UNCLEAR)
        self.assertIn("Gate 2", intent.reason)

        intent = parse_intent("revise copy: shorter", GATE1)
        self.assertEqual(intent.action, UNCLEAR)

    def test_unknown_target(self):
        intent = parse_intent("revise vibes: more energy", GATE1)
        self.assertEqual(intent.action, UNCLEAR)
        self.assertIn("vibes", intent.reason)

    def test_bare_revision_without_instruction_asks_for_one(self):
        intent = parse_intent("revise image", GATE1)
        self.assertEqual(intent.action, UNCLEAR)
        self.assertFalse(intent.may_spend)

    def test_bare_revision_reuses_pending_instruction(self):
        intent = parse_intent("revise image", GATE1, pending_instruction="make the hat red")
        self.assertEqual(intent.action, REVISE)
        self.assertEqual(intent.instruction, "make the hat red")

    def test_remix_instruction_carries_parameters(self):
        intent = parse_intent("revise remix: audio at 4s and subtitles at 4s", GATE2)
        self.assertEqual(intent.target, "remix")
        self.assertEqual(intent.params.get("audio_start_time"), 4.0)
        self.assertEqual(intent.params.get("text_start_time"), 4.0)

    def test_parameters_are_not_extracted_for_other_targets(self):
        """A number in a copy instruction must not reach ffmpeg."""
        intent = parse_intent("revise copy: mention that she is 2 years old", GATE2)
        self.assertEqual(intent.params, {})


class TestOrdinals(unittest.TestCase):
    def test_ordinals_match_the_rendered_menu(self):
        for gate in (GATE1, GATE2):
            menu = gate_menu(gate, has_pending=True)
            for index, option in enumerate(menu, start=1):
                with self.subTest(gate=gate, index=index):
                    intent = parse_intent(str(index), gate, pending_instruction="make it red")
                    self.assertEqual(intent.action, option.action)
                    self.assertEqual(intent.target, option.target)

    def test_ordinal_carries_the_original_wording(self):
        intent = parse_intent("1", GATE1, pending_instruction="the hat should be red")
        self.assertEqual(intent.action, REVISE)
        self.assertEqual(intent.target, "image")
        self.assertEqual(intent.instruction, "the hat should be red")

    def test_ordinal_out_of_range(self):
        intent = parse_intent("99", GATE1, pending_instruction="x")
        self.assertEqual(intent.action, UNCLEAR)

    def test_multiple_ordinals_are_refused_not_guessed(self):
        intent = parse_intent("1+2", GATE1, pending_instruction="x")
        self.assertEqual(intent.action, UNCLEAR)
        self.assertFalse(intent.may_spend)

    def test_ordinal_without_pending_instruction_asks_for_one(self):
        intent = parse_intent("1", GATE1)
        self.assertEqual(intent.action, UNCLEAR)


class TestUnclear(unittest.TestCase):
    """Anything the grammar does not cover must be unclear and must not spend."""

    FREE_TEXT = [
        "the hat should be red",
        "make the camera push in slower",
        "the hat should be red and the camera slower, also try ghibli",
        "change the outfit to purple and add cat ears",
        "fix the audio overlay sync",
        "re-render the video animation",
        "change the caption and hashtags",
        "what do you think?",
        "hmm",
        "",
        "   ",
    ]

    def test_free_text_is_unclear_at_both_gates(self):
        for message in self.FREE_TEXT:
            for gate in (GATE1, GATE2):
                with self.subTest(message=message, gate=gate):
                    intent = parse_intent(message, gate)
                    self.assertEqual(intent.action, UNCLEAR)
                    self.assertFalse(intent.may_spend)
                    self.assertEqual(intent.decision(gate), UNCLEAR)

    def test_no_default_revision_target(self):
        """There is no fall-through target any more.

        The old gate 1 classifier ended with `return "revise_image"` and gate 2
        with `return "revise_copy"`, so an unrecognised message always paid for
        something.
        """
        for message in self.FREE_TEXT:
            self.assertEqual(parse_intent(message, GATE1).target, "")
            self.assertEqual(parse_intent(message, GATE2).target, "")

    def test_run_parameters_are_recognised_but_refused_for_now(self):
        intent = parse_intent("set style ghibli", GATE1)
        self.assertEqual(intent.action, UNCLEAR)
        self.assertIn("decide", intent.reason)


class TestSpendGate(unittest.TestCase):
    """The property the whole tier exists to guarantee."""

    def test_only_parsed_intents_may_spend(self):
        spending = {APPROVE, REVISE, RETRY}
        for message in TestUnclear.FREE_TEXT + ["not good", "no", "1+2", "99", "revise vibes: x"]:
            for gate in (GATE1, GATE2):
                intent = parse_intent(message, gate)
                if intent.may_spend:
                    self.fail(f"{message!r} at {gate} would spend: {intent}")
                self.assertNotIn(intent.action, spending)

    def test_intent_is_immutable(self):
        intent = parse_intent("approve", GATE1)
        with self.assertRaises(Exception):
            intent.action = REVISE  # type: ignore[misc]


class TestExtractRemixParameters(unittest.TestCase):
    def test_timings(self):
        p = extract_remix_parameters("audio should be inserted at 4s. Subtitles should also appear at 4s")
        self.assertEqual(p["audio_start_time"], 4.0)
        self.assertEqual(p["text_start_time"], 4.0)

        p = extract_remix_parameters("audio at 2.5s and subtitles at 2.5s until 5.5s")
        self.assertEqual(p["audio_start_time"], 2.5)
        self.assertEqual(p["text_start_time"], 2.5)
        self.assertEqual(p["text_end_time"], 5.5)

    def test_font_and_position(self):
        p = extract_remix_parameters("Make subtitle font size 64 and position top")
        self.assertEqual(p["font_size"], 64)
        self.assertEqual(p["position"], "top")

        p = extract_remix_parameters("place text in center with font color white")
        self.assertEqual(p["position"], "center")
        self.assertEqual(p["font_color"], "white")

        p = extract_remix_parameters("position at bottom with font size: 52")
        self.assertEqual(p["position"], "bottom")
        self.assertEqual(p["font_size"], 52)

    def test_colour_requires_the_word_colour(self):
        self.assertNotIn("font_color", extract_remix_parameters("she is wearing a red hat"))
        self.assertEqual(
            extract_remix_parameters("font colour red")["font_color"], "red"
        )

    def test_empty(self):
        self.assertEqual(extract_remix_parameters(""), {})


if __name__ == "__main__":
    unittest.main()
