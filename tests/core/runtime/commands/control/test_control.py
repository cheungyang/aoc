"""Control-thread commands: their phrasing, and their replies, from one bot."""
import unittest
from unittest.mock import AsyncMock, MagicMock

from core.runtime.commands.base import ControlContext
from core.runtime.commands.registry import CONTROL


class _Thread:
    """The control thread, as its commands use it."""

    def __init__(self, agent_ids=("main", "zeta"), elected=True, claim_fails=False):
        self._agent_ids, self._elected, self._claim_fails = list(agent_ids), elected, claim_fails
        self.reactions, self.claims = [], 0

    def agent_ids(self):
        return self._agent_ids

    def elected(self):
        return self._elected

    async def claim(self):
        self.claims += 1
        if self._claim_fails:
            raise RuntimeError("no thread")

    def describe(self):
        return "main: prod"

    async def react(self, emoji):
        self.reactions.append(emoji)


def _ctx(thread, agent_id="main"):
    return ControlContext(channel=MagicMock(send=AsyncMock()), agent_id=agent_id, control=thread)


def _matched(text):
    found = CONTROL.match(text)
    return (found[0].name, found[1]) if found else None


class TestPhrasing(unittest.TestCase):

    def test_bang_name_and_its_arguments(self):
        self.assertEqual(_matched("!claim day-planner"), ("claim", "day-planner"))
        self.assertEqual(_matched(" !release "), ("release", ""))
        self.assertEqual(_matched("!status"), ("status", ""))

    def test_a_claim_names_one_agent_and_the_others_take_nothing(self):
        for text in ["!claim", "!claim two agents", "!release please", "!status now", "!Claim main"]:
            self.assertIsNone(CONTROL.match(text), text)

    def test_the_bracketed_lines_already_in_the_thread_still_count(self):
        """The thread is a log replayed at startup: an old [release] must still release."""
        self.assertEqual(_matched("[claim day-planner]"), ("claim", "day-planner"))
        self.assertEqual(_matched(" [ RELEASE ] "), ("release", ""))
        self.assertEqual(_matched("[status]"), ("status", ""))
        for text in ["[release] please", "[claim]", "[claim a b]", "[restart]"]:
            self.assertIsNone(CONTROL.match(text), text)

    def test_chat_is_not_a_command(self):
        for text in ["hello", "claim day-planner", "!new", "", None]:
            self.assertIsNone(CONTROL.match(text), text)


class TestReplies(unittest.IsolatedAsyncioTestCase):

    async def test_claim_for_this_bots_agent_claims_and_says_so(self):
        thread = _Thread()
        await CONTROL.answer("!claim main", _ctx(thread))
        self.assertEqual((thread.claims, thread.reactions), (1, ["✅"]))

    async def test_a_claim_that_fails_says_so(self):
        thread = _Thread(claim_fails=True)
        await CONTROL.answer("!claim main", _ctx(thread))
        self.assertEqual(thread.reactions, ["⚠️"])

    async def test_a_claim_for_an_agent_nobody_runs_is_questioned_once(self):
        elected, other = _Thread(elected=True), _Thread(elected=False)
        await CONTROL.answer("!claim nobody", _ctx(elected))
        await CONTROL.answer("!claim nobody", _ctx(other))
        self.assertEqual((elected.reactions, other.reactions, elected.claims), (["❓"], [], 0))
        await CONTROL.answer("!claim zeta", _ctx(elected))  # another bot's agent: its bot answers
        self.assertEqual(elected.reactions, ["❓"])

    async def test_release_and_status_answer_from_the_elected_bot_only(self):
        for elected in (True, False):
            thread = _Thread(elected=elected)
            ctx = _ctx(thread)
            await CONTROL.answer("!release", ctx)
            await CONTROL.answer("!status", ctx)
            self.assertEqual(thread.reactions, ["✅"] if elected else [])
            if elected:
                ctx.channel.send.assert_awaited_once_with("main: prod")
            else:
                ctx.channel.send.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
