import unittest
from unittest.mock import AsyncMock, patch
import tempfile
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))

from graphs.content_creation.nodes.ideation import generate_image_task

class TestSetupAndGenerateImageNode(unittest.IsolatedAsyncioTestCase):
    async def test_reuses_existing_image_when_no_revision_requested(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, "cat")
            os.makedirs(output_dir, exist_ok=True)
            existing_image_path = os.path.join(output_dir, "cat_image.jpg")
            with open(existing_image_path, "w") as f:
                f.write("existing_image_bytes")

            state = {
                "topic": "cat",
                "project_dir": temp_dir,
                "output_dir": output_dir,
                "gate1_decision": "approved"
            }

            with patch("graphs.content_creation.nodes.ideation.generate_image.generate_image") as mock_gen:
                result = await generate_image_task(state)

                mock_gen.ainvoke.assert_not_called()
                self.assertEqual(result["image_path"], existing_image_path)

    async def test_generates_v2_when_gate1_requests_revise_image(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, "cat")
            os.makedirs(output_dir, exist_ok=True)
            existing_image_path = os.path.join(output_dir, "cat_image.jpg")
            with open(existing_image_path, "w") as f:
                f.write("existing_image_bytes")

            state = {
                "topic": "cat",
                "project_dir": temp_dir,
                "output_dir": output_dir,
                "gate1_decision": "revise_image",
                "latest_human_feedback": "Make the eyes more expressive."
            }

            target_path = os.path.join(output_dir, "cat_image_v2.jpg")
            with patch("graphs.content_creation.nodes.ideation.generate_image.generate_image") as mock_gen:
                mock_gen.ainvoke = AsyncMock(return_value=f"<payload>{target_path}</payload>")

                result = await generate_image_task(state)

                mock_gen.ainvoke.assert_called_once()
                self.assertEqual(result["image_path"], target_path)

    async def test_loads_style_specific_character_sheet(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, "cat")
            char_dir = os.path.join(temp_dir, "character")
            os.makedirs(output_dir, exist_ok=True)
            os.makedirs(char_dir, exist_ok=True)

            # Create 3D and Ghibli character sheets
            with open(os.path.join(char_dir, "01_Character_Sheet_3D.md"), "w") as f:
                f.write("3D_PIXAR_RULES")
            with open(os.path.join(char_dir, "01_Character_Sheet_Ghibli.md"), "w") as f:
                f.write("GHIBLI_ANIME_RULES")

            state = {
                "topic": "cat",
                "style": "Ghibli",
                "project_dir": temp_dir,
                "output_dir": output_dir
            }

            with patch("graphs.content_creation.nodes.ideation.generate_image.generate_image") as mock_gen:
                mock_gen.ainvoke = AsyncMock(return_value="<payload>done</payload>")
                await generate_image_task(state)

                mock_gen.ainvoke.assert_called_once()
                call_prompt = mock_gen.ainvoke.call_args[0][0]["prompt"]
                self.assertIn("GHIBLI_ANIME_RULES", call_prompt)
                self.assertNotIn("3D_PIXAR_RULES", call_prompt)

    async def test_attaches_style_reference_image(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, "cat")
            char_dir = os.path.join(temp_dir, "character")
            os.makedirs(output_dir, exist_ok=True)
            os.makedirs(char_dir, exist_ok=True)

            ref_img_path = os.path.join(char_dir, "hero_3d.jpg")
            with open(ref_img_path, "wb") as f:
                f.write(b"HERO_3D_BYTES")

            with open(os.path.join(char_dir, "01_Character_Sheet_3D.md"), "w") as f:
                f.write("---\nreference_image: hero_3d.jpg\n---\n# 3D Sheet\nPixar rules")

            state = {
                "topic": "cat",
                "style": "3D",
                "project_dir": temp_dir,
                "output_dir": output_dir
            }

            with patch("graphs.content_creation.nodes.ideation.generate_image.generate_image") as mock_gen:
                mock_gen.ainvoke = AsyncMock(return_value="<payload>done</payload>")
                await generate_image_task(state)

                mock_gen.ainvoke.assert_called_once()
                call_args = mock_gen.ainvoke.call_args[0][0]
                self.assertEqual(call_args.get("image_path"), ref_img_path)

    async def test_prompt_dynamically_loaded_from_project_instructions_and_human_feedback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, "cat")
            char_dir = os.path.join(temp_dir, "character")
            os.makedirs(output_dir, exist_ok=True)
            os.makedirs(char_dir, exist_ok=True)

            instr_path = os.path.join(temp_dir, "02_Creator_Instructions.md")
            with open(instr_path, "w") as f:
                f.write("DYNAMIC_CREATOR_RULEBOOK_TEXT")

            sheet_path = os.path.join(char_dir, "01_Character_Sheet_3D.md")
            with open(sheet_path, "w") as f:
                f.write("---\nstyle: 3D\n---\nDYNAMIC_3D_CHARACTER_SHEET")

            state = {
                "topic": "cat",
                "style": "3D",
                "project_dir": temp_dir,
                "output_dir": output_dir,
                "creator_instructions_path": instr_path,
                "latest_human_feedback": "Ayla should wear a cozy kitten onesie."
            }

            with patch("graphs.content_creation.nodes.ideation.generate_image.generate_image") as mock_gen:
                mock_gen.ainvoke = AsyncMock(return_value="<payload>done</payload>")
                await generate_image_task(state)

                mock_gen.ainvoke.assert_called_once()
                call_prompt = mock_gen.ainvoke.call_args[0][0]["prompt"]
                self.assertIn("DYNAMIC_CREATOR_RULEBOOK_TEXT", call_prompt)
                self.assertIn("DYNAMIC_3D_CHARACTER_SHEET", call_prompt)
                self.assertIn("Ayla should wear a cozy kitten onesie.", call_prompt)

    async def test_generates_v2_when_feedback_provided_even_if_gate1_decision_was_approved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, "cat")
            char_dir = os.path.join(temp_dir, "character")
            os.makedirs(output_dir, exist_ok=True)
            os.makedirs(char_dir, exist_ok=True)
            
            existing_image_path = os.path.join(output_dir, "cat_image.jpg")
            with open(existing_image_path, "w") as f:
                f.write("existing_image_bytes")

            ref_img_path = os.path.join(char_dir, "ayla_3d.jpg")
            with open(ref_img_path, "wb") as f:
                f.write(b"AYLA_3D_REF_BYTES")

            # State where gate1_decision is 'approved' (default initialization) but human feedback is provided
            state = {
                "topic": "cat",
                "style": "3D",
                "project_dir": temp_dir,
                "output_dir": output_dir,
                "gate1_decision": "approved",
                "latest_human_feedback": "Use reference image and character/ayla_3d.jpg. have ayla wear a cat costume, in the post of pretending like a cat crawling on the floor. Do not include any actual cats in the image."
            }

            target_path = os.path.join(output_dir, "cat_image_v2.jpg")
            with patch("graphs.content_creation.nodes.ideation.generate_image.generate_image") as mock_gen:
                mock_gen.ainvoke = AsyncMock(return_value=f"<payload>{target_path}</payload>")

                result = await generate_image_task(state)

                mock_gen.ainvoke.assert_called_once()
                self.assertEqual(result["image_path"], target_path)
                call_args = mock_gen.ainvoke.call_args[0][0]
                self.assertEqual(call_args.get("image_path"), ref_img_path)
                self.assertIn("have ayla wear a cat costume", call_args["prompt"])

if __name__ == "__main__":
    unittest.main()
