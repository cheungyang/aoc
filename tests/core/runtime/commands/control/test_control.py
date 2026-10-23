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


class TestPhrasing(unittest.TestCase):

    def test_the_bracketed_forms(self):
        self.assertEqual(CONTROL.match("[claim day-planner]")[0].name, "claim")
        self.assertEqual(CONTROL.match("[claim day-planner]")[1], "day-planner")
        self.assertEqual(CONTROL.match(" [ RELEASE ] ")[0].name, "release")
        self.assertEqual(CONTROL.match("[status]")[0].name, "status")
        for text in ["hello", "[release] please", "claim day-planner", "!claim main", "", None]:
            self.assertIsNone(CONTROL.match(text), text)


class TestReplies(unittest.IsolatedAsyncioTestCase):

    async def test_claim_for_this_bots_agent_claims_and_says_so(self):
        thread = _Thread()
        await CONTROL.answer("[claim main]", _ctx(thread))
        self.assertEqual((thread.claims, thread.reactions), (1, ["✅"]))

    async def test_a_claim_that_fails_says_so(self):
        thread = _Thread(claim_fails=True)
        await CONTROL.answer("[claim main]", _ctx(thread))
        self.assertEqual(thread.reactions, ["⚠️"])

    async def test_a_claim_for_an_agent_nobody_runs_is_questioned_once(self):
        elected, other = _Thread(elected=True), _Thread(elected=False)
        await CONTROL.answer("[claim nobody]", _ctx(elected))
        await CONTROL.answer("[claim nobody]", _ctx(other))
        self.assertEqual((elected.reactions, other.reactions, elected.claims), (["❓"], [], 0))
        await CONTROL.answer("[claim zeta]", _ctx(elected))  # another bot's agent: its bot answers
        self.assertEqual(elected.reactions, ["❓"])

    async def test_release_and_status_answer_from_the_elected_bot_only(self):
        for elected in (True, False):
            thread = _Thread(elected=elected)
            ctx = _ctx(thread)
            await CONTROL.answer("[release]", ctx)
            await CONTROL.answer("[status]", ctx)
            self.assertEqual(thread.reactions, ["✅"] if elected else [])
            if elected:
                ctx.channel.send.assert_awaited_once_with("main: prod")
            else:
                ctx.channel.send.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
