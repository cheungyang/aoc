import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import os
import sys

# Inject root directory
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from graphs.content_creation.graph import (
    create_graph,
    prepare_input,
    should_continue_video_plot_audit,
    should_continue_video_qc,
    should_continue_hitl_gate_1,
    should_continue_hitl_gate_2,
    classify_gate1_intent,
    classify_gate2_intent
)
from langgraph.checkpoint.memory import MemorySaver

class TestContentCreationHITLAndRouting(unittest.IsolatedAsyncioTestCase):

    def test_intent_classifiers(self):
        # Gate 1 Intent Classification
        self.assertEqual(classify_gate1_intent("approved"), "approved")
        self.assertEqual(classify_gate1_intent("looks good, go ahead!"), "approved")
        self.assertEqual(classify_gate1_intent("make Ayla's bangs wispy and change hair color"), "revise_image")
        self.assertEqual(classify_gate1_intent("i am looking for ayla in a full fish mascot outfit, instead of wearing a jacket with fish icons."), "revise_image")
        self.assertEqual(classify_gate1_intent("camera motion should be a slow tracking push in"), "revise_plot")
        self.assertEqual(classify_gate1_intent("huh what is this?"), "clarify")

        # Gate 2 Intent Classification
        self.assertEqual(classify_gate2_intent("approved"), "approved")
        self.assertEqual(classify_gate2_intent("finalize delivery"), "approved")
        self.assertEqual(classify_gate2_intent("change the instagram hashtags and add question"), "revise_copy")
        self.assertEqual(classify_gate2_intent("increase audio track volume and boost sound"), "revise_remix")
        self.assertEqual(classify_gate2_intent("change text overlay font size"), "revise_remix")
        self.assertEqual(classify_gate2_intent("re-render the video animation with faster motion"), "revise_video")
        self.assertEqual(classify_gate2_intent("something seems off"), "clarify")

    def test_routing_conditions(self):
        # Dual-Asset QC audit routing
        self.assertEqual(should_continue_video_plot_audit({"video_plot_qc_passed": True}), "hitl_image_and_plot_approval")
        self.assertEqual(should_continue_video_plot_audit({"video_plot_qc_passed": False, "qc_rejection_target": "image", "video_plot_attempts": 1, "max_video_plot_reviews": 3}), "setup_and_generate_image")
        self.assertEqual(should_continue_video_plot_audit({"video_plot_qc_passed": False, "qc_rejection_target": "both", "video_plot_attempts": 1, "max_video_plot_reviews": 3}), "setup_and_generate_image")
        self.assertEqual(should_continue_video_plot_audit({"video_plot_qc_passed": False, "qc_rejection_target": "plot", "video_plot_attempts": 1, "max_video_plot_reviews": 3}), "draft_video_plot")
        self.assertEqual(should_continue_video_plot_audit({"video_plot_qc_passed": False, "video_plot_attempts": 3, "max_video_plot_reviews": 3}), "hitl_image_and_plot_approval")

        # Gate 1 Routing
        self.assertEqual(should_continue_hitl_gate_1({"gate1_decision": "approved"}), "generate_visual_plate")
        self.assertEqual(should_continue_hitl_gate_1({"gate1_decision": "revise_image"}), "setup_and_generate_image")
        self.assertEqual(should_continue_hitl_gate_1({"gate1_decision": "revise_plot"}), "draft_video_plot")
        self.assertEqual(should_continue_hitl_gate_1({"gate1_decision": "clarify"}), "clarify_gate1")

        # Video QC routing
        self.assertEqual(should_continue_video_qc({"video_qc_passed": True}), "draft_and_save_copy")
        self.assertEqual(should_continue_video_qc({"video_qc_passed": False, "video_qc_rejection_target": "visual_plate", "video_qc_attempts": 1, "max_video_reviews": 3}), "generate_visual_plate")
        self.assertEqual(should_continue_video_qc({"video_qc_passed": False, "video_qc_rejection_target": "remix", "video_qc_attempts": 1, "max_video_reviews": 3}), "remix_video")
        self.assertEqual(should_continue_video_qc({"video_qc_passed": False, "video_qc_rejection_target": "both", "video_qc_attempts": 1, "max_video_reviews": 3}), "generate_visual_plate")
        self.assertEqual(should_continue_video_qc({"video_qc_passed": False, "video_qc_attempts": 3, "max_video_reviews": 3}), "hitl_video_qc_failure_intervention")

        # Gate 2 Routing
        self.assertEqual(should_continue_hitl_gate_2({"gate2_decision": "approved"}), "__end__")
        self.assertEqual(should_continue_hitl_gate_2({"gate2_decision": "revise_copy"}), "draft_and_save_copy")
        self.assertEqual(should_continue_hitl_gate_2({"gate2_decision": "revise_remix"}), "remix_video")
        self.assertEqual(should_continue_hitl_gate_2({"gate2_decision": "revise_video"}), "generate_visual_plate")
        self.assertEqual(should_continue_hitl_gate_2({"gate2_decision": "clarify"}), "clarify_gate2")

    async def test_gate1_image_revision_loop(self):
        """Test user requesting image revision at Gate 1: increments image_version and generates image v2."""
        mock_creator = MagicMock()
        mock_creator.execute = AsyncMock(side_effect=[
            # Pass 1: Image prompt v1 & Plot v1
            "A puppy in the garden.",
            "# Video Plot v1\n**Prompt:** > Puppy runs.",
            # Pass 2: Image prompt v2 (after feedback) & Plot v2
            "A puppy with fluffy ears in the garden.",
            "# Video Plot v2\n**Prompt:** > Fluffy puppy runs.",
        ])

        mock_editor = MagicMock()
        mock_editor.execute = AsyncMock(side_effect=[
            # Audit plot v1
            "VERDICT: APPROVED",
            # Audit plot v2
            "VERDICT: APPROVED"
        ])

        def agent_dispatcher(agent_id):
            return mock_creator if agent_id == "content-creator" else mock_editor

        with patch("core.loaders.agents_loader.AgentsLoader.get_agent", side_effect=agent_dispatcher), \
             patch("graphs.content_creation.nodes.generate_image") as mock_img, \
             patch("graphs.content_creation.nodes.generate_animation_veo3") as mock_anim, \
             patch("graphs.content_creation.nodes.remix_video") as mock_remix, \
             patch("graphs.content_creation.nodes.extract_video_frames") as mock_frames:

            mock_img.ainvoke = AsyncMock(side_effect=lambda args: f"<generate_image_response><payload>{args.get('output_path', 'assets/puppy.jpg')}</payload><errors>None</errors></generate_image_response>")
            mock_anim.ainvoke = AsyncMock(return_value='<generate_animation_veo3_response><payload>assets/puppy/puppy_raw_video.mp4</payload><errors>None</errors></generate_animation_veo3_response>')
            mock_remix.ainvoke = AsyncMock(return_value='<remix_video_response><payload>assets/puppy/puppy_video.mp4</payload><errors>None</errors></remix_video_response>')
            mock_frames.ainvoke = AsyncMock(return_value='<extract_video_frames_response><payload>f1.jpg\nf2.jpg</payload><errors>None</errors></extract_video_frames_response>')

            import sys
            import graphs.content_creation.graph as current_graph_mod
            mod = sys.modules.get("graphs.content_creation.graph", current_graph_mod)
            current_create_graph = getattr(mod, "create_graph")
            current_prepare_input = getattr(mod, "prepare_input")

            checkpointer = MemorySaver()
            test_graph = current_create_graph(checkpointer=checkpointer)

            config = {"configurable": {"thread_id": "test_thread_rev_image_1"}}
            initial_state = current_prepare_input(
                "topic: puppy, project_dir: pkm/wiki/software/toddler-tales",
                session_id="test_sess_rev1"
            )

            # Phase 1: Run to Gate 1 (v1)
            await test_graph.ainvoke(initial_state, config=config)
            s1 = test_graph.get_state(config)
            self.assertEqual(s1.next, ("hitl_image_and_plot_approval",))
            self.assertEqual(s1.values["image_version"], 1)

            # Phase 2: User provides feedback to change character / image
            await test_graph.aupdate_state(config, {"latest_human_feedback": "make puppy ears extra fluffy in the image"})
            await test_graph.ainvoke(None, config=config)

            s2 = test_graph.get_state(config)
            self.assertEqual(s2.next, ("hitl_image_and_plot_approval",))
            self.assertEqual(s2.values["image_version"], 2)
            self.assertTrue(s2.values["image_path"].endswith("puppy_image_v2.jpg"))

    async def test_gate1_plot_revision_loop(self):
        """Test user requesting plot revision at Gate 1: increments video_plot_version without re-generating image."""
        mock_creator = MagicMock()
        mock_creator.execute = AsyncMock(side_effect=[
            # Pass 1: Image prompt v1 & Plot v1
            "A golden puppy in the park.",
            "# Video Plot v1\n**Prompt:** > Fast running shot.",
            # Pass 2: Plot v2 only
            "# Video Plot v2\n**Prompt:** > Slow gentle camera zoom.",
        ])

        mock_editor = MagicMock()
        mock_editor.execute = AsyncMock(side_effect=[
            # Audit plot v1
            "VERDICT: APPROVED",
            # Audit plot v2
            "VERDICT: APPROVED"
        ])

        def agent_dispatcher(agent_id):
            return mock_creator if agent_id == "content-creator" else mock_editor

        with patch("core.loaders.agents_loader.AgentsLoader.get_agent", side_effect=agent_dispatcher), \
             patch("graphs.content_creation.nodes.generate_image") as mock_img, \
             patch("graphs.content_creation.nodes.generate_animation_veo3") as mock_anim, \
             patch("graphs.content_creation.nodes.remix_video") as mock_remix, \
             patch("graphs.content_creation.nodes.extract_video_frames") as mock_frames:

            mock_img.ainvoke = AsyncMock(return_value='<generate_image_response><payload>assets/puppy_v1.jpg</payload><errors>None</errors></generate_image_response>')
            mock_anim.ainvoke = AsyncMock(return_value='<generate_animation_veo3_response><payload>assets/puppy/puppy_raw_video.mp4</payload><errors>None</errors></generate_animation_veo3_response>')
            mock_remix.ainvoke = AsyncMock(return_value='<remix_video_response><payload>assets/puppy.mp4</payload><errors>None</errors></remix_video_response>')
            mock_frames.ainvoke = AsyncMock(return_value='<extract_video_frames_response><payload>f1.jpg\nf2.jpg</payload><errors>None</errors></extract_video_frames_response>')

            import sys
            import graphs.content_creation.graph as current_graph_mod
            mod = sys.modules.get("graphs.content_creation.graph", current_graph_mod)
            current_create_graph = getattr(mod, "create_graph")
            current_prepare_input = getattr(mod, "prepare_input")

            checkpointer = MemorySaver()
            test_graph = current_create_graph(checkpointer=checkpointer)

            config = {"configurable": {"thread_id": "test_thread_rev_plot_1"}}
            initial_state = current_prepare_input(
                "topic: puppy, project_dir: pkm/wiki/software/toddler-tales",
                session_id="test_sess_rev2"
            )

            # Phase 1: Run to Gate 1
            await test_graph.ainvoke(initial_state, config=config)
            s1 = test_graph.get_state(config)
            self.assertEqual(s1.next, ("hitl_image_and_plot_approval",))
            self.assertEqual(s1.values["image_version"], 1)
            self.assertEqual(s1.values["video_plot_version"], 1)

            # Phase 2: User provides feedback targeting motion / plot
            await test_graph.aupdate_state(config, {"latest_human_feedback": "camera motion should be a slow gentle zoom"})
            await test_graph.ainvoke(None, config=config)

            s2 = test_graph.get_state(config)
            self.assertEqual(s2.next, ("hitl_image_and_plot_approval",))
            self.assertEqual(s2.values["image_version"], 1)  # Image version unchanged!
            self.assertEqual(s2.values["video_plot_version"], 2) # Plot version incremented!
            self.assertTrue(s2.values["video_plot_path"].endswith("puppy_video_plot_v2.md"))

    async def test_gate1_clarification_loop(self):
        """Test ambiguous feedback at Gate 1: triggers clarification and keeps graph paused at Gate 1."""
        mock_creator = MagicMock()
        mock_creator.execute = AsyncMock(side_effect=[
            "A playful puppy in garden.",
            "# Video Plot\n**Prompt:** > Runs."
        ])
        mock_editor = MagicMock()
        mock_editor.execute = AsyncMock(return_value="VERDICT: APPROVED")

        def agent_dispatcher(agent_id):
            return mock_creator if agent_id == "content-creator" else mock_editor

        with patch("core.loaders.agents_loader.AgentsLoader.get_agent", side_effect=agent_dispatcher), \
             patch("graphs.content_creation.nodes.generate_image") as mock_img, \
             patch("graphs.content_creation.nodes.generate_animation_veo3") as mock_anim, \
             patch("graphs.content_creation.nodes.remix_video") as mock_remix, \
             patch("graphs.content_creation.nodes.extract_video_frames") as mock_frames:

            mock_img.ainvoke = AsyncMock(return_value='<generate_image_response><payload>assets/puppy.jpg</payload></generate_image_response>')
            mock_anim.ainvoke = AsyncMock(return_value='<generate_animation_veo3_response><payload>assets/puppy/puppy_raw_video.mp4</payload></generate_animation_veo3_response>')
            mock_remix.ainvoke = AsyncMock(return_value='<remix_video_response><payload>assets/puppy.mp4</payload></remix_video_response>')
            mock_frames.ainvoke = AsyncMock(return_value='<extract_video_frames_response><payload>f1.jpg</payload></extract_video_frames_response>')

            import sys
            import graphs.content_creation.graph as current_graph_mod
            mod = sys.modules.get("graphs.content_creation.graph", current_graph_mod)
            current_create_graph = getattr(mod, "create_graph")
            current_prepare_input = getattr(mod, "prepare_input")

            checkpointer = MemorySaver()
            test_graph = current_create_graph(checkpointer=checkpointer)

            config = {"configurable": {"thread_id": "test_thread_clarify_1"}}
            initial_state = current_prepare_input(
                "topic: puppy, project_dir: pkm/wiki/software/toddler-tales",
                session_id="test_sess_clarify"
            )

            # Phase 1: Run to Gate 1
            await test_graph.ainvoke(initial_state, config=config)
            s1 = test_graph.get_state(config)
            self.assertEqual(s1.next, ("hitl_image_and_plot_approval",))

            # Phase 2: Ambiguous feedback
            await test_graph.aupdate_state(config, {"latest_human_feedback": "hmm not sure what to think"})
            res = await test_graph.ainvoke(None, config=config)

            s2 = test_graph.get_state(config)
            # Clarify loops back and pauses at Gate 1
            self.assertEqual(s2.next, ("hitl_image_and_plot_approval",))
            self.assertIn("HITL Gate 1 Clarification Needed", s2.values["clarification_question"])

    async def test_gate2_copy_revision_loop(self):
        """Test copy revision loop at Gate 2: increments copy_version and generates copy v2."""
        mock_creator = MagicMock()
        mock_creator.execute = AsyncMock(side_effect=[
            # Pass 1: Image, Plot, Copy v1
            "Puppy in garden.",
            "# Video Plot\n**Prompt:** > Puppy runs.",
            "Draft caption v1 #Puppy",
            # Pass 2: Copy v2 after feedback
            "Draft caption v2 with question and extra hashtags #Puppy #ToddlerAdventures"
        ])
        mock_editor = MagicMock()
        mock_editor.execute = AsyncMock(side_effect=[
            # Audit plot
            "VERDICT: APPROVED",
            # QC frames
            "VERDICT: APPROVED",
            # Polish copy v1
            "Polished copy v1 🐶",
            # Polish copy v2
            "Polished copy v2 🐶 What is your puppy's name? Tell us below! #Puppy #ToddlerAdventures"
        ])

        def agent_dispatcher(agent_id):
            return mock_creator if agent_id == "content-creator" else mock_editor

        def fake_anim(args):
            p = args.get('output_path', 'puppy_raw_video.mp4')
            os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
            with open(p, "wb") as f:
                f.write(b"dummy_video_bytes")
            return f"<generate_animation_veo3_response><payload>{p}</payload><errors>None</errors></generate_animation_veo3_response>"

        def fake_remix(args):
            p = args.get('output_path', 'puppy_video.mp4')
            os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
            with open(p, "wb") as f:
                f.write(b"dummy_video_bytes")
            return f"<remix_video_response><payload>{p}</payload><errors>None</errors></remix_video_response>"

        with patch("core.loaders.agents_loader.AgentsLoader.get_agent", side_effect=agent_dispatcher), \
             patch("graphs.content_creation.nodes.generate_image") as mock_img, \
             patch("graphs.content_creation.nodes.generate_animation_veo3") as mock_anim, \
             patch("graphs.content_creation.nodes.remix_video") as mock_remix, \
             patch("graphs.content_creation.nodes._has_audio_stream", return_value=True), \
             patch("graphs.content_creation.nodes.extract_video_frames") as mock_frames:

            mock_img.ainvoke = AsyncMock(side_effect=lambda args: f"<generate_image_response><payload>{args.get('output_path')}</payload></generate_image_response>")
            mock_anim.ainvoke = AsyncMock(side_effect=fake_anim)
            mock_remix.ainvoke = AsyncMock(side_effect=fake_remix)
            mock_frames.ainvoke = AsyncMock(return_value='<extract_video_frames_response><payload>f1.jpg\nf2.jpg</payload></extract_video_frames_response>')

            import sys
            import graphs.content_creation.graph as current_graph_mod
            mod = sys.modules.get("graphs.content_creation.graph", current_graph_mod)
            current_create_graph = getattr(mod, "create_graph")
            current_prepare_input = getattr(mod, "prepare_input")

            checkpointer = MemorySaver()
            test_graph = current_create_graph(checkpointer=checkpointer)

            config = {"configurable": {"thread_id": "test_thread_rev_copy_1"}}
            initial_state = current_prepare_input(
                "topic: puppy, project_dir: pkm/wiki/software/toddler-tales",
                session_id="test_sess_rev_copy"
            )

            # Phase 1: Run to Gate 1
            await test_graph.ainvoke(initial_state, config=config)

            # Phase 2: Approve Gate 1 -> runs to Gate 2 (Copy v1)
            await test_graph.aupdate_state(config, {"latest_human_feedback": "approved"})
            await test_graph.ainvoke(None, config=config)

            s_gate2 = test_graph.get_state(config)
            self.assertEqual(s_gate2.next, ("hitl_final_package_approval",))
            self.assertEqual(s_gate2.values["copy_version"], 1)

            # Phase 3: Provide copy feedback at Gate 2
            await test_graph.aupdate_state(config, {"latest_human_feedback": "change the copy caption to add a question for parents"})
            await test_graph.ainvoke(None, config=config)

            s_gate2_rev = test_graph.get_state(config)
            self.assertEqual(s_gate2_rev.next, ("hitl_final_package_approval",))
            self.assertEqual(s_gate2_rev.values["copy_version"], 2)
            self.assertTrue(s_gate2_rev.values["copy_path"].endswith("puppy_copy_v2.md"))
            self.assertIn("What is your puppy's name?", s_gate2_rev.values["copy_text"])

    async def test_gate2_remix_revision_loop(self):
        """Test remix (text/audio) revision loop at Gate 2: increments video_version and routes to remix_video without re-generating visual plate."""
        mock_creator = MagicMock()
        mock_creator.execute = AsyncMock(side_effect=[
            # Pass 1: Image, Plot, Copy v1
            "Puppy in garden.",
            "# Video Plot\n**Prompt:** > Puppy runs.",
            "Draft caption v1 #Puppy",
            # Pass 2: Copy v2 after remix revision
            "Draft caption v2 #Puppy"
        ])
        mock_editor = MagicMock()
        mock_editor.execute = AsyncMock(side_effect=[
            # Audit plot
            "VERDICT: APPROVED",
            # QC frames v1
            "VERDICT: APPROVED",
            # Polish copy v1
            "Polished copy v1 🐶",
            # QC frames v2 (after remix)
            "VERDICT: APPROVED",
            # Polish copy v2
            "Polished copy v2 🐶"
        ])

        def agent_dispatcher(agent_id):
            return mock_creator if agent_id == "content-creator" else mock_editor

        def fake_anim(args):
            p = args.get('output_path', 'puppy_raw_video.mp4')
            os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
            with open(p, "wb") as f:
                f.write(b"dummy_video_bytes")
            return f"<generate_animation_veo3_response><payload>{p}</payload><errors>None</errors></generate_animation_veo3_response>"

        def fake_remix(args):
            p = args.get('output_path', 'puppy_video.mp4')
            os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
            with open(p, "wb") as f:
                f.write(b"dummy_video_bytes")
            return f"<remix_video_response><payload>{p}</payload><errors>None</errors></remix_video_response>"

        with patch("core.loaders.agents_loader.AgentsLoader.get_agent", side_effect=agent_dispatcher), \
             patch("graphs.content_creation.nodes.generate_image") as mock_img, \
             patch("graphs.content_creation.nodes.generate_animation_veo3") as mock_anim, \
             patch("graphs.content_creation.nodes.remix_video") as mock_remix, \
             patch("graphs.content_creation.nodes._has_audio_stream", return_value=True), \
             patch("graphs.content_creation.nodes.extract_video_frames") as mock_frames:

            mock_img.ainvoke = AsyncMock(side_effect=lambda args: f"<generate_image_response><payload>{args.get('output_path')}</payload></generate_image_response>")
            mock_anim.ainvoke = AsyncMock(side_effect=fake_anim)
            mock_remix.ainvoke = AsyncMock(side_effect=fake_remix)
            mock_frames.ainvoke = AsyncMock(return_value='<extract_video_frames_response><payload>f1.jpg\nf2.jpg</payload></extract_video_frames_response>')

            import sys
            import graphs.content_creation.graph as current_graph_mod
            mod = sys.modules.get("graphs.content_creation.graph", current_graph_mod)
            current_create_graph = getattr(mod, "create_graph")
            current_prepare_input = getattr(mod, "prepare_input")

            checkpointer = MemorySaver()
            test_graph = current_create_graph(checkpointer=checkpointer)

            config = {"configurable": {"thread_id": "test_thread_rev_remix_1"}}
            initial_state = current_prepare_input(
                "topic: puppy, project_dir: pkm/wiki/software/toddler-tales",
                session_id="test_sess_rev_remix"
            )

            # Phase 1: Run to Gate 1
            await test_graph.ainvoke(initial_state, config=config)

            # Phase 2: Approve Gate 1 -> runs to Gate 2 (Video v1)
            await test_graph.aupdate_state(config, {"latest_human_feedback": "approved"})
            await test_graph.ainvoke(None, config=config)

            s_gate2 = test_graph.get_state(config)
            self.assertEqual(s_gate2.next, ("hitl_final_package_approval",))
            self.assertEqual(s_gate2.values["video_version"], 1)

            # Phase 3: Provide text overlay / audio feedback at Gate 2
            await test_graph.aupdate_state(config, {"latest_human_feedback": "please adjust the text overlay font size and audio volume"})
            await test_graph.ainvoke(None, config=config)

            s_gate2_rev = test_graph.get_state(config)
            self.assertEqual(s_gate2_rev.next, ("hitl_final_package_approval",))
            # Animation was NOT called a second time
            self.assertEqual(mock_anim.ainvoke.call_count, 1)
            # Remix WAS called twice
            self.assertEqual(mock_remix.ainvoke.call_count, 2)
            self.assertEqual(s_gate2_rev.values["video_version"], 2)
            self.assertTrue(s_gate2_rev.values["video_path"].endswith("puppy_video_v2.mp4"))


if __name__ == "__main__":
    unittest.main()
