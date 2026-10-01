"""Scheduled posts reach the session that answers the next reply in the channel."""
import datetime
import unittest
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, MessagesState, StateGraph

from core.knowledge.memory.sqlite_checkpointer import SqliteCheckpointer
from core.knowledge.memory.sqlite_session_store import SqliteSessionStore
from core.runtime import channel_context as cc
from core.runtime.session_manager import SessionManager


class FakeLoader:
    """Just enough of AgentsLoader for routing decisions."""

    def __init__(self, configs):
        self.configs = configs

    def list_agent_ids(self):
        return list(self.configs)

    def get_agent_config(self, agent_id):
        return self.configs.get(agent_id)

    def hosts_channel(self, agent_id, channel_name):
        hosts = (self.configs.get(agent_id) or {}).get("channel_hosts") or []
        return channel_name.lower() in [h.lower() for h in hosts]

    def eligible_agents_for_channel(self, channel_name, exclude_agent_id=None):
        out = []
        for aid, cfg in self.configs.items():
            chans = [c.lower() for c in cfg.get("channels") or []]
            if aid != exclude_agent_id and "*" not in chans and channel_name.lower() in chans:
                out.append(aid)
        return sorted(out)


def configs(**overrides):
    base = {
        "main": {"channel_hosts": ["day-planning", "general"], "channels": ["*"], "tools": {"agent_call": {}}},
        "day-planner": {"channels": ["day-planning", "general"]},
        "meal-planner": {"channels": ["meal-planning", "general"]},
        "graph-worker": {"channels": ["*"], "stateless": True},
    }
    base.update(overrides)
    return base


def echo_graph():
    """A graph whose reply reports how much history it was handed."""
    def node(state):
        texts = [m.content for m in state["messages"]]
        return {"messages": [AIMessage(content=f"saw {len(texts)}: {texts[:-1]}")]}

    g = StateGraph(MessagesState)
    g.add_node("n", node)
    g.add_edge(START, "n")
    g.add_edge("n", END)
    return g.compile(checkpointer=SqliteCheckpointer())


class TestReplySessions(unittest.TestCase):
    def test_sole_specialist_channel_targets_its_delegation_session(self):
        sessions = cc.reply_sessions("day-planning", loader=FakeLoader(configs()))
        self.assertEqual([s.session_id for s in sessions], ["day-planner:tool:day-planning"])

    def test_crowded_channel_targets_the_host_concierge_session(self):
        sessions = cc.reply_sessions("general", loader=FakeLoader(configs()))
        self.assertEqual([s.session_id for s in sessions], ["main:discord:general"])

    def test_unhosted_channel_has_no_reply_path(self):
        self.assertEqual(cc.reply_sessions("meal-planning", loader=FakeLoader(configs())), [])

    def test_stateless_specialist_is_skipped(self):
        cfg = configs(**{"day-planner": {"channels": ["day-planning"], "stateless": True}})
        self.assertEqual(cc.reply_sessions("day-planning", loader=FakeLoader(cfg)), [])

    def test_thread_keys_on_parent_name_and_thread_id(self):
        parent = SimpleNamespace(name="day-planning")
        thread = SimpleNamespace(name="morning", id=42, parent=parent, is_thread=True)
        sessions = cc.reply_sessions(thread, loader=FakeLoader(configs()))
        self.assertEqual([s.session_id for s in sessions], ["day-planner:tool:day-planning:42"])


class TestFormatPost(unittest.TestCase):
    def test_attributed_and_truncated(self):
        at = datetime.datetime(2026, 10, 1, 7, 0)
        body = cc.format_post("x" * (cc.MAX_RECORDED_CHARS + 50), "day-planner:morning", posted_at=at)
        self.assertTrue(body.startswith("[Posted to this channel by scheduled job 'day-planner:morning' at 2026-10-01 07:00]"))
        self.assertIn("truncated", body)
        self.assertLess(len(body), cc.MAX_RECORDED_CHARS + 200)


class TestAppendAndReplay(unittest.IsolatedAsyncioTestCase):
    def session(self, agent="day-planner", source="tool", channel="day-planning"):
        return SessionManager.get_session(agent, source=source, channel=channel)

    def config(self, session):
        return {"configurable": {"thread_id": session.get_session_thread_id()}}

    async def test_fresh_thread_replays_the_post_on_first_turn(self):
        s = self.session()
        self.assertTrue(cc.append_to_session(s, AIMessage(content="POST")))
        out = await echo_graph().ainvoke({"messages": [HumanMessage(content="reply")]}, self.config(s))
        self.assertEqual(out["messages"][-1].content, "saw 2: ['POST']")

    async def test_existing_thread_keeps_history_and_appends(self):
        s = self.session()
        graph = echo_graph()
        await graph.ainvoke({"messages": [HumanMessage(content="hi")]}, self.config(s))
        self.assertTrue(cc.append_to_session(s, AIMessage(content="POST")))
        out = await graph.ainvoke({"messages": [HumanMessage(content="reply")]}, self.config(s))
        texts = [m.content for m in out["messages"]]
        self.assertEqual(texts[:4], ["hi", "saw 1: []", "POST", "reply"])

    def test_interrupted_tool_call_stays_paired_after_append(self):
        from langchain_core.messages import ToolMessage
        s = self.session()
        cp = SqliteCheckpointer()
        dangling = AIMessage(content="", tool_calls=[{"name": "t", "args": {}, "id": "c1"}])
        cc.append_to_session(s, dangling, checkpointer=cp)
        self.assertTrue(cc.append_to_session(s, AIMessage(content="POST"), checkpointer=cp))
        msgs = cp.get_tuple(self.config(s)).checkpoint["channel_values"]["messages"]
        self.assertIsInstance(msgs[-2], ToolMessage)
        self.assertEqual(msgs[-2].tool_call_id, "c1")
        self.assertEqual(msgs[-1].content, "POST")

    def test_record_channel_post_writes_checkpoint_and_transcript(self):
        written = cc.record_channel_post("day-planning", "Today's plan", "day-planner:morning",
                                         loader=FakeLoader(configs()))
        self.assertEqual(written, ["day-planner:tool:day-planning"])
        tup = SqliteCheckpointer().get_tuple({"configurable": {"thread_id": written[0]}})
        last = tup.checkpoint["channel_values"]["messages"][-1]
        self.assertIn("Today's plan", last.content)
        self.assertIn("day-planner:morning", last.content)
        log = SqliteSessionStore().load_history(written[0], limit=5)
        self.assertTrue(any("Today's plan" in str(row) for row in log))

    def test_record_channel_post_never_raises(self):
        class Broken(FakeLoader):
            def list_agent_ids(self):
                raise RuntimeError("boom")
        self.assertEqual(cc.record_channel_post("day-planning", "x", "o", loader=Broken({})), [])

    def test_empty_text_or_missing_channel_records_nothing(self):
        loader = FakeLoader(configs())
        self.assertEqual(cc.record_channel_post("day-planning", "   ", "o", loader=loader), [])
        self.assertEqual(cc.record_channel_post(None, "text", "o", loader=loader), [])


if __name__ == "__main__":
    unittest.main()
