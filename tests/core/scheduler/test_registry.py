import os
import tempfile
import textwrap
import unittest
from unittest.mock import MagicMock

from core.scheduler.registry import (
    Rejection,
    ScheduleValidationError,
    build_spec,
    load_agent_schedules,
    load_all,
    schedule_id_for,
    validate_entry,
)
from core.scheduler.spec import PromptSchedule, ScriptSchedule, SessionPolicy
from tests.core.scheduler import use_real_croniter

ALWAYS = {"type": "always", "reason": "external API"}

GOOD_SCRIPT = textwrap.dedent('''
    def has_work(ctx):
        return True

    if __name__ == "__main__":
        print("x")
''')


def prompt_entry(**kw):
    entry = {
        "kind": "prompt",
        "cron": "0 9 * * *",
        "channel": "general",
        "precondition": dict(ALWAYS),
        "prompt": ["do", "it"],
    }
    entry.update(kw)
    return entry


class RegistryCase(unittest.TestCase):
    def setUp(self):
        cron = use_real_croniter()
        cron.__enter__()
        self.addCleanup(cron.__exit__, None, None, None)
        self._tmp = tempfile.TemporaryDirectory()
        self.scripts = self._tmp.name
        self.write_script("ok.py", GOOD_SCRIPT)
        self.write_script("bad.py", "print('side effect')\n")

    def tearDown(self):
        self._tmp.cleanup()

    def write_script(self, name, src):
        with open(os.path.join(self.scripts, name), "w") as f:
            f.write(src)

    def errors(self, entry):
        return validate_entry("a", entry, self.scripts)

    def assertRejected(self, entry, fragment):
        errs = self.errors(entry)
        self.assertTrue(any(fragment in e for e in errs), f"{fragment!r} not in {errs}")


class TestPromptEntries(RegistryCase):
    def test_valid_prompt_entry(self):
        spec = build_spec("a", prompt_entry(session_policy="persistent"), self.scripts)
        self.assertIsInstance(spec, PromptSchedule)
        self.assertEqual(spec.prompt, "do\nit")
        self.assertIs(spec.session_policy, SessionPolicy.PERSISTENT)
        self.assertEqual(spec.channel, "general")

    def test_default_policy_is_stateless(self):
        spec = build_spec("a", prompt_entry(), self.scripts)
        self.assertIs(spec.session_policy, SessionPolicy.STATELESS)

    def test_precondition_required(self):
        entry = prompt_entry()
        del entry["precondition"]
        self.assertRejected(entry, "require a 'precondition'")

    def test_always_needs_reason(self):
        self.assertRejected(prompt_entry(precondition={"type": "always"}), "precondition:")

    def test_unknown_precondition(self):
        self.assertRejected(prompt_entry(precondition={"type": "vibes"}), "unknown precondition type")

    def test_channel_required(self):
        entry = prompt_entry()
        del entry["channel"]
        self.assertRejected(entry, "require a 'channel'")

    def test_prompt_required(self):
        self.assertRejected(prompt_entry(prompt=[]), "'prompt' must be")
        self.assertRejected(prompt_entry(prompt="  "), "'prompt' must be")

    def test_bad_session_policy(self):
        self.assertRejected(prompt_entry(session_policy="shared"), "unknown session_policy")

    def test_script_key_not_allowed_on_prompt(self):
        self.assertRejected(prompt_entry(script="ok.py"), "unknown key(s)")


class TestCommonRules(RegistryCase):
    def test_kind_required(self):
        entry = prompt_entry()
        del entry["kind"]
        self.assertRejected(entry, "missing or unknown 'kind'")
        self.assertRejected(prompt_entry(kind="shell"), "missing or unknown 'kind'")

    def test_cron_validated(self):
        self.assertRejected(prompt_entry(cron="every day"), "invalid or missing 'cron'")
        entry = prompt_entry()
        del entry["cron"]
        self.assertRejected(entry, "invalid or missing 'cron'")

    def test_enabled_parsing(self):
        self.assertFalse(build_spec("a", prompt_entry(enabled="false"), self.scripts).enabled)
        self.assertTrue(build_spec("a", prompt_entry(enabled=True), self.scripts).enabled)
        self.assertRejected(prompt_entry(enabled="maybe"), "'enabled' must be")

    def test_empty_thread(self):
        self.assertRejected(prompt_entry(thread=""), "'thread' must be")

    def test_not_an_object(self):
        self.assertEqual(self.errors(["x"]), ["entry must be a JSON object"])

    def test_all_errors_reported_together(self):
        with self.assertRaises(ScheduleValidationError) as cm:
            build_spec("a", prompt_entry(cron="bad", precondition={"type": "x"}, prompt=[]), self.scripts)
        self.assertGreaterEqual(len(cm.exception.errors), 3)


