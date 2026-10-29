import unittest
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.channel.response_parser import AgentResponse

class TestAgentResponse(unittest.TestCase):

    def test_perfect_xml(self):
        reply_text = "Hello"
        response = AgentResponse.from_string(reply_text)
        self.assertEqual(response.text, "Hello")
        self.assertIsNone(response.poll_data)

    def test_xml_with_filler_text(self):
        reply_text = "Sure, here it is: Hello Hope that helps!"
        response = AgentResponse.from_string(reply_text)
        self.assertEqual(response.text, "Sure, here it is: Hello Hope that helps!")
        self.assertIsNone(response.poll_data)

    def test_malformed_xml_fallback(self):
        # Special characters in text should be preserved as it's not parsed as XML
        reply_text = "Hello & welcome"
        response = AgentResponse.from_string(reply_text)
        self.assertEqual(response.text, "Hello & welcome")
        self.assertIsNone(response.poll_data)

    def test_perfect_poll(self):
        reply_text = """Choose one:
<poll allow_multiple="false">
  <question>Color?</question>
  <options>
    <option><text>Red</text><emoji>🔴</emoji><response>Red</response></option>
    <option><text>Blue</text><emoji>🔵</emoji><response>Blue</response></option>
  </options>
</poll>
"""
        response = AgentResponse.from_string(reply_text)
        self.assertEqual(response.text.strip(), "Choose one:")
        self.assertIsNotNone(response.poll_data)
        self.assertEqual(response.poll_data["question"], "Color?")
        self.assertFalse(response.poll_data["allow_multiple"])
        self.assertEqual(len(response.poll_data["options"]), 2)
        self.assertEqual(response.poll_data["options"][0]["text"], "Red")
        self.assertEqual(response.poll_data["options"][0]["emoji"], "🔴")
        self.assertEqual(response.poll_data["options"][0]["response"], "Red")

    def test_malformed_poll_fallback(self):
        # & makes it invalid XML in question
        reply_text = """Choose one:
<poll allow_multiple="false">
  <question>Color & Shape?</question>
  <options>
    <option><text>Red Circle</text><emoji>🔴</emoji><response>Red Circle</response></option>
    <option><text>Blue Square</text><emoji>🟦</emoji><response>Blue Square</response></option>
  </options>
</poll>
"""
        response = AgentResponse.from_string(reply_text)
        self.assertEqual(response.text.strip(), "Choose one:")
        self.assertIsNotNone(response.poll_data)
        self.assertEqual(response.poll_data["question"], "Color & Shape?")
        self.assertFalse(response.poll_data["allow_multiple"])
        self.assertEqual(len(response.poll_data["options"]), 2)
        self.assertEqual(response.poll_data["options"][0]["text"], "Red Circle")

    def test_no_xml_block(self):
        reply_text = "This is just raw text without any tags."
        response = AgentResponse.from_string(reply_text)
        self.assertEqual(response.text, reply_text)
        self.assertIsNone(response.poll_data)

    def test_perfect_images(self):
        reply_text = """Here are the images:
<images>
  <image path="assets/img1.png"/>
  <image path="assets/img2.jpg"/>
</images>
"""
        response = AgentResponse.from_string(reply_text)
        self.assertEqual(response.text.strip(), "Here are the images:")
        self.assertIsNotNone(response.image_paths)
        self.assertEqual(len(response.image_paths), 2)
        self.assertEqual(response.image_paths[0], "assets/img1.png")
        self.assertEqual(response.image_paths[1], "assets/img2.jpg")

    def test_images_fallback(self):
        reply_text = """Here are the images:
<images>
  <image path="assets/img1.png"/>
  <image path="assets/img2.jpg"/>
  <invalid>
</images>
"""
        response = AgentResponse.from_string(reply_text)
        self.assertEqual(response.text.strip(), "Here are the images:")
        self.assertIsNotNone(response.image_paths)
    def test_perfect_videos(self):
        reply_text = """Here are the videos:
<videos>
  <video path="assets/video1.mp4"/>
  <video path="assets/video2.webm"/>
</videos>
"""
        response = AgentResponse.from_string(reply_text)
        self.assertEqual(response.text.strip(), "Here are the videos:")
        self.assertIsNotNone(response.video_paths)
        self.assertEqual(len(response.video_paths), 2)
        self.assertEqual(response.video_paths[0], "assets/video1.mp4")
        self.assertEqual(response.video_paths[1], "assets/video2.webm")

    def test_videos_fallback(self):
        reply_text = """Here are the videos:
<videos>
  <video path="assets/video1.mp4"/>
  <video path="assets/video2.webm"/>
  <invalid>
</videos>
"""
        response = AgentResponse.from_string(reply_text)
        self.assertEqual(response.text.strip(), "Here are the videos:")
        self.assertIsNotNone(response.video_paths)
        self.assertEqual(len(response.video_paths), 2)
        self.assertEqual(response.video_paths[0], "assets/video1.mp4")
        self.assertEqual(response.video_paths[1], "assets/video2.webm")

    def test_combined_images_and_videos(self):
        reply_text = """Here is the package:
<images>
  <image path="assets/img1.png"/>
</images>
<videos>
  <video path="assets/video1.mp4"/>
</videos>
"""
        response = AgentResponse.from_string(reply_text)
        self.assertEqual(response.text.strip(), "Here is the package:")
        self.assertIsNotNone(response.image_paths)
        self.assertEqual(response.image_paths, ["assets/img1.png"])
        self.assertIsNotNone(response.video_paths)
        self.assertEqual(response.video_paths, ["assets/video1.mp4"])

    def test_perfect_system_memory_log(self):
        reply_text = """I have completed your task successfully!
<system_memory_log>
- [12:00:00] [MEMORY] Task: Generated chart. Status: Success. Decisions: Used matplotlib.
- [12:00:00] [CONTEXT] Evergreen: User prefers dark mode charts.
- [12:00:00] [FEEDBACK] Explicit: Ensure axes are always labeled.
</system_memory_log>
"""
        response = AgentResponse.from_string(reply_text)
        self.assertEqual(response.text.strip(), "I have completed your task successfully!")
        self.assertIsNotNone(response.system_memory_log)
        self.assertIn("[MEMORY] Task: Generated chart.", response.system_memory_log)
        self.assertIn("[CONTEXT] Evergreen: User prefers dark mode charts.", response.system_memory_log)
        self.assertIn("[FEEDBACK] Explicit: Ensure axes are always labeled.", response.system_memory_log)
        self.assertNotIn("<system_memory_log>", response.text)
        self.assertNotIn("</system_memory_log>", response.text)

    def test_combined_all_xml_blocks(self):
        reply_text = """Here is the complete report and options:
<poll allow_multiple="false">
  <question>Proceed?</question>
  <options>
    <option><text>Yes</text><emoji>✅</emoji><response>Yes</response></option>
  </options>
</poll>
<images>
  <image path="assets/report.png"/>
</images>
<videos>
  <video path="assets/summary.mp4"/>
</videos>
<system_memory_log>
- [12:00:00] [MEMORY] Task: Full report generated.
</system_memory_log>
"""
        response = AgentResponse.from_string(reply_text)
        self.assertEqual(response.text.strip(), "Here is the complete report and options:")
        self.assertIsNotNone(response.poll_data)
        self.assertIsNotNone(response.image_paths)
        self.assertIsNotNone(response.video_paths)
        self.assertIsNotNone(response.system_memory_log)
        self.assertEqual(response.poll_data["question"], "Proceed?")
        self.assertEqual(response.image_paths, ["assets/report.png"])
        self.assertEqual(response.video_paths, ["assets/summary.mp4"])
        self.assertEqual(response.system_memory_log, "- [12:00:00] [MEMORY] Task: Full report generated.")
        self.assertNotIn("<system_memory_log>", response.text)
        self.assertNotIn("<poll", response.text)
        self.assertNotIn("<images>", response.text)
        self.assertNotIn("<videos>", response.text)

    # --- <speech>: what a voice turn says aloud ----------------------------

    def test_no_speech_on_an_ordinary_reply(self):
        response = AgentResponse.from_string("You have two meetings today.")
        self.assertIsNone(response.speech)
        self.assertEqual(response.text, "You have two meetings today.")

    def test_speech_is_extracted_and_kept_out_of_the_text(self):
        reply = (
            "<speech>You have two meetings: standup at nine and the dentist at three.</speech>\n\n"
            "**Today**\n- 9:00 Standup\n- 15:00 Dentist"
        )
        response = AgentResponse.from_string(reply)
        self.assertEqual(response.speech, "You have two meetings: standup at nine and the dentist at three.")
        self.assertEqual(response.text, "**Today**\n- 9:00 Standup\n- 15:00 Dentist")

    def test_every_speech_block_is_kept_in_order(self):
        # Tokens accumulated across steps: the preamble before a tool call, then the answer.
        reply = "<speech>Checking your calendar.</speech><speech>You're free after four.</speech>You're free after 16:00."
        response = AgentResponse.from_string(reply)
        self.assertEqual(response.speech, "Checking your calendar. You're free after four.")
        self.assertEqual(response.text, "You're free after 16:00.")

    def test_an_unclosed_block_is_speech_and_stays_in_the_text(self):
        response = AgentResponse.from_string("<speech>The kitchen light is on.")
        self.assertEqual(response.speech, "The kitchen light is on.")
        self.assertEqual(response.text, "The kitchen light is on.")

    def test_a_reply_that_is_only_speech_has_empty_text(self):
        response = AgentResponse.from_string("<speech>Done, the lights are off.</speech>")
        self.assertEqual(response.speech, "Done, the lights are off.")
        self.assertEqual(response.text, "")

    def test_speech_beside_other_blocks(self):
        reply = (
            "<SPEECH>Want me to book the earlier slot?</SPEECH>Two slots are open.\n"
            '<poll allow_multiple="false"><question>Which slot?</question><options><option>'
            "<text>9:00</text><emoji>🌅</emoji><response>Book 9:00</response></option></options></poll>"
        )
        response = AgentResponse.from_string(reply)
        self.assertEqual(response.speech, "Want me to book the earlier slot?")
        self.assertEqual(response.text, "Two slots are open.")
        self.assertEqual(response.poll_data["question"], "Which slot?")
        self.assertIsNone(response.question, "a poll's question is not a marked one")

    def test_a_marked_question_is_stored_and_still_spoken_in_place(self):
        response = AgentResponse.from_string(
            "<speech>Yesterday the afternoon slipped away. <question>How's your energy, one to ten?</question>"
            "</speech>Good morning! Rate your energy 1-10."
        )
        self.assertEqual(response.speech, "Yesterday the afternoon slipped away. How's your energy, one to ten?")
        self.assertEqual(response.question, "How's your energy, one to ten?")
        self.assertEqual(response.text, "Good morning! Rate your energy 1-10.")

    def test_no_marked_question_no_question(self):
        response = AgentResponse.from_string("<speech>Want to go with today's workout?</speech>Workout below.")
        self.assertIsNone(response.question)

    def test_split_question_joins_several_and_tidies_whitespace(self):
        from core.channel.response_parser import split_question

        self.assertEqual(
            split_question("<question>Merge the\n notes?</question> And <QUESTION>keep tags?</QUESTION>"),
            ("Merge the\n notes? And keep tags?", "Merge the notes? keep tags?"),
        )
        self.assertEqual(split_question(None), (None, None))
        self.assertEqual(split_question("Plain."), ("Plain.", None))


