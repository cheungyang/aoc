import io
import os
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from unittest.mock import patch

from graphs.content_creation.utils.logging import _append_execution_log

ENTRY_RE = re.compile(r"^## (?P<actor>.+?) \[(?P<time>\d{2}:\d{2}:\d{2})\] — (?P<title>.+)$", re.MULTILINE)


def _seconds_of_day(hh_mm_ss: str) -> int:
    h, m, s = (int(p) for p in hh_mm_ss.split(":"))
    return h * 3600 + m * 60 + s


class TestLogging(unittest.TestCase):
    """`_append_execution_log` is the markdown audit trail every node writes to.

    It swallows every exception on purpose (a broken log must never fail a
    node), which means nothing else in the system will ever notice if it stops
    writing. These tests are the only thing standing between it and silence.
    """

    def test_append_execution_log_creates_file_with_header(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            _append_execution_log(
                output_path=temp_dir,
                topic="  CAT  ",
                actor="🎬 Content Creator",
                event_title="Visual Plate Generation",
                details={"Raw Video Path": "/tmp/cat_raw_video.mp4"}
            )

            log_file = os.path.join(temp_dir, "execution_log.md")
            self.assertTrue(os.path.isfile(log_file), "execution_log.md was never created")
            content = open(log_file, encoding="utf-8").read()

            # Header is written once, on creation, with the normalized topic.
            self.assertIn("# 📜 Content Creation Trajectory: cat\n", content)
            self.assertIn("- **Topic**: `cat`\n", content)
            self.assertIn(f"- **Output Path**: `{temp_dir}`\n", content)
            self.assertRegex(content, r"- \*\*Initiated At\*\*: `\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC`")
            self.assertIn("---\n", content)

            # Entry line: "## {actor} [HH:MM:SS] — {event_title}"
            entries = ENTRY_RE.findall(content)
            self.assertEqual(len(entries), 1, f"expected exactly one entry heading in:\n{content}")
            actor, stamp, title = entries[0]
            self.assertEqual(actor, "🎬 Content Creator")
            self.assertEqual(title, "Visual Plate Generation")
            self.assertIn("- **Raw Video Path**: `/tmp/cat_raw_video.mp4`\n", content)

            # The stamp is UTC, not local time.
            now_utc = datetime.now(timezone.utc)
            delta = abs(_seconds_of_day(stamp) - _seconds_of_day(now_utc.strftime("%H:%M:%S")))
            delta = min(delta, 86400 - delta)
            self.assertLess(delta, 120, f"timestamp {stamp} is not UTC now ({now_utc:%H:%M:%S})")

    def test_append_execution_log_appends_without_overwriting(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            for i in (1, 2, 3):
                _append_execution_log(
                    output_path=temp_dir,
                    topic="cat",
                    actor=f"Actor {i}",
                    event_title=f"Event {i}",
                    details={"Step": i}
                )

            content = open(os.path.join(temp_dir, "execution_log.md"), encoding="utf-8").read()

            # Every entry survives, in order — nothing was overwritten.
            entries = ENTRY_RE.findall(content)
            self.assertEqual([a for a, _, _ in entries], ["Actor 1", "Actor 2", "Actor 3"])
            self.assertEqual([t for _, _, t in entries], ["Event 1", "Event 2", "Event 3"])
            for i in (1, 2, 3):
                self.assertIn(f"- **Step**: `{i}`\n", content)

            # The trajectory header belongs to the first write only.
            self.assertEqual(content.count("# 📜 Content Creation Trajectory: cat"), 1)
            self.assertEqual(content.count("- **Initiated At**"), 1)

    def test_append_execution_log_formats_detail_values(self):
        long_value = "L" * 81
        boundary_value = "B" * 80
        multiline_value = "  first line\nsecond line  "
        with tempfile.TemporaryDirectory() as temp_dir:
            _append_execution_log(
                output_path=temp_dir,
                topic="cat",
                actor="⚙️ Graph Worker",
                event_title="Video Plot Drafting",
                details={
                    "Scalar": 6,
                    "Short": "ok",
                    "Long": long_value,
                    "Boundary": boundary_value,
                    "Multiline": multiline_value,
                    "Listy": ["a", "b"],
                    "Empty": "",
                    "Missing": None,
                }
            )

            content = open(os.path.join(temp_dir, "execution_log.md"), encoding="utf-8").read()

            self.assertIn("- **Scalar**: `6`\n", content)
            self.assertIn("- **Short**: `ok`\n", content)
            # Long and multiline strings are fenced, stripped, and not inlined.
            self.assertIn(f"- **Long**:\n```markdown\n{long_value}\n```\n", content)
            self.assertIn("- **Multiline**:\n```markdown\nfirst line\nsecond line\n```\n", content)
            self.assertNotIn(f"- **Long**: `{long_value}`", content)
            # Exactly 80 characters still fits inline — the fence starts above 80.
            self.assertIn(f"- **Boundary**: `{boundary_value}`\n", content)
            # Lists are expanded one bullet per item.
            self.assertIn("- **Listy**:\n  - `a`\n  - `b`\n", content)
            # Empty and None details are dropped entirely.
            self.assertNotIn("Empty", content)
            self.assertNotIn("Missing", content)

    def test_append_execution_log_honours_explicit_log_path_and_creates_parents(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            nested = os.path.join(temp_dir, "deep", "nested")
            explicit = os.path.join(nested, "execution_log.md")

            _append_execution_log(
                output_path=temp_dir,
                topic="cat",
                actor="Actor",
                event_title="Event",
                details={"Step": 1},
                log_path=explicit
            )

            self.assertTrue(os.path.isfile(explicit), "explicit log_path was not honoured")
            self.assertFalse(
                os.path.exists(os.path.join(temp_dir, "execution_log.md")),
                "log_path must win over output_path, not write both"
            )
            self.assertIn("- **Step**: `1`\n", open(explicit, encoding="utf-8").read())

    def test_append_execution_log_writes_nothing_without_a_target(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cwd = os.getcwd()
            os.chdir(temp_dir)
            try:
                _append_execution_log(
                    output_path="",
                    topic="cat",
                    actor="Actor",
                    event_title="Event",
                    details={"Step": 1}
                )
            finally:
                os.chdir(cwd)
            self.assertEqual(os.listdir(temp_dir), [], "a stray log file was written with no target path")

    def test_append_execution_log_swallows_write_failures(self):
        """A failing audit log must never take a node down with it."""
        real_open = open

        def exploding_open(*args, **kwargs):
            if args and str(args[0]).endswith("execution_log.md"):
                raise OSError("disk on fire")
            return real_open(*args, **kwargs)

        with tempfile.TemporaryDirectory() as temp_dir:
            buf = io.StringIO()
            with patch("builtins.open", side_effect=exploding_open):
                with redirect_stdout(buf):
                    _append_execution_log(
                        output_path=temp_dir,
                        topic="cat",
                        actor="Actor",
                        event_title="Event",
                        details={"Step": 1}
                    )

            # No exception escaped, and the failure was reported rather than hidden.
            self.assertIn("Error appending to execution_log.md", buf.getvalue())
            self.assertIn("disk on fire", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
