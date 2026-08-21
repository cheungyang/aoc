import unittest
from graphs.content_creation.prompts import (
    build_draft_plot_prompt,
    build_audit_plot_prompt,
    build_draft_copy_prompt,
)

class TestContentCreationPrompts(unittest.TestCase):
    def test_build_draft_plot_prompt_full_fields_and_path_output(self):
        prompt = build_draft_plot_prompt(
            topic="cat",
            style_str="3D Animation",
            image_path="cat_image.jpg",
            audio_path="cat.wav",
            project_path="/pkm/wiki/cat",
            output_path="/pkm/wiki/cat/output",
            video_plot_path="/pkm/wiki/cat/output/cat_video_plot.md",
            video_plot_json_path="/pkm/wiki/cat/output/cat_video_plot.json",
            project_guidelines="GUIDELINES_TXT",
            char_guidelines="CHAR_SHEET_TXT",
            feedback="Fix lighting",
            human_feedback="Make cat jump"
        )
        self.assertIn("<playbook>", prompt)
        self.assertIn("GUIDELINES_TXT", prompt)
        self.assertIn("CHAR_SHEET_TXT", prompt)
        self.assertIn("</playbook>", prompt)
        self.assertIn("<current_state>", prompt)
        self.assertIn("cat_image.jpg", prompt)
        self.assertIn("cat.wav", prompt)
        self.assertIn("Fix lighting", prompt)
        self.assertIn("Make cat jump", prompt)
        self.assertIn("</current_state>", prompt)
        self.assertIn("<assigned_task>", prompt)
        
        # Path-based XML template schema tags
        self.assertIn("<status>{success|error}</status>", prompt)
        self.assertIn("<error>{error_details_if_any_else_empty}</error>", prompt)
        self.assertIn("<title>{plot_title}</title>", prompt)
        self.assertIn("<video_plot_path>{video_plot_path}</video_plot_path>", prompt)
        self.assertIn("<motion_prompt>{exact_veo3_motion_prompt}</motion_prompt>", prompt)
        self.assertIn("<overlay_text>{exact_chinese_or_word_overlay_text}</overlay_text>", prompt)
        self.assertNotIn("<markdown_content>", prompt)
        self.assertIn("</assigned_task>", prompt)

    def test_build_draft_plot_prompt_minimal_fields(self):
        prompt = build_draft_plot_prompt(
            topic="dog",
            style_str="Ghibli",
            image_path="",
            audio_path="",
            project_path="",
            output_path="",
            video_plot_path="",
            video_plot_json_path="",
            project_guidelines="",
            char_guidelines="",
            feedback="",
            human_feedback=""
        )
        self.assertIn("<playbook>", prompt)
        self.assertIn("</playbook>", prompt)
        self.assertIn("<current_state>", prompt)
        self.assertIn("Topic / Word: `dog`", prompt)
        self.assertIn("Episode Style: `Ghibli`", prompt)
        self.assertNotIn("BRAND QC FEEDBACK", prompt)
        self.assertNotIn("HUMAN REVISION FEEDBACK", prompt)
        self.assertIn("<status>{success|error}</status>", prompt)
        self.assertIn("<video_plot_path>{video_plot_path}</video_plot_path>", prompt)
        self.assertIn("<assigned_task>", prompt)

    def test_build_audit_plot_prompt_full_fields_and_no_markdown_report(self):
        prompt = build_audit_plot_prompt(
            topic="cat",
            image_path="cat_image.jpg",
            video_plot_path="cat_video_plot.md",
            project_path="/pkm/wiki/cat",
            output_path="/pkm/wiki/cat/output",
            qc_playbook_content="QC_RULES",
            plot_content="DRAFTED_PLOT_DATA"
        )
        self.assertIn("<playbook>", prompt)
        self.assertIn("QC_RULES", prompt)
        self.assertIn("</playbook>", prompt)
        self.assertIn("<current_state>", prompt)
        self.assertIn("cat_image.jpg", prompt)
        self.assertIn("DRAFTED_PLOT_DATA", prompt)
        self.assertIn("</current_state>", prompt)
        self.assertIn("<assigned_task>", prompt)
        
        # Streamlined verdict XML template schema tags
        self.assertIn("<status>{success|error}</status>", prompt)
        self.assertIn("<error>{error_details_if_any_else_empty}</error>", prompt)
        self.assertIn("<verdict>{APPROVED|REJECTED}</verdict>", prompt)
        self.assertIn("<rejection_target>{none|plot|image|both}</rejection_target>", prompt)
        self.assertIn("<feedback>{detailed_actionable_qc_feedback_or_approval_summary}</feedback>", prompt)
        self.assertNotIn("<markdown_report>", prompt)
        self.assertIn("</assigned_task>", prompt)

    def test_build_audit_plot_prompt_minimal_fields(self):
        prompt = build_audit_plot_prompt(
            topic="bird",
            image_path="bird.jpg",
            video_plot_path="bird_plot.md",
            project_path="",
            output_path=""
        )
        self.assertIn("<playbook>", prompt)
        self.assertIn("</playbook>", prompt)
        self.assertIn("<current_state>", prompt)
        self.assertIn("bird.jpg", prompt)
        self.assertNotIn("DRAFTED VIDEO PLOT CONTENT", prompt)
        self.assertIn("<status>{success|error}</status>", prompt)
        self.assertIn("<assigned_task>", prompt)

    def test_build_draft_copy_prompt_with_revision_and_path_output(self):
        prompt = build_draft_copy_prompt(
            topic="puppy",
            project_path="/pkm/wiki/puppy",
            output_path="/pkm/wiki/puppy/output",
            copy_path="puppy_copy.md",
            copy_json_path="puppy_copy.json",
            instructions_text="COPY_RULES",
            human_feedback="Add more hashtags",
            is_revision=True
        )
        self.assertIn("<playbook>", prompt)
        self.assertIn("COPY_RULES", prompt)
        self.assertIn("</playbook>", prompt)
        self.assertIn("<current_state>", prompt)
        self.assertIn("Add more hashtags", prompt)
        self.assertIn("</current_state>", prompt)
        self.assertIn("<assigned_task>", prompt)
        
        # Path-based XML template schema tags
        self.assertIn("<status>{success|error}</status>", prompt)
        self.assertIn("<error>{error_details_if_any_else_empty}</error>", prompt)
        self.assertIn("<copy_path>{copy_path}</copy_path>", prompt)
        self.assertIn("<caption_text>{engaging_caption_content}</caption_text>", prompt)
        self.assertIn("<hashtags>{space_separated_hashtags}</hashtags>", prompt)
        self.assertIn("<vocabulary>{vocabulary_pronunciation_notes}</vocabulary>", prompt)
        self.assertNotIn("<markdown_content>", prompt)
        self.assertIn("</assigned_task>", prompt)

    def test_build_draft_copy_prompt_non_revision(self):
        prompt = build_draft_copy_prompt(
            topic="puppy",
            project_path="/pkm/wiki/puppy",
            output_path="/pkm/wiki/puppy/output",
            copy_path="puppy_copy.md",
            copy_json_path="puppy_copy.json",
            instructions_text="COPY_RULES",
            human_feedback="Ignored feedback because not a revision",
            is_revision=False
        )
        self.assertIn("<playbook>", prompt)
        self.assertIn("<current_state>", prompt)
        self.assertNotIn("HUMAN REVISION INSTRUCTIONS", prompt)
        self.assertIn("<assigned_task>", prompt)
        self.assertIn("<status>{success|error}</status>", prompt)
        self.assertIn("<copy_path>{copy_path}</copy_path>", prompt)

    def test_prompt_xml_tag_nesting_and_closure(self):
        prompts = [
            build_draft_plot_prompt("cat", "3D", "img", "aud", "p", "o", "v", "j"),
            build_audit_plot_prompt("cat", "img", "v", "p", "o"),
            build_draft_copy_prompt("cat", "p", "o", "c", "j")
        ]
        for p in prompts:
            self.assertEqual(p.count("<playbook>"), 1)
            self.assertEqual(p.count("</playbook>"), 1)
            self.assertEqual(p.count("<current_state>"), 1)
            self.assertEqual(p.count("</current_state>"), 1)
            self.assertEqual(p.count("<assigned_task>"), 1)
            self.assertEqual(p.count("</assigned_task>"), 1)

if __name__ == "__main__":
    unittest.main()
