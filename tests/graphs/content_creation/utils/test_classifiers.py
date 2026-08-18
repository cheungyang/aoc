import unittest
from graphs.content_creation.utils.classifiers import classify_gate1_intent, classify_gate2_intent

class TestClassifiers(unittest.TestCase):
    def test_classify_gate1_intent(self):
        # Approvals
        self.assertEqual(classify_gate1_intent("approved"), "approved")
        self.assertEqual(classify_gate1_intent("looks good"), "approved")
        self.assertEqual(classify_gate1_intent("lgtm"), "approved")
        self.assertEqual(classify_gate1_intent("proceed"), "approved")
        self.assertEqual(classify_gate1_intent("yes"), "approved")

        # Plot-specific
        self.assertEqual(classify_gate1_intent("change camera movement in the plot"), "revise_plot")
        self.assertEqual(classify_gate1_intent("revise the motion prompt script"), "revise_plot")

        # General / Costume / Image revisions
        feedback = "I want Ayla to be wearing a costume of the target animal and behave like that targeted animal, instead of having the target animal surrounding her."
        self.assertEqual(classify_gate1_intent(feedback), "revise_image")
        self.assertEqual(classify_gate1_intent("make the outfit purple and add cat ears"), "revise_image")

    def test_classify_gate2_intent(self):
        self.assertEqual(classify_gate2_intent("approved"), "approved")
        self.assertEqual(classify_gate2_intent("change the caption and hashtags"), "revise_copy")
        self.assertEqual(classify_gate2_intent("fix the audio overlay sync"), "revise_remix")
        self.assertEqual(classify_gate2_intent("re-render the video animation"), "revise_video")

if __name__ == "__main__":
    unittest.main()
