import datetime
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core.channel.discord import ownership as own
from core.channel.discord.ownership import DEV, MANUAL, PROD, Event, Ownership, parse
from core.util.config import Config

T0 = datetime.datetime(2026, 9, 30, 12, 0, tzinfo=datetime.timezone.utc)
CONTROL = Config().control_thread_id


def msg(content, id_, created=T0, bot=False, channel_id=CONTROL):
    m = MagicMock()
    m.content = content
    m.id = id_
    m.created_at = created
    m.author.bot = bot
    m.channel.id = channel_id
    m.add_reaction = AsyncMock()
    m.channel.send = AsyncMock()
    return m


def fresh(role=PROD, host="laptop", agents=None, ready=True):
    Ownership.reset()
    o = Ownership()
    o.configure(role=role, agents=agents, host=host)
    if ready:
        o.mark_ready()
    return o


class TestParse(unittest.TestCase):
    def test_parses_bot_and_user_lines(self):
        self.assertEqual(parse("🔒 claim day-planner @laptop"), Event("claim", "day-planner", "laptop"))
        self.assertEqual(parse("🔓 release day-planner @laptop"), Event("release", "day-planner", "laptop"))
        self.assertEqual(parse("[claim day-planner]"), Event("cmd_claim", "day-planner"))
        self.assertEqual(parse(" [ RELEASE ] "), Event("cmd_release"))
        self.assertEqual(parse("[status]"), Event("cmd_status"))

    def test_ignores_chat(self):
        for text in ["hello", "[release] please", "claim day-planner", "", None]:
            self.assertIsNone(parse(text))

    def test_round_trips_own_text(self):
        self.assertEqual(parse(own.claim_text("a", "h")), Event("claim", "a", "h"))
        self.assertEqual(parse(own.release_text("a", "h")), Event("release", "a", "h"))


class TestReduceAndOwnership(unittest.TestCase):
    def test_prod_owns_everything_unclaimed(self):
        o = fresh(PROD)
        self.assertTrue(o.is_mine("a"))
        o.apply(msg("🔒 claim a @laptop", 1, bot=True))
        self.assertFalse(o.is_mine("a"))
        self.assertTrue(o.is_mine("b"))

    def test_dev_owns_only_its_own_claims(self):
        o = fresh(DEV, host="laptop")
        self.assertFalse(o.is_mine("a"))
        o.apply(msg("🔒 claim a @laptop", 1, bot=True))
        o.apply(msg("🔒 claim b @other", 2, bot=True))
        self.assertTrue(o.is_mine("a"))
        self.assertFalse(o.is_mine("b"))

    def test_release_only_by_the_holder(self):
        o = fresh(PROD)
        o.apply(msg("🔒 claim a @laptop", 1))
        o.apply(msg("🔓 release a @other", 2))
        self.assertEqual(o.holder("a"), "laptop")
        o.apply(msg("🔓 release a @laptop", 3))
        self.assertIsNone(o.holder("a"))

    def test_user_claim_is_manual_until_dev_takes_it(self):
        o = fresh(PROD)
        o.apply(msg("[claim a]", 1))
        self.assertEqual(o.holder("a"), MANUAL)
        self.assertFalse(o.is_mine("a"))
        o.apply(msg("🔒 claim a @laptop", 2, bot=True))
        self.assertEqual(o.holder("a"), "laptop")

    def test_user_release_clears_all(self):
        o = fresh(PROD)
        o.apply(msg("🔒 claim a @laptop", 1))
        o.apply(msg("[claim b]", 2))
        o.apply(msg("[release]", 3))
        self.assertEqual(o.holds, {})

    def test_apply_is_idempotent_per_message(self):
        o = fresh(PROD)
        claim = msg("🔒 claim a @laptop", 1)
        o.apply(claim)
        o.apply(msg("🔓 release a @laptop", 2))
        o.apply(claim)  # the same message seen through another bot
        self.assertIsNone(o.holder("a"))

    def test_nothing_is_mine_before_ready(self):
        o = fresh(PROD, ready=False)
        self.assertFalse(o.is_mine("a"))

    def test_dev_ignores_messages_before_claim_or_stale(self):
        o = fresh(DEV, host="laptop")
        now = datetime.datetime.now(datetime.timezone.utc)
        o.apply(msg("🔒 claim a @laptop", 1, created=now - datetime.timedelta(seconds=30)))
        self.assertTrue(o.is_mine("a", msg("hi", 2, created=now)))
        self.assertFalse(o.is_mine("a", msg("hi", 3, created=now - datetime.timedelta(seconds=60))))
        o2 = fresh(DEV, host="laptop")
        o2.apply(msg("🔒 claim a @laptop", 1, created=now - datetime.timedelta(hours=1)))
        self.assertFalse(o2.is_mine("a", msg("replayed", 4, created=now - datetime.timedelta(minutes=10))))

    def test_listeners_are_notified_on_change(self):
        o = fresh(PROD)
        seen = []
        o.add_listener(seen.append)
        o.apply(msg("🔒 claim a @laptop", 1))
        o.apply(msg("🔒 claim a @laptop", 2))  # no change
        o.apply(msg("[release]", 3))
        self.assertEqual(seen, ["a", "a"])

    def test_describe_lists_holders(self):
        o = fresh(PROD, host="nas")
        o.apply(msg("🔒 claim a @laptop", 1))
        text = o.describe(["a", "b"])
        self.assertIn("prod@nas", text)
        self.assertIn("`a`: dev@laptop", text)
        self.assertIn("`b`: prod ← this instance", text)


class _History:
    def __init__(self, messages):
        self.messages = messages

    def __call__(self, limit, oldest_first):
        async def gen():
            for m in reversed(self.messages):  # newest first, like Discord
                yield m
        return gen()


