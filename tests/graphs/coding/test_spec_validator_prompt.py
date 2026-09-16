"""The spec validator is the gate that stops the pipeline before it wastes a
worker on an unbuildable spec.

Goldfish 0 has no memory by design: it must judge the spec text alone, so the
prompt has to carry the entire spec verbatim and state the zero-context rule
that makes an implicit requirement a failure rather than a detail to infer.
The other half of the contract is the <spec_validation_result> shape, because
tools.spec_validator hands the raw model output straight on for
parse_spec_validation_xml to read; a tag the parser does not recognise degrades
to the default FAIL and blocks planning on a spec that was fine.
"""
import re
import unittest

from graphs.coding.prompts.spec_validator_prompt import build_spec_validator_prompt
from graphs.coding.utils.xml_parsers import parse_spec_validation_xml


def fill_tag(test, template, tag, value):
    """Puts `value` inside `<tag>` in a copy of the prompt's own example block.

    The placeholder text is read out of the template rather than written down
    here. Hardcoding it is how this file rotted once already: the fills said
    `.replace("PASS | FAIL", "FAIL")` long after the prompt had moved to
    `PASS_OR_FAIL`, so every fill was a no-op and the "round trip" tests were
    only ever parsing the untouched placeholder — i.e. the parser's defaults.
    A no-op is impossible now: a missing tag fails the assertion below.
    """
    match = re.search(rf"<{tag}>(.*?)</{tag}>", template, re.DOTALL)
    test.assertIsNotNone(match, f"prompt template no longer offers a <{tag}> field")
    return template[: match.start(1)] + value + template[match.end(1) :]


class SpecValidatorPromptInputTestCase(unittest.TestCase):
    def test_spec_text_reaches_the_prompt(self):
        """tools.spec_validator reads the file and passes its whole contents as
        the single positional argument."""
        prompt = build_spec_validator_prompt("SPEC_MARKER: build a widget")
        self.assertIn("SPEC_MARKER: build a widget", prompt)

    def test_spec_text_is_wrapped_in_the_spec_content_block(self):
        prompt = build_spec_validator_prompt("SPEC_MARKER")
        block = re.search(r"<spec_content>(.*?)</spec_content>", prompt, re.DOTALL)
        self.assertIsNotNone(block)
        self.assertIn("SPEC_MARKER", block.group(1))

    def test_multiline_spec_is_embedded_verbatim(self):
        """Specs are Markdown documents. Collapsing or truncating them would
        hide exactly the schema and path sections the checklist asks about."""
        spec = (
            "# Feature\n\n"
            "## Target files\n- core/widget.py\n\n"
            "## Acceptance Criteria\nGiven X, When Y, Then Z\n"
        )
        prompt = build_spec_validator_prompt(spec)
        self.assertIn(spec, prompt)

    def test_empty_spec_still_produces_a_well_formed_prompt(self):
        """An unreadable or empty spec must still reach the model as a spec to
        fail, not crash the tool before it can report anything."""
        prompt = build_spec_validator_prompt("")
        self.assertIn("<spec_content>", prompt)
        self.assertIn("</spec_content>", prompt)
        self.assertIn("<spec_validation_result>", prompt)


class SpecValidatorPromptRulesTestCase(unittest.TestCase):
    def test_zero_context_failure_stance_is_stated(self):
        """Without this line the model fills gaps with plausible assumptions,
        which is precisely the failure the gate exists to catch."""
        prompt = build_spec_validator_prompt("spec")
        self.assertIn("NO prior conversational memory", prompt)
        self.assertIn("MUST FAIL", prompt)

    def test_all_four_checklist_items_are_present(self):
        prompt = build_spec_validator_prompt("spec")
        self.assertIn("target file paths", prompt)
        self.assertIn("schemas/types", prompt)
        self.assertIn("Given-When-Then", prompt)
        self.assertIn("verification command", prompt)


