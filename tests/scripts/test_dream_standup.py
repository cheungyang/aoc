"""Tests for the nightly dream standup script.

The value of moving this out of a prompt is that a missing or malformed reply
becomes visible instead of being smoothed over, so most of these tests are about
what the script does when an agent misbehaves. Memory v2: the dream replies
with ops on numbered entries; `dream_ops` (tested on its own) applies them.
"""
import asyncio
import datetime
import os
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.knowledge.memory import dream_ops, store
from core.knowledge.memory import state as mstate
from core.knowledge.memory.entries import PRIVATE, PROFILE, Entry, MemoryFile
from core.runtime.delegation import DelegationResult
from core.util import format_tool_response
from core.util.config import Config
from scripts import dream_standup as ds
from scripts.dream_standup import (
    build_compaction_prompt,
    build_prompt,
    dream,
    quiet_stdout,
    render,
    render_row,
    resolve_channel,
    standup_agents,
)

TODAY = datetime.date(2026, 9, 24)


def dream_xml(ops="", status="Dreamed", learnings="User prefers morning workouts."):
    return (f"<dream_response><status>{status}</status><ops>{ops}</ops>"
            f"<errors>None</errors><learnings>{learnings}</learnings></dream_response>")


EMPTY_XML = "<dream_response><status>No new memories</status><errors>None</errors><learnings></learnings></dream_response>"


def ok(text):
    return DelegationResult(agent_id="x", text=text)


def _deadline(seconds=60):
    """A standup deadline `seconds` from now on the running loop."""
    return asyncio.get_running_loop().time() + seconds


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

    def logs(self, agent_id):
        try:
            return sorted(os.listdir(self.logs_dir(agent_id)))
        except FileNotFoundError:
            return []

    def night(self, tags=(("food", "Diet."),), subscriptions=None):
        return {
            "today": TODAY,
            "tags": list(tags),
            "tag_names": [name for name, _ in tags],
            "subscriptions": subscriptions or {},
            "state": mstate.load(),
            "changed_topics": set(),
        }


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
        self.assertEqual([a for a, _ in standup_agents(loader)], ["day-planner", "main"])

    def test_order_is_stable(self):
        loader = self._loader({"wiki-gardener": {}, "day-planner": {}})
        self.assertEqual([a for a, _ in standup_agents(loader)], ["day-planner", "wiki-gardener"])


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


class TestPrompt(unittest.TestCase):

    def test_inlines_the_input_and_the_contract(self):
        prompt = build_prompt(dream_ops.DreamInput(xml="<dream_input>X</dream_input>", ids={}))
        self.assertTrue(prompt.startswith("Run your dream skill."))
        self.assertIn("<dream_input>X</dream_input>", prompt)
        self.assertIn("Do not use any tools", prompt)
        self.assertIn("<system_memory_log>", prompt)
        self.assertIn(dream_ops.RESPONSE_FORMAT, prompt)
        self.assertNotIn("pkm/", prompt)
        self.assertNotIn("memory_logs/", prompt)

    def test_compaction_prompt(self):
        prompt = build_compaction_prompt(dream_ops.DreamInput(xml="<compaction_input/>", ids={}))
        self.assertIn("<compaction_input/>", prompt)
        self.assertIn(dream_ops.COMPACTION_FORMAT, prompt)
        self.assertIn("Do not add new facts", prompt)


