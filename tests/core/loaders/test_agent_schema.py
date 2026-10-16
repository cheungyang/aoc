"""agent.json validation: the shapes AgentsLoader accepts, and the ones it rejects."""
import unittest

from core.loaders.agent_schema import validate_agent_config

KNOWN = {"research", "weekly_review", "coach_home_workout"}


def errors(**config):
    return validate_agent_config({"agent_id": "a", "name": "A", **config}, KNOWN)


class TestSkills(unittest.TestCase):

    def test_object_form_is_accepted(self):
        self.assertEqual(errors(skills={
            "coach_home_workout": {"preload": "full"},
            "research": {"preload": "core"},
            "weekly_review": {},
        }), [])

    def test_list_form_is_rejected(self):
        """No compatibility path: every agent.json was migrated to the object form."""
        errs = errors(skills=["research"])
        self.assertEqual(len(errs), 1)
        self.assertIn("'skills' must be an object", errs[0])

    def test_unknown_skill(self):
        self.assertEqual(errors(skills={"memory": {}}), ["skills: unknown skill 'memory'"])

    def test_bad_preload_value(self):
        self.assertIn("skills.research.preload must be one of full, core, got 'all'",
                      errors(skills={"research": {"preload": "all"}}))

    def test_unknown_setting_and_non_object_settings(self):
        self.assertIn("skills.research: unknown key(s) eager", errors(skills={"research": {"eager": True}}))
        self.assertIn("skills.research: settings must be an object ({} for defaults)",
                      errors(skills={"research": "core"}))

    def test_skills_are_optional(self):
        self.assertEqual(errors(), [])


class TestThinking(unittest.TestCase):

    def test_overrides_per_surface(self):
        self.assertEqual(errors(thinking={"text": "high", "voice": "minimal", "scheduled": "medium"}), [])

    def test_unknown_surface_and_level(self):
        errs = errors(thinking={"default": "medium", "text": "max"})
        self.assertTrue(any("unknown surface 'default'" in e for e in errs), errs)
        self.assertTrue(any("thinking.text: unknown level 'max'" in e for e in errs), errs)

    def test_must_be_an_object(self):
        self.assertTrue(errors(thinking="high")[0].startswith("'thinking' must be an object"))


class TestVoice(unittest.TestCase):

    def test_name_and_rate(self):
        self.assertEqual(errors(voice={"name": "en-US-Chirp3-HD-Zephyr", "rate": 1.15}), [])
        self.assertEqual(errors(voice={"name": "en-GB-Chirp3-HD-Charon"}), [])

    def test_name_required_and_shaped(self):
        self.assertTrue(any("voice.name" in e for e in errors(voice={"rate": 1.0})))
        self.assertTrue(any("voice.name" in e for e in errors(voice={"name": "en-US-AriaNeural"})))

    def test_rate_range(self):
        for rate in (0.1, 2.5, "fast", True):
            with self.subTest(rate=rate):
                self.assertTrue(any("voice.rate" in e for e in errors(voice={"name": "en-US-Chirp3-HD-Puck", "rate": rate})))

    def test_unknown_key(self):
        self.assertIn("voice: unknown key(s) pitch",
                      errors(voice={"name": "en-US-Chirp3-HD-Puck", "pitch": 2}))


class TestTopLevel(unittest.TestCase):

    def test_unknown_keys_are_rejected(self):
        self.assertEqual(errors(skill_preload={"research": "core"}), ["unknown key(s): skill_preload"])

    def test_not_an_object(self):
        self.assertEqual(validate_agent_config(["x"], KNOWN), ["agent.json must be a JSON object"])


if __name__ == "__main__":
    unittest.main()
