import unittest
from langgraph.graph import END
from graphs.content_creation.routers import should_continue_setup, should_continue_video_qc

class TestRouters(unittest.TestCase):
    def test_should_continue_setup(self):
        self.assertEqual(should_continue_setup({"error_message": "foo"}), END)
        self.assertEqual(should_continue_setup({}), "draft_video_plot")

    def test_should_continue_video_qc(self):
        # If passed, return END
        self.assertEqual(should_continue_video_qc({"video_qc_passed": True}), END)
        
        # If max attempts reached, intervene
        self.assertEqual(should_continue_video_qc({"video_qc_passed": False, "video_qc_attempts": 3, "max_video_reviews": 3}), "hitl_video_qc_failure_intervention")
        
        # If rejected target is remix, go to remix
        self.assertEqual(should_continue_video_qc({"video_qc_passed": False, "video_qc_attempts": 1, "max_video_reviews": 3, "video_qc_rejection_target": "remix"}), "remix_video")
        
        # If rejected target is visual_plate, go to generate_visual_plate
        self.assertEqual(should_continue_video_qc({"video_qc_passed": False, "video_qc_attempts": 1, "max_video_reviews": 3, "video_qc_rejection_target": "visual_plate"}), "generate_visual_plate")

if __name__ == "__main__":
    unittest.main()