class TestRendering(unittest.TestCase):
    CONFIG = {"emoji": "🌼", "name": "Daisy"}

    def row(self, **result):
        return render_row({"agent_id": "day-planner", "config": self.CONFIG, **result}, result.pop("state", None))

    def test_dreamed_row(self):
        self.assertEqual(self.row(status="Dreamed", learnings="Prefers mornings."),
                         "🌼 Daisy | 🌙 Dreamed -> Key Update: Prefers mornings.")

    def test_quiet_row(self):
        self.assertEqual(self.row(status="No new memories", learnings=""),
                         "🌼 Daisy | 💤 No new memories to process today.")

    def test_dreamed_without_learnings_still_reads_sensibly(self):
        self.assertIn("no notable learnings recorded", self.row(status="Dreamed", learnings=""))

    def test_failure_is_visible_rather_than_omitted(self):
        self.assertIn("⚠️ Standup failed: timed out", self.row(error="timed out"))

    def test_repeated_failure_is_an_alarm(self):
        state = {"dreams": {"day-planner": {"consecutive_failures": 2}}}
        row = render_row({"agent_id": "day-planner", "config": self.CONFIG, "error": "boom"}, state)
        self.assertIn("🚨 Dream failed 2 nights in a row: boom", row)

    def test_report_details_and_profile_changes(self):
        report = dream_ops.Report(archived={"evicted": 1}, profile_changes=["PROFILE + Lives in San Jose."])
        row = self.row(status="Dreamed", learnings="x", report=report)
        self.assertEqual(row, "🌼 Daisy | 🌙 Dreamed -> Key Update: x (evicted 1)\n    PROFILE + Lives in San Jose.")

    def test_header_body_and_sections(self):
        output = render(
            [{"agent_id": "day-planner", "config": self.CONFIG, "status": "Dreamed", "learnings": "x"}],
            today="2026-09-17", extra=["🧹 Compacted food"], alerts=["🚨 a: stalled"], suggestions=["add it?"],
        )
        lines = output.split("\n")
        self.assertEqual(lines[0], "**Nightly Dream Standup - 2026-09-17**")
        self.assertEqual(lines[1], "")
        self.assertIn("🌼 Daisy", lines[2])
        self.assertEqual(lines[3:], ["🧹 Compacted food", "", "**Alerts**", "🚨 a: stalled",
                                     "", "**Suggestions**", "- add it?"])


