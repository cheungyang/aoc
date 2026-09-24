import json
import os
import tempfile
import unittest
from unittest.mock import patch

from tools import schedule_validate as sv
from tests.core.scheduler import use_real_croniter

ALWAYS = {"type": "always", "reason": "external"}


def entry(**kw):
    e = {"kind": "prompt", "cron": "0 9 * * *", "channel": "c", "precondition": dict(ALWAYS), "prompt": "p"}
    e.update(kw)
    return e


class TestScheduleValidate(unittest.TestCase):
    def setUp(self):
        cron = use_real_croniter()
        cron.__enter__()
        self.addCleanup(cron.__exit__, None, None, None)

    def invoke(self, **args):
        return sv.schedule_validate.invoke(args)

    def test_valid_list(self):
        out = self.invoke(agent_id="a", schedules_json=json.dumps([entry()]))
        self.assertIn("1/1 schedule(s) valid", out)
        self.assertIn("OK schedules[0]", out)

    def test_single_entry_and_full_agent_json(self):
        payload, errors = sv.validate_schedules("a", json.dumps(entry()))
        self.assertEqual(errors, "None")
        payload, errors = sv.validate_schedules("a", json.dumps({"agent_id": "a", "schedules": [entry()]}))
        self.assertEqual(errors, "None")

    def test_rejections_listed_with_reasons(self):
        bad = {"cron": "0 9 * * *", "prompt": "x", "channel": "c"}
        payload, errors = sv.validate_schedules("a", json.dumps([entry(), bad]))
        self.assertIn("1/2 schedule(s) valid", payload)
        self.assertIn("REJECTED schedules[1]", payload)
        self.assertIn("'kind'", payload)
        self.assertIn("1 schedule(s) would be rejected", errors)

    def test_bad_input(self):
        self.assertIn("required", sv.validate_schedules("", "[]")[1])
        self.assertIn("not valid JSON", sv.validate_schedules("a", "{")[1])
        self.assertIn("expected", sv.validate_schedules("a", "3")[1])
        self.assertIn("must be a list", sv.validate_schedules("a", '{"schedules": 1}')[1])
        self.assertEqual(sv.validate_schedules("a", "[]"), ("No schedules to validate.", "None"))

    def test_reads_agent_json_from_disk(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "agents", "a"))
            with open(os.path.join(root, "agents", "a", "agent.json"), "w") as f:
                json.dump({"schedules": [entry()]}, f)
            with patch.object(sv, "PROJECT_ROOT", root):
                payload, errors = sv.validate_schedules("a")
                self.assertEqual(errors, "None")
                self.assertIn("does not exist", sv.validate_schedules("zzz")[1])

    def test_real_agents_validate(self):
        payload, errors = sv.validate_schedules("script-executor")
        self.assertEqual(errors, "None", payload)


if __name__ == "__main__":
    unittest.main()
