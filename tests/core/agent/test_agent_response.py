import unittest
import os
import sys
from core.agent.agent_response import AgentResponse

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


if __name__ == '__main__':
    unittest.main()


