import unittest
from graphs.content_creation.utils.classifiers import (
    classify_gate1_intent,
    classify_gate2_intent,
    extract_remix_parameters
)

class TestClassifiers(unittest.TestCase):
    def test_classify_gate1_intent(self):
        # Approvals
        self.assertEqual(classify_gate1_intent("approve"), "approved")
        self.assertEqual(classify_gate1_intent("approved"), "approved")
        self.assertEqual(classify_gate1_intent("I approve"), "approved")
        self.assertEqual(classify_gate1_intent("approve please"), "approved")
        self.assertEqual(classify_gate1_intent("looks good"), "approved")
        self.assertEqual(classify_gate1_intent("lgtm"), "approved")
        self.assertEqual(classify_gate1_intent("proceed"), "approved")
        self.assertEqual(classify_gate1_intent("yes"), "approved")
        self.assertEqual(classify_gate1_intent("good to go"), "approved")

        # Plot-specific
        self.assertEqual(classify_gate1_intent("change camera movement in the plot"), "revise_plot")
        self.assertEqual(classify_gate1_intent("revise the motion prompt script"), "revise_plot")

        # General / Costume / Image revisions
        feedback = "I want Ayla to be wearing a costume of the target animal and behave like that targeted animal, instead of having the target animal surrounding her."
        self.assertEqual(classify_gate1_intent(feedback), "revise_image")
        self.assertEqual(classify_gate1_intent("make the outfit purple and add cat ears"), "revise_image")

    def test_classify_gate2_intent(self):
        self.assertEqual(classify_gate2_intent("approve"), "approved")
        self.assertEqual(classify_gate2_intent("approved"), "approved")
        self.assertEqual(classify_gate2_intent("I approve"), "approved")
        self.assertEqual(classify_gate2_intent("looks great, proceed"), "approved")
        self.assertEqual(classify_gate2_intent("change the caption and hashtags"), "revise_copy")
        self.assertEqual(classify_gate2_intent("fix the audio overlay sync"), "revise_remix")
        self.assertEqual(classify_gate2_intent("re-render the video animation"), "revise_video")

        # Mixed prompt containing 'text' but intended for Remix
        self.assertEqual(
            classify_gate2_intent("Remix: audio should be inserted at 4s .  The text should also appear at 4s"),
            "revise_remix"
        )
        self.assertEqual(
            classify_gate2_intent("Remix: audio should be inserted at 4s. Subtitles should also appear at 4s"),
            "revise_remix"
        )

    def test_extract_remix_parameters(self):
        # 1. User's exact prompt
        p1 = extract_remix_parameters("Remix: audio should be inserted at 4s .  The text should also appear at 4s")
        self.assertEqual(p1.get("audio_start_time"), 4.0)
        self.assertEqual(p1.get("text_start_time"), 4.0)

        # 2. Subtitles and audio at 2.5s
        p2 = extract_remix_parameters("audio at 2.5s and subtitles at 2.5s until 5.5s")
        self.assertEqual(p2.get("audio_start_time"), 2.5)
        self.assertEqual(p2.get("text_start_time"), 2.5)
        self.assertEqual(p2.get("text_end_time"), 5.5)

        # 3. Position and font size
        p3 = extract_remix_parameters("Make subtitle font size 64 and position top")
        self.assertEqual(p3.get("font_size"), 64)
        self.assertEqual(p3.get("position"), "top")

        # 4. Center position and bottom position
        p4 = extract_remix_parameters("place text in center with font color white")
        self.assertEqual(p4.get("position"), "center")
        self.assertEqual(p4.get("font_color"), "white")

        p5 = extract_remix_parameters("position at bottom with font size: 52")
        self.assertEqual(p5.get("position"), "bottom")
        self.assertEqual(p5.get("font_size"), 52)

if __name__ == "__main__":
    unittest.main()
