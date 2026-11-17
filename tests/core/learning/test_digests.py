"""Episode digests (core/learning/digests.py): the turns around a card, cut by code."""
import unittest

from core.learning import digests

AT = 1_800_000_000.0


def message(row_id, role, text, at=AT):
    return {"id": row_id, "entry_type": "message", "from_role": role, "message": text, "created_at": at}


SESSION = [
    message(10, "user", "<caller>main</caller> [Mon 09:00] What's due today?"),
    message(11, "ai", "Three things: the report, the call and the gym."),
    message(20, "user", "<caller>main</caller> [Mon 09:05] Read the topic notes, my api_key=sk-abcdefghijklmnopqrstuvwx"),
    message(21, "system", 'Tool filesystem: {"instructions": [{"read": "pkm/wiki/topics"}]}'),
    message(22, "system", "Tool Output [0.01s]: <errors>Error: Both 'action' and 'path' are required in each instruction.</errors>"),
    message(23, "ai", "I couldn't read them."),
]


class FakeStore:
    def __init__(self, rows=(), turns=(), events=None):
        self.rows, self.turns, self.events = list(rows), list(turns), events or {}

    def session_rows(self, table, since, until):
        return [r for r in self.rows if since <= r["created_at"] < until]

    def ledger_turns_around(self, turn_id, before=2):
        return self.turns

    def learning_event(self, event_id):
        return self.events.get(event_id)


def card(**fields):
    base = {"id": 7, "at": AT, "agent_id": "topic-researcher", "detector": "tool_failure", "evidence": "Error: Both",
            "evidence_role": "tool", "severity": 2, "session": "ctx_topic_researcher_x", "source": "ctx_topic_researcher_x",
            "source_id": "22"}
    base.update(fields)
    return base


class TestFromASession(unittest.TestCase):

    def test_the_cards_turn_and_the_one_before(self):
        found = digests.digest(FakeStore(SESSION), card())
        self.assertEqual(found["card"]["id"], 7)
        before, hit = found["turns"]
        self.assertEqual(before, {"you": "What's due today?", "tools": [],
                                  "agent": "Three things: the report, the call and the gym."})
        self.assertTrue(hit["card"])
        self.assertEqual(hit["tools"], [{"tool": "filesystem", "status": "failed",
                                         "error": "Error: Both 'action' and 'path' are required in each instruction."}])
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwx", hit["you"], "secrets are scrubbed")
        self.assertTrue(hit["you"].startswith("Read the topic notes"))

    def test_rows_gone_from_the_table_say_so(self):
        found = digests.digest(FakeStore([]), card())
        self.assertEqual(found["turns"], [])
        self.assertIn("no longer", found["note"])

    def test_a_range_of_rows(self):
        found = digests.digest(FakeStore(SESSION), card(source_id="20-23", detector="tool_loop"))
        self.assertTrue(found["turns"][-1]["card"])


class TestFromTheLedgerAndTheLog(unittest.TestCase):

    def test_a_ledger_turn_with_the_ones_before_it(self):
        turns = [{"id": 4, "surface": "text", "user_text": "[Mon 09:00] list them", "reply_text": "Here are all 12.",
                  "effects": [{"tool": "task_query", "status": "ok"}]},
                 {"id": 5, "surface": "text", "user_text": "no, just the top three", "reply_text": "Sure: ...",
                  "effects": []}]
        found = digests.digest(FakeStore(turns=turns), card(source="turn_ledger", source_id="5", detector="correction"))
        self.assertEqual([t["you"] for t in found["turns"]], ["list them", "no, just the top three"])
        self.assertEqual(found["turns"][0]["tools"], [{"tool": "task_query", "status": "ok"}])
        self.assertTrue(found["turns"][1]["card"])

    def test_a_logged_event(self):
        events = {3: {"kind": "refusal", "name": "filesystem", "channel": "day-planning",
                      "detail": {"action": "write", "path": "pkm/vault/x.md"}}}
        found = digests.digest(FakeStore(events=events), card(source="learning_events", source_id="3", detector="refusal"))
        self.assertEqual(found["event"]["detail"], {"action": "write", "path": "pkm/vault/x.md"})

    def test_anything_else_is_the_card_alone(self):
        found = digests.digest(FakeStore(), card(source="routes", source_id="9", agent_id=""))
        self.assertEqual(found["turns"], [])
        self.assertEqual(found["card"]["agent_id"], "?")


class TestAsText(unittest.TestCase):

    def test_every_word_the_personal_check_compares_against(self):
        found = digests.digest(FakeStore(SESSION), card())
        text = digests.as_text([found])
        for words in ("Error: Both", "What's due today?", "I couldn't read them.", "required in each instruction"):
            self.assertIn(words, text)


if __name__ == "__main__":
    unittest.main()