class TestDream(VaultMixin, unittest.IsolatedAsyncioTestCase):

    async def _dream(self, reply=None, agent_id="day-planner", config=None, side_effect=None, night=None,
                     deadline=60):
        night = night or self.night()
        mock = AsyncMock(side_effect=side_effect) if side_effect else AsyncMock(return_value=ok(reply))
        with patch.object(ds, "stream_delegate", mock):
            result = await dream(agent_id, config or {"channels": ["general"]}, asyncio.Semaphore(1),
                                 _deadline(deadline), night)
        return result, mock

    async def test_dreamed_applies_ops_and_consumes_the_logs(self):
        self.log("day-planner", "2026-09-20.md", "- prefers mornings")
        night = self.night(subscriptions={"day-planner": ["food"]})

        result, call = await self._dream(dream_xml('<add tag="food">Oat milk OK.</add>'
                                                    '<add tag="private">Mornings work.</add>'), night=night)

        self.assertEqual(result["status"], "Dreamed")
        self.assertEqual(result["learnings"], "User prefers morning workouts.")
        self.assertEqual([e.text for e in store.load(store.topic_scope("food")).entries], ["Oat milk OK."])
        self.assertEqual([e.text for e in store.load(store.private_scope("day-planner", PRIVATE)).entries],
                         ["Mornings work."])
        self.assertEqual(self.logs("day-planner"), [])
        self.assertEqual(night["changed_topics"], {"food"})
        prompt = call.await_args.kwargs["prompt"]
        self.assertIn("- prefers mornings", prompt)

    async def test_dream_is_non_recording_and_floor_tiered(self):
        self.log("day-planner")
        _, call = await self._dream(EMPTY_XML, config={"channels": ["general"], "model": "FLASH_LITE"})
        kwargs = call.await_args.kwargs
        self.assertIs(kwargs["record_memory"], False)
        self.assertEqual(kwargs["model"], "FLASH")
        self.assertEqual(kwargs["caller"], "script-executor")

        _, call = await self._dream(EMPTY_XML, config={"channels": ["general"], "model": "PRO"})
        self.assertEqual(call.await_args.kwargs["model"], "PRO")

    async def test_existing_entries_are_shown_with_ids(self):
        self.log("day-planner")
        store.save(store.profile_scope(), MemoryFile([Entry(PROFILE, "Lives in San Jose.", "main", TODAY, TODAY)]))
        _, call = await self._dream(dream_xml('<confirm id="e1"/>'))
        self.assertIn('<entry id="e1" tag="profile">Lives in San Jose.</entry>', call.await_args.kwargs["prompt"])
        self.assertEqual(store.load(store.profile_scope()).entries[0].count, 2)

    async def test_no_new_memories_consumes_the_logs_and_writes_nothing(self):
        self.log("day-planner", "2026-09-20.md")
        result, _ = await self._dream(EMPTY_XML)
        self.assertEqual(result["status"], "No new memories")
        self.assertEqual(self.logs("day-planner"), [])
        self.assertFalse(os.path.exists(store.private_scope("day-planner", PRIVATE).path))

    async def test_a_malformed_reply_keeps_the_logs(self):
        for reply in ("Sure! Happy to help.", dream_xml(status="[Dreamed or No new memories]"),
                      "<dream_response><status>Dreamed</status><errors>None</errors></dream_response>"):
            self.log("day-planner", "2026-09-20.md")
            result, _ = await self._dream(reply)
            self.assertIn("error", result, reply)
            self.assertEqual(self.logs("day-planner"), ["2026-09-20.md"])

    async def test_a_log_written_by_the_dream_itself_is_removed(self):
        """A dream about logs that writes a log would dream every night forever."""
        self.log("day-planner", "2026-09-20.md", "old")

        async def reply_and_log(**_):
            self.log("day-planner", "2026-09-24.md", "- dreamed")
            with open(os.path.join(self.logs_dir("day-planner"), "2026-09-20.md"), "a") as f:
                f.write("\n- appended by the dream")
            return ok(EMPTY_XML)

        await self._dream(side_effect=reply_and_log)
        self.assertEqual(self.logs("day-planner"), [])

    async def test_a_failed_dream_restores_the_logs_it_was_given(self):
        self.log("day-planner", "2026-09-20.md", "old")

        async def log_then_fail(**_):
            self.log("day-planner", "2026-09-24.md", "- dreamed")
            with open(os.path.join(self.logs_dir("day-planner"), "2026-09-20.md"), "a") as f:
                f.write("\n- appended by the dream")
            raise RuntimeError("boom")

        result, _ = await self._dream(side_effect=log_then_fail)
        self.assertEqual(result["error"], "boom")
        self.assertEqual(self.logs("day-planner"), ["2026-09-20.md"])
        with open(os.path.join(self.logs_dir("day-planner"), "2026-09-20.md")) as f:
            self.assertEqual(f.read(), "old")

    async def test_a_delegation_error_becomes_a_failed_row(self):
        self.log("day-planner")
        result, _ = await self._dream(side_effect=lambda **_: DelegationResult("x", error="Agent unreachable"))
        self.assertEqual(result["error"], "Agent unreachable")
        self.assertEqual(self.logs("day-planner"), ["log.md"])

    async def test_a_dream_queued_past_the_deadline_is_not_started(self):
        self.log("day-planner", "2026-09-20.md", "old")
        result, call = await self._dream(EMPTY_XML, deadline=-1)
        call.assert_not_called()
        self.assertIn("deadline", result["error"])
        self.assertEqual(self.logs("day-planner"), ["2026-09-20.md"])

    async def test_a_dream_is_cut_off_at_the_deadline(self):
        self.log("day-planner", "2026-09-20.md", "old")

        async def slow(**_):
            await asyncio.sleep(5)

        result, _ = await self._dream(side_effect=slow, deadline=0.05)
        self.assertIn("deadline", result["error"])
        self.assertEqual(self.logs("day-planner"), ["2026-09-20.md"])

    async def test_the_deadline_stops_short_of_the_scheduler_kill(self):
        with patch.object(ds, "default_timeout", return_value=300):
            now = asyncio.get_running_loop().time()
            deadline = ds.standup_deadline()
        self.assertAlmostEqual(deadline - now, 300 - ds.DEADLINE_MARGIN, delta=1)

    async def test_triggers_the_agent_on_a_permitted_channel(self):
        _, call = await self._dream(EMPTY_XML, agent_id="property-scout", config={"channels": ["real-estate"]})
        kwargs = call.await_args.kwargs
        self.assertEqual((kwargs["agent_id"], kwargs["channel"]), ("property-scout", "real-estate"))

    async def test_exceptions_timeouts_and_cancellation_do_not_abort_the_standup(self):
        """One agent failing must not take the other eleven rows with it."""
        for exc, expected in ((RuntimeError("boom"), "boom"), (asyncio.TimeoutError(), "timed out"),
                              (asyncio.CancelledError(), "CancelledError")):
            result, _ = await self._dream(side_effect=exc)
            self.assertIn(expected, result["error"])

    async def test_dream_does_not_create_directories(self):
        await self._dream(EMPTY_XML, agent_id="ghost")
        self.assertFalse(os.path.exists(self.agent_dir("ghost")))


