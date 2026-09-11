import os
import stat
import tempfile
import unittest
from unittest.mock import patch

from core.util.push_identity import (
    PushIdentity,
    PushIdentityError,
    TOKEN_ENV_VAR,
    default_email_for,
    describe,
    git_commit_args,
    git_credential_args,
    load_push_identity,
    read_token,
    resolve_token_path,
    subprocess_env,
)


class TestTokenFile(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.token_path = os.path.join(self.tmpdir, "bot_token")

    def _write_token(self, content="ghp_secret", mode=0o600):
        with open(self.token_path, "w", encoding="utf-8") as f:
            f.write(content)
        os.chmod(self.token_path, mode)

    def test_reads_token_and_strips_whitespace(self):
        self._write_token("ghp_secret\n")
        self.assertEqual(read_token(self.token_path), "ghp_secret")

    def test_missing_file_is_actionable(self):
        with self.assertRaises(PushIdentityError) as ctx:
            read_token(os.path.join(self.tmpdir, "nope"))
        self.assertIn("not found", str(ctx.exception))

    def test_empty_file_rejected(self):
        # An empty token would silently fall back to the human's gh credentials.
        self._write_token("   \n")
        with self.assertRaises(PushIdentityError) as ctx:
            read_token(self.token_path)
        self.assertIn("empty", str(ctx.exception))

    def test_world_readable_file_rejected(self):
        self._write_token(mode=0o644)
        with self.assertRaises(PushIdentityError) as ctx:
            read_token(self.token_path)
        self.assertIn("chmod 600", str(ctx.exception))

    def test_env_var_overrides_default_path(self):
        self._write_token()
        with patch.dict(os.environ, {"AOC_BOT_TOKEN_FILE": self.token_path}):
            self.assertEqual(resolve_token_path(), self.token_path)
            self.assertEqual(read_token(), "ghp_secret")

    def test_load_push_identity_defaults_email(self):
        self._write_token()
        identity = load_push_identity("cheungyang-bot", token_path=self.token_path)
        self.assertEqual(identity.login, "cheungyang-bot")
        self.assertEqual(identity.email, default_email_for("cheungyang-bot"))
        self.assertEqual(identity.author, "cheungyang-bot <cheungyang-bot@users.noreply.github.com>")

    def test_load_push_identity_requires_login(self):
        with self.assertRaises(PushIdentityError):
            load_push_identity("", token_path=self.token_path)


class TestSubprocessPlumbing(unittest.TestCase):
    def setUp(self):
        self.identity = PushIdentity(
            login="bot", email="bot@users.noreply.github.com",
            token="ghp_secret", token_path="/tmp/bot_token"
        )

    def test_env_carries_token(self):
        self.assertEqual(subprocess_env(self.identity), {TOKEN_ENV_VAR: "ghp_secret"})

    def test_env_empty_without_identity(self):
        self.assertEqual(subprocess_env(None), {})

    def test_credential_helper_does_not_embed_token(self):
        args = git_credential_args(self.identity)
        joined = " ".join(args)
        # The token must be dereferenced from the environment at run time, never
        # written into argv (visible in `ps`) or into .git/config.
        self.assertNotIn("ghp_secret", joined)
        self.assertIn(f"${TOKEN_ENV_VAR}", joined)
        # A helper configured globally must not shadow the machine user's.
        self.assertEqual(args[1], "credential.helper=")

    def test_commit_args_attribute_the_bot(self):
        self.assertEqual(
            git_commit_args(self.identity),
            ["-c", "user.name=bot", "-c", "user.email=bot@users.noreply.github.com"]
        )

    def test_no_args_without_identity(self):
        self.assertEqual(git_credential_args(None), [])
        self.assertEqual(git_commit_args(None), [])

    def test_token_never_appears_in_repr_or_description(self):
        self.assertNotIn("ghp_secret", repr(self.identity))
        self.assertNotIn("ghp_secret", describe(self.identity))
        self.assertIn("bot", describe(self.identity))


if __name__ == "__main__":
    unittest.main()
