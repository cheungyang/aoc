"""The critic prompt is the only thing that makes the audit an audit.

_run_audit hands the model a spec and a diff and expects a verdict back. Two
things in this prompt carry the whole behaviour: the zero-memory framing that
stops the critic from rationalising shortcuts it "already discussed", and the
<critic_verdict> shape that parse_critic_verdict_xml is written against. Drop
the diff or the spec and the critic reviews thin air; rename a tag and the
parser falls back to REJECT with no evidence attached, which routes a healthy
task back into implement forever.
"""
import re
import unittest

from graphs.coding.prompts.critic_prompt import build_critic_prompt
from graphs.coding.utils.xml_parsers import parse_critic_verdict_xml


class CriticPromptInputsTestCase(unittest.TestCase):
    def test_spec_and_diff_both_reach_the_prompt(self):
        """audit._run_audit passes exactly these two values; either one going
        missing leaves the model auditing half the evidence."""
        prompt = build_critic_prompt(
            spec_text="SPEC_MARKER: the widget must persist to sqlite",
            git_diff_text="DIFF_MARKER\n+def widget(): return 'dummy'",
        )
        self.assertIn("SPEC_MARKER: the widget must persist to sqlite", prompt)
        self.assertIn("DIFF_MARKER", prompt)
        self.assertIn("+def widget(): return 'dummy'", prompt)

    def test_spec_and_diff_land_in_their_own_labelled_blocks(self):
        prompt = build_critic_prompt(spec_text="SPEC_MARKER", git_diff_text="DIFF_MARKER")
        spec_block = re.search(r"<master_spec>(.*?)</master_spec>", prompt, re.DOTALL)
        diff_block = re.search(r"<git_diff>(.*?)</git_diff>", prompt, re.DOTALL)
        self.assertIsNotNone(spec_block)
        self.assertIsNotNone(diff_block)
        self.assertIn("SPEC_MARKER", spec_block.group(1))
        self.assertNotIn("DIFF_MARKER", spec_block.group(1))
        self.assertIn("DIFF_MARKER", diff_block.group(1))
        self.assertNotIn("SPEC_MARKER", diff_block.group(1))

    def test_multiline_diff_survives_intact(self):
        """A diff truncated to its first line hides the hunk the critic is
        supposed to judge."""
        diff = (
            "diff --git a/core/a.py b/core/a.py\n"
            "@@ -1,3 +1,4 @@\n"
            "-    return compute(x)\n"
            "+    return 42\n"
        )
        prompt = build_critic_prompt(spec_text="spec", git_diff_text=diff)
        self.assertIn(diff, prompt)

    def test_placeholder_diff_from_the_call_site_is_embedded_as_given(self):
        """audit substitutes "(No git diff changes)" for an empty diff rather
        than skipping the call, so the prompt must carry that sentinel through
        instead of swallowing a falsy value."""
        prompt = build_critic_prompt(spec_text="spec", git_diff_text="(No git diff changes)")
        self.assertIn("(No git diff changes)", prompt)

    def test_empty_inputs_still_produce_a_well_formed_prompt(self):
        prompt = build_critic_prompt(spec_text="", git_diff_text="")
        self.assertIn("<master_spec>", prompt)
        self.assertIn("</git_diff>", prompt)
        self.assertIn("<critic_verdict>", prompt)


class CriticPromptAuditRulesTestCase(unittest.TestCase):
    def test_zero_memory_rejection_stance_is_stated(self):
        """The critic exists to fail closed on shortcuts. Softening this line
        turns the audit into a rubber stamp without changing any code path."""
        prompt = build_critic_prompt(spec_text="spec", git_diff_text="diff")
        self.assertIn("Zero prior memory", prompt)
        self.assertIn("MUST REJECT", prompt)

    def test_all_four_anti_pattern_rules_are_listed(self):
        """These four names are echoed back in <rule> and rendered into the
        worker's retry feedback by audit._run_audit, so they are a shared
        vocabulary and not free text."""
        prompt = build_critic_prompt(spec_text="spec", git_diff_text="diff")
        for rule in ("Fake It Trap", "Happy Path Bias", "Silent Failure", "Bloated Files"):
            self.assertIn(rule, prompt)