class SpecValidatorPromptOutputContractTestCase(unittest.TestCase):
    def test_prompt_asks_for_the_spec_validation_result_block(self):
        prompt = build_spec_validator_prompt("spec")
        self.assertIn("<output_format>", prompt)
        self.assertIn("<spec_validation_result>", prompt)
        self.assertIn("</spec_validation_result>", prompt)

    def test_both_verdict_values_the_parser_understands_are_offered(self):
        prompt = build_spec_validator_prompt("spec")
        self.assertIn("PASS", prompt)
        self.assertIn("FAIL", prompt)

    def test_a_filled_in_template_round_trips_through_the_real_parser(self):
        """Every advertised tag must be one parse_spec_validation_xml reads.

        The error path in tools.spec_validator hand-writes this same XML, so a
        rename here without a rename there would leave the failure branch and
        the success branch speaking different dialects.
        """
        prompt = build_spec_validator_prompt("spec")
        template = re.search(
            r"<spec_validation_result>.*?</spec_validation_result>", prompt, re.DOTALL
        )
        self.assertIsNotNone(template, "prompt no longer contains a result example")

        filled = template.group(0)
        filled = fill_tag(self, filled, "verdict", "FAIL")
        filled = fill_tag(self, filled, "unambiguous", "false")
        filled = fill_tag(self, filled, "item", "No verification command given")
        filled = fill_tag(self, filled, "summary", "Spec relies on unstated context")
        self.assertNotEqual(filled, template.group(0), "the template was never filled in")

        parsed = parse_spec_validation_xml(filled)
        self.assertEqual(parsed["verdict"], "FAIL")
        self.assertFalse(parsed["passed"])
        self.assertFalse(parsed["unambiguous"])
        self.assertEqual(parsed["missing_assumptions"], ["No verification command given"])
        self.assertEqual(parsed["summary"], "Spec relies on unstated context")

    def test_a_passing_result_round_trips_with_unambiguous_true(self):
        """The PASS half of the contract, driven through the shipped template.

        A spec that clears the gate has to be expressible in the shape the model
        is actually shown; if only the FAIL path round-tripped, a template that
        could never yield a PASS would look healthy.
        """
        prompt = build_spec_validator_prompt("spec")
        template = re.search(
            r"<spec_validation_result>.*?</spec_validation_result>", prompt, re.DOTALL
        ).group(0)
        filled = fill_tag(self, template, "verdict", "PASS")
        filled = fill_tag(self, filled, "unambiguous", "true")
        filled = fill_tag(self, filled, "summary", "Spec is self-contained")
        self.assertNotEqual(filled, template, "the template was never filled in")

        parsed = parse_spec_validation_xml(filled)
        self.assertEqual(parsed["verdict"], "PASS")
        self.assertTrue(parsed["passed"])
        self.assertTrue(parsed["unambiguous"])
        self.assertEqual(parsed["summary"], "Spec is self-contained")

    def test_an_unfilled_template_verdict_does_not_pass(self):
        """The laziest possible output must not be the most permissive one.

        The parser exact-matches the verdict, so a model that echoes the
        placeholder it was shown reads as FAIL. This previously returned PASS:
        `"PASS" if "PASS" in v` matched the placeholder's own text, so the
        sloppiest response cleared the check. Renaming the placeholder to
        `PASS_OR_FAIL` removes the temptation as well.
        """
        prompt = build_spec_validator_prompt("spec")
        example = re.search(
            r"<spec_validation_result>.*?</spec_validation_result>", prompt, re.DOTALL
        ).group(0)
        parsed = parse_spec_validation_xml(example)
        self.assertEqual(parsed["verdict"], "FAIL")
        self.assertFalse(parsed["passed"])
        self.assertFalse(parsed["unambiguous"])

    def test_a_verdict_that_merely_contains_the_word_pass_does_not_pass(self):
        """Guards the exact-match rule directly, independent of the template."""
        for hedge in ("PASS | FAIL", "probably PASS", "PASS with reservations", "NOT A PASS"):
            with self.subTest(hedge=hedge):
                parsed = parse_spec_validation_xml(f"<verdict>{hedge}</verdict>")
                self.assertEqual(parsed["verdict"], "FAIL")
                self.assertFalse(parsed["passed"])


if __name__ == "__main__":
    unittest.main()
