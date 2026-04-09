import unittest
import os
import sys
import datetime

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.prompts.knowledge_prompt_hook import knowledge_prompt_hook

class TestKnowledgePromptHook(unittest.TestCase):

    def test_knowledge_prompt_hook_prepends(self):
        initial_prompt = "Initial prompt content."
        updated_prompt = knowledge_prompt_hook(initial_prompt)
        
        self.assertIn("Common Knowledge:\n", updated_prompt)
        self.assertTrue(updated_prompt.startswith(initial_prompt))
        
        # Verify some content
        now = datetime.datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        self.assertIn(f"Today's Date: {date_str}", updated_prompt)

if __name__ == "__main__":
    unittest.main()