class CriticPromptOutputContractTestCase(unittest.TestCase):
    def test_prompt_asks_for_the_critic_verdict_block(self):
        prompt = build_critic_prompt(spec_text="spec", git_diff_text="diff")
        self.assertIn("<output_format>", prompt)
        self.assertIn("<critic_verdict>", prompt)
        self.assertIn("</critic_verdict>", prompt)

    def test_both_verdict_values_the_parser_understands_are_offered(self):
        prompt = build_critic_prompt(spec_text="spec", git_diff_text="diff")
        self.assertIn("APPROVE", prompt)
        self.assertIn("REJECT", prompt)

    def test_the_example_verdict_round_trips_through_the_real_parser(self):
        """Every field the prompt advertises must be a field the parser reads.

        A tag renamed here alone would strip the evidence out of the feedback
        audit hands back to the coder, leaving "Remediation:" with nothing above
        it and the worker with nothing to fix.
        """
        prompt = build_critic_prompt(spec_text="spec", git_diff_text="diff")
        example = re.search(r"<critic_verdict>.*?</critic_verdict>", prompt, re.DOTALL)
        self.assertIsNotNone(example, "prompt no longer contains a critic_verdict example")

        parsed = parse_critic_verdict_xml(example.group(0))
        self.assertEqual(len(parsed["anti_patterns_detected"]), 1)
        pattern = parsed["anti_patterns_detected"][0]
        self.assertIn("Fake It Trap", pattern["rule"])
        self.assertEqual(pattern["file"], "path/to/file")
        self.assertEqual(pattern["line_numbers"], "12-18")
        self.assertEqual(pattern["evidence"], "Hardcoded return 'dummy'")
        self.assertEqual(parsed["feedback_for_worker"], "Specific remediation instructions")

    def test_a_verdict_shaped_like_the_prompt_template_parses_as_a_rejection(self):
        """End-to-end sanity on the shape the model is told to emit: a filled-in
        copy of the template must produce a REJECT the pipeline can act on."""
        prompt = build_critic_prompt(spec_text="spec", git_diff_text="diff")
        template = re.search(r"<critic_verdict>.*?</critic_verdict>", prompt, re.DOTALL).group(0)
        filled = (
            template.replace("APPROVE | REJECT", "REJECT")
            .replace("Fake It Trap | Happy Path Bias | Silent Failure | Bloated Files", "Silent Failure")
            .replace("path/to/file", "core/a.py")
        )
        parsed = parse_critic_verdict_xml(filled)
        self.assertEqual(parsed["verdict"], "REJECT")
        self.assertFalse(parsed["passed"])
        self.assertEqual(parsed["anti_patterns_detected"][0]["rule"], "Silent Failure")
        self.assertEqual(parsed["anti_patterns_detected"][0]["file"], "core/a.py")


    def test_an_unfilled_template_verdict_parses_as_APPROVE_today(self):
        """Documents current behaviour, not desired behaviour.

        parse_critic_verdict_xml decides with `"APPROVE" if "APPROVE" in v`, so
        a model that echoes the literal placeholder "APPROVE | REJECT" is read
        as an approval. The audit is advisory and already fails open on errors,
        so this is consistent rather than dangerous here — but it is pinned so
        that changing it is a deliberate act with a red test.
        """
        prompt = build_critic_prompt(spec_text="spec", git_diff_text="diff")
        example = re.search(r"<critic_verdict>.*?</critic_verdict>", prompt, re.DOTALL).group(0)
        parsed = parse_critic_verdict_xml(example)
        self.assertEqual(parsed["verdict"], "APPROVE")
        self.assertTrue(parsed["passed"])


if __name__ == "__main__":
    unittest.main()
