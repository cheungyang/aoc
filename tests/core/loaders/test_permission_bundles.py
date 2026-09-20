"""Tests for named permission bundles.

Three groups, in increasing order of how much they would cost to get wrong:

  * expansion mechanics -- recursion, de-duplication, termination;
  * the fail-closed property -- an unknown bundle must never widen a grant;
  * backwards compatibility -- every real agent config must merge to exactly the
    same permissions it did before bundles existed.

The last group is the one that matters most. Bundles are sugar, and sugar that
changes the meaning of an existing grant is a security bug.
"""
import copy
import json
import os
import unittest
from unittest.mock import patch

from core.loaders import permission_bundles as pb


AGENTS_DIR = os.path.join(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")), "agents"
)


class TestDefinitions(unittest.TestCase):
    def test_every_bundle_resolves(self):
        """Catches a typo'd cross-reference or an empty bundle at build time."""
        pb.validate()

    def test_home_assistant_bundles_name_real_actions(self):
        """A bundle naming an action that does not exist is a dead grant.

        It would look granted in `agent.json`, pass review, and silently never
        match -- the worst kind of permission bug, because it fails in the
        direction of "mysteriously denied" long after the config was written.
        """
        from core.integrations.homeassistant.routing import ALL_ACTIONS

        for name in pb.BUNDLES["home_assistant"]:
            for action in pb.expand_actions("home_assistant", [name]):
                self.assertIn(action, ALL_ACTIONS, f"{name} names unknown action '{action}'")

    def test_home_assistant_bundles_cover_the_whole_vocabulary(self):
        """Every action should be reachable through some bundle.

        An action in no bundle can only be granted by naming it directly, which
        is the verbosity bundles exist to remove. This is a completeness check,
        not a safety one -- but it is how a new action added in a later phase
        gets noticed and classified rather than quietly orphaned.
        """
        from core.integrations.homeassistant.routing import ALL_ACTIONS

        covered = set()
        for name in pb.BUNDLES["home_assistant"]:
            covered.update(pb.expand_actions("home_assistant", [name]))

        self.assertEqual(
            set(ALL_ACTIONS) - covered, set(),
            "actions reachable through no bundle",
        )

    def test_filesystem_bundles_name_real_actions(self):
        """Mirrors the HA check against the filesystem tool's dispatch branches."""
        import re

        source = open(
            os.path.join(
                os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")),
                "tools", "filesystem.py",
            ),
            encoding="utf-8",
        ).read()
        real_actions = set(re.findall(r'action == "([a-z_]+)"', source))

        for name in pb.BUNDLES["filesystem"]:
            for action in pb.expand_actions("filesystem", [name]):
                self.assertIn(action, real_actions, f"{name} names unknown action '{action}'")


class TestExpansion(unittest.TestCase):
    def test_a_bundle_expands_to_its_actions(self):
        self.assertEqual(
            pb.expand_actions("filesystem", ["@read"]),
            ["read", "read_image", "ls", "find", "grep"],
        )

    def test_nested_bundles_are_resolved(self):
        expanded = pb.expand_actions("filesystem", ["@write"])
        self.assertIn("read", expanded)      # from @read
        self.assertIn("overwrite", expanded)  # from @write itself

    def test_deeply_nested_bundles_are_resolved(self):
        expanded = pb.expand_actions("filesystem", ["@manage"])
        for action in ("read", "write", "delete"):
            self.assertIn(action, expanded, action)

    def test_bundles_and_bare_actions_mix(self):
        expanded = pb.expand_actions("home_assistant", ["@control", "reload"])
        self.assertEqual(expanded, ["call_service", "reload"])

    def test_duplicates_are_collapsed(self):
        expanded = pb.expand_actions("filesystem", ["@read", "read", "@read"])
        self.assertEqual(len(expanded), len(set(expanded)))

    def test_order_is_stable(self):
        """Stable output keeps merged permissions diffable between runs."""
        first = pb.expand_actions("filesystem", ["@manage"])
        second = pb.expand_actions("filesystem", ["@manage"])
        self.assertEqual(first, second)

    def test_unknown_tool_passes_actions_through(self):
        self.assertEqual(pb.expand_actions("nosuchtool", ["read"]), ["read"])

    def test_empty_input_stays_empty(self):
        self.assertEqual(pb.expand_actions("filesystem", []), [])

    def test_a_cyclic_definition_terminates(self):
        """A self-referential bundle must not hang the loader at startup."""
        cyclic = {"demo": {"@a": ["@b", "x"], "@b": ["@a", "y"]}}
        with patch.object(pb, "BUNDLES", cyclic):
            self.assertEqual(sorted(pb.expand_actions("demo", ["@a"])), ["x", "y"])


