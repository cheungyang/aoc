"""@agent_tool: permission, the injected Report, explain, the envelope, and failures as `failed`.

`from __future__ import annotations` on purpose: the decorator must resolve
string annotations against the tool's own module.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from typing import List, Literal, Optional
from unittest.mock import patch

from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.errors import GraphInterrupt
from pydantic import BaseModel, Field

from core.loaders.tools_loader import ToolsLoader
from core.tooling import (
    AgentTool,
    DataProblem,
    Report,
    ToolError,
    agent_tool,
    current_agent_id,
    has_permission,
    pkm_path,
    require_permission,
    tool_grant,
)
from core.config.settings import Config
from tests.helpers import envelope, execution_context


class Item(BaseModel):
    """One row to change."""
    name: str = Field(description="Item name as listed.")
    qty: Optional[float] = None


def lib_parse(text: str) -> list:
    """Stands in for an agent's lib/ parser that keeps its own report."""
    report = Report()
    report.missing("pkm/wallet/kitchen/INVENTORY.md", "no 冷凍 section")
    raise DataProblem(report)


@agent_tool
def fake_snapshot(report: Report, mode: Literal["all", "expiring"] = "all", limit: int = 5,
                  items: Optional[List[Item]] = None) -> dict:
    """What's in the kitchen, in one read.

    Reads: pkm/wallet/kitchen/INVENTORY.md.
    Computes: days left per item.
    Leaves out: what to cook.

    Args:
        mode: all rows, or only those expiring.
    """
    if mode == "expiring":
        report.problem("pkm/wallet/kitchen/INVENTORY.md", "row 3 has no date")
    report.read("pkm/wallet/kitchen/INVENTORY.md", records=12)
    report.trace("草莓: bought Oct 14 + 3 days -> 1 day left")
    return {"mode": mode, "limit": limit, "items": [i.name for i in items or []], "first": "草莓",
            "explain": report.explain}


@agent_tool
def fake_text(report: Report, text: str = "plain") -> str:
    """Returns text as is.

    Reads: nothing.
    Computes: nothing.
    Leaves out: everything.
    """
    return text


@agent_tool
def fake_failures(report: Report, case: str) -> str:
    """Fails in every way a tool can."""
    if case == "missing":
        report.problem("pkm/a.md", "row 3 unreadable")
        report.missing("pkm/wallet/real-estate/criteria.md", "names no state")
        report.raise_if_failed()
    if case == "lib":
        lib_parse("")
    if case == "bare":
        raise DataProblem(Report())
    if case == "refused":
        raise ToolError("the receipt's lines add up to 41.50, not 42.00; nothing was saved")
    if case == "bug":
        return {}["missing"]
    if case == "interrupt":
        raise GraphInterrupt(())
    return "fine"


@agent_tool
def fake_writer(report: Report, name: str = "2026-10.md") -> str:
    """Appends to a ledger."""
    path = pkm_path("wallet", "finance", name)
    require_permission("append", path)
    return f"appended; may read: {has_permission('read', path)}; agent: {current_agent_id()}; grant: {tool_grant()!r}"


def status(out):
    return envelope(out)["status"]


def section(out, tag):
    return envelope(out)[tag]


