import unittest
import os
import sys

# Inject root
sys.path.append(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
)

from core.voice.prompts.verbalizer_prompt import build_verbalizer_prompt


class TestVerbalizerPrompt(unittest.TestCase):
    """The prompt is the only place the no-compression contract is stated.

    Nothing downstream enforces it -- the pipeline cannot tell a faithful
    rewrite from one that dropped an item -- so these assertions stand in
    for a guarantee the code cannot make.
    """

    def setUp(self):
        self.prompt = build_verbalizer_prompt()

    def test_forbids_summarisation(self):
        self.assertIn("NOT summarising", self.prompt)
        self.assertIn("Preserve every fact", self.prompt)

    def test_names_the_phrases_that_stand_in_for_dropped_content(self):
        """Generic "don't summarise" is easy to comply with while still
        eliding. The concrete phrasings are what make it checkable."""
        self.assertIn("and others", self.prompt)
        self.assertIn("several more", self.prompt)

    def test_states_that_longer_output_is_correct(self):
        """Without this, a model asked to 'rewrite for speech' shortens by
        default and the loss is invisible to the listener."""
        self.assertIn("LONGER", self.prompt)

    def test_covers_each_structure_the_detector_looks_for(self):
        """`has_structure` triggers a model call on these; the prompt has
        to say what to do with each, or the call is wasted."""
        for topic in ("bullet points", "table row", "headings", "code blocks"):
            with self.subTest(topic=topic):
                self.assertIn(topic, self.prompt)

    def test_demands_plain_output(self):
        """The result goes straight to a synthesiser, which reads a stray
        asterisk or a preamble out loud."""
        self.assertIn("Output only the spoken text", self.prompt)
        self.assertIn("No preamble", self.prompt)

    def test_renders_the_rewriting_rules_as_a_bulleted_list(self):
        """Assembly is from lists; a formatting slip would silently run
        the rules together into one unreadable paragraph."""
        bullets = [
            line for line in self.prompt.splitlines() if line.startswith("- ")
        ]
        self.assertGreaterEqual(len(bullets), 8)
        for bullet in bullets:
            self.assertNotEqual(bullet.strip(), "-")

    def test_is_stable_across_calls(self):
        """Callers build it per turn; it must not vary between them."""
        self.assertEqual(build_verbalizer_prompt(), build_verbalizer_prompt())

    def test_has_no_unsubstituted_placeholders(self):
        self.assertNotIn("{", self.prompt)
        self.assertNotIn("}", self.prompt)


if __name__ == "__main__":
    unittest.main()
