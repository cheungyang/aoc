"""core/tooling/invalid_call.py: the reply to a call whose arguments a tool's schema refuses.

The graph builder gives it to every tool an agent gets, so a refused call still ends
with a reply: a `Tool Output` row in the session log, which the tool_failure detector
counts, and the turn's later replies pair with the right calls.
"""
import unittest
from typing import List, Literal
from unittest.mock import MagicMock

from langchain_core.tools import tool
from pydantic import ValidationError
from typing_extensions import TypedDict

from core.learning.detectors import first_error, session_friction
from core.runtime.foundation.sessions import SessionManager
from core.runtime.observability.transcript import LoggingHandler, tool_status
from core.tooling import Report, agent_tool
from core.tooling.invalid_call import AGAIN, give_reply
from tools.filesystem import filesystem


@agent_tool
def card_window(report: Report, card: str, days: int = 7, period: Literal["week", "month"] = "week") -> dict:
    """Where a card's credits stand.

    Args:
        card: The card.
        days: How far ahead to look.
        period: The credit period.
    """
    return {"card": card, "days": days, "period": period}


class Item(TypedDict):
    name: str
    qty: int


@tool
def pantry(path: str, items: List[Item] = []) -> str:
    """Adds items to a pantry list.

    Args:
        path: The list's file.
        items: What to add.
    """
    return "<pantry_response>\n  <payload>added</payload>\n  <errors>None</errors>\n</pantry_response>"


def call(t, args, callbacks=()):
    return t.invoke({"name": t.name, "args": args, "id": "call-1", "type": "tool_call"},
                    config={"callbacks": list(callbacks)})


def error_lines(text):
    return text.split("<errors>", 1)[1].split("</errors>", 1)[0].splitlines()


class TestTheReply(unittest.TestCase):

    def test_it_names_what_is_missing_and_what_is_bad_never_a_value(self):
        reply = call(give_reply(card_window.model_copy()), {"cards": "gold card", "days": "two", "period": "year"})
        self.assertEqual(reply.status, "error")
        self.assertEqual(error_lines(reply.content), [
            'Error: no "card". Sent: "cards", "days", "period".',
            'Error: bad "days": Input should be a valid integer, unable to parse string as an integer.',
            "Error: bad \"period\": Input should be 'week' or 'month'.",
            AGAIN,
        ])
        for value in ("gold card", "two", "year"):
            self.assertNotIn(value, reply.content)

    def test_an_agent_tool_answers_in_its_envelope_failed(self):
        reply = call(give_reply(card_window.model_copy()), {"days": 3})
        self.assertIn("<status>failed</status>", reply.content)
        self.assertEqual(tool_status(reply.content), "failed")
        self.assertEqual(error_lines(reply.content)[0], 'Error: no "card". Sent: "days".')

    def test_a_shared_tool_answers_in_its_own_envelope(self):
        reply = call(give_reply(pantry.model_copy()), {"items": [{"name": "rice", "qty": "lots"}, {"qty": 2}]})
        self.assertTrue(reply.content.startswith("<pantry_response>"), reply.content)
        self.assertNotIn("<status>", reply.content)
        self.assertEqual(tool_status(reply.content), "failed")
        self.assertEqual(error_lines(reply.content), [
            'Error: no "path". Sent: "items".',
            'Error: bad "items[0].qty": Input should be a valid integer, unable to parse string as an integer.',
            'Error: no "items[1].name". Sent: "qty".',
            AGAIN,
        ])
        self.assertNotIn("rice", reply.content)

    def test_arguments_missing_together_share_a_line(self):
        @tool
        def move(source: str, destination: str, reason: str) -> str:
            """Moves a file.

            Args:
                source: From.
                destination: To.
                reason: Why.
            """
            return "moved"

        reply = call(give_reply(move), {})
        self.assertEqual(error_lines(reply.content)[0],
                         'Error: no "source", "destination" or "reason". Sent: no arguments.')

    def test_its_first_line_is_the_friction_cards_evidence(self):
        reply = call(give_reply(card_window.model_copy()), {"card": "x", "days": "two"})
        self.assertEqual(first_error(reply.content),
                         'Error: bad "days": Input should be a valid integer, unable to parse string as an integer.')


class TestWhoGetsIt(unittest.TestCase):

    def test_a_tool_without_a_way_of_its_own(self):
        copy = pantry.model_copy()
        self.assertFalse(copy.handle_validation_error)
        self.assertIs(give_reply(copy), copy)
        self.assertTrue(callable(copy.handle_validation_error))

    def test_a_tool_with_its_own_keeps_it(self):
        copy = filesystem.model_copy()
        own = copy.handle_validation_error
        give_reply(copy)
        self.assertIs(copy.handle_validation_error, own, "filesystem's reply also shows an instruction's shape")

    def test_giving_it_twice_changes_nothing(self):
        copy = give_reply(pantry.model_copy())
        first = copy.handle_validation_error
        give_reply(copy)
        self.assertIs(copy.handle_validation_error, first)


class TestTheSessionLog(unittest.TestCase):
    """A refused call, then a good one, through the transcript handler and the detectors."""

    def setUp(self):
        session = SessionManager.get_session(agent_id="test-agent", source="discord", channel="tool-errors")
        self.handler = LoggingHandler(session=session)
        self.handler.manager = MagicMock()

    def rows(self):
        logged = [c.args[2] for c in self.handler.manager.append_message.call_args_list]
        rows = [{"id": 1, "entry_type": "message", "from_role": "user", "message": "add rice", "created_at": 1.0}]
        rows += [{"id": i, "entry_type": "message", "from_role": "system", "message": text, "created_at": float(i)}
                 for i, text in enumerate(logged, start=2)]
        return rows

    def test_without_a_reply_the_call_raises_and_leaves_no_output_row(self):
        with self.assertRaises(ValidationError):
            call(card_window.model_copy(), {"cards": "x"}, [self.handler])
        call(pantry.model_copy(), {"path": "pantry.md"}, [self.handler])
        texts = [r["message"] for r in self.rows()[1:]]
        self.assertEqual([t.split(":", 1)[0].split(" [")[0] for t in texts],
                         ["Tool card_window", "Tool pantry", "Tool Output"])
        self.assertEqual(session_friction("ctx_test", "test-agent", self.rows()), [],
                         "the failure goes uncounted, and pantry's output pairs with card_window")

    def test_with_it_the_detector_counts_the_failure_and_pairs_the_rest(self):
        call(give_reply(card_window.model_copy()), {"cards": "x"}, [self.handler])
        call(give_reply(pantry.model_copy()), {"path": "pantry.md"}, [self.handler])
        texts = [r["message"] for r in self.rows()[1:]]
        self.assertEqual([t.split(":", 1)[0].split(" [")[0] for t in texts],
                         ["Tool card_window", "Tool Output", "Tool pantry", "Tool Output"])
        [event] = session_friction("ctx_test", "test-agent", self.rows())
        self.assertEqual((event.detector, event.details["tool"], event.details["status"]),
                         ("tool_failure", "card_window", "failed"))
        self.assertEqual(event.evidence, 'Error: no "card". Sent: "cards".')


if __name__ == "__main__":
    unittest.main()
