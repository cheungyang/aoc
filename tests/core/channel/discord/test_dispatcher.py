"""dispatch_discord_output sends replies through the markdown-safe splitter."""
import itertools
import re
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.channel.discord.dispatcher import dispatch_discord_output
from core.channel.response_parser import AgentResponse


class TestDispatchDiscordOutput(unittest.IsolatedAsyncioTestCase):

    @patch("core.channel.discord.dispatcher.asyncio.sleep", new_callable=AsyncMock)
    async def test_long_reply_is_sent_in_markdown_safe_chunks(self, _sleep):
        channel = AsyncMock()
        code = "\n".join(f"value_{i} = compute({i})  # step {i}" for i in range(200))
        text = f"Results:\n```python\n{code}\n```\nDone."

        await dispatch_discord_output(channel, AgentResponse(text=text))

        sent = [c.args[0] for c in channel.send.call_args_list]
        self.assertGreater(len(sent), 1)
        for chunk in sent:
            # Discord's cap, closing fence included.
            self.assertLessEqual(len(chunk), 2000)
            self.assertEqual(len(re.findall(r"`{3,}", chunk)) % 2, 0, chunk)
        self.assertTrue(all(c.startswith("```python\n") for c in sent[1:]))
        self.assertTrue(sent[-1].endswith("Done."))

    async def test_a_proactive_reply_is_posted_and_may_carry_a_poll(self):
        poll = {"question": "Add them?", "allow_multiple": False,
                "options": [{"text": "Add all", "emoji": "", "response": "Add them all"}]}
        for source, polled in (("proactive", True), ("scheduled", False)):
            channel = AsyncMock()
            with patch("core.channel.discord.dispatcher.PollButtonView") as view:
                await dispatch_discord_output(channel, AgentResponse(text="Want these in the inventory?",
                                                                     poll_data=poll), source=source)
            kwargs = {"view": view.return_value} if polled else {}
            channel.send.assert_awaited_once_with("Want these in the inventory?", **kwargs)

    async def test_a_job_posts_nothing(self):
        channel = AsyncMock()
        await dispatch_discord_output(channel, AgentResponse(text="done"), source="job")
        channel.send.assert_not_called()

    @patch("core.channel.discord.dispatcher.asyncio.sleep", new_callable=AsyncMock)
    async def test_it_returns_the_messages_it_sent(self, _sleep):
        """A proactive delivery keeps their ids: a ✅ or ❌ on one is the post's outcome."""
        channel, ids = AsyncMock(), itertools.count(1)
        channel.send.side_effect = lambda chunk, **kwargs: SimpleNamespace(id=next(ids), content=chunk)
        sent = await dispatch_discord_output(channel, AgentResponse(text="Saw the 99 Ranch run. " * 120),
                                             source="proactive")
        self.assertEqual([m.id for m in sent], [1, 2])
        self.assertEqual([m.content for m in sent], [c.args[0] for c in channel.send.call_args_list])
        self.assertEqual(await dispatch_discord_output(channel, AgentResponse(text="done"), source="job"), [])


if __name__ == "__main__":
    unittest.main()
