"""Who may give a command (core/runtime/commands/base.py `Command.needs`), checked
before it runs (CommandRegistry.answer): in household mode, and with nobody
configured, when every account is the owner and nothing is refused."""
import unittest
from unittest import mock

from core.permissions import access
from core.runtime.commands.base import (
    ADMIN,
    ANYONE,
    ControlCommand,
    ControlContext,
    TypedCommand,
    TypedContext,
    VoiceRoomCommand,
)
from core.runtime.commands.registry import CONTROL, SPOKEN, TYPED, CommandRegistry
from core.runtime.commands.typed.compact import CompactCommand
from core.runtime.commands.typed.new import NewCommand
from tests.household import CONFIGS, PLAYER2_ID, ConfigLoader, dormant, household

# The system's own commands: whoever may use the Concierge.
ADMIN_COMMANDS = {"newall", "restart", "jobs", "job", "kill", "python", "signals", "followups", "learn", "join",
                  "leave", "inbox", "enroll", "voiceprint", "gate"}


class _Channel:
    def __init__(self):
        self.sent = []

    async def send(self, text):
        self.sent.append(text)


class _Author:
    def __init__(self, id_):
        self.id = id_


class _Voice:
    def __init__(self, host):
        self.host = host

    def hosts_voice(self):
        return self.host


class _Control:
    def __init__(self, elected):
        self.is_elected = elected

    def elected(self):
        return self.is_elected


class _Recording:
    """Records its runs instead of doing anything."""

    def __init__(self, name, needs):
        self.name, self.needs, self.calls = name, needs, []

    async def run(self, ctx, args):
        self.calls.append(args)


class _Typed(_Recording, TypedCommand):
    pass


class _VoiceRoom(_Recording, VoiceRoomCommand):
    pass


class _Claim(_Recording, ControlCommand):
    pass


def typed(person, voice=None):
    """A command typed by `person` in a channel this bot answers."""
    return TypedContext(channel=_Channel(), author=_Author(None), person=person, agent_id="main",
                        answers_here=True, voice=voice)


class TestTheLevels(unittest.TestCase):

    def test_the_systems_commands_are_the_admins(self):
        self.assertEqual({c.name for c in TYPED if c.needs == ADMIN}, ADMIN_COMMANDS)
        self.assertEqual([c.needs for c in CONTROL], [ADMIN, ADMIN, ADMIN])

    def test_help_new_and_compact_are_anyones_and_spoken_commands_are_unchanged(self):
        self.assertEqual({c.name for c in TYPED if c.needs == ANYONE}, {"help", "new", "compact"})
        self.assertEqual({c.needs for c in SPOKEN}, {ANYONE})

    def test_a_typed_command_is_the_admins_unless_it_says_otherwise(self):
        self.assertEqual(TypedCommand.needs, ADMIN)


class TestInAHousehold(unittest.IsolatedAsyncioTestCase):
    """The Concierge has no access list, so it's the owner's alone: the owner is the admin."""

    def setUp(self):
        for patcher in (household(), mock.patch("core.runtime.commands.base.AgentsLoader", ConfigLoader)):
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_a_refused_command_says_so_in_one_line_and_never_runs(self):
        restart = _Typed("restart", ADMIN)
        ctx = typed("player2")
        self.assertIs(await CommandRegistry([restart]).answer("!restart now", ctx), restart)  # consumed
        self.assertEqual(restart.calls, [])
        self.assertEqual(ctx.channel.sent, ["`!restart` is admin-only."])

    async def test_the_admin_may(self):
        restart = _Typed("restart", ADMIN)
        ctx = typed("owner")
        await CommandRegistry([restart]).answer("!restart now", ctx)
        self.assertEqual((restart.calls, ctx.channel.sent), (["now"], []))

    def test_whoever_the_concierge_lists_is_admin(self):
        listed = {**CONFIGS, "main": {"agent_id": "main", "name": "Concierge", "access": ["owner", "player2"]}}
        with mock.patch("core.runtime.commands.base.AgentsLoader", lambda: ConfigLoader(listed)):
            self.assertIsNone(_Typed("restart", ADMIN).refusal(typed("player2")))

    async def test_anyone_known_may_ask_for_help(self):
        help_ = _Typed("help", ANYONE)
        ctx = typed("player2")
        await CommandRegistry([help_]).answer("!help", ctx)
        self.assertEqual((help_.calls, ctx.channel.sent), ([""], []))

    def test_anyone_known_may_start_over_or_compact(self):
        for command in (NewCommand(), CompactCommand()):
            with self.subTest(command=command.name):
                self.assertIsNone(command.refusal(typed("player2")))

    def test_a_command_given_by_nobody_is_never_refused(self):
        """No person: a spoken command, or a context built without one."""
        self.assertIsNone(_Typed("restart", ADMIN).refusal(typed(None)))

    async def test_where_every_bot_hears_a_command_one_says_no(self):
        join = _VoiceRoom("join", ADMIN)
        quiet, host = typed("player2", voice=_Voice(host=False)), typed("player2", voice=_Voice(host=True))
        self.assertIs(await CommandRegistry([join]).answer("!join", quiet), join)
        self.assertIs(await CommandRegistry([join]).answer("!join", host), join)
        self.assertEqual((quiet.channel.sent, host.channel.sent), ([], ["`!join` is admin-only."]))
        self.assertEqual(join.calls, [])

    async def test_in_the_control_thread_the_elected_bot_says_no(self):
        claim = _Claim("claim", ADMIN)
        claim.takes = lambda args: True
        for elected, said in ((False, []), (True, ["`!claim` is admin-only."])):
            ctx = ControlContext(channel=_Channel(), author=_Author(PLAYER2_ID), person="player2", agent_id="main",
                                 control=_Control(elected))
            self.assertIs(await CommandRegistry([claim]).answer("!claim main", ctx), claim)
            self.assertEqual(ctx.channel.sent, said)
        self.assertEqual(claim.calls, [])


class TestNobodyConfigured(unittest.TestCase):
    """Every account is the owner, who may give every command, as before."""

    def test_the_owner_may_give_every_command(self):
        with dormant():
            owner = access.person_for(PLAYER2_ID)
            self.assertEqual(owner, access.OWNER)
            for command in (*TYPED, *CONTROL):
                with self.subTest(command=command.name):
                    self.assertIsNone(command.refusal(typed(owner)))


if __name__ == "__main__":
    unittest.main()
