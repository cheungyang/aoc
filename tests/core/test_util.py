import unittest
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.util import split_message

class TestUtil(unittest.TestCase):

    def test_split_message_short(self):
        text = "Hello world"
        chunks = split_message(text)
        self.assertEqual(chunks, [text])

    def test_split_message_newline(self):
        text = "Line 1\n" + "a" * 1990 + "\nLine 2"
        chunks = split_message(text)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0], "Line 1\n" + "a" * 1990)
        self.assertEqual(chunks[1], "Line 2")

    def test_split_message_hard_split(self):
        text = "a" * 3000
        chunks = split_message(text, limit=1000)
        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[0], "a" * 1000)
        self.assertEqual(chunks[1], "a" * 1000)
        self.assertEqual(chunks[2], "a" * 1000)

    def test_split_message_empty(self):
        chunks = split_message("")
        self.assertEqual(chunks, [])

    def test_split_message_none(self):
        chunks = split_message(None)
        self.assertEqual(chunks, [])

    def test_get_knowledge_prompt(self):
        from core.util import get_knowledge_prompt
        import datetime
        
        prompt = get_knowledge_prompt()
        self.assertIn("<common_knowledge>", prompt)
        self.assertIn("Today's Date:", prompt)
        
        now = datetime.datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        self.assertIn(date_str, prompt)

if __name__ == "__main__":
    unittest.main()
