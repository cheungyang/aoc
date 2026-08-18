import unittest
from graphs.content_creation.utils.errors import is_quota_exceeded_error, format_quota_exceeded_message

class TestErrorUtils(unittest.TestCase):
    def test_detect_quota_errors(self):
        self.assertTrue(is_quota_exceeded_error("Error code: 429 - insufficient_quota"))
        self.assertTrue(is_quota_exceeded_error("ResourceExhausted: 429 Quota exceeded for quota metric"))
        self.assertTrue(is_quota_exceeded_error("You exceeded your current quota, please check your plan and billing details."))
        self.assertTrue(is_quota_exceeded_error("Rate limit reached. Please retry in 60s."))
        self.assertTrue(is_quota_exceeded_error("Credits exhausted on account."))

        self.assertFalse(is_quota_exceeded_error("File not found at /path/to/img.jpg"))
        self.assertFalse(is_quota_exceeded_error("SyntaxError: invalid syntax"))
        self.assertFalse(is_quota_exceeded_error(""))
        self.assertFalse(is_quota_exceeded_error(None))

    def test_format_quota_exceeded_message(self):
        msg = format_quota_exceeded_message("Google Veo 3", "429 RESOURCE_EXHAUSTED", "cat")
        self.assertIn("PIPELINE HALTED: API Quota Exceeded / Rate Limit (429)", msg)
        self.assertIn("Google Veo 3", msg)
        self.assertIn("`cat`", msg)
        self.assertIn("State Safely Preserved", msg)
        self.assertIn("reply **'retry'**", msg)

if __name__ == "__main__":
    unittest.main()
