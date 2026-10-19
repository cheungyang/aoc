"""Credential files in the project directory stay out of git and the image,
and at 600 in the container.

Three hand-kept lists protect them, and two have drifted: home_assistant_token
was missing from AOC_SECRET_NAMES in entrypoint.sh, so every start widened it
to 644 through the `.:/app` bind mount and the Home Assistant client refused
it; e2b_api_key was missing from .dockerignore, so `COPY . .` baked it into
the image. SECRETS is checked against all three lists.

The mode tests run the entrypoint's own permission block against a scratch
directory in place of /app. Ownership is stubbed out: only modes are under
test here, and the real calls need a container.
"""
import fnmatch
import os
import re
import shlex
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "entrypoint.sh"

# Credentials in the project directory, one per AOC_SECRET_NAMES entry. A new
# one goes here and in .gitignore, .dockerignore and AOC_SECRET_NAMES.
SECRETS = [
    ".env",
    ".env.local",
    "client_secret_1234-abcd.apps.googleusercontent.com.json",
    "e2b_api_key",
    "github_bot_token",
    "google_health_credentials.json",
    "home_assistant_token",
]
# Files the sweep must make readable, the tracked sample config among them.
PUBLIC = [".env.example", "main.py", os.path.join("scripts", "run_tool.py")]

# runuser and chown need root and an appuser account. A runuser that succeeds
# also means "appuser can read main.py", which skips the chown branch.
_STUBS = "runuser() { return 0; }\nchown() { return 0; }\n"


def permission_block(root: str) -> str:
    """The entrypoint's secret-permission lines, retargeted from /app to `root`."""
    text = ENTRYPOINT.read_text(encoding="utf-8")
    try:
        start = text.index("AOC_SECRET_NAMES=(")
        end = text.index("# --- SSH credential provisioning", start)
    except ValueError:
        raise AssertionError("entrypoint.sh changed shape; update permission_block()") from None
    return re.sub(r"/app\b", lambda _: shlex.quote(root), text[start:end])


def ignored_by(ignore_file: str, name: str) -> bool:
    """Whether ROOT/`ignore_file` excludes the top-level file `name`.

    Covers what .gitignore and .dockerignore agree on for top-level files:
    globs, a leading `/`, and `!` exceptions, the last match winning. A pattern
    ending in `/` is directory-only to git, so it never counts for a file.
    """
    ignored = False
    for line in (ROOT / ignore_file).read_text(encoding="utf-8").splitlines():
        pattern = line.strip()
        if not pattern or pattern.startswith("#") or pattern.endswith("/"):
            continue
        negated = pattern.startswith("!")
        if negated:
            pattern = pattern[1:]
        if fnmatch.fnmatchcase(name, pattern.lstrip("/")):
            ignored = not negated
    return ignored


class TestSecretModes(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        os.makedirs(os.path.join(self.root, "scripts"))

    def modes_after_start(self, secret_mode: int) -> dict:
        """Runs the block over SECRETS at `secret_mode` and PUBLIC at 600."""
        for names, mode in ((SECRETS, secret_mode), (PUBLIC, 0o600)):
            for name in names:
                path = os.path.join(self.root, name)
                Path(path).write_text("x\n", encoding="utf-8")
                os.chmod(path, mode)
        subprocess.run(["bash", "-c", _STUBS + permission_block(self.root)], check=True, capture_output=True)
        return {
            name: oct(stat.S_IMODE(os.stat(os.path.join(self.root, name)).st_mode))
            for name in SECRETS + PUBLIC
        }

    def test_a_secret_at_600_is_not_widened(self):
        modes = self.modes_after_start(0o600)
        self.assertEqual({n: modes[n] for n in SECRETS}, {n: "0o600" for n in SECRETS})

    def test_a_widened_secret_is_locked_back_to_600(self):
        modes = self.modes_after_start(0o644)
        self.assertEqual({n: modes[n] for n in SECRETS}, {n: "0o600" for n in SECRETS})

    def test_everything_else_is_made_readable(self):
        """Also keeps the tests above from passing because nothing ran."""
        modes = self.modes_after_start(0o600)
        self.assertEqual({n: modes[n] for n in PUBLIC}, {n: "0o644" for n in PUBLIC})


class TestSecretsAreIgnored(unittest.TestCase):

    def test_git_ignores_every_secret(self):
        self.assertEqual([n for n in SECRETS if not ignored_by(".gitignore", n)], [])

    def test_the_image_build_ignores_every_secret(self):
        """Otherwise `COPY . .` bakes it into an image layer."""
        self.assertEqual([n for n in SECRETS if not ignored_by(".dockerignore", n)], [])

    def test_the_sample_config_and_code_are_not_ignored(self):
        """Also keeps the tests above from passing on a matcher that matches everything."""
        for ignore_file in (".gitignore", ".dockerignore"):
            wrongly_ignored = [n for n in (".env.example", "main.py") if ignored_by(ignore_file, n)]
            self.assertEqual(wrongly_ignored, [], ignore_file)


if __name__ == "__main__":
    unittest.main()
