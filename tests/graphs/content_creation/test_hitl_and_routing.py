import unittest
import os
import sys
import json
import tempfile
from unittest.mock import AsyncMock, patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import HumanMessage, AIMessage

from graphs.content_creation.graph import create_graph
from graphs.content_creation.adapters import prepare_input
from graphs.content_creation.utils.invariants import assert_gate1_revision_invariants, assert_gate2_revision_invariants


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
    Automated Multi-Turn Integration Tests covering all HITL Gates scenarios:
    1. HITL Gate 1: User sees an image from the response presentation card.
    2. HITL Gate 1: User asks for image update -> Returns new image (v2) with same plot.
    3. HITL Gate 1: User asks for plot update -> Returns new plot (v2) with same image.
    4. HITL Gate 2: User sees a video that is remixed.
    5. HITL Gate 2: User asks for video update -> Returns new raw video (v2) with new remixed video (v2).
    6. HITL Gate 2: User asks for remix update -> Returns same raw video with new remixed video (v2).
    7. HITL Gate 2: User asks for copy update -> Returns new copy (v2) with same video.
    """

    async def test_scenario_1_gate1_user_sees_image_in_response(self):
        """Scenario 1: At HITL Gate 1, the user sees an image preview and link in the presentation card."""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, "cat")
            os.makedirs(output_dir, exist_ok=True)

            audio_path = os.path.join(output_dir, "cat.m4a")
            with open(audio_path, "wb") as f: f.write(b"AUDIO")

            checkpointer = MemorySaver()
            graph = create_graph(checkpointer=checkpointer)
            config = {"configurable": {"thread_id": "test_scenario_1"}}

            initial_state = prepare_input(
                f"topic: cat, project_dir: {temp_dir}, output_dir: {output_dir}",
                session_id="test_scenario_1"
            )

            expected_img = os.path.join(output_dir, "cat_image.jpg")
            expected_plot = os.path.join(output_dir, "cat_video_plot.md")

            with patch("graphs.content_creation.nodes.ideation.generate_image.generate_image") as mock_img, \
                 patch("tools.agent_call.agent_call") as mock_agent_call:

                async def fake_img(args):
                    out = args["output_path"]
                    with open(out, "wb") as f: f.write(b"CAT_IMG")
                    return f"<payload>{out}</payload>"
                mock_img.ainvoke = AsyncMock(side_effect=fake_img)
                mock_agent_call.ainvoke = AsyncMock(side_effect=_mock_agent_call_side_effect("# Cat Plot", {}))

                state = await graph.ainvoke(initial_state, config=config)

                # Assert image exists and response message contains image presentation link
                self.assertEqual(state["image_path"], expected_img)
                self.assertTrue(os.path.isfile(expected_img))
                self.assertEqual(state["video_plot_path"], expected_plot)
                self.assertTrue(os.path.isfile(expected_plot))

                last_msg = state["messages"][-1].content
                self.assertIn("HITL GATE 1: Image & Video Plot Approval Required", last_msg)
                self.assertIn(expected_img, last_msg)
                self.assertIn(f'<image path="{expected_img}"/>', last_msg)

    async def test_scenario_2_gate1_image_update_returns_new_image_with_same_plot(self):
        """Scenario 2: HITL Gate 1, user asks for image update -> Returns new image (v2) with same plot."""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, "cat")
            os.makedirs(output_dir, exist_ok=True)

            audio_path = os.path.join(output_dir, "cat.m4a")
            with open(audio_path, "wb") as f: f.write(b"AUDIO")

            checkpointer = MemorySaver()
            graph = create_graph(checkpointer=checkpointer)
            config = {"configurable": {"thread_id": "test_scenario_2"}}

            initial_state = prepare_input(
                f"topic: cat, project_dir: {temp_dir}, output_dir: {output_dir}",
                session_id="test_scenario_2"
            )

            with patch("graphs.content_creation.nodes.ideation.generate_image.generate_image") as mock_img, \
                 patch("tools.agent_call.agent_call") as mock_agent_call:

                async def fake_img(args):
                    out = args["output_path"]
                    with open(out, "wb") as f: f.write(b"CAT_IMG")
                    return f"<payload>{out}</payload>"
                mock_img.ainvoke = AsyncMock(side_effect=fake_img)
                mock_agent_call.ainvoke = AsyncMock(side_effect=_mock_agent_call_side_effect("# Cat Plot v1", {}))

                # Turn 1: Run to Gate 1
                state_turn1 = await graph.ainvoke(initial_state, config=config)
                v1_img = state_turn1["image_path"]
                v1_plot = state_turn1["video_plot_path"]

                # Turn 2: User requests image modification (costume change)
                image_feedback = "Change the character costume to an orange cat onesie."
                graph.update_state(config, {
                    "latest_human_feedback": image_feedback,
                    "messages": [HumanMessage(content=image_feedback)]
                }, as_node="ideate_package")

                state_turn2 = await graph.ainvoke(None, config=config)

                v2_img = os.path.join(output_dir, "cat_image_v2.jpg")
                self.assertEqual(state_turn2["image_path"], v2_img)
                self.assertTrue(os.path.isfile(v2_img))

                # Video plot MUST be preserved (the same plot as v1)
                self.assertEqual(state_turn2["video_plot_path"], v1_plot)

                # Invariants must pass cleanly
                assert_gate1_revision_invariants(state_turn1, state_turn2)

    async def test_scenario_3_gate1_plot_update_returns_new_plot_with_same_image(self):
        """Scenario 3: HITL Gate 1, user asks for plot update -> Returns new plot (v2) with same image."""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, "dog")
            os.makedirs(output_dir, exist_ok=True)

            audio_path = os.path.join(output_dir, "dog.m4a")
            with open(audio_path, "wb") as f: f.write(b"AUDIO")

            checkpointer = MemorySaver()
            graph = create_graph(checkpointer=checkpointer)
            config = {"configurable": {"thread_id": "test_scenario_3"}}

            initial_state = prepare_input(
                f"topic: dog, project_dir: {temp_dir}, output_dir: {output_dir}",
                session_id="test_scenario_3"
            )

            with patch("graphs.content_creation.nodes.ideation.generate_image.generate_image") as mock_img, \
                 patch("tools.agent_call.agent_call") as mock_agent_call:

                async def fake_img(args):
                    out = args["output_path"]
                    with open(out, "wb") as f: f.write(b"DOG_IMG")
                    return f"<payload>{out}</payload>"
                mock_img.ainvoke = AsyncMock(side_effect=fake_img)
                mock_agent_call.ainvoke = AsyncMock(side_effect=_mock_agent_call_side_effect("# Dog Plot v1", {}))

                # Turn 1: Run to Gate 1
                state_turn1 = await graph.ainvoke(initial_state, config=config)
                v1_img = state_turn1["image_path"]

                # Turn 2: User requests plot revision
                mock_img.reset_mock()
                mock_agent_call.ainvoke = AsyncMock(side_effect=_mock_agent_call_side_effect("# Dog Plot v2 with Zoom", {}))
                plot_feedback = "Update the video plot motion: add a slow camera push-in."
                graph.update_state(config, {
                    "latest_human_feedback": plot_feedback,
                    "messages": [HumanMessage(content=plot_feedback)]
                }, as_node="ideate_package")

                state_turn2 = await graph.ainvoke(None, config=config)

                # Base image MUST NOT be regenerated (same image as v1)
                mock_img.ainvoke.assert_not_called()
                self.assertEqual(state_turn2["image_path"], v1_img)

                # Video plot MUST be incremented to v2
                v2_plot = os.path.join(output_dir, "dog_video_plot_v2.md")
                self.assertEqual(state_turn2["video_plot_path"], v2_plot)
                self.assertTrue(os.path.isfile(v2_plot))

                # Invariants must pass cleanly
                assert_gate1_revision_invariants(state_turn1, state_turn2)

    async def test_scenario_4_gate2_user_sees_remixed_video(self):
        """Scenario 4: At HITL Gate 2, the user sees a remixed video deliverable with audio and overlay."""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, "horse")
            os.makedirs(output_dir, exist_ok=True)

            audio_path = os.path.join(output_dir, "horse.m4a")
            with open(audio_path, "wb") as f: f.write(b"AUDIO")

            checkpointer = MemorySaver()
            graph = create_graph(checkpointer=checkpointer)
            config = {"configurable": {"thread_id": "test_scenario_4"}}

            initial_state = prepare_input(
                f"topic: horse, project_dir: {temp_dir}, output_dir: {output_dir}",
                session_id="test_scenario_4"
            )

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
                    with open(p, "wb") as f: f.write(b"RAW_PLATE")
                    return f"<payload>{p}</payload>"
                mock_veo.ainvoke = AsyncMock(side_effect=fake_veo)

                async def fake_remix(args):
                    p = args["output_video_path"]
                    with open(p, "wb") as f: f.write(b"REMIXED_VIDEO")
                    return f"<payload>{p}</payload>"
                mock_remix.ainvoke = AsyncMock(side_effect=fake_remix)
                mock_verify.return_value = {"video_qc_passed": True, "extracted_frames_path": ["frame.jpg"]}
                mock_agent_call.ainvoke = AsyncMock(side_effect=_mock_agent_call_side_effect("# Horse Plot", {"caption": "Horse"}))

                # Turn 1: Run to Gate 1
                await graph.ainvoke(initial_state, config=config)

                # Turn 2: Gate 1 Approved -> Run to Gate 2
                graph.update_state(config, {
                    "latest_human_feedback": "approved",
                    "messages": [HumanMessage(content="approved")]
                }, as_node="ideate_package")
                state_turn2 = await graph.ainvoke(None, config=config)

                raw_video = os.path.join(output_dir, "horse_raw_video.mp4")
                master_video = os.path.join(output_dir, "horse_video.mp4")
                self.assertEqual(state_turn2["raw_video_path"], raw_video)
                self.assertEqual(state_turn2["remixed_video_path"], master_video)
                self.assertTrue(os.path.isfile(raw_video))
                self.assertTrue(os.path.isfile(master_video))

                last_msg = state_turn2["messages"][-1].content
                self.assertIn("HITL GATE 2: Final Package Review & Approval", last_msg)
                self.assertIn(master_video, last_msg)
                self.assertIn(f'<video path="{master_video}"/>', last_msg)

    async def test_scenario_5_gate2_video_update_returns_new_video_with_remix(self):
        """Scenario 5: HITL Gate 2, user asks for video update -> Returns new raw video (v2) and new remixed video (v2)."""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, "banana")
            os.makedirs(output_dir, exist_ok=True)

            audio_path = os.path.join(output_dir, "banana.m4a")
            with open(audio_path, "wb") as f: f.write(b"AUDIO")

            checkpointer = MemorySaver()
            graph = create_graph(checkpointer=checkpointer)
            config = {"configurable": {"thread_id": "test_scenario_5"}}

            initial_state = prepare_input(
                f"topic: banana, project_dir: {temp_dir}, output_dir: {output_dir}",
                session_id="test_scenario_5"
            )

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
                mock_agent_call.ainvoke = AsyncMock(side_effect=_mock_agent_call_side_effect("# Banana Plot", {"caption": "Banana"}))

                # Turn 1: Run to Gate 1
                await graph.ainvoke(initial_state, config=config)

                # Turn 2: Gate 1 Approved -> Run to Gate 2
                graph.update_state(config, {
                    "latest_human_feedback": "approved",
                    "messages": [HumanMessage(content="approved")]
                }, as_node="ideate_package")
                state_turn2 = await graph.ainvoke(None, config=config)
                copy_v1 = state_turn2["copy_path"]

                # Turn 3: Video animation revision requested
                video_feedback = "Re-render the video animation with smoother toddler motion."
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

                # Copy is preserved
                self.assertEqual(state_turn3["copy_path"], copy_v1)

                # Invariants must pass cleanly
                assert_gate2_revision_invariants(state_turn2, state_turn3)

    async def test_scenario_6_gate2_remix_update_returns_same_raw_video_with_new_remix(self):
        """Scenario 6: HITL Gate 2, user asks for remix update -> Returns same raw video with new remixed video (v2)."""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, "panda")
            os.makedirs(output_dir, exist_ok=True)

            audio_path = os.path.join(output_dir, "panda.m4a")
            with open(audio_path, "wb") as f: f.write(b"AUDIO")

            checkpointer = MemorySaver()
            graph = create_graph(checkpointer=checkpointer)
            config = {"configurable": {"thread_id": "test_scenario_6"}}

            initial_state = prepare_input(
                f"topic: panda, project_dir: {temp_dir}, output_dir: {output_dir}",
                session_id="test_scenario_6"
            )

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
                mock_agent_call.ainvoke = AsyncMock(side_effect=_mock_agent_call_side_effect("# Panda Plot", {"caption": "Panda"}))

                # Turn 1: Run to Gate 1
                await graph.ainvoke(initial_state, config=config)

                # Turn 2: Gate 1 Approved -> Run to Gate 2
                graph.update_state(config, {
                    "latest_human_feedback": "approved",
                    "messages": [HumanMessage(content="approved")]
                }, as_node="ideate_package")
                state_turn2 = await graph.ainvoke(None, config=config)

                raw_vid_v1 = state_turn2["raw_video_path"]

                # Turn 3: User requests remix/subtitle update
                mock_veo.reset_mock()
                remix_feedback = "Adjust subtitle overlay font size and change audio track timing."
                graph.update_state(config, {
                    "latest_human_feedback": remix_feedback,
                    "messages": [HumanMessage(content=remix_feedback)]
                }, as_node="produce_deliverables")
                state_turn3 = await graph.ainvoke(None, config=config)

                # Raw video animation MUST NOT be regenerated (preserved from v1)
                mock_veo.ainvoke.assert_not_called()
                self.assertEqual(state_turn3["raw_video_path"], raw_vid_v1)

                # Remixed video MUST be incremented to v2
                video_path_v2 = os.path.join(output_dir, "panda_video_v2.mp4")
                self.assertEqual(state_turn3["remixed_video_path"], video_path_v2)
                self.assertTrue(os.path.isfile(video_path_v2))

                # Invariants must pass cleanly
                assert_gate2_revision_invariants(state_turn2, state_turn3)

    async def test_scenario_7_gate2_copy_update_returns_new_copy_with_same_video(self):
        """Scenario 7: HITL Gate 2, user asks for copy update -> Returns new copy (v2) with same video."""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, "apple")
            os.makedirs(output_dir, exist_ok=True)

            audio_path = os.path.join(output_dir, "apple.m4a")
            with open(audio_path, "wb") as f: f.write(b"AUDIO")

            checkpointer = MemorySaver()
            graph = create_graph(checkpointer=checkpointer)
            config = {"configurable": {"thread_id": "test_scenario_7"}}

            initial_state = prepare_input(
                f"topic: apple, project_dir: {temp_dir}, output_dir: {output_dir}",
                session_id="test_scenario_7"
            )

            plot_text = "# Apple Plot"
            copy_v1_dict = {"caption": "Learn Apple in Cantonese!", "hashtags": ["#Apple"], "markdown_content": "# Apple Copy v1"}
            copy_v2_dict = {"caption": "Learn Apple in Cantonese with Ayla!", "hashtags": ["#Apple", "#ToddlerLearning"], "markdown_content": "# Apple Copy v2"}

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

                # Turn 1: Run to Gate 1
                await graph.ainvoke(initial_state, config=config)

                # Turn 2: Gate 1 Approved -> Run to Gate 2
                graph.update_state(config, {
                    "latest_human_feedback": "approved",
                    "messages": [HumanMessage(content="approved")]
                }, as_node="ideate_package")
                state_turn2 = await graph.ainvoke(None, config=config)

                raw_vid_v1 = state_turn2["raw_video_path"]
                remix_vid_v1 = state_turn2["remixed_video_path"]

                # Turn 3: User requests copy revision
                mock_veo.reset_mock()
                mock_remix.reset_mock()
                mock_agent_call.ainvoke = AsyncMock(side_effect=_mock_agent_call_side_effect(plot_text, copy_v2_dict))
                copy_feedback = "Update the copy caption and add #ToddlerLearning."
                graph.update_state(config, {
                    "latest_human_feedback": copy_feedback,
                    "messages": [HumanMessage(content=copy_feedback)]
                }, as_node="produce_deliverables")

                state_turn3 = await graph.ainvoke(None, config=config)

                # Videos MUST NOT be regenerated (preserved from v1)
                mock_veo.ainvoke.assert_not_called()
                mock_remix.ainvoke.assert_not_called()
                self.assertEqual(state_turn3["raw_video_path"], raw_vid_v1)
                self.assertEqual(state_turn3["remixed_video_path"], remix_vid_v1)

                # Copy MUST be incremented to v2
                expected_v2_copy = os.path.join(output_dir, "apple_copy_v2.md")
                self.assertEqual(state_turn3["copy_path"], expected_v2_copy)
                self.assertTrue(os.path.isfile(expected_v2_copy))

                # Invariants must pass cleanly
                assert_gate2_revision_invariants(state_turn2, state_turn3)

    async def test_scenario_8_gate1_user_says_approve_moves_to_next_node(self):
        """Scenario 8: At HITL Gate 1, user says 'approve', workflow moves to next node (produce_deliverables / Gate 2)."""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, "orange")
            os.makedirs(output_dir, exist_ok=True)

            audio_path = os.path.join(output_dir, "orange.m4a")
            with open(audio_path, "wb") as f: f.write(b"AUDIO")

            checkpointer = MemorySaver()
            graph = create_graph(checkpointer=checkpointer)
            config = {"configurable": {"thread_id": "test_scenario_8"}}

            initial_state = prepare_input(
                f"topic: orange, project_dir: {temp_dir}, output_dir: {output_dir}",
                session_id="test_scenario_8"
            )

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
                mock_agent_call.ainvoke = AsyncMock(side_effect=_mock_agent_call_side_effect("# Orange Plot", {"caption": "Orange"}))

                # Turn 1: Run to Gate 1 (ideate_package)
                await graph.ainvoke(initial_state, config=config)

                # Turn 2: User says "approve" at Gate 1
                graph.update_state(config, {
                    "latest_human_feedback": "approve",
                    "messages": [HumanMessage(content="approve")]
                }, as_node="ideate_package")

                state_turn2 = await graph.ainvoke(None, config=config)

                # Assert that it transitioned past Gate 1 and executed produce_deliverables (Gate 2)
                self.assertEqual(state_turn2.get("gate1_decision"), "approved")
                raw_video = os.path.join(output_dir, "orange_raw_video.mp4")
                remix_video = os.path.join(output_dir, "orange_video.mp4")
                copy_path = os.path.join(output_dir, "orange_copy.md")

                self.assertEqual(state_turn2["raw_video_path"], raw_video)
                self.assertEqual(state_turn2["remixed_video_path"], remix_video)
                self.assertEqual(state_turn2["copy_path"], copy_path)
                self.assertTrue(os.path.isfile(raw_video))
                self.assertTrue(os.path.isfile(remix_video))
                self.assertTrue(os.path.isfile(copy_path))

    async def test_scenario_9_gate2_user_says_approve_completes_the_flow(self):
        """Scenario 9: At HITL Gate 2, user says 'approve', workflow completes the entire flow (transitions to END)."""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, "grape")
            os.makedirs(output_dir, exist_ok=True)

            audio_path = os.path.join(output_dir, "grape.m4a")
            with open(audio_path, "wb") as f: f.write(b"AUDIO")

            checkpointer = MemorySaver()
            graph = create_graph(checkpointer=checkpointer)
            config = {"configurable": {"thread_id": "test_scenario_9"}}

            initial_state = prepare_input(
                f"topic: grape, project_dir: {temp_dir}, output_dir: {output_dir}",
                session_id="test_scenario_9"
            )

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
                mock_agent_call.ainvoke = AsyncMock(side_effect=_mock_agent_call_side_effect("# Grape Plot", {"caption": "Grape"}))

                # Turn 1: Run to Gate 1
                await graph.ainvoke(initial_state, config=config)

                # Turn 2: Gate 1 Approved -> Run to Gate 2
                graph.update_state(config, {
                    "latest_human_feedback": "approve",
                    "messages": [HumanMessage(content="approve")]
                }, as_node="ideate_package")
                state_turn2 = await graph.ainvoke(None, config=config)

                # Turn 3: User says "approve" at Gate 2
                graph.update_state(config, {
                    "latest_human_feedback": "approve",
                    "messages": [HumanMessage(content="approve")]
                }, as_node="produce_deliverables")

                state_turn3 = await graph.ainvoke(None, config=config)

                # Assert that Gate 2 is approved and workflow has completed (no pending next steps)
                self.assertEqual(state_turn3.get("gate2_decision"), "approved")
                snap = graph.get_state(config)
                self.assertEqual(snap.next, ())

    async def test_scenario_10_quota_exceeded_in_veo3_halts_and_preserves_state(self):
        """Scenario 10: When Veo 3 hits a 429 / Quota Exceeded error, workflow halts cleanly and preserves state."""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, "cat")
            os.makedirs(output_dir, exist_ok=True)

            audio_path = os.path.join(output_dir, "cat.m4a")
            with open(audio_path, "wb") as f: f.write(b"AUDIO")

            checkpointer = MemorySaver()
            graph = create_graph(checkpointer=checkpointer)
            config = {"configurable": {"thread_id": "test_scenario_10"}}

            initial_state = prepare_input(
                f"topic: cat, project_dir: {temp_dir}, output_dir: {output_dir}",
                session_id="test_scenario_10"
            )

            with patch("graphs.content_creation.nodes.ideation.generate_image.generate_image") as mock_img, \
                 patch("graphs.content_creation.nodes.production.render_plate.generate_animation_veo3") as mock_veo, \
                 patch("graphs.content_creation.nodes.production.remix_video.remix_video") as mock_remix, \
                 patch("tools.agent_call.agent_call") as mock_agent_call:

                async def fake_img(args):
                    p = args["output_path"]
                    with open(p, "wb") as f: f.write(b"IMG")
                    return f"<payload>{p}</payload>"
                mock_img.ainvoke = AsyncMock(side_effect=fake_img)

                # Veo 3 fails with 429 Quota Exceeded
                mock_veo.ainvoke = AsyncMock(return_value="<errors>Error generating video with Veo: 429 RESOURCE_EXHAUSTED: You exceeded your current quota</errors>")
                mock_remix.ainvoke = AsyncMock()
                mock_agent_call.ainvoke = AsyncMock(side_effect=_mock_agent_call_side_effect("# Cat Plot", {}))

                # Turn 1: Run to Gate 1
                state_turn1 = await graph.ainvoke(initial_state, config=config)
                saved_img = state_turn1["image_path"]
                saved_plot = state_turn1["video_plot_path"]

                # Turn 2: Gate 1 Approved -> Executes produce_deliverables -> Hits Veo 3 429 Quota Error
                graph.update_state(config, {
                    "latest_human_feedback": "approve",
                    "messages": [HumanMessage(content="approve")]
                }, as_node="ideate_package")

                state_turn2 = await graph.ainvoke(None, config=config)

                # Assert that graph halted due to quota exceeded
                self.assertTrue(state_turn2.get("quota_exceeded"))
                self.assertIn("PIPELINE HALTED: API Quota Exceeded / Rate Limit (429)", state_turn2["error_message"])
                self.assertIn("Google Veo 3", state_turn2["error_message"])

                # Verify remix task was NOT called in a wasted loop
                mock_remix.ainvoke.assert_not_called()

                # Verify previous assets are safely preserved
                self.assertEqual(state_turn2["image_path"], saved_img)
                self.assertEqual(state_turn2["video_plot_path"], saved_plot)


if __name__ == "__main__":
    unittest.main()
