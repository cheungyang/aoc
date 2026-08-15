import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import os
import sys
import shutil

# Inject root directory
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from langgraph.checkpoint.memory import MemorySaver

class TestContentCreationPipelineExecution(unittest.IsolatedAsyncioTestCase):

    async def test_dual_asset_qc_image_failure_loop(self):
        """Test Brand Editor rejecting base image at Node 3: routes back to Node 1 with incremented image_version."""
        mock_creator = MagicMock()
        mock_creator.execute = AsyncMock(side_effect=[
            # Pass 1: Image v1 prompt & Plot v1
            "Puppy in garden.",
            "# Video Plot v1\n**Prompt:** > Puppy runs.",
            # Pass 2 (after QC image rejection): Image v2 prompt & Plot v2
            "Fluffy golden puppy with curly ears.",
            "# Video Plot v2\n**Prompt:** > Fluffy puppy runs.",
        ])

        mock_editor = MagicMock()
        mock_editor.execute = AsyncMock(side_effect=[
            # Audit 1: Reject base image
            "VERDICT: REJECTED TARGET: IMAGE\nBase image lacks curly hair specified in instructions.",
            # Audit 2: Approve
            "VERDICT: APPROVED\nBoth base image and plot meet all QC standards."
        ])

        def agent_dispatcher(agent_id):
            return mock_creator if agent_id == "content-creator" else mock_editor

        with patch("core.loaders.agents_loader.AgentsLoader.get_agent", side_effect=agent_dispatcher), \
             patch("graphs.content_creation.nodes.generate_image") as mock_img, \
             patch("graphs.content_creation.nodes.generate_animation_runway") as mock_anim, \
             patch("graphs.content_creation.nodes.extract_video_frames") as mock_frames:

            mock_img.ainvoke = AsyncMock(side_effect=lambda args: f"<generate_image_response><payload>{args.get('output_path')}</payload></generate_image_response>")
            mock_anim.ainvoke = AsyncMock(return_value='<generate_animation_runway_response><payload>assets/puppy.mp4</payload></generate_animation_runway_response>')
            mock_frames.ainvoke = AsyncMock(return_value='<extract_video_frames_response><payload>f1.jpg\nf2.jpg</payload></extract_video_frames_response>')

            import sys
            import graphs.content_creation.graph as current_graph_mod
            mod = sys.modules.get("graphs.content_creation.graph", current_graph_mod)
            current_create_graph = getattr(mod, "create_graph")
            current_prepare_input = getattr(mod, "prepare_input")

            checkpointer = MemorySaver()
            test_graph = current_create_graph(checkpointer=checkpointer)

            config = {"configurable": {"thread_id": "test_thread_qc_img_fail"}}
            initial_state = current_prepare_input(
                "topic: puppy, project_dir: pkm/wiki/software/toddler-tales",
                session_id="test_sess_qc_img"
            )

            # Phase 1: Run graph -> Node 1 -> Node 2 -> Node 3 (rejects image) -> Node 1 (v2) -> Node 2 -> Node 3 (approves) -> pauses at Gate 1
            await test_graph.ainvoke(initial_state, config=config)

            s = test_graph.get_state(config)
            self.assertEqual(s.next, ("hitl_image_and_plot_approval",))
            self.assertEqual(s.values["image_version"], 2)
            self.assertTrue(s.values["image_path"].endswith("puppy_image_v2.jpg"))
            self.assertTrue(s.values["video_plot_qc_passed"])

    async def test_graph_two_hitl_gates_and_resume(self):
        """Test full happy path traversing both HITL Gate 1 and HITL Gate 2."""
        mock_creator = MagicMock()
        mock_creator.execute = AsyncMock(side_effect=[
            # 1. Image prompt (Node 1)
            "A playful golden puppy in a sunny garden, studio lighting.",
            # 2. Video plot markdown (Node 2)
            (
                "# Video Plot: Puppy\n\n"
                "## 🎬 Motion Prompt\n"
                "> A golden puppy running forward towards the camera with joyful energy. Smooth tracking shot.\n\n"
                "## Notes\n"
                "- Keep lighting consistent.\n"
            ),
            # 3. Draft caption (Node 6)
            "Meet our new little friend exploring the sunny garden! 🐶✨ #PuppyAdventures"
        ])

        mock_editor = MagicMock()
        mock_editor.execute = AsyncMock(side_effect=[
            # 1. Audit video plot (Node 3)
            "VERDICT: APPROVED\nVideo plot satisfies all criteria in the QC playbook.",
            # 2. QC extracted frames (Node 5)
            "VERDICT: APPROVED\nAll keyframes maintain character consistency and visual fidelity.",
            # 3. Polish caption (Node 6)
            "🐶 Meet our cutest little explorer enjoying the sunshine! 🌟 What's your favorite puppy moment? Tell us below! 👇 #PuppyAdventures #StoryTime"
        ])

        def agent_dispatcher(agent_id):
            if agent_id == "content-creator":
                return mock_creator
            return mock_editor

        def fake_anim(args):
            p = args.get('output_path', 'puppy_video.mp4')
            os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
            with open(p, "wb") as f:
                f.write(b"dummy_video_bytes")
            return f"<generate_animation_runway_response><payload>{p}</payload><errors>None</errors></generate_animation_runway_response>"

        with patch("core.loaders.agents_loader.AgentsLoader.get_agent", side_effect=agent_dispatcher), \
             patch("graphs.content_creation.nodes.generate_image") as mock_img, \
             patch("graphs.content_creation.nodes.generate_animation_runway") as mock_anim, \
             patch("graphs.content_creation.nodes.extract_video_frames") as mock_frames:

            mock_img.ainvoke = AsyncMock(
                return_value='<generate_image_response><payload>assets/puppy/puppy_image.jpg</payload><errors>None</errors></generate_image_response>'
            )
            mock_anim.ainvoke = AsyncMock(side_effect=fake_anim)
            mock_frames.ainvoke = AsyncMock(
                return_value='<extract_video_frames_response><payload>assets/puppy/frames/frame_001_1_000s.jpg\nassets/puppy/frames/frame_002_2_500s.jpg\nassets/puppy/frames/frame_003_4_000s.jpg</payload><errors>None</errors></extract_video_frames_response>'
            )

            import sys
            import graphs.content_creation.graph as current_graph_mod
            mod = sys.modules.get("graphs.content_creation.graph", current_graph_mod)
            current_create_graph = getattr(mod, "create_graph")
            current_prepare_input = getattr(mod, "prepare_input")

            checkpointer = MemorySaver()
            test_graph = current_create_graph(checkpointer=checkpointer)

            config = {"configurable": {"thread_id": "test_thread_generic_123"}}
            initial_state = current_prepare_input(
                "topic: puppy, project_dir: pkm/wiki/software/toddler-tales",
                session_id="test_thread_generic_123"
            )

            # -------------------------------------------------------------
            # Phase 1: Run graph until HITL Gate 1
            # -------------------------------------------------------------
            await test_graph.ainvoke(initial_state, config=config)

            state_gate1 = test_graph.get_state(config)
            self.assertEqual(state_gate1.next, ("hitl_image_and_plot_approval",))
            self.assertTrue(state_gate1.values["video_plot_qc_passed"])
            self.assertTrue(state_gate1.values["image_path"].endswith("puppy_image.jpg"))
            self.assertTrue(state_gate1.values["video_plot_path"].endswith("puppy_video_plot.md"))

            # -------------------------------------------------------------
            # Phase 2: Resume from HITL Gate 1 with 'approved' -> runs until HITL Gate 2
            # -------------------------------------------------------------
            await test_graph.aupdate_state(config, {"latest_human_feedback": "approved"})
            await test_graph.ainvoke(None, config=config)

            state_gate2 = test_graph.get_state(config)
            self.assertEqual(state_gate2.next, ("hitl_final_package_approval",))
            self.assertTrue(state_gate2.values["video_qc_passed"])
            self.assertTrue(state_gate2.values["video_path"].endswith("puppy_video.mp4"))
            self.assertEqual(len(state_gate2.values["extracted_frames"]), 3)
            self.assertIn("final_package", state_gate2.values)
            self.assertEqual(state_gate2.values["final_package"]["topic"], "puppy")

            # -------------------------------------------------------------
            # Phase 3: Resume from HITL Gate 2 with 'approved' -> reaches END
            # -------------------------------------------------------------
            await test_graph.aupdate_state(config, {"latest_human_feedback": "approved"})
            final_state = await test_graph.ainvoke(None, config=config)

            final_check = test_graph.get_state(config)
            self.assertEqual(final_check.next, ())
            self.assertIn("HITL GATE 2: Final Package Review & Approval", final_state["messages"][-1].content)
            self.assertIn("puppy_video.mp4", final_state["messages"][-1].content)

            # Verify execution_log.md is written
            log_path = state_gate1.values["execution_log_path"]
            if os.path.exists(log_path):
                with open(log_path, "r", encoding="utf-8") as f:
                    log_content = f.read()
                self.assertIn("Content Creation Trajectory", log_content)
                self.assertIn("Base Image Generation", log_content)
                self.assertIn("Dual-Asset QC Audit", log_content)
                # Cleanup test directory
                shutil.rmtree(state_gate1.values["output_dir"], ignore_errors=True)

    async def test_video_keyframe_qc_rejection_loops_to_generate_visual_plate(self):
        """Test that Node 5 Brand Editor rejection loops back to Node 4 (generate_visual_plate) and does NOT proceed to Gate 2 unconditionally."""
        mock_creator = MagicMock()
        mock_creator.execute = AsyncMock(side_effect=[
            # 1. Image prompt (Node 1)
            "A puppy.",
            # 2. Video plot (Node 2)
            "# Video Plot\n**Prompt:** > Puppy runs.",
            # 3. Draft copy (Node 6)
            "Puppy caption #Dog"
        ])

        mock_editor = MagicMock()
        mock_editor.execute = AsyncMock(side_effect=[
            # 1. Audit video plot (Node 3) -> Approved
            "VERDICT: APPROVED\nPlot is good.",
            # 2. Keyframe QC Attempt 1 (Node 5) -> REJECTED
            "VERDICT: REJECTED\nMotion blur and jitter in frame 2 violates QC standard.",
            # 3. Keyframe QC Attempt 2 (Node 5) -> APPROVED
            "VERDICT: APPROVED\nRe-rendered video keyframes are clean.",
            # 4. Polish copy (Node 6)
            "Puppy caption polished #Dog"
        ])

        def agent_dispatcher(agent_id):
            return mock_creator if agent_id == "content-creator" else mock_editor

        def fake_anim(args):
            p = args.get('output_path', 'puppy_video.mp4')
            os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
            with open(p, "wb") as f:
                f.write(b"dummy_video_bytes")
            return f"<generate_animation_runway_response><payload>{p}</payload><errors>None</errors></generate_animation_runway_response>"

        with patch("core.loaders.agents_loader.AgentsLoader.get_agent", side_effect=agent_dispatcher), \
             patch("graphs.content_creation.nodes.generate_image") as mock_img, \
             patch("graphs.content_creation.nodes.generate_animation_runway") as mock_anim, \
             patch("graphs.content_creation.nodes.extract_video_frames") as mock_frames:

            mock_img.ainvoke = AsyncMock(side_effect=lambda args: f"<generate_image_response><payload>{args.get('output_path')}</payload></generate_image_response>")
            mock_anim.ainvoke = AsyncMock(side_effect=fake_anim)
            mock_frames.ainvoke = AsyncMock(return_value='<extract_video_frames_response><payload>f1.jpg\nf2.jpg</payload></extract_video_frames_response>')

            import sys
            import graphs.content_creation.graph as current_graph_mod
            mod = sys.modules.get("graphs.content_creation.graph", current_graph_mod)
            current_create_graph = getattr(mod, "create_graph")
            current_prepare_input = getattr(mod, "prepare_input")

            checkpointer = MemorySaver()
            test_graph = current_create_graph(checkpointer=checkpointer)

            config = {"configurable": {"thread_id": "test_thread_video_qc_rejection"}}
            initial_state = current_prepare_input(
                "topic: puppy, project_dir: pkm/wiki/software/toddler-tales",
                session_id="test_sess_video_qc_fail"
            )

            # Phase 1: Run to Gate 1
            await test_graph.ainvoke(initial_state, config=config)
            s1 = test_graph.get_state(config)
            self.assertEqual(s1.next, ("hitl_image_and_plot_approval",))

            # Phase 2: Approve Gate 1 -> Video Gen (v1) -> QC 1 (Rejects) -> Video Gen (v2) -> QC 2 (Approves) -> Copy -> Gate 2
            await test_graph.aupdate_state(config, {"latest_human_feedback": "approved"})
            await test_graph.ainvoke(None, config=config)

            s2 = test_graph.get_state(config)
            self.assertEqual(s2.next, ("hitl_final_package_approval",))
            self.assertEqual(s2.values["video_qc_attempts"], 2)
            self.assertEqual(s2.values["video_version"], 2)
            self.assertTrue(s2.values["video_qc_passed"])
            self.assertTrue(s2.values["video_path"].endswith("puppy_video_v2.mp4"))

    async def test_video_qc_retry_exhaustion_hard_blocks_at_intervention(self):
        """Test that exhausting max video QC retries hard-blocks at hitl_video_qc_failure_intervention and does NOT advance to copy or Gate 2."""
        mock_creator = MagicMock()
        mock_creator.execute = AsyncMock(side_effect=[
            "A puppy.",
            "# Video Plot\n**Prompt:** > Puppy runs."
        ])

        mock_editor = MagicMock()
        mock_editor.execute = AsyncMock(return_value="VERDICT: APPROVED\nPlot is good.")

        def agent_dispatcher(agent_id):
            return mock_creator if agent_id == "content-creator" else mock_editor

        with patch("core.loaders.agents_loader.AgentsLoader.get_agent", side_effect=agent_dispatcher), \
             patch("graphs.content_creation.nodes.generate_image") as mock_img, \
             patch("graphs.content_creation.nodes.generate_animation_runway") as mock_anim, \
             patch("graphs.content_creation.nodes.extract_video_frames") as mock_frames:

            mock_img.ainvoke = AsyncMock(side_effect=lambda args: f"<generate_image_response><payload>{args.get('output_path')}</payload></generate_image_response>")
            # Simulate failure where no video is written to disk
            mock_anim.ainvoke = AsyncMock(return_value='<generate_animation_runway_response><payload></payload><errors>Runway API timeout</errors></generate_animation_runway_response>')
            mock_frames.ainvoke = AsyncMock(return_value='<extract_video_frames_response><payload></payload><errors>File not found</errors></extract_video_frames_response>')

            import sys
            import graphs.content_creation.graph as current_graph_mod
            mod = sys.modules.get("graphs.content_creation.graph", current_graph_mod)
            current_create_graph = getattr(mod, "create_graph")
            current_prepare_input = getattr(mod, "prepare_input")

            checkpointer = MemorySaver()
            test_graph = current_create_graph(checkpointer=checkpointer)

            config = {"configurable": {"thread_id": "test_thread_video_qc_exhaustion"}}
            initial_state = current_prepare_input(
                "topic: puppy, project_dir: pkm/wiki/software/toddler-tales",
                session_id="test_sess_video_qc_exhaust"
            )

            # Phase 1: Run to Gate 1
            await test_graph.ainvoke(initial_state, config=config)
            s1 = test_graph.get_state(config)
            self.assertEqual(s1.next, ("hitl_image_and_plot_approval",))

            # Phase 2: Approve Gate 1 -> Generation fails across max reviews -> Hard blocks at Intervention
            await test_graph.aupdate_state(config, {"latest_human_feedback": "approved"})
            await test_graph.ainvoke(None, config=config)

            s2 = test_graph.get_state(config)
            self.assertEqual(s2.next, ("hitl_video_qc_failure_intervention",))
            self.assertFalse(s2.values["video_qc_passed"])
            self.assertEqual(s2.values["video_qc_attempts"], 3)
            current_format_output = getattr(mod, "format_output")
            formatted = current_format_output(s2.values)
            self.assertIn("HITL INTERVENTION REQUIRED", formatted)

    async def test_missing_project_and_output_dir_halts_graph(self):
        """Test that running graph without project_dir or output_dir immediately halts and returns error message."""
        import graphs.content_creation.graph as current_graph_mod
        mod = sys.modules.get("graphs.content_creation.graph", current_graph_mod)
        current_create_graph = getattr(mod, "create_graph")
        current_prepare_input = getattr(mod, "prepare_input")
        current_format_output = getattr(mod, "format_output")

        checkpointer = MemorySaver()
        test_graph = current_create_graph(checkpointer=checkpointer)

        config = {"configurable": {"thread_id": "test_thread_missing_paths"}}
        initial_state = current_prepare_input("generate content for horse", session_id="test_sess_missing")

        final_state = await test_graph.ainvoke(initial_state, config=config)
        s = test_graph.get_state(config)
        self.assertEqual(s.next, ())
        self.assertIn("Missing required project/output path", final_state["error_message"])

        formatted = current_format_output(final_state)
        self.assertIn("Content creation failed: Missing required project/output path", formatted)


if __name__ == "__main__":
    unittest.main()
