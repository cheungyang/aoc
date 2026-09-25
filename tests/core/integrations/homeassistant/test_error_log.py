"""Tests for the Home Assistant error-log condenser."""
import unittest

from core.integrations.homeassistant import error_log

TRACE = (
    "{ts} ERROR (MainThread) [homeassistant.components.soundtouch] Error fetching now_playing\n"
    "Traceback (most recent call last):\n"
    "  File \"/usr/src/homeassistant/x.py\", line 12, in poll\n"
    "    resp = session.get(url)\n"
    "urllib3.exceptions.MaxRetryError: HTTPConnection(host='10.60.1.32', port=8090): "
    "Max retries exceeded with url: /now_playing\n"
)
OTHER = "{ts} WARNING (SyncWorker_1) [homeassistant.setup] Integration 'spotcast' not found\n"


def _log(repeats: int) -> str:
    lines = []
    for i in range(repeats):
        lines.append(TRACE.format(ts=f"2026-09-21 21:{i // 60 % 60:02d}:{i % 60:02d}.{i % 1000:03d}"))
    lines.append(OTHER.format(ts="2026-09-21 23:59:59.000"))
    return "".join(lines)


class TestCondense(unittest.TestCase):
    def test_small_log_passes_through(self):
        text = OTHER.format(ts="2026-09-21 23:59:59.000")
        self.assertEqual(error_log.condense(text), text)

    def test_repeats_are_collapsed_with_count(self):
        out = error_log.condense(_log(5000))
        self.assertLess(len(out), error_log.DEFAULT_MAX_CHARS + 200)
        self.assertIn("[x5000,", out)
        self.assertEqual(out.count("Max retries exceeded"), 1)
        self.assertIn("5001 entries, 2 distinct", out)

    def test_newest_first(self):
        out = error_log.condense(_log(500))
        self.assertLess(out.index("spotcast"), out.index("now_playing"))

    def test_budget_is_enforced(self):
        # 200 distinct entries, each forced unique by its logger name.
        text = "".join(
            f"2026-09-21 21:00:{i % 60:02d}.000 ERROR (MainThread) [comp{i}] " + "x" * 800 + "\n"
            for i in range(200)
        )
        out = error_log.condense(text, limit=500, max_chars=5000)
        self.assertLessEqual(len(out), 5000 + 100)
        self.assertIn("older distinct entries omitted", out)

    def test_max_chars_is_clamped(self):
        out = error_log.condense(_log(20000), max_chars=10**9)
        self.assertLessEqual(len(out), error_log.HARD_MAX_CHARS + 200)


if __name__ == "__main__":
    unittest.main()
