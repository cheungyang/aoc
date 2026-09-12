import unittest
import tempfile
import os
from graphs.coding.utils.token_opt import (
    sanitize_traceback,
    sanitize_diff,
)

class TestTokenOpt(unittest.TestCase):
    def test_sanitize_traceback_truncation(self):
        long_traceback = "\n".join([f"line error stack frame {i}" for i in range(100)])
        sanitized = sanitize_traceback(long_traceback, max_lines=20, max_chars=1000)
        self.assertIn("Truncated", sanitized)
        self.assertLess(len(sanitized.splitlines()), 30)

    def test_sanitize_diff_truncation(self):
        long_diff = "diff --git a/file.ts b/file.ts\n" + ("+ line of code\n" * 500)
        sanitized = sanitize_diff(long_diff, max_chars=500)
        self.assertIn("Diff truncated", sanitized)
        self.assertLessEqual(len(sanitized), 600)


if __name__ == "__main__":
    unittest.main()
