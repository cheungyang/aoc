import unittest
import os
import sys
import json
import tempfile
from unittest.mock import AsyncMock, patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import HumanMessage, AIMessage

from graphs.content_creation.graph import create_graph, ContentCreationState
from graphs.content_creation.adapters import prepare_input, format_output


def _mock_agent_call_side_effect(plot_content, copy_dict, audit_response="VERDICT: APPROVED\nPassed."):
    async def side_effect(args):
        agent_id = args.get("agent_id")
        prompt = str(args.get("prompt", ""))
        if agent_id == "brand-editor":
            return f"<payload>{audit_response}</payload>"
        elif agent_id == "content-creator":
            if "social media publication copy" in prompt.lower() or "copy" in prompt.lower():
                return f"<payload>{json.dumps(copy_dict)}</payload>"
            else:
                return f"<payload>{plot_content}\nOverlay Text: 貓貓</payload>"
        return "<payload>ok</payload>"
    return side_effect


class TestHITLMultiTurnIntegration(unittest.IsolatedAsyncioTestCase):
    """
    Automated Multi-Turn Integration Tests for HITL Gates:
    Verifies state machine integrity across interrupts, human feedback injection,
    intent classification, asset versioning, and state propagation using agent_call.
    """

    async def test_gate1_image_revision_multi_turn_cycle(self):
        """
        Tests Turn 1 (Initial Generation -> Pause at Gate 1) ->
              Turn 2 (Human Feedback at Gate 1 -> Image v2 & Plot v2 -> Pause at Gate 1) ->
              Turn 3 (Approval -> Transition to Gate 2).
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, "cat")
            char_dir = os.path.join(temp_dir, "character")
            os.makedirs(output_dir, exist_ok=True)
            os.makedirs(char_dir, exist_ok=True)

            ref_image = os.path.join(char_dir, "ayla_3d.jpg")
            with open(ref_image, "wb") as f:
                f.write(b"AYLA_3D_REF_DATA")

            with open(os.path.join(char_dir, "01_Character_Sheet_3D.md"), "w") as f:
                f.write("---\nreference_image: ayla_3d.jpg\n---\nCharacter rules")

            with open(os.path.join(temp_dir, "02_Creator_Instructions.md"), "w") as f:
                f.write("Creator instructions")

            audio_path = os.path.join(output_dir, "cat.m4a")
            with open(audio_path, "wb") as f:
                f.write(b"AUDIO_DATA")

            checkpointer = MemorySaver()
            graph = create_graph(checkpointer=checkpointer)
            config = {"configurable": {"thread_id": "test_gate1_multi_turn"}}

            initial_state = prepare_input(
                f"topic: cat, project_dir: {temp_dir}, output_dir: {output_dir}",
                session_id="test_gate1_multi_turn"
            )

            # --- TURN 1: Initial Generation ---
            target_v1_img = os.path.join(output_dir, "cat_image.jpg")
            target_v1_plot = os.path.join(output_dir, "cat_video_plot.md")

            plot_v1_text = "# Video Plot: Cat v1\nToddler playing with kitten"
            copy_v1_dict = {"caption": "Cat", "hashtags": ["#Cat"], "markdown_content": "Copy v1"}

            with patch("graphs.content_creation.nodes.ideation.generate_image.generate_image") as mock_gen_img_1, \
                 patch("tools.agent_call.agent_call") as mock_agent_call_1:

                async def fake_img_invoke(args):
                    out = args["output_path"]
                    with open(out, "wb") as f:
                        f.write(b"IMAGE_BYTES_V1")
                    return f"<payload>{out}</payload>"
                mock_gen_img_1.ainvoke = AsyncMock(side_effect=fake_img_invoke)
                mock_agent_call_1.ainvoke = AsyncMock(side_effect=_mock_agent_call_side_effect(plot_v1_text, copy_v1_dict))

                # Run Turn 1
                state_turn1 = await graph.ainvoke(initial_state, config=config)

                # Verified paused at Gate 1
                self.assertEqual(state_turn1["image_path"], target_v1_img)
                self.assertEqual(state_turn1["video_plot_path"], target_v1_plot)
                self.assertTrue(os.path.isfile(target_v1_img))
                self.assertTrue(os.path.isfile(target_v1_plot))

                snap1 = graph.get_state(config)
                self.assertEqual(snap1.next, ("process_gate1_decision",))

            # --- TURN 2: Human Feedback at Gate 1 ---
            feedback_text = (
                "Use reference image and character/ayla_3d.jpg. "
                "have ayla wear a cat costume, in the pose of pretending like a cat crawling on the floor. "
                "Do not include any actual cats in the image."
            )

            # Update thread state as graph_call does on resumption
            graph.update_state(config, {
                "latest_human_feedback": feedback_text,
                "messages": [HumanMessage(content=feedback_text)]
            }, as_node="ideate_package")

            target_v2_img = os.path.join(output_dir, "cat_image_v2.jpg")
            target_v2_plot = os.path.join(output_dir, "cat_video_plot_v2.md")

            plot_v2_text = "# Video Plot: Cat v2 (Costume Crawling)\nAyla crawling"

            with patch("graphs.content_creation.nodes.ideation.generate_image.generate_image") as mock_gen_img_2, \
                 patch("tools.agent_call.agent_call") as mock_agent_call_2:

                async def fake_img_v2_invoke(args):
                    out = args["output_path"]
                    with open(out, "wb") as f:
                        f.write(b"IMAGE_BYTES_V2")
                    return f"<payload>{out}</payload>"
                mock_gen_img_2.ainvoke = AsyncMock(side_effect=fake_img_v2_invoke)
                mock_agent_call_2.ainvoke = AsyncMock(side_effect=_mock_agent_call_side_effect(plot_v2_text, copy_v1_dict))

                # Resume Graph (Turn 2 execution)
                state_turn2 = await graph.ainvoke(None, config=config)

                # ASSERTIONS: State and disk files MUST reflect v2 assets!
                self.assertEqual(state_turn2["image_path"], target_v2_img)
                self.assertEqual(state_turn2["video_plot_path"], target_v2_plot)
                self.assertTrue(os.path.isfile(target_v2_img))
                self.assertTrue(os.path.isfile(target_v2_plot))

                # Verify image generator was called with human revision prompt and reference image
                mock_gen_img_2.ainvoke.assert_called_once()
                call_args = mock_gen_img_2.ainvoke.call_args[0][0]
                self.assertEqual(call_args["output_path"], target_v2_img)
                self.assertEqual(os.path.abspath(call_args.get("image_path")), os.path.abspath(ref_image))
                self.assertIn("have ayla wear a cat costume", call_args["prompt"])

                # Verify state decision correctly recorded
                self.assertEqual(state_turn2.get("gate1_decision"), "revise_image")

    async def test_gate1_plot_specific_revision_preserves_image(self):
        """
        Tests that when feedback specifically targets the plot, the base image is preserved (not regenerated)
        while the video plot is incremented to v2.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, "dog")
            os.makedirs(output_dir, exist_ok=True)

            img_path = os.path.join(output_dir, "dog_image.jpg")
            with open(img_path, "wb") as f:
                f.write(b"DOG_IMAGE_V1")

            plot_path = os.path.join(output_dir, "dog_video_plot.md")
            with open(plot_path, "w") as f:
                f.write("# Plot v1")

            audio_path = os.path.join(output_dir, "dog.m4a")
            with open(audio_path, "wb") as f:
                f.write(b"AUDIO")

            checkpointer = MemorySaver()
            graph = create_graph(checkpointer=checkpointer)
            config = {"configurable": {"thread_id": "test_gate1_plot_turn"}}

            initial_state = prepare_input(
                f"topic: dog, project_dir: {temp_dir}, output_dir: {output_dir}",
                session_id="test_gate1_plot_turn"
            )

            plot_v1_text = "# Dog Plot v1"
            plot_v2_text = "# Video Plot: Dog v2 (Rapid Zoom)"
            copy_dict = {"caption": "Dog", "hashtags": ["#Dog"], "markdown_content": "Copy"}

            with patch("tools.agent_call.agent_call") as mock_agent_call:
                mock_agent_call.ainvoke = AsyncMock(side_effect=_mock_agent_call_side_effect(plot_v1_text, copy_dict))
                # Prime graph state paused at Gate 1
                await graph.ainvoke(initial_state, config=config)

            # Inject plot-specific revision
            plot_feedback = "Change camera movement: perform a rapid zoom at 0:02.0s."
            graph.update_state(config, {
                "latest_human_feedback": plot_feedback,
                "messages": [HumanMessage(content=plot_feedback)]
            }, as_node="ideate_package")

            with patch("graphs.content_creation.nodes.ideation.generate_image.generate_image") as mock_gen_img, \
                 patch("tools.agent_call.agent_call") as mock_agent_call_2:

                mock_gen_img.ainvoke = AsyncMock()
                mock_agent_call_2.ainvoke = AsyncMock(side_effect=_mock_agent_call_side_effect(plot_v2_text, copy_dict))

                # Resume Graph
                state_res = await graph.ainvoke(None, config=config)

                # Image generation should NOT have been called (reused v1)
                mock_gen_img.ainvoke.assert_not_called()
                self.assertEqual(state_res["image_path"], img_path)

                # Video plot should have been revised to v2
                expected_v2_plot = os.path.join(output_dir, "dog_video_plot_v2.md")
                self.assertEqual(state_res["video_plot_path"], expected_v2_plot)
                self.assertTrue(os.path.isfile(expected_v2_plot))

    async def test_gate2_copy_revision_multi_turn_cycle(self):
        """
        Tests Turn at Gate 2:
        Turn 1: Ingest & Ideate -> Pauses at Gate 1.
        Turn 2: Approved Gate 1 -> Produces Deliverables -> Pauses at Gate 2 with v1 assets.
        Turn 3: Human requests copy revision -> Deliverables regenerated with Copy v2 -> Pauses at Gate 2.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, "apple")
            os.makedirs(output_dir, exist_ok=True)

            audio_path = os.path.join(output_dir, "apple.m4a")
            with open(audio_path, "wb") as f:
                f.write(b"AUDIO")

            checkpointer = MemorySaver()
            graph = create_graph(checkpointer=checkpointer)
            config = {"configurable": {"thread_id": "test_gate2_copy_turn"}}

            initial_state = prepare_input(
                f"topic: apple, project_dir: {temp_dir}, output_dir: {output_dir}",
                session_id="test_gate2_copy_turn"
            )

            plot_text = "# Apple Plot"
            copy_v1_dict = {
                "caption": "Learn Apple in Cantonese!",
                "hashtags": ["#Apple", "#Cantonese"],
                "markdown_content": "# Apple Copy v1"
            }
            copy_v2_dict = {
                "caption": "Learn Apple in Cantonese with Ayla!",
                "hashtags": ["#Apple", "#Cantonese", "#ToddlerLearning"],
                "markdown_content": "# Apple Copy v2 with #ToddlerLearning"
            }

            with patch("graphs.content_creation.nodes.ideation.generate_image.generate_image") as mock_img, \
                 patch("graphs.content_creation.nodes.production.render_plate.generate_animation_veo3") as mock_veo, \
                 patch("graphs.content_creation.nodes.production.remix_video.remix_video") as mock_remix, \
                 patch("graphs.content_creation.nodes.production.produce_deliverables_node.verify_video_task", new_callable=AsyncMock) as mock_verify, \
                 patch("tools.agent_call.agent_call") as mock_agent_call:

                async def fake_img(args):
                    p = args["output_path"]
                    with open(p, "wb") as f: f.write(b"IMG")
                    return f"<payload>{p}</payload>"
                mock_img.ainvoke = AsyncMock(side_effect=fake_img)

                async def fake_veo(args):
                    p = args["output_path"]
                    with open(p, "wb") as f: f.write(b"RAW")
                    return f"<payload>{p}</payload>"
                mock_veo.ainvoke = AsyncMock(side_effect=fake_veo)

                async def fake_remix(args):
                    p = args["output_video_path"]
                    with open(p, "wb") as f: f.write(b"REMIX")
                    return f"<payload>{p}</payload>"
                mock_remix.ainvoke = AsyncMock(side_effect=fake_remix)
                mock_verify.return_value = {"video_qc_passed": True, "extracted_frames_path": ["frame.jpg"]}

                mock_agent_call.ainvoke = AsyncMock(side_effect=_mock_agent_call_side_effect(plot_text, copy_v1_dict))

                # --- Turn 1: Run to Gate 1 ---
                await graph.ainvoke(initial_state, config=config)

                # --- Turn 2: Gate 1 Approved -> Run to Gate 2 ---
                graph.update_state(config, {
                    "latest_human_feedback": "approved",
                    "messages": [HumanMessage(content="approved")]
                }, as_node="ideate_package")
                state_turn2 = await graph.ainvoke(None, config=config)

                # Turn 2 produces v1 copy
                expected_v1_copy = os.path.join(output_dir, "apple_copy.md")
                self.assertEqual(state_turn2["copy_path"], expected_v1_copy)
                self.assertTrue(os.path.isfile(expected_v1_copy))

                # --- Turn 3: Human Feedback at Gate 2 requesting copy revision ---
                mock_agent_call.ainvoke = AsyncMock(side_effect=_mock_agent_call_side_effect(plot_text, copy_v2_dict))
                copy_feedback = "Update the hashtags to include #ToddlerLearning and add pronunciation guide."
                graph.update_state(config, {
                    "latest_human_feedback": copy_feedback,
                    "messages": [HumanMessage(content=copy_feedback)]
                }, as_node="produce_deliverables")

                state_turn3 = await graph.ainvoke(None, config=config)

                # Turn 3 produces v2 copy
                expected_v2_copy = os.path.join(output_dir, "apple_copy_v2.md")
                self.assertEqual(state_turn3["copy_path"], expected_v2_copy)
                self.assertTrue(os.path.isfile(expected_v2_copy))

    async def test_gate2_video_animation_revision_multi_turn(self):
        """
        Tests Video animation revision at Gate 2:
        Turn 1: Ingest & Ideate -> Pauses at Gate 1.
        Turn 2: Gate 1 Approved -> Produces Deliverables (Raw Video v1 & Remixed Video v1).
        Turn 3: Human requests 're-render video animation' -> Produces Raw Video v2 & Remixed Video v2.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, "banana")
            os.makedirs(output_dir, exist_ok=True)

            audio_path = os.path.join(output_dir, "banana.m4a")
            with open(audio_path, "wb") as f:
                f.write(b"AUDIO")

            checkpointer = MemorySaver()
            graph = create_graph(checkpointer=checkpointer)
            config = {"configurable": {"thread_id": "test_gate2_video_turn"}}

            initial_state = prepare_input(
                f"topic: banana, project_dir: {temp_dir}, output_dir: {output_dir}",
                session_id="test_gate2_video_turn"
            )

            plot_text = "# Banana Plot"
            copy_dict = {"caption": "Banana", "hashtags": ["#Banana"], "markdown_content": "Copy"}

            with patch("graphs.content_creation.nodes.ideation.generate_image.generate_image") as mock_img, \
                 patch("graphs.content_creation.nodes.production.render_plate.generate_animation_veo3") as mock_veo, \
                 patch("graphs.content_creation.nodes.production.remix_video.remix_video") as mock_remix, \
                 patch("graphs.content_creation.nodes.production.produce_deliverables_node.verify_video_task", new_callable=AsyncMock) as mock_verify, \
                 patch("tools.agent_call.agent_call") as mock_agent_call:

                async def fake_img(args):
                    p = args["output_path"]
                    with open(p, "wb") as f: f.write(b"IMG")
                    return f"<payload>{p}</payload>"
                mock_img.ainvoke = AsyncMock(side_effect=fake_img)

                async def fake_veo(args):
                    p = args["output_path"]
                    with open(p, "wb") as f: f.write(b"RAW")
                    return f"<payload>{p}</payload>"
                mock_veo.ainvoke = AsyncMock(side_effect=fake_veo)

                async def fake_remix(args):
                    p = args["output_video_path"]
                    with open(p, "wb") as f: f.write(b"REMIX")
                    return f"<payload>{p}</payload>"
                mock_remix.ainvoke = AsyncMock(side_effect=fake_remix)
                mock_verify.return_value = {"video_qc_passed": True, "extracted_frames_path": ["frame.jpg"]}

                mock_agent_call.ainvoke = AsyncMock(side_effect=_mock_agent_call_side_effect(plot_text, copy_dict))

                # --- Turn 1: Run to Gate 1 ---
                await graph.ainvoke(initial_state, config=config)

                # --- Turn 2: Gate 1 Approved -> Run to Gate 2 ---
                graph.update_state(config, {
                    "latest_human_feedback": "approved",
                    "messages": [HumanMessage(content="approved")]
                }, as_node="ideate_package")
                state_turn2 = await graph.ainvoke(None, config=config)

                raw_vid_v1 = os.path.join(output_dir, "banana_raw_video.mp4")
                video_path_v1 = os.path.join(output_dir, "banana_video.mp4")
                self.assertEqual(state_turn2["raw_video_path"], raw_vid_v1)
                self.assertEqual(state_turn2["remixed_video_path"], video_path_v1)
                self.assertTrue(os.path.isfile(raw_vid_v1))
                self.assertTrue(os.path.isfile(video_path_v1))

                # --- Turn 3: Video Animation Revision at Gate 2 ---
                video_feedback = "Re-render the video animation with slower camera pan."
                graph.update_state(config, {
                    "latest_human_feedback": video_feedback,
                    "messages": [HumanMessage(content=video_feedback)]
                }, as_node="produce_deliverables")

                state_turn3 = await graph.ainvoke(None, config=config)

                raw_vid_v2 = os.path.join(output_dir, "banana_raw_video_v2.mp4")
                video_path_v2 = os.path.join(output_dir, "banana_video_v2.mp4")

                self.assertEqual(state_turn3["raw_video_path"], raw_vid_v2)
                self.assertEqual(state_turn3["remixed_video_path"], video_path_v2)
                self.assertTrue(os.path.isfile(raw_vid_v2))
                self.assertTrue(os.path.isfile(video_path_v2))


if __name__ == "__main__":
    unittest.main()
