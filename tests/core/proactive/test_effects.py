"""The effects table (core/proactive/effects.py): which tool calls the proactive team counts, and how.

The rows name real tools and their real permission bundles, so these tests
also keep the table from going stale when a tool is renamed or its bundles change.
"""
import pytest

from core.loaders.tools_loader import ToolsLoader
from core.permissions.bundles import definitions_for, expand_actions, is_bundle
from core.proactive import effects
from core.proactive.effects import (
    CONTEXT,
    PROACTIVE_EFFECTS,
    TRIGGER,
    ProactiveEffect,
    effects_for,
    keeps_paths,
    role_of,
)


@pytest.mark.parametrize("effect", PROACTIVE_EFFECTS, ids=lambda e: f"{e.tool}-{e.role}")
def test_every_row_names_a_real_tool_and_its_own_actions(effect):
    assert ToolsLoader().module_path(effect.tool), f"{effect.tool} is not a tool (tools/ or agents/*/tools/)"
    bundles = definitions_for(effect.tool)
    known = set(expand_actions(effect.tool, list(bundles)))
    for name in effect.actions:
        if is_bundle(name):
            assert name in bundles, f"{effect.tool} has no permission bundle {name}"
        else:
            assert name in known, f"{effect.tool} has no action {name!r} in its bundles"


@pytest.mark.parametrize("tool, actions, role", [
    ("ledger_append", [""], TRIGGER),                       # every call counts
    ("kitchen_inventory", ["log"], TRIGGER),
    ("calendar_add", [""], TRIGGER),
    ("log_workout_session", ["log_workout"], TRIGGER),
    ("write_weekly_review", [""], CONTEXT),
    ("graph_call", [""], CONTEXT),
    ("home_assistant", ["call_service"], CONTEXT),          # @control
    ("home_assistant", ["get_state", "upsert_automation"], CONTEXT),  # one of them is in @admin
    ("home_assistant", ["get_state"], None),                # reads are ignored
    ("filesystem", ["write"], CONTEXT),                     # @change
    ("filesystem", ["read", "grep"], None),
    ("web_search", [""], None),                             # not in the table
])
def test_what_a_call_is_to_the_team(tool, actions, role):
    assert role_of(tool, actions) == role


def test_only_filesystem_counts_by_its_paths():
    assert keeps_paths("filesystem")
    assert not keeps_paths("ledger_append") and not keeps_paths("web_search")


def test_a_role_must_be_trigger_or_context():
    with pytest.raises(ValueError):
        ProactiveEffect("ledger_append", "urgent")


class TestSeveralRowsForOneTool:
    """A tool may be news for some actions and background for others: the trigger wins."""

    @pytest.fixture(autouse=True)
    def table(self, monkeypatch):
        monkeypatch.setattr(effects, "PROACTIVE_EFFECTS", (
            ProactiveEffect("home_assistant", TRIGGER, actions=("reload",)),
            ProactiveEffect("home_assistant", CONTEXT, actions=("@control",)),
        ))
        effects._rows.cache_clear()
        yield
        effects._rows.cache_clear()

    def test_the_trigger_wins(self):
        assert len(effects_for("home_assistant")) == 2
        assert role_of("home_assistant", ["call_service", "reload"]) == TRIGGER
        assert role_of("home_assistant", ["call_service"]) == CONTEXT
        assert role_of("home_assistant", ["delete_script"]) is None
