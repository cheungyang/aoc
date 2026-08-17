import unittest
from unittest.mock import AsyncMock, patch, MagicMock
import tempfile
import os
import json
import asyncio
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))

from graphs.content_creation.nodes.audit_video_plot_node import audit_video_plot_node
from graphs.content_creation.schemas import PlotAudit

class TestAuditVideoPlotNode(unittest.IsolatedAsyncioTestCase):
    async def test_audit_approves_when_playbook_satisfied(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_state = {
                "topic": "fish",
                "project_dir": temp_dir,
                "output_dir": temp_dir,
                "qc_playbook_path": os.path.join(temp_dir, "03_QC_Playbook.md"),
                "image_prompt": "A cute fish",
                "video_plot_content": "Motion prompt for a fish",
                "video_plot_attempts": 0
            }
            
            with open(test_state["qc_playbook_path"], "w") as f:
                f.write("Fish must be cute.")
            
            # Create base image file
            with open(os.path.join(temp_dir, "fish_image.jpg"), "wb") as f:
                f.write(b"IMAGE_BYTES")
                
            mock_audit = PlotAudit(
                is_approved=True,
                rejection_target="none",
                revision_notes="",
                markdown_report="Everything is great."
            )
            
            with patch("langchain_google_genai.ChatGoogleGenerativeAI") as MockLLM:
                mock_llm_instance = MagicMock()
                mock_structured = AsyncMock()
                mock_structured.ainvoke.return_value = mock_audit
                mock_llm_instance.with_structured_output.return_value = mock_structured
                MockLLM.return_value = mock_llm_instance
                
                result = await audit_video_plot_node(test_state)
                
                self.assertIn("video_plot_qc_passed", result)
                self.assertTrue(result["video_plot_qc_passed"])
                self.assertEqual(result["video_plot_feedback"], "")
                self.assertEqual(result["video_plot_attempts"], 1)
                
                audit_md = os.path.join(temp_dir, "fish_plot_audit.md")
                self.assertTrue(os.path.exists(audit_md))
                with open(audit_md, "r") as f:
                    self.assertEqual(f.read(), "Everything is great.")

    async def test_audit_rejects_when_image_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_state = {
                "topic": "cat",
                "project_dir": temp_dir,
                "output_dir": temp_dir,
            }
            
            # Create base image file so rejection comes from QC assessment
            with open(os.path.join(temp_dir, "cat_image.jpg"), "wb") as f:
                f.write(b"IMAGE_BYTES")
                
            mock_audit = PlotAudit(
                is_approved=False,
                rejection_target="image",
                revision_notes="Cat is not blue.",
                markdown_report="Rejecting image."
            )
            
            with patch("langchain_google_genai.ChatGoogleGenerativeAI") as MockLLM:
                mock_llm_instance = MagicMock()
                mock_structured = AsyncMock()
                mock_structured.ainvoke.return_value = mock_audit
                mock_llm_instance.with_structured_output.return_value = mock_structured
                MockLLM.return_value = mock_llm_instance
                
                result = await audit_video_plot_node(test_state)
                
                self.assertFalse(result["video_plot_qc_passed"])
                self.assertEqual(result["qc_rejection_target"], "image")
                self.assertEqual(result["video_plot_feedback"], "Cat is not blue.")

if __name__ == "__main__":
    unittest.main()
