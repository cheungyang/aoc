import os
import tempfile
import unittest

from graphs.content_creation.nodes.gates import process_gate1_node, process_gate2_node


class TestGateNodes(unittest.IsolatedAsyncioTestCase):

    async def test_process_gate1_node_records_an_explicit_revision(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = {
                "topic": "cat",
                "output_path": temp_dir,
                "latest_human_feedback": "revise image: Ayla wears a dinosaur costume",
            }
            res = await process_gate1_node(state)
            self.assertEqual(res["gate1_decision"], "revise_image")
            # The instruction is narrowed to the part after the colon, so the
            # generator is not also handed the word "revise".
            self.assertEqual(res["latest_human_feedback"], "Ayla wears a dinosaur costume")
            self.assertEqual(res["pending_notice"], "")

    async def test_process_gate1_node_approves_on_an_exact_token(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = {
                "topic": "cat",
                "output_path": temp_dir,
                "latest_human_feedback": "looks good",
            }
            res = await process_gate1_node(state)
            self.assertEqual(res["gate1_decision"], "approved")

    async def test_free_text_parks_at_the_gate_instead_of_regenerating(self):
        """The behaviour this whole change exists for.

        'Please have Ayla wear a dinosaur costume' used to classify as
        `revise_image` and pay for a new image immediately. It now produces a
        clarification card and no decision to act on.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            state = {
                "topic": "cat",
                "output_path": temp_dir,
                "latest_human_feedback": "Please have Ayla wear a dinosaur costume instead.",
            }
            res = await process_gate1_node(state)
            self.assertEqual(res["gate1_decision"], "unclear")
            self.assertIn("Clarification needed", res["pending_notice"])
            # The wording is preserved so answering "1" still works.
            self.assertEqual(
                res["pending_feedback"],
                "Please have Ayla wear a dinosaur costume instead.",
            )

    async def test_ordinal_reply_uses_the_preserved_wording(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = {
                "topic": "cat",
                "output_path": temp_dir,
                "pending_feedback": "Ayla wears a dinosaur costume",
                "pending_notice": "…previous clarification card…",
                "latest_human_feedback": "1",
            }
            res = await process_gate1_node(state)
            self.assertEqual(res["gate1_decision"], "revise_image")
            self.assertEqual(res["latest_human_feedback"], "Ayla wears a dinosaur costume")
            self.assertEqual(res["pending_notice"], "")
            self.assertEqual(res["pending_feedback"], "")

    async def test_a_newer_instruction_supersedes_the_pending_one(self):
        """Restating the request before answering the menu must take effect."""
        with tempfile.TemporaryDirectory() as temp_dir:
            res = await process_gate1_node({
                "topic": "cat",
                "output_path": temp_dir,
                "pending_feedback": "make the hat red",
                "latest_human_feedback": "actually, make the hat blue",
            })
            self.assertEqual(res["pending_feedback"], "actually, make the hat blue")

    async def test_a_menu_shaped_reply_does_not_overwrite_the_pending_one(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            res = await process_gate1_node({
                "topic": "cat",
                "output_path": temp_dir,
                "pending_feedback": "make the hat red",
                "latest_human_feedback": "99",
            })
            self.assertEqual(res["pending_feedback"], "make the hat red")

    async def test_process_gate2_node_records_copy_revision(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = {
                "topic": "cat",
                "output_path": temp_dir,
                "latest_human_feedback": "revise copy: mention the 粵語 pronunciation",
            }
            res = await process_gate2_node(state)
            self.assertEqual(res["gate2_decision"], "revise_copy")

    async def test_process_gate2_node_records_video_reanimation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = {
                "topic": "cat",
                "output_path": temp_dir,
                "latest_human_feedback": "revise video: smoother camera movement",
            }
            res = await process_gate2_node(state)
            self.assertEqual(res["gate2_decision"], "revise_video")

    async def test_process_gate2_node_carries_remix_parameters(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = {
                "topic": "cat",
                "output_path": temp_dir,
                "latest_human_feedback": "revise remix: audio at 4s and subtitles at 4s",
            }
            res = await process_gate2_node(state)
            self.assertEqual(res["gate2_decision"], "revise_remix")
            self.assertEqual(res["remix_params"]["audio_start_time"], 4.0)
            self.assertEqual(res["remix_params"]["text_start_time"], 4.0)

    async def test_abort_is_recorded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            res = await process_gate1_node({
                "topic": "cat", "output_path": temp_dir, "latest_human_feedback": "abort",
            })
            self.assertEqual(res["gate1_decision"], "abort")

    async def test_status_does_not_change_the_decision_to_a_revision(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            res = await process_gate2_node({
                "topic": "cat", "output_path": temp_dir, "latest_human_feedback": "status",
            })
            self.assertEqual(res["gate2_decision"], "status")
            self.assertIn("Status", res["pending_notice"])

    async def test_retry_clears_the_error_channel(self):
        """`errors.py` has always told the human to reply 'retry'.

        Until now every node short-circuited on the sticky `error_message` and
        nothing ever cleared it, so that instruction could not be followed.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            res = await process_gate1_node({
                "topic": "cat",
                "output_path": temp_dir,
                "error_message": "Imagen quota exceeded (429).",
                "quota_exceeded": True,
                "latest_human_feedback": "retry",
            })
            self.assertEqual(res["gate1_decision"], "retry")
            self.assertEqual(res["error_message"], "")
            self.assertFalse(res["quota_exceeded"])

    async def test_other_intents_defer_to_an_existing_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            res = await process_gate1_node({
                "topic": "cat",
                "output_path": temp_dir,
                "error_message": "Imagen quota exceeded (429).",
                "latest_human_feedback": "approve",
            })
            self.assertEqual(res, {})

    async def test_silence_preserves_the_recorded_decision(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            res = await process_gate1_node({
                "topic": "cat", "output_path": temp_dir, "latest_human_feedback": "",
            })
            self.assertEqual(res["gate1_decision"], "approved")


if __name__ == "__main__":
    unittest.main()