class TestFailsClosed(unittest.TestCase):
    """An unknown bundle must never turn into a wider grant.

    `check_permission` treats an empty list as allow-all. So if expansion dropped
    unrecognised `@` names, a grant of `["@typo"]` would become `[]` and the agent
    would gain every action on that tool -- a privilege escalation caused by a
    spelling mistake.
    """

    def test_an_unknown_bundle_is_not_dropped(self):
        self.assertEqual(pb.expand_actions("filesystem", ["@nope"]), ["@nope"])

    def test_a_sole_unknown_bundle_does_not_produce_an_empty_list(self):
        self.assertNotEqual(pb.expand_actions("filesystem", ["@nope"]), [])

    def test_an_unknown_bundle_matches_no_real_action(self):
        import re

        source = open(
            os.path.join(
                os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")),
                "tools", "filesystem.py",
            ),
            encoding="utf-8",
        ).read()
        real_actions = set(re.findall(r'action == "([a-z_]+)"', source))
        self.assertEqual(set(pb.expand_actions("filesystem", ["@nope"])) & real_actions, set())


class TestScopeShapes(unittest.TestCase):
    def test_dict_scopes_expand_per_selector(self):
        scope = {"*": ["@observe"], "light.*": ["@control"]}
        expanded = pb.expand_scope("home_assistant", scope)

        self.assertIn("get_state", expanded["*"])
        self.assertEqual(expanded["light.*"], ["call_service"])

    def test_list_scopes_expand(self):
        self.assertIn("call_service", pb.expand_scope("home_assistant", ["@control"]))

    def test_a_wildcard_survives_expansion(self):
        """`*` is meaningful to check_permission and must not be mangled."""
        self.assertEqual(pb.expand_scope("home_assistant", ["*"]), ["*"])

    def test_an_empty_dict_stays_empty(self):
        """`{}` means allow-all; expansion must not change that either way."""
        self.assertEqual(pb.expand_scope("home_assistant", {}), {})

    def test_unexpected_shapes_pass_through(self):
        self.assertEqual(pb.expand_scope("home_assistant", "weird"), "weird")

    def test_tools_without_bundles_are_untouched(self):
        permissions = {"gh": {"repo": ["@read"]}}
        pb.expand_permissions(permissions)
        self.assertEqual(permissions["gh"]["repo"], ["@read"])


class TestBackwardsCompatibility(unittest.TestCase):
    """Every real agent config must mean exactly what it meant before.

    Synthetic fixtures cannot establish this: the guarantee is about the configs
    that actually exist on disk, including whatever odd shapes have accumulated
    in them. So these read the real `agents/` directory.
    """

    @staticmethod
    def agent_configs():
        for name in sorted(os.listdir(AGENTS_DIR)):
            path = os.path.join(AGENTS_DIR, name, "agent.json")
            if os.path.isfile(path):
                with open(path, encoding="utf-8") as handle:
                    yield name, json.load(handle)

    def test_there_are_agents_to_check(self):
        """Guards against this whole class silently passing on an empty list."""
        self.assertGreater(len(list(self.agent_configs())), 5)

    def test_no_real_agent_config_is_changed_by_expansion(self):
        for name, config in self.agent_configs():
            tools = config.get("tools", {})
            before = copy.deepcopy(tools)
            pb.expand_permissions(tools)
            self.assertEqual(tools, before, f"{name}: expansion altered its grants")

    def test_expansion_is_idempotent(self):
        """Running twice must equal running once.

        The loader caches merged permissions, but a cache clear re-runs the merge
        on the same underlying dicts. Expansion that appended on every pass would
        grow grants without bound.
        """
        scope = {"*": ["@observe"], "light.*": ["@control", "read"]}
        once = pb.expand_scope("home_assistant", copy.deepcopy(scope))
        twice = pb.expand_scope("home_assistant", copy.deepcopy(once))
        self.assertEqual(once, twice)


