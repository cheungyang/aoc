"""The coder prompt is the retry mechanism, not just a wrapper for the task.

Everything the worker learns between attempt one and attempt two arrives through
this string: the test traceback, the critic's anti-pattern list, the human's
review comments. Two failure modes matter. A block that leaks into a first
attempt tells the model to fix a failure that never happened, and it will
happily invent one. A block that goes missing on a retry re-runs the identical
attempt and burns the implement budget three times over.

The other contract is the boundary: an empty allowed-files list must widen to
the whole workspace rather than emit an empty list the model reads as
"touch nothing", and the <worker_handoff> shape asked for here must be the one
parse_worker_handoff_xml actually accepts.
"""
import re
import unittest

from graphs.coding.prompts.coder_prompt import build_coder_prompt
from graphs.coding.utils.xml_parsers import parse_worker_handoff_xml

TRACEBACK_TAG = "<test_failure_traceback>"
CRITIC_TAG = "<critic_anti_pattern_feedback>"
HUMAN_TAG = "<human_reviewer_feedback>"


def _build(**overrides):
    """Builds a prompt with the required arguments filled in."""
    kwargs = {
        "workspace_path": "/tmp/ws/goldfish",
        "task_id": "T-042",
        "spec_path": "specs/master_spec.md",
        "allowed_files": ["core/a.py", "core/b.py"],
        "acceptance_criteria": "Given a spec, When validated, Then it passes.",
        "verification_command": "pytest tests/core/test_a.py -q",
    }
    kwargs.update(overrides)
    return build_coder_prompt(**kwargs)


class CoderPromptTaskContextTestCase(unittest.TestCase):
    def test_every_caller_supplied_value_reaches_the_prompt(self):
        """These are the exact fields implement_node pulls off the task record.

        A parameter that is accepted but never interpolated is invisible at the
        call site and silently drops task context on the floor.
        """
        prompt = _build()
        for value in (
            "/tmp/ws/goldfish",
            "T-042",
            "specs/master_spec.md",
            "core/a.py",
            "core/b.py",
            "Given a spec, When validated, Then it passes.",
            "pytest tests/core/test_a.py -q",
        ):
            self.assertIn(value, prompt)

    def test_allowed_files_are_rendered_as_one_bullet_each(self):
        prompt = _build(allowed_files=["pkg/one.py", "pkg/two.py"])
        self.assertIn("- pkg/one.py\n- pkg/two.py", prompt)

    def test_empty_allowed_files_widens_the_boundary_to_the_whole_workspace(self):
        """An empty list is "no restriction", not "no files".

        Rendering an empty Allowed Files section would read to the model as a
        prohibition on touching anything, and the worker would return with no
        modified files at all.
        """
        prompt = _build(allowed_files=[])
        self.assertIn("- (All files within workspace)", prompt)

    def test_none_allowed_files_widens_the_boundary_to_the_whole_workspace(self):
        """implement_node defaults to `.get("allowed_files", [])`, but a manifest
        entry with an explicit null must not raise or render "None"."""
        prompt = _build(allowed_files=None)
        self.assertIn("- (All files within workspace)", prompt)
        self.assertNotIn("- None", prompt)

    def test_spec_content_is_wrapped_in_its_own_block_when_supplied(self):
        prompt = _build(spec_content="# Master Spec\nSchema: {id: str}")
        self.assertIn("<spec_content>", prompt)
        self.assertIn("</spec_content>", prompt)
        self.assertIn("Schema: {id: str}", prompt)

    def test_spec_content_block_is_absent_when_the_spec_file_could_not_be_read(self):
        """_read_spec returns "" for a missing spec; an empty <spec_content>
        block would tell the model the spec itself is empty."""
        self.assertNotIn("<spec_content>", _build(spec_content=""))
        self.assertNotIn("<spec_content>", _build(spec_content=None))
        self.assertNotIn("<spec_content>", _build())


