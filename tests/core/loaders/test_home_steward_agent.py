"""Tests for Butler (home-steward) agent configuration, permissions, and workflow."""
import copy
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.loaders.agents_loader import AgentsLoader
from core.loaders.skills_loader import SkillsLoader
from core.loaders.tools_loader import ToolsLoader
from tools.home_assistant import home_assistant, READ_ACTIONS, WRITE_ACTIONS
from tests.helpers import make_context, execution_context


class TestButlerAgentConfig(unittest.TestCase):
    """Verifies Butler's agent.json and skill configuration on disk."""

    def setUp(self):
        AgentsLoader._instance = None
        SkillsLoader._instance = None
        ToolsLoader._instance = None
        self.agents_loader = AgentsLoader()
        self.skills_loader = SkillsLoader()
        self.tools_loader = ToolsLoader()

    def test_agent_loads_with_expected_metadata(self):
        agent = self.agents_loader.get_agent("home-steward")
        self.assertIsNotNone(agent)
        self.assertEqual(agent.get_config("name"), "Butler")
        self.assertEqual(agent.get_config("emoji"), "🛎️")
        self.assertEqual(agent.get_config("model"), "FLASH")
        self.assertEqual(agent.get_config("discord_token_key"), "DISCORD_TOKEN_HOME_STEWARD")
        self.assertIn("home-automation", agent.get_config("channels", []))
        self.assertIn("general", agent.get_config("channels", []))
        self.assertIn("home-automation", agent.get_config("channel_hosts", []))

    def test_skills_declared_and_valid(self):
        agent = self.agents_loader.get_agent("home-steward")
        declared_skills = agent.get_config("skills", [])
        expected_skills = ["ha_inventory", "ha_automation_authoring", "ha_config_audit"]
        self.assertEqual(declared_skills, expected_skills)

        ctx = make_context(agent_id="home-steward")
        for skill_id in expected_skills:
            with self.subTest(skill=skill_id):
                prompt = self.skills_loader.get_skill_prompt(ctx, skill_id)
                self.assertIn("<skill>", prompt)
                self.assertGreater(len(prompt), 100)

    def test_schedules_configured(self):
        agent = self.agents_loader.get_agent("home-steward")
        schedules = agent.get_config("schedules", [])
        self.assertEqual(len(schedules), 2)
        crons = [s["cron"] for s in schedules]
        self.assertIn("0 8 * * *", crons)   # 8:00 AM daily
        self.assertIn("0 20 * * 0", crons)  # 8:00 PM Sunday
        for s in schedules:
            self.assertEqual(s["channel"], "home-automation")

    def test_bundle_grants_expand_to_real_actions(self):
        ctx = make_context(agent_id="home-steward")
        ha_grants = self.tools_loader.get_tool_permissions(ctx, "home_assistant")

        # Broad observe grant on "*"
        self.assertIn("*", ha_grants)
        for action in ("list_entities", "get_state", "search_registry", "inventory", "check_config"):
            self.assertIn(action, ha_grants["*"])

        # Control grants on reversible domains
        for domain in ("light.*", "switch.*", "fan.*", "media_player.*", "scene.*", "climate.*"):
            self.assertIn(domain, ha_grants)
            self.assertIn("call_service", ha_grants[domain])

        # Control + author grants on automations and scripts
        for domain in ("automation.*", "script.*"):
            self.assertIn(domain, ha_grants)
            self.assertIn("call_service", ha_grants[domain])
            self.assertIn("reload", ha_grants[domain])
            if domain == "automation.*":
                self.assertIn("upsert_automation", ha_grants[domain])
            if domain == "script.*":
                self.assertIn("upsert_script", ha_grants[domain])

        # Deletion is @admin and was deliberately not granted
        self.assertNotIn("delete_automation", ha_grants.get("automation.*", []))
        self.assertNotIn("delete_script", ha_grants.get("script.*", []))

        # Default filesystem grant to pkm/agents/home-steward/
        fs_grants = self.tools_loader.get_tool_permissions(ctx, "filesystem")
        self.assertTrue(
            any("pkm/agents/home-steward" in path for path in fs_grants),
            f"Expected pkm/agents/home-steward in filesystem grants: {fs_grants}",
        )


