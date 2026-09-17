import unittest
import os
import re
import sys
from pathlib import Path

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.util import models

PROJECT_ROOT = Path(__file__).resolve().parents[3]

TIERS = ("FLASH_LITE", "FLASH", "PRO", "IMAGE")
DEFAULTS = ("DEFAULT_AGENT_MODEL", "DEFAULT_VERBALIZER_MODEL", "DEFAULT_BROWSER_MODEL")


class TestModelConstants(unittest.TestCase):

    def test_every_default_points_at_a_declared_tier(self):
        """A default that is its own string is a hardcode with a nicer name."""
        tier_values = {getattr(models, name) for name in TIERS}
        for name in DEFAULTS:
            with self.subTest(default=name):
                self.assertIn(getattr(models, name), tier_values)

    def test_names_look_like_model_identifiers(self):
        for name in TIERS:
            with self.subTest(tier=name):
                self.assertRegex(getattr(models, name), r"^gemini-[0-9]")

    def test_tiers_are_distinct(self):
        values = [getattr(models, name) for name in TIERS]
        self.assertEqual(len(values), len(set(values)), "two tiers name the same model")

    def test_the_verbalizer_never_defaults_to_pro(self):
        """It runs mid-turn, before the listener has heard anything, so the
        round-trip is audible as silence."""
        self.assertNotEqual(models.DEFAULT_VERBALIZER_MODEL, models.PRO)


class TestResolveModel(unittest.TestCase):

    def test_resolves_each_tier_name(self):
        for name, expected in models.TIERS.items():
            with self.subTest(tier=name):
                self.assertEqual(models.resolve_model(name), expected)

    def test_tier_names_are_case_and_separator_insensitive(self):
        """The config is hand-edited; "flash-lite" is the same intent."""
        for spelling in ("FLASH_LITE", "flash_lite", "Flash-Lite", "  FLASH_LITE  "):
            with self.subTest(spelling=spelling):
                self.assertEqual(models.resolve_model(spelling), models.FLASH_LITE)

    def test_missing_value_falls_back_to_the_default(self):
        self.assertEqual(models.resolve_model(None), models.DEFAULT_AGENT_MODEL)
        self.assertEqual(models.resolve_model(""), models.DEFAULT_AGENT_MODEL)
        self.assertEqual(models.resolve_model("   "), models.DEFAULT_AGENT_MODEL)

    def test_an_explicit_default_is_honoured(self):
        self.assertEqual(models.resolve_model(None, default=models.PRO), models.PRO)

    def test_a_literal_model_id_passes_through(self):
        """Pinning an exact version stays possible -- there are legitimate
        reasons, like reproducing a bug against a specific release."""
        self.assertEqual(
            models.resolve_model("gemini-9.9-experimental"), "gemini-9.9-experimental"
        )

    def test_a_mistyped_tier_raises_instead_of_falling_back(self):
        """Falling back would run that agent on the cheapest model and report
        nothing; it would just be quietly worse at its job."""
        for typo in ("FLASH_LIT", "PRO_MAX", "pro-preview", "gpt"):
            with self.subTest(typo=typo):
                with self.assertRaises(ValueError) as caught:
                    models.resolve_model(typo)
                self.assertIn("FLASH_LITE", str(caught.exception))

    def test_resolution_is_idempotent(self):
        """`resolve_model(resolve_model(x))` is the same, so a resolved value
        surviving back into config does not become an error."""
        for name in models.TIERS:
            with self.subTest(tier=name):
                once = models.resolve_model(name)
                self.assertEqual(models.resolve_model(once), once)


class TestNoInlineModelNames(unittest.TestCase):
    """The reason the constants exist: a model bump is one edit, not a search.

    Missing a call site does not raise -- it leaves that subsystem quietly
    running the old model -- so the grep is the enforcement.
    """

    SEARCH_DIRS = ("core", "tools", "graphs", "scripts")
    ALLOWED = {Path("core/util/models.py")}
    PATTERN = re.compile(r"[\"']gemini-[0-9][^\"']*[\"']")

    def test_no_python_source_hardcodes_a_model_name(self):
        offenders = []
        for directory in self.SEARCH_DIRS:
            for path in (PROJECT_ROOT / directory).rglob("*.py"):
                relative = path.relative_to(PROJECT_ROOT)
                if relative in self.ALLOWED:
                    continue
                for number, line in enumerate(path.read_text().splitlines(), 1):
                    if self.PATTERN.search(line):
                        offenders.append(f"{relative}:{number}: {line.strip()}")

        self.assertEqual(
            offenders,
            [],
            "Model names belong in core/util/models.py:\n" + "\n".join(offenders),
        )

    def test_agent_configs_name_a_tier(self):
        """A version string here is the same hardcode, spread over 14 files.
        If a literal is genuinely needed, add a tier for it instead."""
        import json

        offenders = []
        for path in sorted((PROJECT_ROOT / "agents").glob("*/agent.json")):
            declared = json.loads(path.read_text()).get("model")
            if declared is None:
                continue
            if declared.upper().replace("-", "_") not in models.TIERS:
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}: {declared!r}")

        self.assertEqual(
            offenders,
            [],
            "agent.json `model` must name a tier "
            f"({', '.join(sorted(models.TIERS))}):\n" + "\n".join(offenders),
        )

    def test_every_agent_config_resolves(self):
        """Catches a tier renamed in models.py without updating the configs --
        which would otherwise surface as a crash when that bot starts."""
        import json

        for path in sorted((PROJECT_ROOT / "agents").glob("*/agent.json")):
            with self.subTest(agent=path.parent.name):
                resolved = models.resolve_model(json.loads(path.read_text()).get("model"))
                self.assertRegex(resolved, r"^gemini-[0-9]")


if __name__ == "__main__":
    unittest.main()
