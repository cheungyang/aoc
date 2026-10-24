"""`!help`: every command, typed or spoken, from the registries."""
import unittest
from unittest.mock import AsyncMock, MagicMock

from core.runtime.commands.base import TypedContext
from core.runtime.commands.registry import CONTROL, SPOKEN, TYPED


class TestHelp(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.help = TYPED.find("help")

    def test_it_lists_every_command_by_how_it_is_given(self):
        text = self.help.text()
        headings = [line for line in text.splitlines() if line.startswith("**")]
        self.assertEqual(headings, ["**Typed in a channel the bot answers**", "**Typed, for the voice room**",
                                    "**Said in the voice room**", "**Typed in the control thread**"])
        for command in TYPED:
            self.assertIn(f"`!{command.name}", text)
        for command in (*SPOKEN, *CONTROL):
            self.assertIn(command.summary, text)
        self.assertIn("`!compact` (or `!summarize`)", text)
        self.assertIn("`!join [voice channel]`: Joins the voice room", text)
        self.assertIn("\u201cstop\u201d, \u201cnever mind\u201d, \u201ccancel\u201d: Stops the reply", text)
        self.assertIn("`!claim <agent>`: Hands <agent> to the dev instance", text)
        self.assertIn("`!release`: Hands every agent back to prod.", text)
        self.assertNotIn("[claim", text)

    def test_one_command_by_name(self):
        self.assertTrue(self.help.text("!join").startswith("`!join [voice channel]`: "))
        self.assertTrue(self.help.text("summarize").startswith("`!compact` (or `!summarize`): "))
        self.assertTrue(self.help.text("Next agent").startswith("\u201cnext agent\u201d: "))
        self.assertTrue(self.help.text("!claim").startswith("`!claim <agent>`: "))
        self.assertEqual(self.help.text("nope"), "There's no command called `nope`. `!help` lists them all.")

    async def test_answered_in_the_channel_by_the_bot_that_answers_it(self):
        channel = MagicMock(send=AsyncMock())
        self.assertIsNone(await TYPED.answer("!help", TypedContext(channel=channel, answers_here=False)))
        channel.send.assert_not_awaited()
        self.assertIs(await TYPED.answer("!help", TypedContext(channel=channel, answers_here=True)), self.help)
        self.assertIn("**Said in the voice room**", "\n".join(c.args[0] for c in channel.send.await_args_list))


if __name__ == "__main__":
    unittest.main()
