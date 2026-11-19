"""Household mode for tests (core/permissions/access.py).

Tests run with nobody configured, so every account is the owner and nothing is
refused: the dormant guarantee. A test of household mode configures two people
the way tests/core/permissions/test_access.py does, by patching the environment
(`household()`), and gives its agent configs `access` blocks. `dormant()` pins
the dormant case even when the shell running the tests configures people.
"""
import os
from unittest import mock

from core.permissions import access

# owner (account 111) owns the system; player2 has two accounts. 999 is nobody's.
HOUSEHOLD_ENV = {
    "DISCORD_USER_OWNER": "111",
    "DISCORD_USER_PLAYER2": "222, 223",
}
OWNER_ID, PLAYER2_ID, STRANGER_ID = 111, 222, 999

# Agent configs as the loader returns them. The Concierge has no access block, so
# it's the owner's alone, and the owner is the admin.
CONCIERGE = {"agent_id": "main", "name": "Concierge"}
RAMSAY = {"agent_id": "meal-planner", "name": "Ramsay", "access": ["owner", "player2"]}
ERIC = {"agent_id": "excursion-planner", "name": "Eric", "access": ["owner", "player2"]}
DAISY = {"agent_id": "day-planner", "name": "Daisy"}  # no block: the owner's alone
CONFIGS = {config["agent_id"]: config for config in (CONCIERGE, RAMSAY, ERIC, DAISY)}


def env(values):
    """os.environ with exactly these DISCORD_USER_* variables."""
    cleaned = {k: v for k, v in os.environ.items() if not k.startswith(access.USER_ENV_PREFIX)}
    return mock.patch.dict(os.environ, {**cleaned, **values}, clear=True)


def household():
    """owner and player2 configured (HOUSEHOLD_ENV): household mode."""
    return env(HOUSEHOLD_ENV)


def dormant():
    """Nobody configured: every account is the owner."""
    return env({})


class ConfigLoader:
    """An AgentsLoader that knows only `configs` (CONFIGS by default)."""

    def __init__(self, configs=None):
        self.configs = CONFIGS if configs is None else configs

    def get_agent_config(self, agent_id):
        return self.configs.get(agent_id)
