"""Tests for agents/work-intel, one module per module.

`JournalsCase` points Config().pkm_dir at a throwaway PKM holding an empty
pkm/vault/journals/work/, and the scans' clock reads Los Angeles time. The
journals the tests write are made up; the real vault is never touched.
"""
import os
import tempfile
import unittest
from datetime import date, datetime, time
from typing import Optional
from unittest import mock
from zoneinfo import ZoneInfo

from core.config.settings import Config

ZONE = ZoneInfo("America/Los_Angeles")
ENTRY = "# Daily briefing\n- The beta shipped; the launch review moved to Friday.\n"


def journal_text(day: date, body: str = "") -> str:
    """A daily journal as the vault's template starts it, with `body` under its front matter."""
    return f"---\njournal: work\njournal-date: {day.isoformat()}\n---\n{body}"


def at(day: date, hour: int, minute: int = 0) -> float:
    """Epoch seconds for `day` at hour:minute in Los Angeles."""
    return datetime.combine(day, time(hour, minute), ZONE).timestamp()


class JournalsCase(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        real_pkm = os.path.realpath(Config().pkm_dir)
        Config().pkm_dir = tmp.name
        self.addCleanup(setattr, Config(), "pkm_dir", None)
        self.pkm = tmp.name
        self.folder = os.path.join(tmp.name, "vault", "journals", "work")
        self.assertFalse(os.path.realpath(self.folder).startswith(real_pkm + os.sep))
        os.makedirs(self.folder)
        zone = mock.patch("core.tooling.get_local_timezone", return_value=ZONE)  # where the scans read it
        zone.start()
        self.addCleanup(zone.stop)

    def path(self, name: str) -> str:
        return os.path.join(self.folder, name)

    def write(self, name: str, text: str, mtime: Optional[float] = None) -> str:
        path = self.path(name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def write_journal(self, day: date, body: str = ENTRY, mtime: Optional[float] = None) -> str:
        """`day`'s journal with an entry; `mtime` defaults to that morning, after the 6 AM update."""
        return self.write(f"{day.isoformat()}.md", journal_text(day, body), at(day, 8, 30) if mtime is None else mtime)

    def write_rules(self, text: str) -> None:
        """work-intel's RULES.md in the temp PKM."""
        folder = os.path.join(self.pkm, "agents", "work-intel")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "RULES.md"), "w", encoding="utf-8") as f:
            f.write(text)
