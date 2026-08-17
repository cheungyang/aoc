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
            
            mock_audit = PlotAudit(
                is_approved=True,
                rejection_target="NONE",
                revision_notes="",
                markdown_report="Everything is great."
            )
            
            with patch("langchain_google_genai.ChatGoogleGenerativeAI") as MockLLM:
                mock_llm_instance = MagicMock()
                mock_structured = AsyncMock()
                mock_structured.ainvoke.return_value = mock_audit
                mock_llm_instance.with_structured_output.return_value = mock_structured
                MockLLM.return_value = mock_llm_instance
                
                result = await audit_plot_task(test_state)
                
                self.assertIn("video_plot_qc_passed", result)
                self.assertTrue(result["video_plot_qc_passed"])
                self.assertEqual(result["video_plot_feedback"], "Everything is great.")

    async def test_audit_rejects_when_image_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_state = {
                "topic": "cat",
                "project_dir": temp_dir,
                "output_dir": temp_dir,
            }
            
            with open(os.path.join(temp_dir, "cat_image.jpg"), "wb") as f:
                f.write(b"IMAGE_BYTES")
                
            mock_audit = PlotAudit(
                is_approved=False,
                rejection_target="IMAGE",
                revision_notes="Cat is not blue.",
                markdown_report="Rejecting image."
            )
            
            with patch("langchain_google_genai.ChatGoogleGenerativeAI") as MockLLM:
                mock_llm_instance = MagicMock()
                mock_structured = AsyncMock()
                mock_structured.ainvoke.return_value = mock_audit
                mock_llm_instance.with_structured_output.return_value = mock_structured
                MockLLM.return_value = mock_llm_instance
                
                result = await audit_plot_task(test_state)
                
                self.assertFalse(result["video_plot_qc_passed"])
                self.assertEqual(result["qc_rejection_target"], "image")
                self.assertEqual(result["video_plot_feedback"], "Cat is not blue.")

if __name__ == "__main__":
    unittest.main()