class TestSwitchDirective(unittest.TestCase):
    """<switch to="..."/>: an agent handing the user to a teammate
    (core/prompts/templates/system/team.md)."""

    def test_the_directive_names_the_teammate_and_leaves_no_trace(self):
        response = AgentResponse.from_string(
            '<speech>Passing you to Grace.</speech>Grace runs project planning. <switch to="goal-setter"/>')
        self.assertEqual(response.switch_to, "goal-setter")
        self.assertEqual(response.text, "Grace runs project planning.")
        self.assertEqual(response.speech, "Passing you to Grace.")

    def test_forms_a_model_may_write(self):
        for raw in ("Over to Grace. <switch to='goal-setter'></switch>", 'Over to Grace.<SWITCH to="goal-setter" />',
                    'Over to Grace. <switch  to = "goal-setter">'):
            with self.subTest(raw=raw):
                response = AgentResponse.from_string(raw)
                self.assertEqual((response.switch_to, response.text), ("goal-setter", "Over to Grace."))

    def test_inside_speech_it_is_never_read_out(self):
        response = AgentResponse.from_string('<speech>Over to Grace. <switch to="goal-setter"/></speech>Done.')
        self.assertEqual((response.speech, response.switch_to), ("Over to Grace.", "goal-setter"))

    def test_the_last_one_counts_and_none_means_no_switch(self):
        self.assertEqual(AgentResponse.from_string('<switch to="ted"/> no, <switch to="grace"/>').switch_to, "grace")
        self.assertIsNone(AgentResponse.from_string("Talk to Grace about it.").switch_to)
        self.assertIsNone(AgentResponse.from_string('<switch to=""/>Hm.').switch_to)


if __name__ == '__main__':
    unittest.main()