class TestThroughTheLoader(unittest.TestCase):
    """Bundles must reach `check_permission` already expanded.

    The unit tests above prove `expand_*` is correct in isolation. These prove it
    is actually wired into the merge -- a bundle that expands perfectly but never
    gets called is a grant that denies everything.
    """

    def setUp(self):
        from core.loaders.agents_loader import AgentsLoader
        from core.loaders.tools_loader import ToolsLoader

        ToolsLoader._instance = None
        self._original = AgentsLoader._instance
        self.addCleanup(lambda: setattr(AgentsLoader, "_instance", self._original))

    def _loader_for(self, tools):
        from unittest.mock import MagicMock

        from core.loaders.agents_loader import AgentsLoader
        from core.loaders.tools_loader import ToolsLoader

        agents = MagicMock()
        agent = MagicMock()
        agent.config = {"tools": tools}
        agents.get_agent.return_value = agent
        AgentsLoader._instance = agents

        loader = ToolsLoader()
        loader.clear_permissions_cache()
        return loader

    def test_a_granted_bundle_permits_its_actions(self):
        from tests.helpers import make_context

        loader = self._loader_for({"home_assistant": {"light.porch": ["@control"]}})
        ctx = make_context(agent_id="bundle-test")

        self.assertTrue(
            loader.check_permission(ctx, "home_assistant", "call_service", "light.porch")
        )

    def test_a_bundle_does_not_permit_actions_outside_it(self):
        from tests.helpers import make_context

        loader = self._loader_for({"home_assistant": {"light.porch": ["@control"]}})
        ctx = make_context(agent_id="bundle-test")

        self.assertFalse(
            loader.check_permission(ctx, "home_assistant", "upsert_automation", "light.porch")
        )

    def test_a_nested_bundle_permits_the_inherited_actions(self):
        """`@admin` includes `@author`, so an @admin grant must allow authoring."""
        from tests.helpers import make_context

        loader = self._loader_for({"home_assistant": {"automation.x": ["@admin"]}})
        ctx = make_context(agent_id="bundle-test")

        self.assertTrue(
            loader.check_permission(ctx, "home_assistant", "upsert_automation", "automation.x")
        )
        self.assertTrue(
            loader.check_permission(ctx, "home_assistant", "delete_automation", "automation.x")
        )

    def test_no_bundle_name_survives_into_merged_permissions(self):
        from tests.helpers import make_context

        loader = self._loader_for({
            "home_assistant": {"*": ["@observe"], "light.*": ["@control"]},
            "filesystem": {"pkm/wiki": ["@write"]},
        })
        ctx = make_context(agent_id="bundle-test")
        merged = loader._merge_tool_permissions(ctx)

        for tool_id, scope in merged.items():
            if isinstance(scope, dict):
                for selector, actions in scope.items():
                    if isinstance(actions, list):
                        leftover = [a for a in actions if pb.is_bundle(a)]
                        self.assertEqual(leftover, [], f"{tool_id}[{selector}]")

    def test_bare_action_grants_still_work(self):
        """The backwards-compatibility guarantee, exercised through the loader."""
        from tests.helpers import make_context

        loader = self._loader_for({"home_assistant": {"light.porch": ["call_service"]}})
        ctx = make_context(agent_id="bundle-test")

        self.assertTrue(
            loader.check_permission(ctx, "home_assistant", "call_service", "light.porch")
        )


if __name__ == "__main__":
    unittest.main()
