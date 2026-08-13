import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import os
import sys
from langchain_core.messages import AIMessage, HumanMessage

# Inject root directory
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from graphs.content_creation.graph import (
    create_graph,
    prepare_input,
    format_output,
    should_continue_image_review,
    should_continue_motion_review,
    _load_character_sheet
)
from core.loaders.graphs_loader import GraphsLoader
from langgraph.checkpoint.memory import MemorySaver

class TestContentCreationGraph(unittest.IsolatedAsyncioTestCase):

    def test_prepare_input(self):
        input_data = prepare_input("Toddler Story: The Little Duck", caller="main-bot", session_id="test_sess_1")
        self.assertEqual(input_data["topic"], "Toddler Story: The Little Duck")
        self.assertEqual(input_data["query"], "<caller>main-bot</caller>\nToddler Story: The Little Duck")
        self.assertEqual(input_data["session_id"], "test_sess_1")
        self.assertIn("messages", input_data)
        self.assertEqual(len(input_data["messages"]), 1)
        self.assertEqual(input_data["messages"][0]["content"], "<caller>main-bot</caller>\nToddler Story: The Little Duck")

    def test_format_output(self):
        state = {
            "messages": [
                AIMessage(content="🎉 Final Content Delivery")
            ]
        }
        self.assertEqual(format_output(state), "🎉 Final Content Delivery")

        state_with_error = {"error_message": "Something went wrong"}
        self.assertEqual(format_output(state_with_error), "Content creation failed: Something went wrong")

    def test_routing_conditions(self):
        # Image review routing
        self.assertEqual(should_continue_image_review({"image_prompts_approved": True}), "generate_images")
        self.assertEqual(should_continue_image_review({"image_prompts_approved": False, "image_review_attempts": 1, "max_image_reviews": 3}), "draft_prompts")
        self.assertEqual(should_continue_image_review({"image_prompts_approved": False, "image_review_attempts": 3, "max_image_reviews": 3}), "generate_images")

        # Motion review routing
        self.assertEqual(should_continue_motion_review({"motion_prompt_approved": True}), "generate_video")
        self.assertEqual(should_continue_motion_review({"motion_prompt_approved": False, "motion_review_attempts": 1, "max_motion_reviews": 3}), "draft_motion")
        self.assertEqual(should_continue_motion_review({"motion_prompt_approved": False, "motion_review_attempts": 3, "max_motion_reviews": 3}), "generate_video")

    def test_load_character_sheet_fallback(self):
        content = _load_character_sheet("/nonexistent/path/sheet.md")
        self.assertIn("Pixar/Disney", content)

    def test_graphs_loader_discovery(self):
        loader = GraphsLoader()
        graph_info = loader.get_graph("content_creation")
        self.assertIsNotNone(graph_info)
        self.assertEqual(graph_info["metadata"].get("name"), "content_creation")
        self.assertIsNotNone(graph_info["create_graph"])

    @patch("core.loaders.agents_loader.AgentsLoader.get_agent")
    @patch("graphs.content_creation.graph.dalle_image_generator")
    @patch("graphs.content_creation.graph.runway_video_animator")
    async def test_graph_interrupt_and_resume(self, mock_runway, mock_dalle, mock_get_agent):
        # Setup mock agents
        mock_creator = MagicMock()
        mock_creator.execute = AsyncMock(side_effect=[
            # 1. Image prompts JSON
            '["Prompt 1", "Prompt 2", "Prompt 3", "Prompt 4", "Prompt 5"]',
            # 2. Motion prompt
            'Gentle camera push in, character waves happily',
            # 3. Draft caption
            'Meet our little adventurer exploring the woods! 🌲✨ #ToddlerTales'
        ])

        mock_editor = MagicMock()
        mock_editor.execute = AsyncMock(side_effect=[
            # 1. Review image prompts
            'VERDICT: APPROVED\nAll prompts maintain character sheet guidelines.',
            # 2. Review motion prompt
            'VERDICT: APPROVED\nMotion is subtle and avoids distortion.',
            # 3. Polished caption
            '🌟 Meet our cutest little adventurer exploring the enchanted woods! 🌲✨ What is your toddler\'s favorite outdoor game? Let us know below! 👇 #ToddlerTales #ParentingMagic #KidsStories'
        ])

        def agent_dispatcher(agent_id):
            if agent_id == "content-creator":
                return mock_creator
            return mock_editor

        mock_get_agent.side_effect = agent_dispatcher

        # Mock DALL-E and Runway tool calls
        mock_dalle.ainvoke = AsyncMock(side_effect=[
            '<dalle_image_generator_response><payload>/assets/scene_1.png</payload><errors>None</errors></dalle_image_generator_response>',
            '<dalle_image_generator_response><payload>/assets/scene_2.png</payload><errors>None</errors></dalle_image_generator_response>',
            '<dalle_image_generator_response><payload>/assets/scene_3.png</payload><errors>None</errors></dalle_image_generator_response>',
            '<dalle_image_generator_response><payload>/assets/scene_4.png</payload><errors>None</errors></dalle_image_generator_response>',
            '<dalle_image_generator_response><payload>/assets/scene_5.png</payload><errors>None</errors></dalle_image_generator_response>'
        ])
        mock_runway.ainvoke = AsyncMock(return_value='<runway_video_animator_response><payload>/assets/animated_story.mp4</payload><errors>None</errors></runway_video_animator_response>')

        # Create checkpointer
        checkpointer = MemorySaver()
        test_graph = create_graph(checkpointer=checkpointer)

        config = {"configurable": {"thread_id": "test_thread_123"}}
        initial_state = prepare_input("Toddler Tales: Exploring the Garden", session_id="test_thread_123")

        # 1. Run graph until interrupt
        await test_graph.ainvoke(initial_state, config=config)

        state = test_graph.get_state(config)
        # Verify paused at select_image
        self.assertEqual(state.next, ("select_image",))
        self.assertEqual(len(state.values["generated_images"]), 5)

        # 2. Resume graph with user selection (picking image 2, i.e., scene 3)
        test_graph.update_state(config, {"selected_image_index": 2})
        final_state = await test_graph.ainvoke(None, config=config)

        # Verify completed
        final_check = test_graph.get_state(config)
        self.assertEqual(final_check.next, ())
        self.assertTrue(final_state["selected_image_path"].endswith("scene_3.png"))
        self.assertTrue(final_state["generated_video_path"].endswith("animated_story.mp4"))
        self.assertIn("Toddler Tales Content Creation Complete", final_state["messages"][-1].content)

if __name__ == "__main__":
    unittest.main()
