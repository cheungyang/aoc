"""Local guard: nothing personal in any file git would push.

The two checks and their rules are in core/git/privacy.py. The coding
graph runs the same checks before it pushes; here they run over this checkout.
Check 1 (copied from the user's notes) runs where the PKM is and skips
elsewhere; check 2 (patterns) runs everywhere.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.config.settings import Config
from core.git.privacy import (
    PERSONAL_DIRS,
    SELF,
    NotesMissing,
    check,
    copied_lines,
    describe,
    pattern_hits,
    personal_runs,
    pushed_files,
    pushed_texts,
    render,
    runs,
)

ROOT = Path(__file__).resolve().parents[1]


# -- The guards ------------------------------------------------------------------

class TestNothingPersonalInThePushedFiles(unittest.TestCase):

    def setUp(self):
        try:
            self.files = [path for path in pushed_files(ROOT) if path != SELF]
        except (OSError, subprocess.CalledProcessError) as e:
            self.skipTest(f"git ls-files failed: {e}")

    def test_no_line_is_copied_from_the_users_notes(self):
        pkm = Path(Config().pkm_dir)
        if not any((pkm / folder).is_dir() for folder in PERSONAL_DIRS):
            self.skipTest(f"no personal notes at {pkm}")
        hits = copied_lines(pushed_texts(ROOT, self.files), personal_runs(pkm))
        if hits:
            self.fail(render("line(s) copied from your notes into files git would push", hits))

    def test_no_personal_pattern(self):
        hits = pattern_hits(pushed_texts(ROOT, self.files))
        if hits:
            self.fail(render("personal-looking value(s) in files git would push", hits))


# -- How the checks work ---------------------------------------------------------

class TestCopiedLines(unittest.TestCase):

    def notes(self, files):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for rel, text in files.items():
            path = Path(tmp.name) / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return personal_runs(Path(tmp.name))

    def test_a_run_of_eight_words_from_a_note_is_a_hit(self):
        personal = self.notes({"vault/journals/2026-01-02.md":
                               "Lunch with Pat, then the quarterly review at noon in the big room.\n"})
        texts = [("tests/fixture.py", 'NOTE = "then the Quarterly Review at noon in the"\n'
                                      'SHORT = "lunch with Pat, then the quarterly review"\n')]
        self.assertEqual(copied_lines(iter(texts), personal),
                         [("tests/fixture.py", 1, "\u201cthen the quarterly review at noon in the\u201d")])

    def test_eight_chinese_characters_are_a_hit(self):
        personal = self.notes({"wallet/kitchen/INVENTORY.md": "- 冷凍櫃最下層的自製水餃還有兩包\n"})
        hits = copied_lines(iter([("a.md", "「冷凍櫃最下層的自製水餃」\n「冷凍櫃最下層」\n")]), personal)
        self.assertEqual([(path, line) for path, line, _ in hits], [("a.md", 1)])

    def test_a_street_address_from_a_note_is_a_hit_however_it_is_written(self):
        personal = self.notes({"vault/journals/2026-01-02.md": "---\nlocation: 48 Willow Creek Rd\n---\nQuiet day.\n"})
        texts = [("tests/f.py", 'ADDRESS = "48 Willow Creek Road, Springfield"\nOTHER = "49 Willow Creek Rd"\n')]
        self.assertEqual(copied_lines(iter(texts), personal),
                         [("tests/f.py", 1, "\u201caddress: 48 willow creek rd\u201d")])

    def test_code_urls_paths_and_tags_are_not_content(self):
        self.assertEqual(list(runs("Logged to pkm/vault/journals/2026-01-02.md, see `git log -n 5` "
                                   "and https://example.com/a/b/c via core.tooling")), [])
        self.assertEqual(list(runs("Booked the lake house<br>with Pat and Sam, see vault/trips/lake.md")),
                         ["booked the lake house with pat and sam", "the lake house with pat and sam see"])

    def test_a_run_stops_at_an_emoji_and_needs_words(self):
        self.assertEqual(list(runs("- [ ] Pay the water bill on the 15th ⏫ 🔁 every month 📅 2026-10-15")), [])
        self.assertEqual(list(runs("Plates: 45×2, 25×4, 10×2, 5×2, 2.5×2, 1.25×2")), [])

    def test_structure_and_template_text_are_not_content(self):
        personal = self.notes({
            "vault/journals/2026-01-02.md": "---\nmood: tired after the long drive home from the coast\n---\n"
                                            "### Brain dump for the whole week ahead of the move\n"
                                            "| Benefit and card name | Monthly value in dollars | Win rate |\n"
                                            "| --- | --- | --- |\n"
                                            "Write down three things you are grateful for today\n",
            "templates/Daily.md": "Write down three things you are grateful for today\n",
        })
        self.assertEqual(personal, set())

    def test_only_personal_folders_count_and_settings_files_dont(self):
        personal = self.notes({
            "wiki/research/llm.md": "Transformers attend to every token in the context window.\n",
            "wiki/memory/TAGS.md": "## travel\nTrips and getting around: how far you like to go from home.\n",
            "agents/meal-planner/RULES.md": "- Lists a row as expiring soon when it is three days out.\n",
            "agents/meal-planner/MEMORY.md": "- Prefers the rice cooker on busy weekday nights.\n",
        })
        self.assertEqual(personal, {"prefers the rice cooker on busy weekday nights"})


class TestPatterns(unittest.TestCase):

    def found(self, text):
        return [what for _, _, what in pattern_hits(iter([("f.md", text)]))]

    def test_personal_values_are_hits(self):
        text = ("mail sam.lee@gmail.com\ncall (212) 321-7788\ncard ending in 4821, ****9917\n"
                "see /Users/samlee/notes\nthread 1987654321098765432\nha at home.ddns.net\n")
        self.assertEqual(self.found(text), [
            "email sam.lee@gmail.com", "phone number (212) 321-7788", "card digits ending in 4821",
            "card digits ****9917", "home folder /Users/samlee", "id 1987654321098765432",
            "dynamic-DNS host home.ddns.net"])

    def test_placeholders_pass(self):
        text = ("mail pat@example.com, bot@users.noreply.github.com, git@github.com, worker@egm.internal\n"
                "call 415-555-0134\ncard ending in 0000 or ****1234\n"
                "see /Users/<name>/aoc, /home/appuser/pkm and /Users/x\n"
                "not home folders: pkm/wallet/home/DEVICES.md, #project/home/kitchen, ~/home/notes\n"
                "thread 123456789012345678, 234567890123456789 and 111111111111111111\n")
        self.assertEqual(self.found(text), [])


class TestPushedFiles(unittest.TestCase):

    def test_tracked_plus_untracked_but_not_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            # No user or system git config: a global excludes file could hide files.
            env = {"HOME": tmp, "XDG_CONFIG_HOME": tmp, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
            with patch.dict(os.environ, env):
                root = Path(tmp) / "repo"
                root.mkdir()
                subprocess.run(["git", "-C", str(root), "init", "-q"], check=True, capture_output=True)
                (root / ".gitignore").write_text("secret.txt\n", encoding="utf-8")
                for name in ("tracked.txt", "new.txt", "secret.txt"):
                    (root / name).write_text("x\n", encoding="utf-8")
                subprocess.run(["git", "-C", str(root), "add", "tracked.txt"], check=True, capture_output=True)
                self.assertEqual(pushed_files(root), [".gitignore", "new.txt", "tracked.txt"])

    def test_skips_binaries_missing_files_and_the_pkm_link(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as vault:
            root = Path(tmp)
            (root / "a.txt").write_text("hello\n", encoding="utf-8")
            (root / "b.bin").write_bytes(b"\x00\x01hello")
            (Path(vault) / "note.md").write_text("hello\n", encoding="utf-8")
            (root / "pkm").symlink_to(vault)
            paths = ["a.txt", "b.bin", "gone.txt", "pkm", "pkm/note.md"]
            self.assertEqual(list(pushed_texts(root, paths)), [("a.txt", "hello\n")])


class TestCheckForCallersAboutToPublish(unittest.TestCase):
    """`check` and `describe`, which the coding graph calls before it pushes or posts."""

    def notes(self, files):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for rel, text in files.items():
            path = Path(tmp.name) / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return Path(tmp.name)

    def test_both_checks_run_and_hits_come_back_by_file_and_line(self):
        pkm = self.notes({"vault/journals/2026-01-02.md":
                          "Lunch with Pat, then the quarterly review at noon in the big room.\n"})
        texts = [("b.py", 'MAIL = "sam.lee@gmail.com"\n'),
                 ("a.py", 'x = 1\nNOTE = "then the quarterly review at noon in the"\n')]
        self.assertEqual(check(texts, pkm), [
            ("a.py", 2, "\u201cthen the quarterly review at noon in the\u201d"),
            ("b.py", 1, "email sam.lee@gmail.com"),
        ])

    def test_clean_text_has_no_hits(self):
        pkm = self.notes({"vault/journals/2026-01-02.md": "Quiet day at home with a book.\n"})
        self.assertEqual(check([("a.py", 'MAIL = "pat@example.com"\n')], pkm), [])

    def test_no_notes_is_an_error_not_a_pass(self):
        """The test skips check 1 without notes; a caller about to publish must not."""
        pkm = self.notes({"wiki/research/llm.md": "Not a personal folder.\n"})
        with self.assertRaises(NotesMissing):
            check([("a.py", "x = 1\n")], pkm)

    def test_describe_names_the_kind_never_the_value(self):
        hits = [
            ("a", 1, "\u201cthen the quarterly review at noon in the\u201d"),
            ("a", 2, "\u201c冷凍櫃最下層的自製\u201d"),
            ("a", 3, "\u201caddress: 48 willow creek rd\u201d"),
            ("a", 4, "email sam.lee@gmail.com"),
            ("a", 5, "phone number (212) 321-7788"),
            ("a", 6, "card digits ending in 4821"),
            ("a", 7, "home folder /Users/samlee"),
            ("a", 8, "id 1987654321098765432"),
            ("a", 9, "dynamic-DNS host home.ddns.net"),
        ]
        self.assertEqual([describe(hit) for hit in hits], [
            "8 words in a row from the user's notes",
            "8 Chinese characters in a row from the user's notes",
            "a street address from the user's notes",
            "an email address",
            "a phone number",
            "card digits",
            "a home folder path",
            "a Discord-style id",
            "a dynamic-DNS host name",
        ])


if __name__ == "__main__":
    unittest.main()
