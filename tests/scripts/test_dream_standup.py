"""Tests for the nightly dream standup script.

The value of moving this out of a prompt is that a missing or malformed reply
becomes visible instead of being smoothed over, so most of these tests are about
what the script does when an agent misbehaves.
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from scripts.dream_standup import (
    dream,
    extract_tag,
    parse_dream_response,
    render,
    render_row,
    resolve_channel,
    standup_agents,
)

DREAMED_XML = """
<dream_response>
  <payload>
    <status>Dreamed</status>
    <logs_processed>2</logs_processed>
  </payload>
  <errors>None</errors>
  <learnings>User prefers morning workouts.</learnings>
</dream_response>
"""

EMPTY_XML = """
<dream_response>
  <payload>
    <status>No new memories</status>
  </payload>
  <errors>None</errors>
  <learnings></learnings>
</dream_response>
"""


def _tool_response(inner):
    """Wraps content the way `format_tool_response` does."""
    return f"<tool_response><payload>{inner}</payload><errors>None</errors></tool_response>"


class TestAgentSelection(unittest.TestCase):

    def _loader(self, configs):
        loader = MagicMock()
        loader.list_agent_ids.return_value = list(configs)
        loader.get_agent_config.side_effect = lambda a: configs.get(a)
        return loader

    def test_excludes_the_script_runner_and_stateless_workers(self):
        """Neither has memory logs: one has no model, the others keep no session."""
        loader = self._loader({
            "main": {"channels": ["*"]},
            "script-executor": {"channels": ["general"]},
            "graph-worker": {"channels": ["*"], "stateless": True},
            "day-planner": {"channels": ["day-planning", "general"]},
        })

        selected = [agent_id for agent_id, _ in standup_agents(loader)]

        self.assertEqual(selected, ["day-planner", "main"])

    def test_order_is_stable(self):
        loader = self._loader({
            "wiki-gardener": {"channels": ["general"]},
            "day-planner": {"channels": ["general"]},
        })
        self.assertEqual(
            [a for a, _ in standup_agents(loader)], ["day-planner", "wiki-gardener"]
        )


class TestChannelResolution(unittest.TestCase):

    def test_prefers_general_when_permitted(self):
        self.assertEqual(resolve_channel({"channels": ["receipts", "general"]}), "general")

    def test_wildcard_uses_general(self):
        self.assertEqual(resolve_channel({"channels": ["*"]}), "general")

    def test_single_channel_agent_is_called_where_it_is_allowed(self):
        """property-scout only exists in #real-estate. Assuming #general would
        have had delegation refuse the call and silently drop it from the
        standup."""
        self.assertEqual(resolve_channel({"channels": ["real-estate"]}), "real-estate")

    def test_no_channels_falls_back(self):
        self.assertEqual(resolve_channel({}), "general")


class TestParsing(unittest.TestCase):

    def test_extract_tag(self):
        self.assertEqual(extract_tag("learnings", DREAMED_XML), "User prefers morning workouts.")
        self.assertEqual(extract_tag("missing", DREAMED_XML), "")

    def test_parses_a_dreamed_response(self):
        status, learnings = parse_dream_response(DREAMED_XML)
        self.assertEqual(status, "Dreamed")
        self.assertEqual(learnings, "User prefers morning workouts.")

    def test_parses_an_empty_response(self):
        status, learnings = parse_dream_response(EMPTY_XML)
        self.assertEqual(status, "No new memories")
        self.assertEqual(learnings, "")

    def test_conversational_reply_is_not_treated_as_a_quiet_night(self):
        """An agent that answered in prose did not dream. Rendering that as
        'No new memories' is exactly the kind of invented row this replaces."""
        status, detail = parse_dream_response("Sure! I'd be happy to help with that.")
        self.assertIsNone(status)
        self.assertIn("no <dream_response>", detail)

    def test_empty_reply_is_reported(self):
        status, detail = parse_dream_response("")
        self.assertIsNone(status)
        self.assertEqual(detail, "empty response")


class TestRendering(unittest.TestCase):

    def test_dreamed_row(self):
        row = render_row({
            "agent_id": "day-planner",
            "config": {"emoji": "🌼", "name": "Daisy"},
            "status": "Dreamed",
            "learnings": "Prefers mornings.",
        })
        self.assertEqual(row, "🌼 Daisy | 🌙 Dreamed -> Key Update: Prefers mornings.")

    def test_quiet_row(self):
        row = render_row({
            "agent_id": "day-planner",
            "config": {"emoji": "🌼", "name": "Daisy"},
            "status": "No new memories",
            "learnings": "",
        })
        self.assertEqual(row, "🌼 Daisy | 💤 No new memories to process today.")

    def test_failure_is_visible_rather_than_omitted(self):
        row = render_row({
            "agent_id": "day-planner",
            "config": {"emoji": "🌼", "name": "Daisy"},
            "error": "timed out",
        })
        self.assertIn("⚠️ Standup failed: timed out", row)

    def test_dreamed_without_learnings_still_reads_sensibly(self):
        row = render_row({
            "agent_id": "x",
            "config": {"emoji": "🤖", "name": "X"},
            "status": "Dreamed",
            "learnings": "",
        })
        self.assertIn("no notable learnings recorded", row)

    def test_header_and_body(self):
        output = render(
            [{
                "agent_id": "day-planner",
                "config": {"emoji": "🌼", "name": "Daisy"},
                "status": "Dreamed",
                "learnings": "Prefers mornings.",
            }],
            today="2026-09-17",
        )
        lines = output.split("\n")
        self.assertEqual(lines[0], "**Nightly Dream Standup - 2026-09-17**")
        self.assertEqual(lines[1], "")
        self.assertIn("🌼 Daisy", lines[2])


class TestDream(unittest.IsolatedAsyncioTestCase):

    async def test_triggers_the_agent_on_a_permitted_channel(self):
        config = {"channels": ["real-estate"], "emoji": "🏠", "name": "Scout"}

        with patch("scripts.dream_standup.agent_call") as mock_tool:
            mock_tool.ainvoke = AsyncMock(return_value=_tool_response(DREAMED_XML))
            result = await dream("property-scout", config, asyncio.Semaphore(1))

        args = mock_tool.ainvoke.await_args.args[0]
        self.assertEqual(args["agent_id"], "property-scout")
        self.assertEqual(args["channel"], "real-estate")
        self.assertEqual(result["status"], "Dreamed")

    async def test_reported_errors_become_a_failed_row(self):
        with patch("scripts.dream_standup.agent_call") as mock_tool:
            mock_tool.ainvoke = AsyncMock(
                return_value="<tool_response><payload></payload><errors>Agent unreachable</errors></tool_response>"
            )
            result = await dream("x", {"channels": ["general"]}, asyncio.Semaphore(1))

        self.assertEqual(result["error"], "Agent unreachable")

    async def test_an_exception_does_not_abort_the_standup(self):
        """One agent failing must not take the other eleven rows with it."""
        with patch("scripts.dream_standup.agent_call") as mock_tool:
            mock_tool.ainvoke = AsyncMock(side_effect=RuntimeError("boom"))
            result = await dream("x", {"channels": ["general"]}, asyncio.Semaphore(1))

        self.assertEqual(result["error"], "boom")


if __name__ == "__main__":
    unittest.main()