class TestRebuild(unittest.IsolatedAsyncioTestCase):
    async def test_rebuild_replays_history(self):
        o = fresh(PROD, ready=False)
        thread = MagicMock()
        thread.history = _History([
            msg("🔒 claim a @laptop", 1),
            msg("🔒 claim b @laptop", 2),
            msg("hello", 3),
            msg("[release]", 4),
            msg("🔒 claim c @laptop", 5),
        ])
        bot = MagicMock()
        bot.get_channel.return_value = thread
        await o.rebuild(bot)
        self.assertTrue(o.is_ready())
        self.assertEqual({k: v.holder for k, v in o.holds.items()}, {"c": "laptop"})
        self.assertTrue(o.is_mine("a"))
        self.assertFalse(o.is_mine("c"))

    async def test_rebuild_failure_still_becomes_ready(self):
        o = fresh(PROD, ready=False)
        bot = MagicMock()
        bot.get_channel.return_value = None
        bot.fetch_channel = AsyncMock(side_effect=RuntimeError("forbidden"))
        await o.rebuild(bot)
        self.assertTrue(o.is_ready())
        self.assertTrue(o.is_mine("a"))

    async def test_dev_claim_posts_once(self):
        o = fresh(DEV, host="laptop")
        thread = MagicMock()
        thread.send = AsyncMock(side_effect=lambda text: msg(text, 10))
        bot = MagicMock()
        bot.get_channel.return_value = thread
        await o.claim(bot, "a")
        await o.claim(bot, "a")
        thread.send.assert_awaited_once_with("🔒 claim a @laptop")
        self.assertTrue(o.is_mine("a"))

    async def test_prod_never_posts_claims(self):
        o = fresh(PROD)
        bot = MagicMock()
        await o.claim(bot, "a")
        bot.get_channel.assert_not_called()

    async def test_release_all_mine_posts_release_per_held_agent(self):
        o = fresh(DEV, host="laptop")
        o.apply(msg("🔒 claim a @laptop", 1))
        o.apply(msg("🔒 claim b @other", 2))
        resp = MagicMock(status=200)
        post_cm = MagicMock()
        post_cm.__aenter__ = AsyncMock(return_value=resp)
        post_cm.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.post = MagicMock(return_value=post_cm)
        session_cm = MagicMock()
        session_cm.__aenter__ = AsyncMock(return_value=session)
        session_cm.__aexit__ = AsyncMock(return_value=False)
        with patch("aiohttp.ClientSession", return_value=session_cm):
            await o.release_all_mine({"a": "tok-a", "b": "tok-b"})
        session.post.assert_called_once()
        kwargs = session.post.call_args.kwargs
        self.assertEqual(kwargs["json"], {"content": "🔓 release a @laptop"})
        self.assertEqual(kwargs["headers"], {"Authorization": "Bot tok-a"})


class TestRunnerIntegration(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from core.channel.discord.runner import BotRunner
        self.runner = BotRunner("test_token", "main")
        self.runner.bot = MagicMock()
        self.runner.bot.user = MagicMock()
        self.runner.bot.user.bot = True

    def _chat(self, id_=100):
        m = msg("hello", id_, created=datetime.datetime.now(datetime.timezone.utc), channel_id="111")
        m.channel.name = "general"
        m.channel.parent = None
        m.mentions = []
        m.attachments = []
        return m

    def _agent(self, mock_agents_loader):
        async def empty(*a, **k):
            if False:
                yield None
        agent = MagicMock()
        agent.config = {"channel_hosts": ["general"]}
        agent.execute_stream = MagicMock(side_effect=empty)
        mock_agents_loader.return_value.get_agent.return_value = agent
        return agent

    @patch("core.channel.discord.runner.SessionManager")
    @patch("core.channel.discord.runner.AgentsLoader")
    async def test_prod_answers_unclaimed_agent(self, mock_agents_loader, _sm):
        agent = self._agent(mock_agents_loader)
        await self.runner.on_message(self._chat())
        agent.execute_stream.assert_called_once()

    @patch("core.channel.discord.runner.AgentsLoader")
    async def test_prod_skips_agent_held_by_dev(self, mock_agents_loader):
        agent = self._agent(mock_agents_loader)
        Ownership().apply(msg("🔒 claim main @laptop", 1, bot=True))
        await self.runner.on_message(self._chat())
        agent.execute_stream.assert_not_called()

    @patch("core.channel.discord.runner.AgentsLoader")
    async def test_control_messages_are_not_chat(self, mock_agents_loader):
        agent = self._agent(mock_agents_loader)
        await self.runner.on_message(msg("[claim main]", 2))
        agent.execute_stream.assert_not_called()
        self.assertEqual(Ownership().holder("main"), MANUAL)

    @patch("core.channel.discord.loader.BotsLoader")
    async def test_dev_claims_on_user_command(self, mock_bots_loader):
        mock_bots_loader.return_value._bots = {"main": self.runner}
        Ownership().configure(role=DEV, host="laptop")
        thread = MagicMock()
        thread.send = AsyncMock(side_effect=lambda text: msg(text, 11, created=T0))
        self.runner.bot.get_channel.return_value = thread
        command = msg("[claim main]", 3)
        await self.runner.on_message(command)
        thread.send.assert_awaited_once_with("🔒 claim main @laptop")
        command.add_reaction.assert_awaited_once_with("✅")
        self.assertEqual(Ownership().holder("main"), "laptop")

    @patch("core.channel.discord.loader.BotsLoader")
    async def test_status_replies_once_from_elected_bot(self, mock_bots_loader):
        mock_bots_loader.return_value._bots = {"main": self.runner, "zeta": MagicMock()}
        command = msg("[status]", 4)
        await self.runner.on_message(command)
        command.channel.send.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
