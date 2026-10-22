"""Prompt templates stored as files (core/prompts/), filled in by name."""
import unittest
from unittest.mock import patch

from core import prompts
from core.prompts import load_prompt, placeholders, render_prompt


class TestRenderPrompt(unittest.TestCase):

    def _with_template(self, text):
        return patch.object(prompts, "load_prompt", return_value=text)

    def test_placeholders_are_filled_and_other_braces_are_text(self):
        with self._with_template('Hi {{ name }}. JSON stays: {"a": 1}. Price: $5. {{name}} again.'):
            self.assertEqual(render_prompt("t", name="Daisy"), 'Hi Daisy. JSON stays: {"a": 1}. Price: $5. Daisy again.')

    def test_a_missing_value_fails_loudly(self):
        with self._with_template("{{name}} posted in #{{channel}}"), self.assertRaises(KeyError) as raised:
            render_prompt("t", name="Daisy")
        self.assertIn("channel", str(raised.exception))

    def test_values_are_never_scanned_for_placeholders(self):
        with self._with_template("<post>{{post}}</post>"):
            self.assertEqual(render_prompt("t", post="see {{name}}"), "<post>see {{name}}</post>")

    def test_a_placeholder_may_be_called_template(self):
        with self._with_template("{{template}}"):
            self.assertEqual(render_prompt("t", template="x"), "x")


class TestTheSpeechWriterTemplate(unittest.TestCase):

    def test_it_lives_in_core_prompts_with_the_placeholders_its_builder_fills(self):
        self.assertIn("<assigned_task>", load_prompt("speech_writer"))
        self.assertEqual(placeholders("speech_writer"), {"name", "channel", "post", "speech_rules", "question_rule"})


if __name__ == "__main__":
    unittest.main()
