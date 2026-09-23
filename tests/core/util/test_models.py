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

    def test_the_default_tier_exists_in_every_provider_table(self):
        """A provider whose table omits the default cannot serve an agent that
        simply did not mention a model -- and that agent would fail at build
        time, far from the table that forgot it."""
        for provider, tiers in models.PROVIDER_TIERS.items():
            with self.subTest(provider=provider):
                self.assertIn(models.DEFAULT_AGENT_TIER, tiers)


class TestLocalTiers(unittest.TestCase):
    """The on-device table answers the same vocabulary, with one exception."""

    def test_every_text_tier_is_available_locally(self):
        """An agent moving on-device keeps its `model` line, so a missing tier
        would make the move fail for reasons unrelated to the agent."""
        for name in ("FLASH_LITE", "FLASH", "PRO"):
            with self.subTest(tier=name):
                self.assertIn(name, models.LOCAL_TIERS)

    def test_image_is_absent_locally(self):
        """The device generates no images. Mapping IMAGE to the text model
        would defer the failure to an image call site far from the config."""
        self.assertNotIn("IMAGE", models.LOCAL_TIERS)

    def test_local_tiers_may_share_one_model(self):
        """Deliberately the opposite of `test_tiers_are_distinct`: one device
        serving every intent is a fact about the hardware, not a config error.
        Asserting distinctness here would forbid the current, correct state."""
        self.assertTrue(set(models.LOCAL_TIERS.values()))


class TestResolveModel(unittest.TestCase):

    def test_resolves_each_tier_name(self):
        for name, expected in models.GOOGLE_TIERS.items():
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
        for name in models.GOOGLE_TIERS:
            with self.subTest(tier=name):
                once = models.resolve_model(name)
                self.assertEqual(models.resolve_model(once), once)


class TestResolveModelPerProvider(unittest.TestCase):
    """The same tier name means different models in different places."""

    def test_a_tier_resolves_to_the_providers_own_model(self):
        for name in ("FLASH_LITE", "FLASH", "PRO"):
            with self.subTest(tier=name):
                self.assertEqual(
                    models.resolve_model(name, provider="local"),
                    models.LOCAL_TIERS[name],
                )

    def test_the_same_tier_differs_between_providers(self):
        """The property the whole split exists for: adding `provider` to an
        agent.json moves it on-device without touching its `model` line."""
        self.assertNotEqual(
            models.resolve_model("FLASH", provider="google"),
            models.resolve_model("FLASH", provider="local"),
        )

    def test_a_missing_value_uses_the_providers_default_not_geminis(self):
        """An agent that never named a model must not silently reach Gemini
        after being moved on-device."""
        resolved = models.resolve_model(None, provider="local")
        self.assertEqual(resolved, models.LOCAL_TIERS[models.DEFAULT_AGENT_TIER])
        self.assertNotEqual(resolved, models.DEFAULT_AGENT_MODEL)

    def test_image_raises_under_local_rather_than_resolving_to_text(self):
        """Resolving it would defer the failure to an image call site, which is
        further from the config that asked for it."""
        with self.assertRaises(ValueError) as caught:
            models.resolve_model("IMAGE", provider="local")
        self.assertIn("local", str(caught.exception))

    def test_the_error_names_the_providers_own_tiers(self):
        """Listing Gemini's tiers to someone configuring a local agent sends
        them looking in the wrong table."""
        with self.assertRaises(ValueError) as caught:
            models.resolve_model("NOPE", provider="local")
        self.assertIn("local", str(caught.exception))

    def test_a_literal_local_id_passes_through(self):
        self.assertEqual(
            models.resolve_model(models.LOCAL_GEMMA4_26B, provider="local"),
            models.LOCAL_GEMMA4_26B,
        )

    def test_an_unknown_provider_keeps_the_google_vocabulary(self):
        """`ollama` names its models literally and has no tiers of its own;
        raising here would break a provider that never asked for the feature."""
        self.assertEqual(models.resolve_model("gemma:4b", provider="ollama"), "gemma:4b")
        self.assertEqual(models.resolve_model("FLASH", provider="ollama"), models.FLASH)


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
        If a literal is genuinely needed, add a tier for it instead.

        The rule holds for every provider, not just Gemini: an on-device agent
        naming its build id directly would put a date-stamped artifact name,
        specific to one machine's weights, into shared configuration.
        """
        import json

        offenders = []
        for path in sorted((PROJECT_ROOT / "agents").glob("*/agent.json")):
            config = json.loads(path.read_text())
            declared = config.get("model")
            if declared is None:
                continue
            tiers = models.tiers_for(config.get("provider"))
            if declared.upper().replace("-", "_") not in tiers:
                offenders.append(
                    f"{path.relative_to(PROJECT_ROOT)}: {declared!r} "
                    f"(provider {config.get('provider', 'google')!r}, "
                    f"valid: {', '.join(sorted(tiers))})"
                )

        self.assertEqual(
            offenders,
            [],
            "agent.json `model` must name a tier from its provider's table:\n"
            + "\n".join(offenders),
        )

    def test_every_agent_config_resolves(self):
        """Catches a tier renamed in models.py without updating the configs --
        which would otherwise surface as a crash when that bot starts.

        Asserts membership of the provider's table rather than a Gemini-shaped
        name, so that a local agent resolving to its on-device build counts as
        success while a tier that resolves to nothing still fails.
        """
        import json

        for path in sorted((PROJECT_ROOT / "agents").glob("*/agent.json")):
            with self.subTest(agent=path.parent.name):
                config = json.loads(path.read_text())
                provider = config.get("provider", "google")
                resolved = models.resolve_model(config.get("model"), provider=provider)
                self.assertIn(resolved, models.tiers_for(provider).values())


if __name__ == "__main__":
    unittest.main()
