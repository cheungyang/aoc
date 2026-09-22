"""Tests for per-tool permission selector matching.

The bug this fixes was found by measurement, not by reading: `check_permission`
resolves grant keys with `os.path.abspath`, which is right for the filesystem and
silently wrong for Home Assistant entity ids. `{"light.*": [...]}` matched
nothing; `{"*": [...]}` matched only instructions that named no entity.

Two properties matter here, and they pull in opposite directions:

  * a tool that declares a matcher gets sane glob semantics;
  * a tool that declares nothing keeps byte-identical path semantics, because
    widening `filesystem` by accident would be far worse than the bug being fixed.
"""
import unittest
from unittest.mock import MagicMock, patch

from core.loaders import permission_matchers as pm
from core.loaders import tool_declarations as td
from tests.helpers import make_context

from tools.home_assistant import PERMISSION_MATCHER as ha_matches


def loader_for(tools):
    from core.loaders.agents_loader import AgentsLoader
    from core.loaders.tools_loader import ToolsLoader

    agents = MagicMock()
    agent = MagicMock()
    agent.config = {"tools": tools}
    agents.get_agent.return_value = agent
    AgentsLoader._instance = agents
    ToolsLoader._instance = None

    loader = ToolsLoader()
    loader.clear_permissions_cache()
    return loader


class TestHomeAssistantMatcher(unittest.TestCase):
    def test_star_covers_everything(self):
        for target in ("*", "light.porch", "lock.front", "automation.dusk"):
            self.assertTrue(ha_matches("*", target), target)

    def test_domain_glob_covers_that_domain(self):
        self.assertTrue(ha_matches("light.*", "light.porch"))
        self.assertTrue(ha_matches("light.*", "light.hall"))

    def test_domain_glob_excludes_other_domains(self):
        """The whole point of a narrow grant."""
        for target in ("lock.front", "switch.pump", "automation.dusk"):
            self.assertFalse(ha_matches("light.*", target), target)

    def test_exact_entity_matches_only_itself(self):
        self.assertTrue(ha_matches("light.porch", "light.porch"))
        self.assertFalse(ha_matches("light.porch", "light.hall"))

    def test_a_bare_domain_covers_its_entities(self):
        """`{"light": [...]}` and `{"light.*": [...]}` must agree.

        The tool passes a bare domain as the target when an instruction gives
        `domain` without `entity_id`, so both spellings occur naturally. Having
        them mean different things is a trap.
        """
        self.assertTrue(ha_matches("light", "light.porch"))
        self.assertTrue(ha_matches("light", "light"))
        self.assertFalse(ha_matches("light", "lock.front"))

    def test_a_domain_selector_does_not_match_a_similar_prefix(self):
        """`light` must not cover `lightning.*`."""
        self.assertFalse(ha_matches("light", "lightning.bolt"))

    def test_star_target_is_not_covered_by_a_narrow_grant(self):
        """`"*"` as a *target* means "no entity named".

        An instruction with no entity should not be authorised by a grant that
        only covers one domain, or a narrow grant would quietly permit
        whole-home actions like `inventory`.
        """
        self.assertFalse(ha_matches("light.*", "*"))


class TestDiscovery(unittest.TestCase):
    def setUp(self):
        td.clear_cache()
        self.addCleanup(td.clear_cache)

    def test_home_assistant_declares_a_matcher(self):
        self.assertIsNotNone(pm.matcher_for("home_assistant"))

    def test_filesystem_declares_none(self):
        """Path semantics must remain the default, not an opt-out."""
        self.assertIsNone(pm.matcher_for("filesystem"))

    def test_a_broken_tool_yields_none(self):
        """None means "fall back to path matching", which for a non-path tool
        denies nearly everything. A broken module must not open anything up."""
        with patch.object(td, "_module_path", side_effect=ImportError("boom")):
            td.clear_cache()
            self.assertIsNone(pm.matcher_for("home_assistant"))

    def test_a_non_callable_declaration_is_ignored(self):
        with patch.dict(td._cache, {("demo", pm.MATCHER_ATTR): "not a function"}):
            self.assertIsNone(pm.matcher_for("demo"))


class TestMatcherFailure(unittest.TestCase):
    def test_a_raising_matcher_denies(self):
        """An exception is not a verdict, and must never be read as one."""
        def boom(selector, target):
            raise RuntimeError("nope")

        self.assertFalse(pm.matches(boom, "light.*", "light.porch"))

    def test_a_raising_matcher_is_logged(self):
        def boom(selector, target):
            raise RuntimeError("nope")

        with self.assertLogs(pm.logger, level="WARNING"):
            pm.matches(boom, "light.*", "light.porch")


class TestThroughCheckPermission(unittest.TestCase):
    """The behaviour that was broken, asserted end to end."""

    def setUp(self):
        from core.loaders.agents_loader import AgentsLoader

        self._original = AgentsLoader._instance
        self.addCleanup(lambda: setattr(AgentsLoader, "_instance", self._original))
        self.ctx = make_context(agent_id="matcher-test")

    def test_a_star_grant_permits_reads_on_a_named_entity(self):
        """Regression: this returned False, so `@observe` on `"*"` could not
        read any specific entity -- the exact grant the plan documents."""
        loader = loader_for({"home_assistant": {"*": ["@observe"]}})
        self.assertTrue(
            loader.check_permission(self.ctx, "home_assistant", "get_state", "light.porch")
        )

    def test_a_domain_glob_grant_permits_that_domain(self):
        """Regression: `{"light.*": ...}` matched nothing whatsoever."""
        loader = loader_for({"home_assistant": {"light.*": ["@control"]}})
        self.assertTrue(
            loader.check_permission(self.ctx, "home_assistant", "call_service", "light.porch")
        )

    def test_a_domain_glob_grant_denies_other_domains(self):
        loader = loader_for({"home_assistant": {"light.*": ["@control"]}})
        self.assertFalse(
            loader.check_permission(self.ctx, "home_assistant", "call_service", "lock.front")
        )

    def test_the_action_still_has_to_be_granted(self):
        """Matching the selector is necessary, not sufficient."""
        loader = loader_for({"home_assistant": {"*": ["@observe"]}})
        self.assertFalse(
            loader.check_permission(self.ctx, "home_assistant", "call_service", "light.porch")
        )

    def test_filesystem_path_semantics_are_unchanged(self):
        """The guard on the blast radius. `filesystem` declares no matcher, so
        every one of these must behave exactly as it did before."""
        loader = loader_for({"filesystem": {"pkm/wiki": ["read", "write"]}})
        cases = [
            ("pkm/wiki/note.md", "read", True),
            ("pkm/wiki", "read", True),
            ("pkm/other/note.md", "read", False),
            ("pkm/wiki/note.md", "delete", False),
            # Crucially, `*` must NOT become a wildcard for the filesystem.
            ("pkm/wiki*", "read", False),
        ]
        for path, action, expected in cases:
            with self.subTest(path=path, action=action):
                self.assertEqual(
                    loader.check_permission(self.ctx, "filesystem", action, path), expected
                )

    def test_a_filesystem_star_grant_does_not_become_allow_all(self):
        """If the matcher ever leaked into `filesystem`, this would pass falsely
        and every agent with a `*` key would gain the whole repository."""
        loader = loader_for({"filesystem": {"*": ["read"]}})
        self.assertFalse(
            loader.check_permission(self.ctx, "filesystem", "read", "/etc/passwd")
        )


if __name__ == "__main__":
    unittest.main()