class TestButlerPermissionEnforcement(unittest.TestCase):
    """Tests runtime check_permission calls against Butler's expanded grants."""

    def setUp(self):
        AgentsLoader._instance = None
        ToolsLoader._instance = None
        self.tools_loader = ToolsLoader()
        self.ctx = make_context(agent_id="home-steward")

    def test_observe_actions_allowed_for_any_target(self):
        for action in ("get_state", "history", "search_registry", "check_config"):
            self.assertTrue(
                self.tools_loader.check_permission(self.ctx, "home_assistant", action, "any.target"),
                f"Expected {action} to be allowed on any.target",
            )

    def test_control_actions_allowed_on_granted_domains(self):
        self.assertTrue(self.tools_loader.check_permission(self.ctx, "home_assistant", "call_service", "light.porch"))
        self.assertTrue(self.tools_loader.check_permission(self.ctx, "home_assistant", "call_service", "switch.heater"))
        self.assertTrue(self.tools_loader.check_permission(self.ctx, "home_assistant", "call_service", "climate.living_room"))
        self.assertTrue(self.tools_loader.check_permission(self.ctx, "home_assistant", "call_service", "fan.bedroom"))

    def test_control_actions_refused_on_ungranted_domains(self):
        # locks and covers are ungranted (and also on the hardcoded deny-list)
        self.assertFalse(self.tools_loader.check_permission(self.ctx, "home_assistant", "call_service", "lock.front_door"))
        self.assertFalse(self.tools_loader.check_permission(self.ctx, "home_assistant", "call_service", "cover.garage"))

    def test_author_actions_allowed_on_automations_and_scripts(self):
        self.assertTrue(
            self.tools_loader.check_permission(self.ctx, "home_assistant", "upsert_automation", "automation.sunset")
        )
        self.assertTrue(
            self.tools_loader.check_permission(self.ctx, "home_assistant", "reload", "automation")
        )
        self.assertTrue(
            self.tools_loader.check_permission(self.ctx, "home_assistant", "upsert_script", "script.goodnight")
        )

    def test_admin_deletions_refused(self):
        self.assertFalse(
            self.tools_loader.check_permission(self.ctx, "home_assistant", "delete_automation", "automation.sunset")
        )


class TestButlerProposeConfirmApplyWorkflow(unittest.TestCase):
    """End-to-end propose -> confirm -> apply execution test in Butler's ExecutionContext."""

    def setUp(self):
        AgentsLoader._instance = None
        ToolsLoader._instance = None
        self.ctx = make_context(agent_id="home-steward")

    @patch("core.integrations.homeassistant.client.HomeAssistantClient.post")
    @patch("core.integrations.homeassistant.client.HomeAssistantClient.get")
    @patch.dict(os.environ, {"HA_WRITE_ENABLED": "true"})
    def test_end_to_end_automation_lifecycle(self, mock_get, mock_post):
        # Mock GET to simulate existing check_config, error_log, and no existing automation
        def fake_get(endpoint, **kwargs):
            if endpoint.startswith("/api/config/automation/config/"):
                return MagicMock(status_code=404, json=lambda: {"message": "Not found"})
            if endpoint == "/api/config/core/check_config":
                return MagicMock(status_code=200, json=lambda: {"result": "valid"})
            if endpoint == "/api/error_log":
                return MagicMock(status_code=200, text="")
            if endpoint == "/api/states/automation.test_porch":
                return MagicMock(status_code=200, json=lambda: {"entity_id": "automation.test_porch", "state": "on"})
            return MagicMock(status_code=200, json=lambda: {})

        def fake_post(endpoint, **kwargs):
            return MagicMock(status_code=200, json=lambda: {"result": "ok"})

        mock_get.side_effect = fake_get
        mock_post.side_effect = fake_post

        automation_payload = {
            "alias": "Test Porch Sunset",
            "trigger": [{"platform": "sun", "event": "sunset"}],
            "action": [{"service": "light.turn_on", "target": {"entity_id": "light.porch"}}],
        }

        # Step 1: Proposal (dry-run without confirm_token)
        proposal_input = {
            "instructions": [
                {
                    "action": "upsert_automation",
                    "id": "test_porch",
                    "config": automation_payload,
                }
            ]
        }
        with execution_context(agent_id="home-steward"):
            proposal_result = home_assistant.invoke(proposal_input)
            self.assertIn("<confirmation_required", proposal_result)
            self.assertIn("<confirm_token>", proposal_result)

            # Extract confirm_token
            token_start = proposal_result.find("<confirm_token>") + len("<confirm_token>")
            token_end = proposal_result.find("</confirm_token>")
            confirm_token = proposal_result[token_start:token_end]

            # Step 2: Apply with valid confirm_token
            apply_input = {
                "instructions": [
                    {
                        "action": "upsert_automation",
                        "id": "test_porch",
                        "confirm_token": confirm_token,
                        "config": automation_payload,
                    }
                ]
            }
            apply_result = home_assistant.invoke(apply_input)
            self.assertIn("<instruction_result", apply_result)
            self.assertIn("applied", apply_result)
            self.assertNotIn("<instruction_error", apply_result)


if __name__ == "__main__":
    unittest.main()