class TestCompactionAndAlerts(VaultMixin, unittest.IsolatedAsyncioTestCase):

    async def test_compacts_changed_topics_that_need_it(self):
        store.save(store.topic_scope("food"), MemoryFile([Entry("food", "x" * 900, "a", TODAY, TODAY),
                                                          Entry("food", "y" * 50, "a", TODAY, TODAY)]))
        night = self.night()
        night["changed_topics"] = {"food"}
        reply = format_tool_response("agent_call", payload=dream_xml('<merge ids="e1 e2">short</merge>'))
        with patch.object(ds, "agent_call") as tool:
            tool.ainvoke = AsyncMock(return_value=reply)
            line = await ds.compact(night, _deadline())
        args = tool.ainvoke.await_args.args[0]
        self.assertEqual((args["agent_id"], args["caller"]), ("graph-worker", "script-executor"))
        self.assertIn("<compaction_input", args["prompt"])
        self.assertTrue(line.startswith("🧹 Compacted food"))
        self.assertEqual([e.text for e in store.load(store.topic_scope("food")).entries], ["short"])

    async def test_a_failed_worker_call_is_reported(self):
        store.save(store.topic_scope("food"), MemoryFile([Entry("food", "x" * 900, "a", TODAY, TODAY)]))
        night = self.night()
        night["changed_topics"] = {"food"}
        reply = format_tool_response("agent_call", payload="", errors="Error: quota exceeded")
        with patch.object(ds, "agent_call") as tool:
            tool.ainvoke = AsyncMock(return_value=reply)
            line = await ds.compact(night, _deadline())
        self.assertEqual(line, "🧹 Compaction failed (food): Error: quota exceeded")
        self.assertEqual(len(store.load(store.topic_scope("food")).entries), 1)

    async def test_nothing_to_compact(self):
        night = self.night()
        night["changed_topics"] = {"food"}
        self.assertIsNone(await ds.compact(night, _deadline()))

    def test_stalled_logs_raise_an_alert(self):
        self.log("a", "2026-09-10.md")
        self.log("b", "2026-09-22.md")
        self.log("c", "notes.md")
        alerts = ds.stall_alerts([("a", {}), ("b", {}), ("c", {})], TODAY)
        self.assertEqual(alerts, ["🚨 a: oldest unconsumed memory log is 14 days old"])

    def test_suggestions_only_on_sunday(self):
        night = self.night()
        for _ in range(3):
            mstate.record_event(night["state"], mstate.UNKNOWN_TAG, "a", "pets", TODAY)
        self.assertEqual(ds.weekly_suggestions(night), [])
        night["today"] = datetime.date(2026, 9, 27)
        self.assertTrue(any("`pets`" in line for line in ds.weekly_suggestions(night)))


