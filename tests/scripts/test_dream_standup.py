"""Tests for the nightly dream standup script.

The value of moving this out of a prompt is that a missing or malformed reply
becomes visible instead of being smoothed over, so most of these tests are about
what the script does when an agent misbehaves.
"""
import asyncio
import os
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.util.config import Config
from scripts.dream_standup import (
    build_prompt,
    dream,
    extract_tag,
    parse_dream_response,
    quiet_stdout,
    render,
    render_row,
    resolve_channel,
    standup_agents,
)

DREAMED_XML = """
<dream_response>
  <status>Dreamed</status>
  <memory_md>- Morning workouts stick. (Ref: 2026-09-20)</memory_md>
  <feedback_md>- Keep plans under five items.</feedback_md>
  <context_md></context_md>
  <errors>None</errors>
  <learnings>User prefers morning workouts.</learnings>
</dream_response>
"""

EMPTY_XML = """
<dream_response>
  <status>No new memories</status>
  <errors>None</errors>
  <learnings></learnings>
</dream_response>
"""

# What main and reward-travel sent back on 2026-09-21: the skill's template.
ECHOED_TEMPLATE_XML = """
<dream_response>
  <status>Dreamed</status>
  <memory_md>[full new contents of MEMORY.md]</memory_md>
  <feedback_md>- Keep plans under five items.</feedback_md>
  <context_md></context_md>
  <errors>None</errors>
  <learnings>[one-line highlight]</learnings>
</dream_response>
"""


def _deadline(seconds=60):
    """A standup deadline `seconds` from now on the running loop."""
    return asyncio.get_running_loop().time() + seconds


def _tool_response(inner):
    """Wraps content the way `format_tool_response` does."""
    return f"<tool_response><payload>{inner}</payload><errors>None</errors></tool_response>"


