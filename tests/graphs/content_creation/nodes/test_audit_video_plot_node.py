import unittest
from unittest.mock import AsyncMock, patch, MagicMock
import tempfile
import os
import json
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))

from graphs.content_creation.nodes.ideation import audit_plot_task
from graphs.content_creation.schemas import PlotAudit

class TestAuditVideoPlotNode(unittest.IsolatedAsyncioTestCase):
    async def test_audit_approves_from_reinforced_xml_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_state = {
                "topic": "fish",
                "project_path": temp_dir,
                "output_path": temp_dir,
                "qc_playbook_path": os.path.join(temp_dir, "03_QC_Playbook.md")
            }

            mock_response = (
                "<payload>\n"
                "<status>success</status>\n"
                "<error></error>\n"
                "<verdict>APPROVED</verdict>\n"
                "<rejection_target>none</rejection_target>\n"
                "<feedback>All brand QC checks passed.</feedback>\n"
                "</payload>"
            )

            with patch("tools.agent_call.agent_call") as mock_agent_call:
                mock_agent_call.ainvoke = AsyncMock(return_value=mock_response)
                result = await audit_plot_task(test_state)

                mock_agent_call.ainvoke.assert_called_once()
                call_args = mock_agent_call.ainvoke.call_args[0][0]
                self.assertEqual(call_args["agent_id"], "graph-worker")
                call_prompt = call_args["prompt"]
                self.assertIn("<playbook>", call_prompt)
                self.assertIn("<current_state>", call_prompt)
                self.assertIn("<assigned_task>", call_prompt)
                self.assertNotIn("<markdown_report>", call_prompt)
                self.assertIn("video_plot_qc_passed", result)
                self.assertTrue(result["video_plot_qc_passed"])
                self.assertEqual(result["qc_rejection_target"], "none")
                self.assertEqual(result["video_plot_feedback"], "All brand QC checks passed.")

    async def test_audit_rejects_from_reinforced_xml_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_state = {
                "topic": "cat",
                "project_path": temp_dir,
                "output_path": temp_dir,
            }

            mock_response = (
                "<payload>\n"
                "<status>success</status>\n"
                "<error></error>\n"
                "<verdict>REJECTED</verdict>\n"
                "<rejection_target>image</rejection_target>\n"
                "<feedback>Cat base image costume does not match character sheet.</feedback>\n"
                "</payload>"
            )

            with patch("tools.agent_call.agent_call") as mock_agent_call:
                mock_agent_call.ainvoke = AsyncMock(return_value=mock_response)
                result = await audit_plot_task(test_state)

                self.assertFalse(result["video_plot_qc_passed"])
                self.assertEqual(result["qc_rejection_target"], "image")
                self.assertEqual(result["video_plot_feedback"], "Cat base image costume does not match character sheet.")

    async def test_audit_approves_from_json_payload_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_state = {
                "topic": "fish",
                "project_path": temp_dir,
                "output_path": temp_dir
            }

            json_resp = json.dumps({
                "is_approved": True,
                "revision_notes": "All brand checks passed flawlessly.",
                "rejection_target": "none"
            })
            mock_response = f"<payload>{json_resp}</payload>"

            with patch("tools.agent_call.agent_call") as mock_agent_call:
                mock_agent_call.ainvoke = AsyncMock(return_value=mock_response)
                result = await audit_plot_task(test_state)

                self.assertTrue(result["video_plot_qc_passed"])
                self.assertEqual(result["video_plot_feedback"], "All brand checks passed flawlessly.")
                self.assertEqual(result["qc_rejection_target"], "none")

    async def test_audit_rejects_both_targets_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_state = {
                "topic": "cat",
                "project_dir": temp_dir,
                "output_dir": temp_dir
            }

            mock_response = "<payload>VERDICT: REJECTED TARGET: BOTH\nImage style wrong and plot prompt too long.</payload>"

            with patch("tools.agent_call.agent_call") as mock_agent_call:
                mock_agent_call.ainvoke = AsyncMock(return_value=mock_response)
                result = await audit_plot_task(test_state)

                self.assertFalse(result["video_plot_qc_passed"])
                self.assertEqual(result["qc_rejection_target"], "both")

    async def test_audit_exception_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_state = {
                "topic": "cat",
                "project_dir": temp_dir,
                "output_dir": temp_dir
            }

            with patch("tools.agent_call.agent_call") as mock_agent_call:
                mock_agent_call.ainvoke = AsyncMock(side_effect=RuntimeError("Timeout"))
                result = await audit_plot_task(test_state)

                self.assertTrue(result["video_plot_qc_passed"])
                self.assertIn("Auto-approved", result["video_plot_feedback"])

if __name__ == "__main__":
    unittest.main()
