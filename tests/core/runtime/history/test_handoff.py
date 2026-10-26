"""Tests for hand-off capsules (core/runtime/history/handoff.py).

Most tests hand `build_capsule` a transcript directly, through a store double,
so timestamps are exact. The last ones use the real session store and
checkpointer (conftest points them at a throwaway memory.db) to show that
building a capsule reads the source session and never writes to it.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from core.runtime.foundation.sessions import SessionManager
from core.runtime.history import handoff
from core.runtime.history.handoff import (
    CAPSULE_TOKENS,
    LINE_CHARS,
    MAX_EXCHANGES,
    MAX_TOUCHED,
    SUMMARY_CHARS,
    build_capsule,
    capsule_for,
    with_capsule,
)
from core.runtime.history.budget import estimate_message_tokens
from core.runtime.history.summary import SUMMARY_PREFIX, SUMMARY_SUFFIX
from core.util.local_time import get_local_timezone

NOW = datetime(2026, 10, 24, 8, 12, tzinfo=get_local_timezone())


def at(hour, minute, days_ago=0):
    return int((NOW.replace(hour=hour, minute=minute) - timedelta(days=days_ago)).timestamp())


class _Store:
    """SessionStore.load_history, from rows given up front."""

    def __init__(self, rows):
        self.rows = rows

    def load_history(self, session_id, limit=50):
        return self.rows[-limit:] if limit else list(self.rows)


class _Checkpointer:
    """SqliteCheckpointer.get_tuple, with a checkpoint holding `messages`."""

    def __init__(self, messages=()):
        self.messages = list(messages)

    def get_tuple(self, config):
        if not self.messages:
            return None
        return SimpleNamespace(checkpoint={"channel_values": {"messages": self.messages}})


def row(role, message, ts):
    return {"from": role, "message": message, "ts": ts}


def tool(name, extra, args, ts):
    return row("system", f"Tool {name}{extra}:{args}", ts)


SESSION = SessionManager.get_session("wiki-gardener", source="tool", channel="pkm-wiki")


def capsule(rows, messages=(), **kwargs):
    return build_capsule(SESSION, agent_name="William", store=_Store(rows), checkpointer=_Checkpointer(messages),
                         now=NOW, **kwargs)


class TestRecent(unittest.TestCase):

    def test_the_proposals_example(self):
        rows = [
            row("ai", "[Posted to this channel by scheduled job 'wiki-ingest' at 2026-10-24 07:00 PDT]\n"
                      "Ingested \"Effective context engineering\"; no page for prompt caching.", at(7, 0)),
            row("user", "<caller>main</caller>\nhow does it relate to the memory consolidation stuff we have?", at(8, 9)),
            row("ai", "[]", at(8, 9)),
            tool("vault_search", " [path: pkm/wiki/topics/AI_Memory_and_Dreaming.md]",
                 "{'path': 'pkm/wiki/topics/AI_Memory_and_Dreaming.md', 'action': 'read'}", at(8, 10)),
            row("system", "Tool Output [0.004s]: <vault_search_response>...</vault_search_response>", at(8, 10)),
            row("ai", "Close cousins. Compaction trades old turns for a summary; caching keeps the prefix.", at(8, 10)),
        ]
        self.assertEqual(capsule(rows), "\n".join([
            '<handoff_context from="wiki-gardener" channel="pkm-wiki" captured="2026-10-24 08:12">',
            "<recent>",
            "[07:00] William (scheduled post 'wiki-ingest'): Ingested \"Effective context engineering\"; "
            "no page for prompt caching.",
            "[08:09] You: how does it relate to the memory consolidation stuff we have?",
            "[08:10] William: Close cousins. Compaction trades old turns for a summary; caching keeps the prefix.",
            "</recent>",
            "<touched>",
            "pkm/wiki/topics/AI_Memory_and_Dreaming.md (read, 08:10)",
            "</touched>",
            "</handoff_context>",
        ]))

    def test_an_empty_session_gives_no_capsule(self):
        self.assertEqual(capsule([]), "")
        self.assertEqual(capsule([row("ai", "[]", at(8, 0)), row("system", "Tool x:{}", at(8, 0))]), "")

    def test_only_the_last_exchanges(self):
        rows = []
        for i in range(MAX_EXCHANGES + 2):
            rows += [row("user", f"question {i}", at(7, i)), row("ai", f"answer {i}", at(7, i))]
        text = capsule(rows)
        self.assertNotIn("question 1", text)
        for i in range(2, MAX_EXCHANGES + 2):
            self.assertIn(f"question {i}", text)

    def test_the_reply_is_the_agents_last_words_in_the_exchange(self):
        """What it said before a tool call ("Let me check") is left out."""
        rows = [
            row("user", "is the fish still there?", at(8, 0)),
            row("ai", "Let me check the inventory.", at(8, 0)),
            row("ai", "[]", at(8, 1)),
            row("ai", "Yes, two portions of tilapia.", at(8, 1)),
        ]
        text = capsule(rows)
        self.assertNotIn("Let me check", text)
        self.assertIn("[08:01] William: Yes, two portions of tilapia.", text)

    def test_what_the_reader_saw_of_a_reply(self):
        rows = [row("user", "next", at(8, 0)), row("ai", (
            "<speech>Next task.</speech>Moved on.\n<poll><question>Priority?</question><options>"
            "<option><text>High</text></option></options></poll>"
            "<system_memory_log>user skips often</system_memory_log>"), at(8, 0))]
        text = capsule(rows)
        self.assertIn("William: Moved on. [poll: Priority?]", text)
        for hidden in ("<speech>", "Next task.", "<poll>", "user skips often"):
            self.assertNotIn(hidden, text)

    def test_what_wraps_your_words_is_left_out(self):
        rows = [
            row("user", '<caller>main</caller>\nlooks good\n\n<replying_to author="William">the post</replying_to>', at(8, 0)),
            row("user", '[{"type": "text", "text": "this receipt"}, {"type": "image_url", "image_url": {"url": "x"}}]',
                at(8, 1)),
            row("user", 'add it\n\n<handoff_context from="topic-researcher" channel="topic-research">'
                        '<recent>old</recent></handoff_context>', at(8, 2)),
        ]
        text = capsule(rows)
        self.assertIn("You: looks good\n", text)
        self.assertIn("You: this receipt [image]", text)
        self.assertIn("You: add it\n", text)
        for hidden in ("<caller>", "replying_to", "the post", 'from="topic-researcher"', "old"):
            self.assertNotIn(hidden, text)

    def test_older_lines_carry_their_date(self):
        text = capsule([row("user", "yesterday's question", at(21, 30, days_ago=1))])
        self.assertIn("[2026-10-23 21:30] You: yesterday's question", text)


class TestTouched(unittest.TestCase):

    def test_paths_from_the_tools_arguments_newest_first_each_once(self):
        rows = [
            row("user", "tidy the work list", at(8, 0)),
            tool("filesystem", ' [action "read" on pkm/a.md, "replace_block" on pkm/b.md]',
                 "{'instructions': [{'action': 'read', 'path': 'pkm/a.md'}, "
                 "{'action': 'replace_block', 'path': 'pkm/b.md', 'old_block': 'x'}]}", at(8, 1)),
            tool("filesystem", ' [action "read" on pkm/a.md]', "{'instructions': [{'action': 'read', 'path': 'pkm/a.md'}]}",
                 at(8, 2)),
            row("ai", "Done.", at(8, 3)),
        ]
        text = capsule(rows)
        self.assertIn("<touched>\npkm/a.md (read, 08:02)\npkm/b.md (replace_block, 08:01)\n</touched>", text)

    def test_the_log_summary_when_the_arguments_cant_be_read(self):
        rows = [row("user", "go", at(8, 0)),
                tool("filesystem", ' [action "overwrite" on pkm/c.md]', "{'instructions': [truncated", at(8, 1)),
                row("ai", "ok", at(8, 1))]
        self.assertIn("pkm/c.md (overwrite, 08:01)", capsule(rows))

    def test_only_the_kept_exchanges_and_at_most_a_few(self):
        rows = [row("user", "old", at(6, 0)),
                tool("filesystem", "", "{'path': 'pkm/old.md'}", at(6, 0)),
                row("ai", "ok", at(6, 0))]
        rows += [row("user", f"q{i}", at(7, i)) for i in range(MAX_EXCHANGES)]
        rows += [tool("filesystem", "", f"{{'path': 'pkm/{i}.md'}}", at(7, 30)) for i in range(MAX_TOUCHED + 3)]
        text = capsule(rows)
        self.assertNotIn("pkm/old.md", text)
        touched = text.split("<touched>\n")[1].split("\n</touched>")[0].splitlines()
        self.assertEqual(len(touched), MAX_TOUCHED)


class TestSummary(unittest.TestCase):

    def test_the_pruners_summary_comes_first(self):
        summary = SystemMessage(content=f"{SUMMARY_PREFIX}\nUser Requests: <caller>main</caller> triage\n{SUMMARY_SUFFIX}")
        text = capsule([row("user", "next", at(8, 0))], messages=[summary, HumanMessage("next")])
        self.assertIn("<summary>\nUser Requests: triage\n</summary>\n<recent>", text)

    def test_no_summary_without_one_at_the_head_of_the_checkpoint(self):
        text = capsule([row("user", "next", at(8, 0))], messages=[HumanMessage("hi"), AIMessage("hello")])
        self.assertNotIn("<summary>", text)

    def test_a_summary_alone_is_a_capsule(self):
        summary = SystemMessage(content=f"{SUMMARY_PREFIX}\nearlier work\n{SUMMARY_SUFFIX}")
        self.assertIn("<summary>\nearlier work\n</summary>", capsule([], messages=[summary]))

    def test_an_unreadable_checkpoint_only_loses_the_summary(self):
        class Broken:
            def get_tuple(self, config):
                raise RuntimeError("corrupt")
        text = build_capsule(SESSION, agent_name="William", store=_Store([row("user", "hi", at(8, 0))]),
                             checkpointer=Broken(), now=NOW)
        self.assertIn("You: hi", text)


class TestCaps(unittest.TestCase):

    def test_a_long_line_is_cut(self):
        text = capsule([row("user", "x" * 2000, at(8, 0))])
        line = [l for l in text.splitlines() if l.startswith("[08:00] You:")][0]
        self.assertEqual(line, f"[08:00] You: {'x' * LINE_CHARS[0]} [...]")

    def test_a_long_summary_is_cut(self):
        summary = SystemMessage(content=f"{SUMMARY_PREFIX}{'s' * 5000}{SUMMARY_SUFFIX}")
        text = capsule([], messages=[summary])
        self.assertIn("s" * SUMMARY_CHARS + " [...]", text)
        self.assertNotIn("s" * (SUMMARY_CHARS + 1), text)

    def _long_exchanges(self):
        summary = SystemMessage(content=f"{SUMMARY_PREFIX}{'s' * 5000}{SUMMARY_SUFFIX}")
        rows = []
        for i in range(MAX_EXCHANGES):
            rows += [row("user", f"q{i} " + "u" * 3000, at(7, i)), row("ai", f"a{i} " + "a" * 3000, at(7, i))]
        return rows, [summary]

    def test_over_the_cap_lines_shrink_first(self):
        rows, messages = self._long_exchanges()
        text = capsule(rows, messages=messages)
        self.assertLessEqual(estimate_message_tokens(text), CAPSULE_TOKENS)
        self.assertIn("q0", text)
        self.assertIn("<summary>", text)
        self.assertNotIn("u" * (LINE_CHARS[0] + 1), text)

    def test_then_the_summary_goes_then_the_oldest_exchanges(self):
        rows, messages = self._long_exchanges()
        with patch.object(handoff, "CAPSULE_TOKENS", 400):
            text = capsule(rows, messages=messages)
        self.assertLessEqual(estimate_message_tokens(text), 400)
        self.assertNotIn("<summary>", text)
        self.assertIn("q0", text)

        with patch.object(handoff, "CAPSULE_TOKENS", 150):
            text = capsule(rows, messages=messages)
        self.assertLessEqual(estimate_message_tokens(text), 150)
        self.assertNotIn("q0", text)
        self.assertIn(f"q{MAX_EXCHANGES - 1}", text)  # the newest exchange stays


class TestWithCapsule(unittest.TestCase):

    def test_after_your_words(self):
        self.assertEqual(with_capsule("Ted, go", "<handoff_context/>"), "Ted, go\n\n<handoff_context/>")

    def test_a_multimodal_prompt_keeps_its_parts(self):
        prompt = [{"type": "text", "text": "Ramsay, add these"}, {"type": "image_url", "image_url": {"url": "x"}}]
        self.assertEqual(with_capsule(prompt, "<c/>"), prompt + [{"type": "text", "text": "\n\n<c/>"}])

    def test_no_capsule_leaves_the_prompt_alone(self):
        self.assertEqual(with_capsule("Ted, go", ""), "Ted, go")


class TestFromTheRealStore(unittest.TestCase):
    """The session store and checkpointer on conftest's throwaway memory.db."""

    def _seed(self, session):
        from core.knowledge.memory.sqlite_checkpointer import SqliteCheckpointer
        from core.runtime.history.channel_posts import append_to_session
        from core.storage.sessions import SessionStore

        store = SessionStore()
        store.append_message(session.session_id, "user", "<caller>main</caller>\nwhat did the article say?")
        store.append_message(session.session_id, "ai", "It leans on prompt caching.")
        append_to_session(session, HumanMessage("what did the article say?"), checkpointer=SqliteCheckpointer())
        append_to_session(session, AIMessage("It leans on prompt caching."), checkpointer=SqliteCheckpointer())
        return store

    def _snapshot(self, session, store):
        from core.knowledge.memory.sqlite_checkpointer import SqliteCheckpointer

        found = SqliteCheckpointer().get_tuple(
            {"configurable": {"thread_id": session.get_session_thread_id(), "checkpoint_ns": ""}})
        return (store.load_history(session.session_id, limit=0), found.checkpoint["id"],
                [m.content for m in found.checkpoint["channel_values"]["messages"]])

    def test_building_a_capsule_reads_the_source_session_and_writes_nothing(self):
        session = SessionManager.get_session("wiki-gardener", source="tool", channel="pkm-wiki")
        store = self._seed(session)
        before = self._snapshot(session, store)

        text = build_capsule(session, agent_name="William")

        self.assertIn("You: what did the article say?", text)
        self.assertIn("William: It leans on prompt caching.", text)
        self.assertEqual(self._snapshot(session, store), before)

    def test_capsule_for_reads_the_session_that_answers_the_channel(self):
        session = SessionManager.get_session("wiki-gardener", source="tool", channel="pkm-wiki")
        self._seed(session)
        loader = SimpleNamespace(get_agent_config=lambda agent_id: {"name": "William"})
        with patch.object(handoff, "reply_sessions", return_value=[session]) as found:
            text = capsule_for("pkm-wiki", loader=loader)
        found.assert_called_once_with("pkm-wiki", loader=loader)
        self.assertIn("William: It leans on prompt caching.", text)

    def test_capsule_for_gives_nothing_rather_than_failing(self):
        with patch.object(handoff, "reply_sessions", side_effect=RuntimeError("no loader")):
            self.assertEqual(capsule_for("pkm-wiki"), "")
        with patch.object(handoff, "reply_sessions", return_value=[]):
            self.assertEqual(capsule_for("pkm-wiki"), "")


if __name__ == "__main__":
    unittest.main()
