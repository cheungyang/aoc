import unittest
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.agent.prompts.context_pruner_prompt import build_summarization_prompt


class TestContextPrunerPrompt(unittest.TestCase):
    def test_build_summarization_prompt_structure(self):
        transcript = "User: Search for flights to Tokyo\nAssistant: Found 3 flights"
        prompt = build_summarization_prompt(
            transcript=transcript,
            previous_summary="User was looking for vacation spots.",
            max_summary_tokens=800
        )

        self.assertIn("<playbook>", prompt)
        self.assertIn("</playbook>", prompt)
        self.assertIn("<current_state>", prompt)
        self.assertIn("</current_state>", prompt)
        self.assertIn("<assigned_task>", prompt)
        self.assertIn("</assigned_task>", prompt)

        # Check content presence
        self.assertIn("User was looking for vacation spots", prompt)
        self.assertIn("Search for flights to Tokyo", prompt)
        self.assertIn("800 tokens", prompt)
        self.assertIn("<payload>", prompt)
        self.assertIn("<summary>", prompt)


if __name__ == "__main__":
    unittest.main()