class ToolCase(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.pkm = os.path.realpath(tmp.name)
        Config().pkm_dir = self.pkm
        self.addCleanup(setattr, Config(), "pkm_dir", None)
        self.granted = patch.object(ToolsLoader, "check_permission", return_value=True)
        self.check = self.granted.start()
        self.addCleanup(self.granted.stop)

    def call(self, tool, args=None, agent_id="meal-planner"):
        with execution_context(agent_id=agent_id):
            return tool.invoke(args or {})


class TestSchema(unittest.TestCase):

    def test_the_model_sees_the_body_parameters_and_explain_but_not_report(self):
        schema = convert_to_openai_tool(fake_snapshot)["function"]
        self.assertEqual(schema["name"], "fake_snapshot")
        self.assertEqual(list(schema["parameters"]["properties"]), ["mode", "limit", "items", "explain"])
        self.assertNotIn("report", json.dumps(schema))
        explain = schema["parameters"]["properties"]["explain"]
        self.assertEqual((explain["type"], explain["default"]), ("boolean", False))
        self.assertIn("inputs behind each number", explain["description"])
        self.assertEqual(schema["parameters"]["properties"]["mode"]["enum"], ["all", "expiring"])
        self.assertNotIn("required", schema["parameters"])

    def test_it_is_a_langchain_tool_named_after_the_function_with_a_clean_docstring(self):
        self.assertIsInstance(fake_snapshot, AgentTool)
        self.assertEqual(fake_snapshot.name, "fake_snapshot")
        self.assertTrue(fake_snapshot.description.startswith("What's in the kitchen, in one read.\n\nReads: pkm/"))
        self.assertIn("\nComputes: days left per item.\n", fake_snapshot.description)
        self.assertEqual(fake_snapshot.func.__wrapped__.__name__, "fake_snapshot")

    def test_a_badly_shaped_tool_is_refused_when_decorated(self):
        def no_report(limit: int = 1) -> str:
            """Doc."""

        def declares_explain(report: Report, explain: bool = False) -> str:
            """Doc."""

        def star_args(report: Report, *names: str) -> str:
            """Doc."""

        def no_docstring(report: Report) -> str:
            return ""

        async def coroutine(report: Report) -> str:
            """Doc."""

        for func in (no_report, declares_explain, star_args, no_docstring, coroutine):
            with self.subTest(func=func.__name__), self.assertRaises(TypeError):
                agent_tool(func)


class TestCalls(ToolCase):

    def test_an_ok_call_wraps_the_payload_in_the_envelope(self):
        out = self.call(fake_snapshot, {"items": [{"name": "牛奶"}], "limit": 2})
        self.assertEqual(status(out), "ok")
        self.assertEqual(section(out, "issues"), "None")
        self.assertEqual(section(out, "errors"), "None")
        self.assertNotIn("<explain>", out)
        self.assertEqual(json.loads(section(out, "payload")),
                         {"mode": "all", "limit": 2, "items": ["牛奶"], "first": "草莓", "explain": False})
        self.check.assert_called_once()
        ctx, tool = self.check.call_args.args
        self.assertEqual((ctx.agent_id, tool), ("meal-planner", "fake_snapshot"))

    def test_a_string_is_the_payload_as_is(self):
        self.assertEqual(section(self.call(fake_text, {"text": "<b>3</b> rows"}), "payload"), "<b>3</b> rows")
        parts = envelope(self.call(fake_text, {"text": "<errors>quoted from a file</errors>"}))
        self.assertEqual(parts, {"status": "ok", "issues": "None",
                                 "payload": "<errors>quoted from a file</errors>", "errors": "None"})

    def test_issues_make_it_partial(self):
        out = self.call(fake_snapshot, {"mode": "expiring"})
        self.assertEqual(status(out), "partial")
        self.assertEqual(section(out, "issues"), "- pkm/wallet/kitchen/INVENTORY.md: row 3 has no date")

    def test_explain_adds_what_was_read_and_how(self):
        out = self.call(fake_snapshot, {"explain": True})
        self.assertEqual(section(out, "explain"), (
            "Read:\n- pkm/wallet/kitchen/INVENTORY.md (12 records)\n"
            "Computed:\n- 草莓: bought Oct 14 + 3 days -> 1 day left"
        ))
        self.assertTrue(json.loads(section(out, "payload"))["explain"])

    def test_a_call_outside_an_agent_run_is_refused(self):
        out = fake_snapshot.invoke({})
        self.assertEqual(status(out), "failed")
        self.assertIn("no active execution context", section(out, "errors"))
        self.check.assert_not_called()

    def test_an_agent_without_the_grant_is_refused(self):
        self.check.return_value = False
        out = self.call(fake_snapshot, agent_id="day-planner")
        self.assertEqual(status(out), "failed")
        self.assertEqual(section(out, "errors"),
                         "Error: Agent 'day-planner' does not have permission to use fake_snapshot.")
        self.assertEqual(section(out, "payload"), "")

    def test_a_copy_and_an_async_call_behave_the_same(self):
        # GraphBuilder binds a model_copy of each tool and may call it asynchronously.
        with execution_context(agent_id="meal-planner"):
            out = asyncio.run(fake_snapshot.model_copy().ainvoke({"limit": 1}))
        self.assertEqual(json.loads(section(out, "payload"))["limit"], 1)


class TestFailures(ToolCase):

    def test_missing_required_data_is_failed_with_the_issues(self):
        out = self.call(fake_failures, {"case": "missing"})
        self.assertEqual(status(out), "failed")
        self.assertEqual(section(out, "issues"), (
            "- pkm/a.md: row 3 unreadable\n"
            "- pkm/wallet/real-estate/criteria.md: names no state (required)"
        ))
        self.assertEqual(section(out, "errors"), "None")

    def test_a_data_problem_from_lib_brings_its_report(self):
        out = self.call(fake_failures, {"case": "lib"})
        self.assertEqual(status(out), "failed")
        self.assertIn("pkm/wallet/kitchen/INVENTORY.md: no 冷凍 section (required)", out)

    def test_a_bare_data_problem_still_says_something(self):
        out = self.call(fake_failures, {"case": "bare"})
        self.assertEqual(status(out), "failed")
        self.assertEqual(section(out, "issues"), "- fake_failures: required data is missing (required)")

    def test_a_tool_error_is_failed_with_its_message(self):
        out = self.call(fake_failures, {"case": "refused"})
        self.assertEqual(status(out), "failed")
        self.assertEqual(section(out, "errors"),
                         "Error: the receipt's lines add up to 41.50, not 42.00; nothing was saved")

    def test_a_bug_is_failed_and_named_not_raised(self):
        out = self.call(fake_failures, {"case": "bug"})
        self.assertEqual(status(out), "failed")
        self.assertEqual(section(out, "errors"), "Error in fake_failures: KeyError: 'missing'")

    def test_langgraph_control_flow_passes_through(self):
        with self.assertRaises(GraphInterrupt):
            self.call(fake_failures, {"case": "interrupt"})


class TestPermissions(ToolCase):

    def test_a_write_checks_its_target_with_the_running_tool(self):
        def answer(ctx, tool, action=None, path=None, **kwargs):
            return action != "read"

        self.check.side_effect = answer
        with patch.object(ToolsLoader, "get_tool_permissions", return_value={"pkm/wallet/finance": ["append"]}):
            out = self.call(fake_writer, agent_id="receipt-processor")
        self.assertEqual(status(out), "ok")
        self.assertEqual(section(out, "payload"), (
            "appended; may read: False; agent: receipt-processor; grant: {'pkm/wallet/finance': ['append']}"))
        calls = [c.args[1:] for c in self.check.call_args_list]
        path = os.path.join(self.pkm, "wallet", "finance", "2026-10.md")
        self.assertEqual(calls, [("fake_writer",), ("fake_writer", "append", path), ("fake_writer", "read", path)])

    def test_a_refused_write_is_failed_and_says_where(self):
        self.check.side_effect = lambda ctx, tool, action=None, path=None, **kwargs: action is None
        out = self.call(fake_writer, agent_id="receipt-processor")
        self.assertEqual(status(out), "failed")
        self.assertEqual(section(out, "errors"), (
            "Error: Agent 'receipt-processor' does not have permission to 'append' pkm/wallet/finance/2026-10.md."))

    def test_the_checks_work_only_inside_a_tool_call(self):
        for check in (lambda: has_permission("read"), lambda: require_permission("read"), tool_grant):
            with self.assertRaises(RuntimeError):
                check()
        with self.assertRaises(RuntimeError):
            current_agent_id()
        with execution_context(agent_id="meal-planner"):
            self.assertEqual(current_agent_id(), "meal-planner")
            with self.assertRaises(RuntimeError):
                has_permission("read")


if __name__ == "__main__":
    unittest.main()
