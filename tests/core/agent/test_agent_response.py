import unittest
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

if __name__ == '__main__':
    unittest.main()