class CoderPromptRetryDeltaTestCase(unittest.TestCase):
    def test_first_attempt_carries_no_retry_blocks(self):
        """The whole point of the delta: with nothing to retry, nothing is said."""
        prompt = _build()
        self.assertNotIn(TRACEBACK_TAG, prompt)
        self.assertNotIn(CRITIC_TAG, prompt)
        self.assertNotIn(HUMAN_TAG, prompt)

    def test_test_stderr_produces_only_the_traceback_block(self):
        prompt = _build(test_stderr="E   AssertionError: expected 3, got 4")
        self.assertIn(TRACEBACK_TAG, prompt)
        self.assertIn("</test_failure_traceback>", prompt)
        self.assertIn("E   AssertionError: expected 3, got 4", prompt)
        self.assertNotIn(CRITIC_TAG, prompt)
        self.assertNotIn(HUMAN_TAG, prompt)

    def test_critic_feedback_produces_only_the_anti_pattern_block(self):
        prompt = _build(critic_feedback="- [Fake It Trap] core/a.py (L4): returns 'dummy'")
        self.assertIn(CRITIC_TAG, prompt)
        self.assertIn("</critic_anti_pattern_feedback>", prompt)
        self.assertIn("- [Fake It Trap] core/a.py (L4): returns 'dummy'", prompt)
        self.assertNotIn(TRACEBACK_TAG, prompt)
        self.assertNotIn(HUMAN_TAG, prompt)

    def test_human_feedback_produces_only_the_reviewer_block(self):
        prompt = _build(human_feedback="GitHub PR review comments:\nRename the flag.")
        self.assertIn(HUMAN_TAG, prompt)
        self.assertIn("</human_reviewer_feedback>", prompt)
        self.assertIn("Rename the flag.", prompt)
        self.assertNotIn(TRACEBACK_TAG, prompt)
        self.assertNotIn(CRITIC_TAG, prompt)

    def test_all_three_retry_blocks_coexist_and_stay_separated(self):
        """A tick can fail tests, get rejected by the critic and collect PR
        comments before the next implement. All three must survive, and each
        must stay a closed block so the model can tell them apart."""
        prompt = _build(
            test_stderr="TRACEBACK_MARKER",
            critic_feedback="CRITIC_MARKER",
            human_feedback="HUMAN_MARKER",
        )
        for marker in ("TRACEBACK_MARKER", "CRITIC_MARKER", "HUMAN_MARKER"):
            self.assertIn(marker, prompt)

        stderr_end = prompt.index("</test_failure_traceback>")
        critic_start = prompt.index(CRITIC_TAG)
        critic_end = prompt.index("</critic_anti_pattern_feedback>")
        human_start = prompt.index(HUMAN_TAG)

        self.assertLess(stderr_end, critic_start)
        self.assertLess(critic_end, human_start)
        # Blocks are joined by a blank line, never concatenated head-to-tail.
        self.assertIn("</test_failure_traceback>\n\n" + CRITIC_TAG, prompt)
        self.assertIn("</critic_anti_pattern_feedback>\n\n" + HUMAN_TAG, prompt)

    def test_empty_string_retry_inputs_are_treated_as_absent(self):
        """implement_node clears these to "" once consumed, so "" is the normal
        steady state. A truthiness bug here would emit a block whose header
        promises a traceback and whose body is blank."""
        prompt = _build(test_stderr="", critic_feedback="", human_feedback="")
        self.assertNotIn(TRACEBACK_TAG, prompt)
        self.assertNotIn(CRITIC_TAG, prompt)
        self.assertNotIn(HUMAN_TAG, prompt)
        self.assertNotIn("The previous test run failed", prompt)
        self.assertNotIn("Independent QA audit detected", prompt)
        self.assertNotIn("The human reviewer requested", prompt)

    def test_none_retry_inputs_are_treated_as_absent(self):
        """_clean() returns None rather than "" when there is no stderr."""
        prompt = _build(test_stderr=None, critic_feedback=None, human_feedback=None)
        self.assertNotIn(TRACEBACK_TAG, prompt)
        self.assertNotIn(CRITIC_TAG, prompt)
        self.assertNotIn(HUMAN_TAG, prompt)
        self.assertNotIn("None", prompt)

    def test_one_populated_retry_input_does_not_drag_in_its_empty_siblings(self):
        prompt = _build(test_stderr="boom", critic_feedback="", human_feedback=None)
        self.assertIn(TRACEBACK_TAG, prompt)
        self.assertNotIn(CRITIC_TAG, prompt)
        self.assertNotIn(HUMAN_TAG, prompt)

    def test_multiline_traceback_is_embedded_verbatim(self):
        """Tracebacks are the highest-signal retry input; losing interior lines
        loses the frame that names the failing file."""
        stderr = "Traceback (most recent call last):\n  File \"a.py\", line 3\nValueError: nope"
        prompt = _build(test_stderr=stderr)
        self.assertIn(stderr, prompt)


class CoderPromptOutputContractTestCase(unittest.TestCase):
    def test_prompt_asks_for_the_worker_handoff_block(self):
        prompt = _build()
        self.assertIn("<output_format>", prompt)
        self.assertIn("<worker_handoff>", prompt)
        self.assertIn("</worker_handoff>", prompt)

    def test_the_example_handoff_round_trips_through_the_real_parser(self):
        """The prompt is the only spec the model gets for its output shape.

        If a tag is renamed here without renaming it in parse_worker_handoff_xml,
        every implement summary silently becomes empty and the manifest records
        a task that looks like it did nothing.
        """
        prompt = _build()
        example = re.search(r"<worker_handoff>.*?</worker_handoff>", prompt, re.DOTALL)
        self.assertIsNotNone(example, "prompt no longer contains a worker_handoff example")

        parsed = parse_worker_handoff_xml(example.group(0))
        self.assertEqual(parsed["status"], "READY_FOR_TEST")
        self.assertEqual(parsed["modified_files"], ["relative/path/to/modified_file"])
        self.assertEqual(parsed["implementation_summary"], "Concise description of changes made")

    def test_execution_boundary_names_the_workspace(self):
        """The worker has filesystem tools; the only thing keeping it inside the
        worktree is this sentence."""
        prompt = _build(workspace_path="/tmp/ws/isolated")
        self.assertIn("Execution Boundary: STRICTLY inside workspace /tmp/ws/isolated", prompt)


if __name__ == "__main__":
    unittest.main()
