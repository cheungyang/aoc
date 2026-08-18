import unittest
from unittest.mock import AsyncMock, patch, MagicMock
import tempfile
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))

from graphs.content_creation.nodes.ideation import audit_plot_task
from graphs.content_creation.schemas import PlotAudit

class TestAuditVideoPlotNode(unittest.IsolatedAsyncioTestCase):
    async def test_audit_approves_when_playbook_satisfied(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_state = {
                "topic": "fish",
                "project_dir": temp_dir,
                "output_dir": temp_dir,
                "qc_playbook_path": os.path.join(temp_dir, "03_QC_Playbook.md")
            }

            mock_response = "<payload>VERDICT: APPROVED\nEverything is great.</payload>"

            with patch("tools.agent_call.agent_call") as mock_agent_call:
                mock_agent_call.ainvoke = AsyncMock(return_value=mock_response)

                result = await audit_plot_task(test_state)

                self.assertIn("video_plot_qc_passed", result)
                self.assertTrue(result["video_plot_qc_passed"])
                self.assertIn("Everything is great.", result["video_plot_feedback"])

    async def test_audit_rejects_when_image_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_state = {
                "topic": "cat",
                "project_dir": temp_dir,
                "output_dir": temp_dir,
            }

            with open(os.path.join(temp_dir, "cat_image.jpg"), "wb") as f:
                f.write(b"IMAGE_BYTES")

            mock_response = "<payload>VERDICT: REJECTED TARGET: IMAGE\nCat is not blue.</payload>"

            with patch("tools.agent_call.agent_call") as mock_agent_call:
                mock_agent_call.ainvoke = AsyncMock(return_value=mock_response)

                result = await audit_plot_task(test_state)

                self.assertFalse(result["video_plot_qc_passed"])
                self.assertEqual(result["qc_rejection_target"], "image")
                self.assertIn("Cat is not blue.", result["video_plot_feedback"])

if __name__ == "__main__":
    unittest.main()
