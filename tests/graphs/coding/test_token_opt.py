import unittest
import tempfile
import os
from graphs.coding.utils.token_opt import (
    sanitize_traceback,
    sanitize_diff,
    check_static_bloated_files,
    check_static_silent_failures
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

    def test_check_static_bloated_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bloated_file = os.path.join(temp_dir, "bloated.py")
            with open(bloated_file, "w") as f:
                f.write("\n".join([f"# line {i}" for i in range(200)]))

            clean_file = os.path.join(temp_dir, "clean.py")
            with open(clean_file, "w") as f:
                f.write("\n".join([f"# line {i}" for i in range(50)]))

            violations = check_static_bloated_files(temp_dir, ["bloated.py", "clean.py"], max_lines=150)
            self.assertEqual(len(violations), 1)
            self.assertEqual(violations[0]["rule"], "Bloated Files")
            self.assertEqual(violations[0]["file"], "bloated.py")

    def test_check_static_silent_failures_python(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            py_file = os.path.join(temp_dir, "bad.py")
            with open(py_file, "w") as f:
                f.write("try:\n    x = 1 / 0\nexcept Exception:\n    pass\n")

            violations = check_static_silent_failures(temp_dir, ["bad.py"])
            self.assertEqual(len(violations), 1)
            self.assertEqual(violations[0]["rule"], "Silent Failure")
            self.assertEqual(violations[0]["file"], "bad.py")

if __name__ == "__main__":
    unittest.main()
