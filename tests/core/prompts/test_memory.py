"""Memory prompts (core/prompts/memory.py): the context pruner's summary, the dream and compaction."""
import re
import unittest

from core.knowledge.memory import dream_ops
from core.prompts.memory import CompactionPrompt, ContextSummaryPrompt, DreamPrompt, parse_summary


class TestContextSummaryPrompt(unittest.TestCase):

    def test_the_playbook_state_and_task_blocks_hold_the_inputs(self):
        transcript = "User: Search for flights to Tokyo\nAssistant: Found 3 flights"
        prompt = ContextSummaryPrompt(
            transcript=transcript,
            previous_summary="User was looking for vacation spots.",
            max_summary_tokens=800
        ).render()

        self.assertIn("<playbook>", prompt)
        self.assertIn("</playbook>", prompt)
        self.assertIn("<current_state>", prompt)
        self.assertIn("</current_state>", prompt)
        self.assertIn("<assigned_task>", prompt)
        self.assertIn("</assigned_task>", prompt)

        # Check content presence
        self.assertIn("User was looking for vacation spots", prompt)
        self.assertIn("Search for flights to Tokyo", prompt)
        self.assertIn("800 tokens", prompt)
        self.assertIn("<payload>", prompt)
        self.assertIn("<summary>", prompt)

    def test_without_a_previous_summary_only_the_turns_are_in_the_state(self):
        prompt = ContextSummaryPrompt(transcript="  User: hi  ").render()
        self.assertIn("<current_state>\n--- CONVERSATION TURNS TO SUMMARIZE ---\nUser: hi\n</current_state>", prompt)
        self.assertNotIn("EXISTING CUMULATIVE SUMMARY", prompt)
        self.assertIn("{distilled_concise_summary_markdown}", prompt, "single braces are text for the model")

    def test_it_runs_as_the_context_pruner(self):
        self.assertEqual(ContextSummaryPrompt.caller, "context-pruner")


class TestParseSummary(unittest.TestCase):

    def test_the_summary_tag_then_the_payload_then_the_whole_reply(self):
        self.assertEqual(parse_summary("<payload><status>success</status><summary>\n S \n</summary></payload>"), "S")
        self.assertEqual(parse_summary("<payload> P </payload>"), "P")
        self.assertEqual(parse_summary("  just text  "), "just text")

    def test_an_error_or_an_empty_reply_is_no_summary(self):
        self.assertEqual(parse_summary("<errors>Rate limit exceeded</errors><summary>S</summary>"), "")
        self.assertEqual(parse_summary("<errors>None</errors><summary>S</summary>"), "S")
        for empty in ("", None, "   "):
            with self.subTest(reply=empty):
                self.assertEqual(parse_summary(empty), "")


def _shown_reply(prompt) -> dream_ops.Reply:
    """The reply format a prompt shows, read as if the model had sent it verbatim."""
    shown = re.search(r"<dream_response>.*</dream_response>", prompt.render(), re.DOTALL).group(0)
    return dream_ops.parse_reply(shown.replace("Dreamed or No new memories", dream_ops.STATUS_DREAMED))


class TestDreamPrompts(unittest.TestCase):
    """The reply format a dream is shown is the one dream_ops.parse_reply reads."""

    def test_the_dream_prompt_inlines_its_rules_and_input(self):
        prompt = DreamPrompt(rules="RULES", dream_input="<dream_input>X</dream_input>").render()
        self.assertTrue(prompt.startswith("Dream: consolidate your memory logs into memory entries"))
        self.assertIn("<dream_rules>\nRULES\n</dream_rules>", prompt)
        self.assertIn("<dream_input>X</dream_input>", prompt)

    def test_the_ops_shown_are_the_ops_the_parser_reads(self):
        for prompt, kinds in ((DreamPrompt(rules="", dream_input=""), ["add", "confirm", "update", "retire"]),
                              (CompactionPrompt(compaction_input=""), ["merge", "update", "retire"])):
            with self.subTest(prompt=type(prompt).__name__):
                reply = _shown_reply(prompt)
                self.assertIsNone(reply.error)
                self.assertEqual([op.kind for op in reply.ops], kinds)

    def test_a_copied_example_names_no_real_entry(self):
        """Real ids are numbered e1, e2, … (dream_ops._number): an example id like "e9" would act on a real entry."""
        for prompt in (DreamPrompt(rules="", dream_input=""), CompactionPrompt(compaction_input="")):
            with self.subTest(prompt=type(prompt).__name__):
                ids = [i for op in _shown_reply(prompt).ops for i in (op.id, *op.ids) if i]
                self.assertTrue(ids)
                self.assertEqual([i for i in ids if re.fullmatch(r"e\d+", i)], [])

    def test_a_copied_example_text_is_rejected(self):
        """Every op text the formats show is refused as a placeholder (dream_ops.EXAMPLE_TEXTS follows the md)."""
        for prompt in (DreamPrompt(rules="", dream_input=""), CompactionPrompt(compaction_input="")):
            with self.subTest(prompt=type(prompt).__name__):
                texts = [op.text for op in _shown_reply(prompt).ops if op.text]
                self.assertTrue(texts)
                self.assertEqual([t for t in texts if dream_ops._valid_text(t)], [])


if __name__ == "__main__":
    unittest.main()
