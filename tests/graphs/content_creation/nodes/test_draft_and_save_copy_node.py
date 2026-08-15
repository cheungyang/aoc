import unittest
from unittest.mock import AsyncMock, patch, MagicMock
import tempfile
import os
import json
import asyncio

from graphs.content_creation.nodes.draft_and_save_copy_node import draft_and_save_copy_node
from graphs.content_creation.schemas import FinalCopy

class TestDraftAndSaveCopyNode(unittest.IsolatedAsyncioTestCase):
    async def test_node_generates_and_saves_copy_successfully(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            # Setup state with temp directory paths
            test_state = {
                "topic": "puppy",
                "project_dir": temp_dir,
                "output_dir": os.path.join(temp_dir, "puppy"),
                "creator_instructions_path": os.path.join(temp_dir, "02_Creator_Instructions.md"),
                "qc_playbook_path": os.path.join(temp_dir, "03_QC_Playbook.md"),
                "gate2_decision": "approved",
                "error_message": ""
            }

            # Create dummy input files
            with open(test_state["creator_instructions_path"], "w") as f:
                f.write("Write cute things.")
            with open(test_state["qc_playbook_path"], "w") as f:
                f.write("Must include hashtags.")

            # Mock the LLM chain response
            mock_final_copy = FinalCopy(
                caption="Look at this cute puppy!",
                hashtags=["#puppy", "#cute"],
                markdown_content="**Caption:** Look at this cute puppy! \n **Hashtags:** #puppy #cute"
            )

            # We need to mock AgentsLoader and ChatGoogleGenerativeAI
            with patch("graphs.content_creation.nodes.draft_and_save_copy_node.AgentsLoader") as MockLoader, \
                 patch("langchain_google_genai.ChatGoogleGenerativeAI") as MockLLM:
                
                # Setup the LLM mock chain
                mock_llm_instance = MagicMock()
                mock_structured_llm = AsyncMock()
                mock_structured_llm.ainvoke.return_value = mock_final_copy
                mock_llm_instance.with_structured_output.return_value = mock_structured_llm
                MockLLM.return_value = mock_llm_instance

                # Run the async node
                result = await draft_and_save_copy_node(test_state)

                # Assert state updates are correct
                self.assertIn("copy_text", result)
                self.assertEqual(result["copy_text"], mock_final_copy.markdown_content)
                self.assertIn("copy_path", result)
                self.assertIn("puppy_copy", result["copy_path"])

                # Assert files were dual-published to disk
                copy_path = result["copy_path"]
                copy_json_path = copy_path.replace(".md", ".json")

                self.assertTrue(os.path.exists(copy_path), "Markdown copy was not saved")
                self.assertTrue(os.path.exists(copy_json_path), "JSON copy was not saved")

                # Verify JSON payload
                with open(copy_json_path, "r") as f:
                    saved_json = json.load(f)
                self.assertEqual(saved_json["caption"], "Look at this cute puppy!")

    async def test_node_aborts_if_error_message_present(self):
        # Node should fail fast if error_message exists in state
        test_state = {"error_message": "Previous node failed"}
        result = await draft_and_save_copy_node(test_state)
        self.assertEqual(result, {})

if __name__ == "__main__":
    unittest.main()
