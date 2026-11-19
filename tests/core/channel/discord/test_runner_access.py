"""Who a Discord message's turn is for (core/channel/discord/runner.py): the person
who wrote it, if they may use the agent it reaches; a 🔒 if they may not; nothing
for an account nobody is configured as. One turn at a time in a household agent's
channel. With nobody configured every account is the owner, as before."""
import asyncio
import contextlib
import datetime
import io
import itertools
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core.channel.discord import runner as runner_module
from core.channel.discord.runner import LOCKED, BotRunner, voter
from core.channel.discord.turn_locks import TURN_LOCKS
from core.permissions import access
from tests.household import OWNER_ID, CONFIGS, STRANGER_ID, PLAYER2_ID, dormant, household

_ids = itertools.count(5000)


def _message(author_id, content="What's for dinner?", channel="meal-planning", channel_id=77, mentions=()):
    message = MagicMock()
    message.id = next(_ids)
    message.author = MagicMock(bot=False, id=author_id)
    message.author.name = f"account-{author_id}"
    message.content = content
    message.mentions = list(mentions)
    message.attachments = []
    message.reference = None
    message.created_at = datetime.datetime.now(datetime.timezone.utc)
    message.add_reaction = AsyncMock()
    message.channel.name = channel
    message.channel.id = channel_id
    message.channel.send = AsyncMock()
    typing = MagicMock()
    typing.__aenter__ = AsyncMock()
    typing.__aexit__ = AsyncMock()
    message.channel.typing.return_value = typing
    return message


@patch('core.channel.discord.stream_buffer.DiscordStreamBuffer.finalize', new=AsyncMock())
@patch('core.channel.discord.runner.record_reply')
@patch('core.channel.discord.runner.mark_read')
@patch('core.channel.discord.runner.AgentsLoader')
@patch('core.channel.discord.runner.commands.Bot')
class TestAHouseholdChannel(unittest.IsolatedAsyncioTestCase):
    """owner and player2 are configured. Ramsay answers #meal-planning and Eric
    #excursion-planning, which both may use; Daisy (#day-planning) is the owner's alone."""

    def setUp(self):
        for patcher in (household(), patch.object(runner_module, "_strangers", set())):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.turns = []  # (person, the channel lock held) per turn the agent ran

    def _runner(self, bot_class, loader_class, agent_id="main", hosts=("meal-planning", "excursion-planning",
                                                                       "day-planning"), stream=None):
        bot = MagicMock()
        bot.user = MagicMock(bot=True)
        bot_class.return_value = bot
        runner = BotRunner("test_token", agent_id)

        async def answer(content, session=None, callbacks=None):
            self.turns.append((session.person, TURN_LOCKS.held(str(session.channel_obj.id))))
            yield {"type": "final_response", "text": "On it.", "response": None}

        agent = MagicMock()
        agent.config = {"channel_hosts": list(hosts)}
        agent.execute_stream = stream or answer
        loader_class.return_value.get_agent.return_value = agent
        loader_class.return_value.get_agent_config.side_effect = CONFIGS.get
        # Who the host's router hands each channel to (routing.answering_agent).
        routed = {"meal-planning": "meal-planner", "excursion-planning": "excursion-planner",
                  "day-planning": "day-planner"}
        patcher = patch('core.channel.discord.runner.answering_agent',
                        side_effect=lambda host, channel, loader: routed.get(channel, host))
        patcher.start()
        self.addCleanup(patcher.stop)
        return runner

    async def test_someone_who_may_use_the_agent_gets_a_turn_that_acts_for_them(self, bot_class, loader_class,
                                                                                 mark_read, record_reply):
        runner = self._runner(bot_class, loader_class)
        for channel, channel_id in (("meal-planning", 77), ("excursion-planning", 78)):
            message = _message(PLAYER2_ID, channel=channel, channel_id=channel_id)
            await runner.on_message(message)
            message.add_reaction.assert_not_awaited()
        self.assertEqual([person for person, _ in self.turns], ["player2", "player2"])
        self.assertEqual(mark_read.call_count, 2)

    async def test_someone_who_may_not_gets_a_lock_and_the_channel_stays_quiet(self, bot_class, loader_class,
                                                                               mark_read, record_reply):
        runner = self._runner(bot_class, loader_class)
        message = _message(PLAYER2_ID, channel="day-planning", channel_id=80)
        await runner.on_message(message)
        self.assertEqual(self.turns, [])
        message.add_reaction.assert_awaited_once_with(LOCKED)
        message.channel.send.assert_not_awaited()
        mark_read.assert_not_called()  # it wasn't a message to its agent
        record_reply.assert_not_called()

    async def test_the_owner_may_use_the_agent_player2_may_not(self, bot_class, loader_class, mark_read,
                                                               record_reply):
        runner = self._runner(bot_class, loader_class)
        await runner.on_message(_message(OWNER_ID, channel="day-planning", channel_id=80))
        self.assertEqual([person for person, _ in self.turns], ["owner"])

    async def test_an_account_nobody_is_configured_as_is_ignored_and_logged_once(self, bot_class, loader_class,
                                                                                 mark_read, record_reply):
        runner = self._runner(bot_class, loader_class)
        messages = [_message(STRANGER_ID), _message(STRANGER_ID, content="!help")]
        log = io.StringIO()
        with contextlib.redirect_stdout(log), patch('core.channel.discord.runner.TYPED') as typed:
            typed.answer = AsyncMock()
            for message in messages:
                await runner.on_message(message)
        self.assertEqual(self.turns, [])
        typed.answer.assert_not_awaited()
        mark_read.assert_not_called()
        for message in messages:
            message.add_reaction.assert_not_awaited()
            message.channel.send.assert_not_awaited()
        self.assertEqual(log.getvalue().count(f"Ignoring account-{STRANGER_ID} ({STRANGER_ID})"), 1)

    async def test_a_bot_tagged_in_asks_of_its_own_agent(self, bot_class, loader_class, mark_read, record_reply):
        daisy = self._runner(bot_class, loader_class, agent_id="day-planner", hosts=())
        tagged = _message(PLAYER2_ID, channel="general", channel_id=79, mentions=[daisy.bot.user])
        await daisy.on_message(tagged)
        tagged.add_reaction.assert_awaited_once_with(LOCKED)
        ramsay = self._runner(bot_class, loader_class, agent_id="meal-planner", hosts=())
        await ramsay.on_message(_message(PLAYER2_ID, channel="general", channel_id=79, mentions=[ramsay.bot.user]))
        self.assertEqual([person for person, _ in self.turns], ["player2"])

    async def test_typed_commands_are_given_by_the_person(self, bot_class, loader_class, mark_read, record_reply):
        runner = self._runner(bot_class, loader_class)
        with patch('core.channel.discord.runner.TYPED') as typed:
            typed.answer = AsyncMock()
            await runner.on_message(_message(PLAYER2_ID, content="!new"))
        _text, ctx = typed.answer.await_args.args
        self.assertEqual((ctx.person, ctx.session.person), ("player2", "player2"))

    async def test_a_vote_is_the_voters_turn_and_a_strangers_is_ignored(self, bot_class, loader_class, mark_read,
                                                                        record_reply):
        runner = self._runner(bot_class, loader_class)
        for voter_id in (PLAYER2_ID, STRANGER_ID):
            vote = _message(voter_id, content=f"<@{voter_id}>: Tacos")
            vote.author = runner.bot.user  # the bot posts the vote for whoever clicked (ui.py)
            await runner.on_message(vote)
        self.assertEqual([person for person, _ in self.turns], ["player2"])

    async def test_a_household_agents_turn_holds_its_channel_and_a_personal_one_doesnt(self, bot_class, loader_class,
                                                                                       mark_read, record_reply):
        runner = self._runner(bot_class, loader_class)
        await runner.on_message(_message(PLAYER2_ID))                                       # Ramsay: both of theirs
        await runner.on_message(_message(OWNER_ID, channel="day-planning", channel_id=80))  # Daisy: owner's
        self.assertEqual(self.turns, [("player2", True), ("owner", False)])
        self.assertEqual(len(TURN_LOCKS), 0)

    async def test_two_people_in_one_household_channel_take_turns(self, bot_class, loader_class, mark_read,
                                                                  record_reply):
        started, release = [], asyncio.Event()

        async def slow(content, session=None, callbacks=None):
            started.append(session.person)
            if len(started) == 1:
                await release.wait()
            yield {"type": "final_response", "text": "On it.", "response": None}

        runner = self._runner(bot_class, loader_class, stream=slow)
        first = asyncio.create_task(runner.on_message(_message(OWNER_ID)))
        second = asyncio.create_task(runner.on_message(_message(PLAYER2_ID)))
        for _ in range(20):
            await asyncio.sleep(0)
        self.assertEqual(started, ["owner"])  # player2's turn waits for the owner's
        release.set()
        await asyncio.gather(first, second)
        self.assertEqual(started, ["owner", "player2"])
        self.assertEqual(len(TURN_LOCKS), 0)


