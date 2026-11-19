"""core/permissions/access.py: people, who may use each agent, and shared
agents, read from the environment and the agent config, pinned as a matrix."""
import os
import unittest
from unittest import mock

from core.permissions import access

HOUSEHOLD_ENV = {
    "DISCORD_USER_OWNER": "111",
    "DISCORD_USER_PLAYER2": "222, 223",
}

RAMSAY = {"agent_id": "meal-planner", "access": ["owner", "player2"]}
ERIC = {"agent_id": "excursion-planner", "access": ["owner", "player2"]}
DAISY = {"agent_id": "day-planner"}  # no list: the owner's alone
THEIRS = {"agent_id": "player2-planner", "access": ["player2"]}


def env(values):
    """os.environ with exactly these DISCORD_USER_* variables."""
    cleaned = {k: v for k, v in os.environ.items() if not k.startswith(access.USER_ENV_PREFIX)}
    return mock.patch.dict(os.environ, {**cleaned, **values}, clear=True)


class TestNobodyConfigured(unittest.TestCase):
    """With no DISCORD_USER_* set, nothing changes: everyone is the owner."""

    def setUp(self):
        patcher = env({})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_every_account_is_the_owner(self):
        self.assertEqual(access.people(), {})
        self.assertEqual(access.person_for("anyone"), access.OWNER)
        self.assertEqual(access.person_for(None), access.OWNER)
        self.assertFalse(access.household_mode())

    def test_every_agent_without_a_list_is_the_owners_alone(self):
        owner = access.person_for("anyone")
        self.assertTrue(access.can(owner, DAISY))
        self.assertFalse(access.is_household(DAISY))

    def test_a_list_locks_nobody_out(self):
        """A list added before DISCORD_USER_* names people no account is yet:
        the one person there may still use the agent."""
        owner = access.person_for("anyone")
        for config in (RAMSAY, ERIC, THEIRS):
            self.assertTrue(access.can(owner, config))

    def test_no_problems_without_lists(self):
        self.assertEqual(access.problems({"day-planner": DAISY}), [])

    def test_lists_wait_for_people(self):
        found = access.problems({"meal-planner": RAMSAY, "excursion-planner": ERIC, "day-planner": DAISY})
        self.assertEqual(len(found), 1)
        self.assertIn("excursion-planner, meal-planner have an 'access' list, but no DISCORD_USER_<NAME> is set",
                      found[0])


class TestPeople(unittest.TestCase):

    def test_accounts_by_name(self):
        with env(HOUSEHOLD_ENV):
            self.assertEqual(access.people(), {"owner": frozenset({"111"}), "player2": frozenset({"222", "223"})})
            self.assertTrue(access.household_mode())

    def test_each_account_maps_to_its_person(self):
        with env(HOUSEHOLD_ENV):
            self.assertEqual(access.person_for(111), "owner")
            self.assertEqual(access.person_for("223"), "player2")

    def test_an_unknown_account_is_nobody_once_anyone_is_configured(self):
        with env({"DISCORD_USER_OWNER": "111"}):
            self.assertIsNone(access.person_for("999"))
            self.assertIsNone(access.person_for(None))
            self.assertFalse(access.household_mode())

    def test_the_owner_is_always_discord_user_owner(self):
        """Never the one person configured."""
        with env({"DISCORD_USER_PLAYER2": "222"}):
            self.assertEqual(access.owner(), "owner")
            self.assertFalse(access.can("player2", DAISY))

    def test_bad_names_and_empty_values_configure_nobody(self):
        with env({"DISCORD_USER_": "1", "DISCORD_USER_9X": "3", "DISCORD_USER_EMPTY": " , "}):
            self.assertEqual(access.people(), {})


class TestWhoMayUseAnAgent(unittest.TestCase):

    def setUp(self):
        patcher = env(HOUSEHOLD_ENV)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_matrix(self):
        cases = [
            # (agent, person, may use it)
            (RAMSAY, "owner", True),
            (RAMSAY, "player2", True),
            (ERIC, "player2", True),
            (DAISY, "owner", True),
            (DAISY, "player2", False),
            (THEIRS, "owner", False),
            (THEIRS, "player2", True),
            (RAMSAY, "guest", False),
        ]
        for config, person, allowed in cases:
            with self.subTest(agent=config["agent_id"], person=person):
                self.assertEqual(access.can(person, config), allowed)

    def test_a_turn_nobody_started_is_never_refused(self):
        self.assertTrue(access.can(None, THEIRS))

    def test_names_are_read_in_any_case(self):
        self.assertTrue(access.can("PLAYER2", {"access": ["Player2"]}))

    def test_an_empty_or_malformed_list_is_the_owners_alone(self):
        """The loader reports it (`validate_access`); meanwhile nobody else gets in."""
        for listed in ([], {"player2": "use"}, "player2"):
            with self.subTest(access=listed):
                config = {"access": listed}
                self.assertTrue(access.can("owner", config))
                self.assertFalse(access.can("player2", config))


class TestSharedAgents(unittest.TestCase):

    def setUp(self):
        patcher = env(HOUSEHOLD_ENV)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_an_agent_more_than_one_person_may_use_is_the_households(self):
        self.assertTrue(access.is_household(RAMSAY))
        self.assertTrue(access.is_household(ERIC))
        self.assertEqual(access.access_of(ERIC), frozenset({"owner", "player2"}))

    def test_an_agent_of_one_person_isnt(self):
        self.assertFalse(access.is_household(DAISY))
        self.assertFalse(access.is_household(THEIRS))


class TestValidation(unittest.TestCase):

    def test_a_good_list(self):
        self.assertEqual(access.validate_access(["owner", "player2"]), [])

    def test_bad_lists(self):
        for listed in ([], {}, {"owner": "manage"}, "owner"):
            with self.subTest(access=listed):
                self.assertEqual(len(access.validate_access(listed)), 1)
        self.assertEqual(access.validate_access(["owner", "Player2", 7]),
                         ["access: 'Player2' is not a person's name (lowercase letters, digits, '-' or '_')",
                          "access: 7 is not a person's name (lowercase letters, digits, '-' or '_')"])


class TestProblems(unittest.TestCase):

    def test_people_without_an_owner(self):
        for configured in ({"DISCORD_USER_PLAYER2": "222"}, {"DISCORD_USER_PLAYER2": "222", "DISCORD_USER_KID": "333"}):
            with env(configured):
                found = access.problems({})
                self.assertEqual(len(found), 1)
                self.assertIn("No DISCORD_USER_OWNER is set", found[0])

    def test_a_list_naming_nobody_configured(self):
        with env(HOUSEHOLD_ENV):
            found = access.problems({"meal-planner": {"access": ["owner", "kid"]}})
            self.assertEqual(len(found), 1)
            self.assertIn("meal-planner: access names kid", found[0])

    def test_a_good_household(self):
        with env(HOUSEHOLD_ENV):
            self.assertEqual(access.problems({"meal-planner": RAMSAY, "day-planner": DAISY}), [])


if __name__ == "__main__":
    unittest.main()
