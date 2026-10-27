"""Tool hooks (core/tooling/hooks.py): who hears about each finished tool call."""
import pytest

from core.tooling.hooks import ToolCall, ToolHook, set_tool_hooks, tool_called, tool_hooks


class Ear(ToolHook):
    def __init__(self):
        self.heard = []

    def called(self, call):
        self.heard.append(call.tool)


class Broken(ToolHook):
    def called(self, call):
        raise RuntimeError("deaf")


@pytest.fixture(autouse=True)
def restore():
    saved = tool_hooks()
    yield
    set_tool_hooks(saved)


def test_every_hook_hears_every_call_in_order():
    first, second = Ear(), Ear()
    set_tool_hooks([first, second])
    tool_called(ToolCall("ledger_append", {"vendor": "Costco"}))
    tool_called(ToolCall("web_search", {"query": "pumpkins"}, status="failed"))
    assert first.heard == second.heard == ["ledger_append", "web_search"]
    assert tool_hooks() == (first, second)


def test_a_hook_that_raises_is_logged_and_the_others_still_hear(capsys):
    ear = Ear()
    set_tool_hooks([Broken(), ear])
    tool_called(ToolCall("calendar_add"))
    assert ear.heard == ["calendar_add"]
    assert "Broken missed calendar_add: RuntimeError: deaf" in capsys.readouterr().out


def test_no_hooks_no_work():
    set_tool_hooks([])
    tool_called(ToolCall("filesystem"))
    assert tool_hooks() == ()