class TestAPollVote(unittest.TestCase):
    """The bot posts a vote as "<@id>: answer" for whoever clicked (ui.py): its turn is theirs."""

    def test_the_vote_names_who_it_is_for(self):
        with household():
            self.assertEqual(voter(f"<@{PLAYER2_ID}>: Tacos"), "player2")
            self.assertEqual(voter(f"<@!{OWNER_ID}>: Tacos"), "owner")
            self.assertIsNone(voter(f"<@{STRANGER_ID}>: Tacos"))
            self.assertIsNone(voter("Tacos"))
        with dormant():
            self.assertEqual(voter(f"<@{STRANGER_ID}>: Tacos"), access.OWNER)


@patch('core.channel.discord.stream_buffer.DiscordStreamBuffer.finalize', new=AsyncMock())
@patch('core.channel.discord.runner.record_reply')
@patch('core.channel.discord.runner.mark_read')
@patch('core.channel.discord.runner.AgentsLoader')
@patch('core.channel.discord.runner.commands.Bot')
class TestNobodyConfigured(unittest.IsolatedAsyncioTestCase):
    """Every account is the owner, who may use every agent without an access block: as before."""

    async def test_every_account_gets_a_turn_as_the_owner_with_no_lock(self, bot_class, loader_class, mark_read,
                                                                       record_reply):
        turns = []

        async def answer(content, session=None, callbacks=None):
            turns.append((session.person, len(TURN_LOCKS)))
            yield {"type": "final_response", "text": "On it.", "response": None}

        bot_class.return_value = MagicMock(user=MagicMock(bot=True))
        agent = MagicMock(config={"channel_hosts": ["day-planning"]}, execute_stream=answer)
        loader_class.return_value.get_agent.return_value = agent
        loader_class.return_value.get_agent_config.side_effect = CONFIGS.get
        with dormant(), patch('core.channel.discord.runner.answering_agent', return_value="day-planner"):
            runner = BotRunner("test_token", "main")
            message = _message(STRANGER_ID, channel="day-planning", channel_id=80)
            await runner.on_message(message)
        self.assertEqual(turns, [(access.OWNER, 0)])
        message.add_reaction.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