class VaultMixin:
    """Points `Config().pkm_dir` at a throwaway vault for the test."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name
        Config().pkm_dir = self.root
        self.addCleanup(setattr, Config(), "pkm_dir", None)

    def agent_dir(self, agent_id):
        return os.path.join(self.root, "agents", agent_id)

    def logs_dir(self, agent_id):
        return os.path.join(self.agent_dir(agent_id), "memory_logs")

    def log(self, agent_id, name="log.md", text="x", mtime=None):
        os.makedirs(self.logs_dir(agent_id), exist_ok=True)
        path = os.path.join(self.logs_dir(agent_id), name)
        with open(path, "w") as f:
            f.write(text)
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def memory_file(self, agent_id, name, text):
        os.makedirs(self.agent_dir(agent_id), exist_ok=True)
        with open(os.path.join(self.agent_dir(agent_id), name), "w") as f:
            f.write(text)

    def read(self, agent_id, name):
        with open(os.path.join(self.agent_dir(agent_id), name)) as f:
            return f.read()

    def logs(self, agent_id):
        try:
            return sorted(os.listdir(self.logs_dir(agent_id)))
        except FileNotFoundError:
            return []


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


class TestPrompt(unittest.TestCase):

    def test_inlines_current_files_and_logs(self):
        prompt = build_prompt(
            "day-planner",
            {"MEMORY.md": "old memory", "FEEDBACK.md": "", "CONTEXT.md": "ctx"},
            {"2026-09-20.md": "- [09:00:00] prefers mornings"},
        )
        self.assertTrue(prompt.startswith("Run your dream skill."))
        self.assertIn('<file name="MEMORY.md">\nold memory\n</file>', prompt)
        self.assertIn('<log name="2026-09-20.md">\n- [09:00:00] prefers mornings\n</log>', prompt)
        self.assertIn("Do not use any tools", prompt)
        self.assertIn("<system_memory_log>", prompt)
        self.assertIn("<memory_md>", prompt)

    def test_no_filesystem_path_is_given_to_the_agent(self):
        """The path was what the weak model got wrong; it no longer needs one."""
        prompt = build_prompt("day-planner", {}, {})
        self.assertNotIn("pkm/", prompt)
        self.assertNotIn("memory_logs/", prompt)


class TestDream(VaultMixin, unittest.IsolatedAsyncioTestCase):

    async def _dream(self, reply, agent_id="day-planner", side_effect=None):
        with patch("scripts.dream_standup.agent_call") as mock_tool:
            if side_effect is not None:
                mock_tool.ainvoke = AsyncMock(side_effect=side_effect)
            else:
                mock_tool.ainvoke = AsyncMock(return_value=reply)
            result = await dream(agent_id, {"channels": ["general"]}, asyncio.Semaphore(1), _deadline())
        return result, mock_tool

    async def test_dreamed_writes_the_files_and_consumes_the_logs(self):
        self.log("day-planner", "2026-09-20.md", "- prefers mornings")
        self.memory_file("day-planner", "CONTEXT.md", "stale context")

        result, tool = await self._dream(_tool_response(DREAMED_XML))

        self.assertEqual(result["status"], "Dreamed")
        self.assertEqual(self.read("day-planner", "MEMORY.md"), "- Morning workouts stick. (Ref: 2026-09-20)\n")
        self.assertEqual(self.read("day-planner", "FEEDBACK.md"), "- Keep plans under five items.\n")
        self.assertEqual(self.read("day-planner", "CONTEXT.md"), "")
        self.assertEqual(self.logs("day-planner"), [])
        prompt = tool.ainvoke.await_args.args[0]["prompt"]
        self.assertIn("- prefers mornings", prompt)
        self.assertIn("stale context", prompt)

    async def test_no_new_memories_consumes_the_logs_and_writes_nothing(self):
        self.log("day-planner", "2026-09-20.md")
        self.memory_file("day-planner", "MEMORY.md", "keep me")

        result, _ = await self._dream(_tool_response(EMPTY_XML))

        self.assertEqual(result["status"], "No new memories")
        self.assertEqual(self.read("day-planner", "MEMORY.md"), "keep me")
        self.assertEqual(self.logs("day-planner"), [])

    async def test_missing_file_tag_keeps_the_logs(self):
        self.log("day-planner", "2026-09-20.md")
        self.memory_file("day-planner", "MEMORY.md", "keep me")
        reply = DREAMED_XML.replace("<context_md></context_md>", "")

        result, _ = await self._dream(reply)

        self.assertIn("<context_md>", result["error"])
        self.assertEqual(self.read("day-planner", "MEMORY.md"), "keep me")
        self.assertEqual(self.logs("day-planner"), ["2026-09-20.md"])

    async def test_echoed_template_is_rejected(self):
        self.log("day-planner", "2026-09-20.md")
        self.memory_file("day-planner", "MEMORY.md", "keep me")

        result, _ = await self._dream(ECHOED_TEMPLATE_XML)

        self.assertIn("placeholder", result["error"])
        self.assertEqual(self.read("day-planner", "MEMORY.md"), "keep me")
        self.assertFalse(os.path.exists(os.path.join(self.agent_dir("day-planner"), "FEEDBACK.md")))
        self.assertEqual(self.logs("day-planner"), ["2026-09-20.md"])

    async def test_unrecognised_status_keeps_the_logs(self):
        self.log("day-planner", "2026-09-20.md")
        reply = EMPTY_XML.replace("No new memories", "[Strictly either 'Dreamed' or 'No new memories']")

        result, _ = await self._dream(reply)

        self.assertIn("unrecognised status", result["error"])
        self.assertEqual(self.logs("day-planner"), ["2026-09-20.md"])

    async def test_a_log_written_by_the_dream_itself_is_removed(self):
        """A dream reply with a <system_memory_log> is saved as today's log;
        left alone it would make the agent dream every night forever."""
        self.log("day-planner", "2026-09-20.md", "old")
        self_log = os.path.join(self.logs_dir("day-planner"), "2026-09-24.md")

        async def reply_and_log(_args):
            with open(self_log, "w") as f:
                f.write("- dreamed")
            with open(os.path.join(self.logs_dir("day-planner"), "2026-09-20.md"), "a") as f:
                f.write("\n- appended by the dream")
            return EMPTY_XML

        with patch("scripts.dream_standup.agent_call") as mock_tool:
            mock_tool.ainvoke = reply_and_log
            await dream("day-planner", {"channels": ["general"]}, asyncio.Semaphore(1), _deadline())

        self.assertEqual(self.logs("day-planner"), [])

    async def test_a_failed_dream_restores_the_logs_it_was_given(self):
        self.log("day-planner", "2026-09-20.md", "old")

        async def log_then_fail(_args):
            with open(os.path.join(self.logs_dir("day-planner"), "2026-09-24.md"), "w") as f:
                f.write("- dreamed")
            with open(os.path.join(self.logs_dir("day-planner"), "2026-09-20.md"), "a") as f:
                f.write("\n- appended by the dream")
            raise RuntimeError("boom")

        with patch("scripts.dream_standup.agent_call") as mock_tool:
            mock_tool.ainvoke = log_then_fail
            result = await dream("day-planner", {"channels": ["general"]}, asyncio.Semaphore(1), _deadline())

        self.assertEqual(result["error"], "boom")
        self.assertEqual(self.logs("day-planner"), ["2026-09-20.md"])
        with open(os.path.join(self.logs_dir("day-planner"), "2026-09-20.md")) as f:
            self.assertEqual(f.read(), "old")

    async def test_a_dream_queued_past_the_deadline_is_not_started(self):
        """It keeps its logs untouched and is retried the next night."""
        self.log("day-planner", "2026-09-20.md", "old")
        with patch("scripts.dream_standup.agent_call") as mock_tool:
            mock_tool.ainvoke = AsyncMock(return_value=EMPTY_XML)
            result = await dream("day-planner", {"channels": ["general"]}, asyncio.Semaphore(1), _deadline(-1))

        mock_tool.ainvoke.assert_not_called()
        self.assertIn("deadline", result["error"])
        self.assertEqual(self.logs("day-planner"), ["2026-09-20.md"])

    async def test_a_dream_is_cut_off_at_the_deadline(self):
        self.log("day-planner", "2026-09-20.md", "old")

        async def slow(_args):
            await asyncio.sleep(5)

        with patch("scripts.dream_standup.agent_call") as mock_tool:
            mock_tool.ainvoke = slow
            result = await dream("day-planner", {"channels": ["general"]}, asyncio.Semaphore(1), _deadline(0.05))

        self.assertIn("deadline", result["error"])
        self.assertEqual(self.logs("day-planner"), ["2026-09-20.md"])

    async def test_the_deadline_stops_short_of_the_scheduler_kill(self):
        from scripts import dream_standup as ds
        with patch.object(ds, "default_timeout", return_value=300):
            now = asyncio.get_running_loop().time()
            deadline = ds.standup_deadline()
        self.assertAlmostEqual(deadline - now, 300 - ds.DEADLINE_MARGIN, delta=1)

    async def test_triggers_the_agent_on_a_permitted_channel(self):
        config = {"channels": ["real-estate"], "emoji": "🏠", "name": "Scout"}

        with patch("scripts.dream_standup.agent_call") as mock_tool:
            mock_tool.ainvoke = AsyncMock(return_value=_tool_response(DREAMED_XML))
            result = await dream("property-scout", config, asyncio.Semaphore(1), _deadline())

        args = mock_tool.ainvoke.await_args.args[0]
        self.assertEqual(args["agent_id"], "property-scout")
        self.assertEqual(args["channel"], "real-estate")
        self.assertEqual(result["status"], "Dreamed")
        self.assertEqual(result["learnings"], "User prefers morning workouts.")

    async def test_reported_errors_become_a_failed_row(self):
        with patch("scripts.dream_standup.agent_call") as mock_tool:
            mock_tool.ainvoke = AsyncMock(
                return_value="<tool_response><payload></payload><errors>Agent unreachable</errors></tool_response>"
            )
            result = await dream("x", {"channels": ["general"]}, asyncio.Semaphore(1), _deadline())

        self.assertEqual(result["error"], "Agent unreachable")

    async def test_an_exception_does_not_abort_the_standup(self):
        """One agent failing must not take the other eleven rows with it."""
        with patch("scripts.dream_standup.agent_call") as mock_tool:
            mock_tool.ainvoke = AsyncMock(side_effect=RuntimeError("boom"))
            result = await dream("x", {"channels": ["general"]}, asyncio.Semaphore(1), _deadline())

        self.assertEqual(result["error"], "boom")

    async def test_timeout_does_not_abort_the_standup(self):
        with patch("scripts.dream_standup.agent_call") as mock_tool:
            mock_tool.ainvoke = AsyncMock(side_effect=asyncio.TimeoutError())
            result = await dream("x", {"channels": ["general"]}, asyncio.Semaphore(1), _deadline())

        self.assertIn("timed out", result["error"])

    async def test_cancellation_does_not_abort_the_standup(self):
        with patch("scripts.dream_standup.agent_call") as mock_tool:
            mock_tool.ainvoke = AsyncMock(side_effect=asyncio.CancelledError())
            result = await dream("x", {"channels": ["general"]}, asyncio.Semaphore(1), _deadline())

        self.assertIn("CancelledError", result["error"])


class TestQuietStdout(unittest.TestCase):

    def test_quiet_stdout_suppresses_runtime_narration(self):
        import io
        import contextlib

        outer_buf = io.StringIO()
        with contextlib.redirect_stdout(outer_buf):
            with quiet_stdout(verbose=False):
                print("GraphsLoader: Loaded/Reloaded graph 'main'")
                print("Loaded 5 tools for agent-designer")

        self.assertEqual(outer_buf.getvalue(), "")

    def test_quiet_stdout_emits_to_stderr_under_verbose(self):
        import io
        import contextlib

        outer_buf = io.StringIO()
        err_buf = io.StringIO()
        with contextlib.redirect_stdout(outer_buf), contextlib.redirect_stderr(err_buf):
            with quiet_stdout(verbose=True):
                print("GraphsLoader: Loaded/Reloaded graph 'main'")

        self.assertEqual(outer_buf.getvalue(), "")
        self.assertIn("GraphsLoader", err_buf.getvalue())


class TestWorkGate(VaultMixin, unittest.IsolatedAsyncioTestCase):
    """Level 1 skips the whole standup; level 2 skips agents with nothing new."""

    def test_schedulable_by_the_scheduler(self):
        from core.scheduler.script_runner import check_script
        self.assertEqual(check_script("dream_standup.py"), [])

    def test_no_logs_means_no_standup(self):
        from scripts.dream_standup import has_work
        self.log("a", name=".DS_Store")
        has, reason = has_work(MagicMock())
        self.assertFalse(has)
        self.assertIn("no memory logs", reason)

    def test_any_existing_log_counts_regardless_of_age(self):
        # A failed dream leaves its logs behind; they must be retried.
        from scripts.dream_standup import agents_with_logs, has_work
        self.log("old", mtime=1000.0)
        self.log("new", mtime=3000.0)
        self.assertEqual(agents_with_logs(), {"old", "new"})
        has, reason = has_work(MagicMock(last_success_at=5000.0))
        self.assertTrue(has)
        self.assertIn("new, old", reason)

    def test_filter_keeps_only_agents_with_logs(self):
        from scripts.dream_standup import filter_agents_with_logs
        self.log("b")
        agents = [("a", {}), ("b", {}), ("c", {})]
        self.assertEqual(filter_agents_with_logs(agents), [("b", {})])

    async def test_run_standup_dreams_only_agents_with_logs(self):
        from scripts import dream_standup as ds
        self.log("b")
        loader = MagicMock()
        loader.list_agent_ids.return_value = ["a", "b"]
        loader.get_agent_config.side_effect = lambda aid: {"channels": ["general"]}
        with patch.object(ds, "AgentsLoader", return_value=loader), \
             patch.object(ds, "agent_call") as tool:
            tool.ainvoke = AsyncMock(return_value=_tool_response(DREAMED_XML))
            out = await ds.run_standup()
            self.assertEqual(tool.ainvoke.await_count, 1)
            self.assertEqual(tool.ainvoke.await_args.args[0]["agent_id"], "b")
            self.assertIn("Dreamed", out)

            tool.ainvoke.reset_mock()
            await ds.run_standup(include_all=True)
            self.assertEqual(tool.ainvoke.await_count, 2)

    async def test_nothing_new_prints_nothing(self):
        from scripts import dream_standup as ds
        loader = MagicMock()
        loader.list_agent_ids.return_value = ["a"]
        loader.get_agent_config.side_effect = lambda aid: {}
        with patch.object(ds, "AgentsLoader", return_value=loader), \
             patch.object(ds, "agent_call") as tool:
            tool.ainvoke = AsyncMock()
            self.assertEqual(await ds.run_standup(), "")
            tool.ainvoke.assert_not_called()

    async def test_dream_does_not_create_directories(self):
        from scripts import dream_standup as ds
        with patch.object(ds, "agent_call") as tool:
            tool.ainvoke = AsyncMock(return_value=_tool_response(EMPTY_XML))
            await ds.dream("ghost", {"channels": ["general"]}, asyncio.Semaphore(1), _deadline())
        self.assertFalse(os.path.exists(self.agent_dir("ghost")))


if __name__ == "__main__":
    unittest.main()