class TestScriptEntries(RegistryCase):
    def script_entry(self, **kw):
        entry = {"kind": "script", "cron": "*/5 * * * *", "channel": "dev", "script": "ok.py"}
        entry.update(kw)
        return entry

    def test_valid_single_and_list(self):
        spec = build_spec("a", self.script_entry(), self.scripts)
        self.assertIsInstance(spec, ScriptSchedule)
        self.assertEqual([s.script for s in spec.steps], ["ok.py"])
        spec = build_spec("a", self.script_entry(script=["ok.py --x 1", "ok.py"]), self.scripts)
        self.assertEqual(spec.steps[0].args, ("--x", "1"))

    def test_precondition_optional_but_validated(self):
        self.assertEqual(self.errors(self.script_entry()), [])
        self.assertRejected(self.script_entry(precondition={"type": "x"}), "precondition:")

    def test_session_policy_rejected_with_hint(self):
        self.assertRejected(self.script_entry(session_policy="stateless"), "prompt schedules only")

    def test_prompt_key_rejected(self):
        self.assertRejected(self.script_entry(prompt=["script ok.py"]), "unknown key(s)")

    def test_script_must_exist_and_comply(self):
        self.assertRejected(self.script_entry(script="missing.py"), "does not exist")
        self.assertRejected(self.script_entry(script="bad.py"), "has_work")
        self.assertRejected(self.script_entry(script="../ok.py"), "bare file name")
        self.assertRejected(self.script_entry(script=[]), "'script' must be")

    def test_channel_optional_for_scripts(self):
        entry = self.script_entry()
        del entry["channel"]
        self.assertEqual(self.errors(entry), [])


class TestIdentityAndLoading(RegistryCase):
    def test_explicit_id(self):
        self.assertEqual(schedule_id_for("a", {"id": "nightly"}), "a:nightly")

    def test_derived_id_is_content_based(self):
        e1 = prompt_entry()
        e2 = prompt_entry()
        self.assertEqual(schedule_id_for("a", e1), schedule_id_for("a", e2))
        self.assertNotEqual(schedule_id_for("a", e1), schedule_id_for("a", prompt_entry(cron="0 10 * * *")))
        self.assertTrue(schedule_id_for("a", e1).startswith("a:prompt:"))

    def test_load_rejects_individually_and_dedupes(self):
        config = {"schedules": [prompt_entry(), prompt_entry(), prompt_entry(kind="nope")]}
        result = load_agent_schedules("a", config, self.scripts)
        self.assertEqual(len(result.specs), 2)
        self.assertEqual(result.specs[1].schedule_id, result.specs[0].schedule_id + "#1")
        self.assertEqual(len(result.rejections), 1)
        self.assertEqual(result.rejections[0].index, 2)

    def test_schedules_not_a_list(self):
        result = load_agent_schedules("a", {"schedules": {"x": 1}}, self.scripts)
        self.assertEqual(result.specs, [])
        self.assertEqual(len(result.rejections), 1)

    def test_load_all_uses_loader_configs(self):
        loader = MagicMock()
        loader.list_agent_ids.return_value = ["b", "a"]
        loader.get_agent_config.side_effect = lambda aid: {"schedules": [prompt_entry()]} if aid == "a" else None
        result = load_all(loader, self.scripts)
        self.assertEqual([s.agent_id for s in result.specs], ["a"])


class TestRejection(unittest.TestCase):
    def test_alert_channel_is_the_entry_channel(self):
        self.assertEqual(Rejection("a", 0, {"channel": " dev "}, ["x"]).channel, "dev")
        self.assertIsNone(Rejection("a", 0, {"cron": "x"}, ["x"]).channel)
        self.assertIsNone(Rejection("a", 0, "junk", ["x"]).channel)

    def test_message_and_key(self):
        r = Rejection("a", 3, {"cron": "0 9 * * *", "channel": "c"}, ["one", "two"])
        self.assertIn("schedules[3]", r.message())
        self.assertIn("one; two", r.message())
        self.assertEqual(r.key(), "a#3:one; two")
        self.assertEqual(Rejection("a", 0, "junk", ["x"]).cron, "?")


if __name__ == "__main__":
    unittest.main()