class TestQuietStdout(unittest.TestCase):

    def test_quiet_stdout_suppresses_runtime_narration(self):
        import contextlib
        import io

        outer_buf = io.StringIO()
        with contextlib.redirect_stdout(outer_buf):
            with quiet_stdout(verbose=False):
                print("GraphsLoader: Loaded/Reloaded graph 'main'")
        self.assertEqual(outer_buf.getvalue(), "")

    def test_quiet_stdout_emits_to_stderr_under_verbose(self):
        import contextlib
        import io

        outer_buf, err_buf = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(outer_buf), contextlib.redirect_stderr(err_buf):
            with quiet_stdout(verbose=True):
                print("GraphsLoader: Loaded/Reloaded graph 'main'")
        self.assertEqual(outer_buf.getvalue(), "")
        self.assertIn("GraphsLoader", err_buf.getvalue())


class TestWorkGate(VaultMixin, unittest.IsolatedAsyncioTestCase):
    """Level 1 skips the whole standup; level 2 skips agents with nothing new."""

    def loader(self, ids, config=None):
        loader = MagicMock()
        loader.list_agent_ids.return_value = ids
        loader.get_agent_config.side_effect = lambda aid: dict(config or {"channels": ["general"]})
        return loader

    def test_schedulable_by_the_scheduler(self):
        from core.scheduler.script_runner import check_script
        self.assertEqual(check_script("dream_standup.py"), [])

    def test_no_logs_means_no_standup(self):
        self.log("a", name=".DS_Store")
        with patch.object(ds, "AgentsLoader", return_value=self.loader(["a"])):
            has, reason = ds.has_work(MagicMock())
        self.assertFalse(has)
        self.assertIn("no memory logs", reason)

    def test_any_existing_log_counts_regardless_of_age(self):
        # A failed dream leaves its logs behind; they must be retried.
        self.log("old", mtime=1000.0)
        self.log("new", mtime=3000.0)
        self.assertEqual(ds.agents_with_logs(), {"old", "new"})
        with patch.object(ds, "AgentsLoader", return_value=self.loader(["old", "new"])):
            has, reason = ds.has_work(MagicMock(last_success_at=5000.0))
        self.assertTrue(has)
        self.assertIn("new, old", reason)

    def test_logs_of_ineligible_agents_do_not_wake_the_standup(self):
        self.log("worker")
        with patch.object(ds, "AgentsLoader", return_value=self.loader(["worker"], {"stateless": True})):
            has, _ = ds.has_work(MagicMock())
        self.assertFalse(has)

    def test_filter_keeps_only_agents_with_logs(self):
        self.log("b")
        self.assertEqual(ds.filter_agents_with_logs([("a", {}), ("b", {}), ("c", {})]), [("b", {})])

    async def test_run_standup_dreams_only_agents_with_logs_and_records_health(self):
        self.log("b")
        with patch.object(ds, "AgentsLoader", return_value=self.loader(["a", "b"])), \
             patch.object(ds, "get_local_now", return_value=datetime.datetime(2026, 9, 24, 3)), \
             patch.object(ds, "stream_delegate", AsyncMock(return_value=ok(dream_xml()))) as call:
            out = await ds.run_standup()
            self.assertEqual(call.await_count, 1)
            self.assertEqual(call.await_args.kwargs["agent_id"], "b")
            self.assertIn("Dreamed", out)
            self.assertEqual(mstate.load()["dreams"]["b"]["last_success"], "2026-09-24")

            call.reset_mock()
            await ds.run_standup(include_all=True)
            self.assertEqual(call.await_count, 2)

    async def test_malformed_tags_stop_the_standup_visibly(self):
        self.log("b")
        store.write_atomic(store.tags_path(), "## Food\nDiet.\n")
        with patch.object(ds, "AgentsLoader", return_value=self.loader(["b"])), \
             patch.object(ds, "stream_delegate", AsyncMock()) as call:
            out = await ds.run_standup()
        call.assert_not_called()
        self.assertIn("Not run", out)

    async def test_nothing_new_prints_nothing(self):
        with patch.object(ds, "AgentsLoader", return_value=self.loader(["a"], {})), \
             patch.object(ds, "stream_delegate", AsyncMock()) as call:
            self.assertEqual(await ds.run_standup(), "")
        call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
