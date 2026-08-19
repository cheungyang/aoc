import unittest
from unittest.mock import AsyncMock, patch, MagicMock
import tempfile
import os
import json
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))

from graphs.content_creation.nodes.production import draft_copy_task
from graphs.content_creation.schemas import FinalCopy

class TestDraftAndSaveCopyNode(unittest.IsolatedAsyncioTestCase):
    async def test_node_generates_and_saves_copy_from_reinforced_xml_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_state = {
                "topic": "puppy",
                "project_dir": temp_dir,
                "output_dir": os.path.join(temp_dir, "puppy"),
                "creator_instructions_path": os.path.join(temp_dir, "02_Creator_Instructions.md"),
                "qc_playbook_path": os.path.join(temp_dir, "03_QC_Playbook.md"),
                "gate2_decision": "approved",
                "error_message": ""
            }

            with open(test_state["creator_instructions_path"], "w") as f:
                f.write("Write cute things.")
            with open(test_state["qc_playbook_path"], "w") as f:
                f.write("Must include hashtags.")

            copy_file_path = os.path.join(test_state["output_dir"], "puppy_copy.md")
            mock_response = (
                f"<payload>\n"
                f"<status>success</status>\n"
                f"<error></error>\n"
                f"<copy_path>{copy_file_path}</copy_path>\n"
                f"<caption_text>Look at this cute puppy playing in the grass!</caption_text>\n"
                f"<hashtags>#puppy #cute #animals</hashtags>\n"
                f"<vocabulary>小狗 (siu2 gau2) - puppy</vocabulary>\n"
                f"</payload>"
            )

            with patch("tools.agent_call.agent_call") as mock_agent_call:
                mock_agent_call.ainvoke = AsyncMock(return_value=mock_response)
                result = await draft_copy_task(test_state)

                mock_agent_call.ainvoke.assert_called_once()
                call_args = mock_agent_call.ainvoke.call_args[0][0]
                self.assertEqual(call_args["agent_id"], "graph-worker")
                call_prompt = call_args["prompt"]
                self.assertIn("<playbook>", call_prompt)
                self.assertIn("<current_state>", call_prompt)
                self.assertIn("<assigned_task>", call_prompt)
                self.assertIn("<copy_path>{copy_path}</copy_path>", call_prompt)
                self.assertIn("copy_path", result)
                self.assertIn("puppy_copy", result["copy_path"])

                copy_md_path = result["copy_path"]
                self.assertTrue(os.path.exists(copy_md_path))
                with open(copy_md_path, "r", encoding="utf-8") as f:
                    content = f.read()
                    self.assertIn("Look at this cute puppy", content)

                copy_json_path = copy_md_path.replace(".md", ".json")
                self.assertTrue(os.path.exists(copy_json_path))
                with open(copy_json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.assertEqual(data["caption"], "Look at this cute puppy playing in the grass!")
                    self.assertEqual(data["hashtags"], ["#puppy", "#cute", "#animals"])
                    self.assertEqual(data["vocabulary"], "小狗 (siu2 gau2) - puppy")

    async def test_node_generates_and_saves_copy_from_json_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_state = {
                "topic": "puppy",
                "project_dir": temp_dir,
                "output_dir": os.path.join(temp_dir, "puppy"),
                "creator_instructions_path": os.path.join(temp_dir, "02_Creator_Instructions.md"),
                "qc_playbook_path": os.path.join(temp_dir, "03_QC_Playbook.md"),
                "gate2_decision": "approved",
                "error_message": ""
            }

            with open(test_state["creator_instructions_path"], "w") as f:
                f.write("Write cute things.")
            with open(test_state["qc_playbook_path"], "w") as f:
                f.write("Must include hashtags.")

            mock_payload = json.dumps({
                "caption": "Look at this cute puppy!",
                "hashtags": ["#puppy", "#cute"],
                "markdown_content": "**Caption:** Look at this cute puppy! \n **Hashtags:** #puppy #cute"
            })
            mock_response = f"<payload>{mock_payload}</payload>"

            with patch("tools.agent_call.agent_call") as mock_agent_call:
                mock_agent_call.ainvoke = AsyncMock(return_value=mock_response)
                result = await draft_copy_task(test_state)

                copy_md_path = result["copy_path"]
                self.assertTrue(os.path.exists(copy_md_path))
                with open(copy_md_path, "r", encoding="utf-8") as f:
                    self.assertEqual(f.read(), "**Caption:** Look at this cute puppy! \n **Hashtags:** #puppy #cute")

    async def test_node_generates_copy_from_plain_markdown_response(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, "cat")
            os.makedirs(output_dir, exist_ok=True)
            test_state = {
                "topic": "cat",
                "project_dir": temp_dir,
                "output_dir": output_dir
            }

            mock_response = "<payload># Cat Post\nAdorable kitten playing.\n#cats #kitten</payload>"

            with patch("tools.agent_call.agent_call") as mock_agent_call:
                mock_agent_call.ainvoke = AsyncMock(return_value=mock_response)
                result = await draft_copy_task(test_state)

                self.assertIn("copy_path", result)
                with open(result["copy_path"], "r", encoding="utf-8") as f:
                    self.assertEqual(f.read(), "# Cat Post\nAdorable kitten playing.\n#cats #kitten")

    async def test_node_handles_agent_call_exception_gracefully(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, "cat")
            os.makedirs(output_dir, exist_ok=True)
            test_state = {
                "topic": "cat",
                "project_dir": temp_dir,
                "output_dir": output_dir
            }

            with patch("tools.agent_call.agent_call") as mock_agent_call:
                mock_agent_call.ainvoke = AsyncMock(side_effect=RuntimeError("Connection failed"))
                result = await draft_copy_task(test_state)

                self.assertIn("copy_path", result)

    async def test_node_aborts_if_error_message_present(self):
        test_state = {
            "error_message": "Something went wrong upstream."
        }
        result = await draft_copy_task(test_state)
        self.assertEqual(result, {})

if __name__ == "__main__":
    unittest.main()
