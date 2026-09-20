"""Tests for the reversible write pipeline.

No network: a fake REST client stands in for Home Assistant and records the exact
order of calls, which matters here more than in most modules -- the safety
property is that the before-image is on disk *before* anything is sent, and only
an ordering assertion can catch a regression that reverses it.

Snapshots go to a temp directory, so these never touch the real PKM.
"""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from core.integrations.homeassistant import writes
from core.integrations.homeassistant.client import HomeAssistantError


AUTOMATION_PATH = "/api/config/automation/config/{}"


class FakeRest:
    """Records every call, and can be told to fail specific ones.

    `store` is the pretend server state: a dict of path -> config. A GET for a
    path that is not in it raises the same 404-shaped error the real client does,
    which is what `fetch_before_image` keys off.
    """

    def __init__(self, store=None, fail_post_paths=(), fail_delete_paths=(), on_call=None):
        self.store = dict(store or {})
        self.fail_post_paths = set(fail_post_paths)
        self.fail_delete_paths = set(fail_delete_paths)
        self.calls = []
        self.on_call = on_call

    def _record(self, verb, path, body=None):
        self.calls.append((verb, path))
        if self.on_call:
            self.on_call(verb, path, body)

    def get(self, path, params=None):
        self._record("GET", path)
        if path not in self.store:
            raise HomeAssistantError(f"Home Assistant returned 404 for GET {path}")
        return self.store[path]

    def post(self, path, json=None, retry=True):
        self._record("POST", path, json)
        if path in self.fail_post_paths:
            raise HomeAssistantError(f"Home Assistant returned 400 for POST {path}: invalid config")
        self.store[path] = json
        return {"result": "ok"}

    def delete(self, path, retry=True):
        self._record("DELETE", path)
        if path in self.fail_delete_paths:
            raise HomeAssistantError(f"Home Assistant returned 500 for DELETE {path}")
        self.store.pop(path, None)
        return {"result": "ok"}

    def verbs(self):
        return [verb for verb, _path in self.calls]


class WriteTestCase(unittest.TestCase):
    """Redirects snapshots into a temp dir for the duration of each test."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.snapshot_dir = os.path.join(self._tmp.name, "snaps")
        os.makedirs(self.snapshot_dir, exist_ok=True)
        patcher = patch.object(writes, "snapshot_dir", lambda: self.snapshot_dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def snapshots(self):
        return sorted(os.listdir(self.snapshot_dir))

    def read_snapshot(self):
        names = self.snapshots()
        self.assertEqual(len(names), 1, f"expected exactly one snapshot, got {names}")
        with open(os.path.join(self.snapshot_dir, names[0]), encoding="utf-8") as handle:
            return json.load(handle)


class TestCreate(WriteTestCase):
    def test_creating_an_automation_applies_and_verifies(self):
        rest = FakeRest()
        result = writes.apply_config_write(
            rest, "upsert_automation", {"id": "abc", "config": {"alias": "Dusk"}}
        )

        self.assertTrue(result.applied)
        self.assertTrue(result.verified)
        self.assertTrue(result.created)
        self.assertFalse(result.rolled_back)
        self.assertEqual(rest.store[AUTOMATION_PATH.format("abc")], {"alias": "Dusk"})

    def test_id_is_generated_when_omitted(self):
        rest = FakeRest()
        result = writes.apply_config_write(rest, "upsert_automation", {"config": {"alias": "X"}})

        self.assertTrue(result.object_id)
        self.assertTrue(result.verified)
        # The generated id must be the one actually written, or the agent cannot
        # refer to the automation it just created.
        self.assertIn(AUTOMATION_PATH.format(result.object_id), rest.store)

    def test_script_requires_an_explicit_id(self):
        """The script id becomes the entity id, so it must not be invented."""
        rest = FakeRest()
        with self.assertRaises(writes.WriteError) as ctx:
            writes.apply_config_write(rest, "upsert_script", {"config": {"alias": "X"}})
        self.assertIn("entity id", str(ctx.exception))

    def test_empty_config_is_refused_before_any_call(self):
        rest = FakeRest()
        with self.assertRaises(writes.WriteError):
            writes.apply_config_write(rest, "upsert_automation", {"id": "a", "config": {}})
        self.assertEqual(rest.calls, [])


class TestSnapshotOrdering(WriteTestCase):
    def test_snapshot_exists_on_disk_before_the_write_is_sent(self):
        """The property the whole design rests on.

        If the process dies during the POST, the before-image must already be
        recoverable. Asserting only that a snapshot exists at the end would pass
        even if it were written afterwards, so this checks at POST time.
        """
        seen = {}

        def spy(verb, path, body):
            if verb == "POST":
                seen["snapshots_at_post_time"] = os.listdir(self.snapshot_dir)

        rest = FakeRest(
            store={AUTOMATION_PATH.format("abc"): {"alias": "Old"}},
            on_call=spy,
        )
        writes.apply_config_write(rest, "upsert_automation", {"id": "abc", "config": {"alias": "New"}})

        self.assertEqual(len(seen.get("snapshots_at_post_time", [])), 1)

    def test_before_image_is_read_before_it_is_written(self):
        rest = FakeRest(store={AUTOMATION_PATH.format("abc"): {"alias": "Old"}})
        writes.apply_config_write(rest, "upsert_automation", {"id": "abc", "config": {"alias": "New"}})

        # GET (before-image) precedes POST (apply).
        self.assertEqual(rest.verbs()[0], "GET")
        self.assertEqual(rest.verbs()[1], "POST")

    def test_snapshot_records_the_previous_version(self):
        rest = FakeRest(store={AUTOMATION_PATH.format("abc"): {"alias": "Old"}})
        writes.apply_config_write(rest, "upsert_automation", {"id": "abc", "config": {"alias": "New"}})

        record = self.read_snapshot()
        self.assertTrue(record["existed"])
        self.assertEqual(record["before"], {"alias": "Old"})
        self.assertEqual(record["after"], {"alias": "New"})

    def test_snapshot_marks_a_new_object_as_not_existing(self):
        """A create and a replace need different undo actions, so this is explicit."""
        rest = FakeRest()
        writes.apply_config_write(rest, "upsert_automation", {"id": "new", "config": {"alias": "N"}})

        record = self.read_snapshot()
        self.assertFalse(record["existed"])
        self.assertIsNone(record["before"])

    def test_rapid_writes_to_one_object_do_not_overwrite_each_others_snapshots(self):
        """Regression: filenames were second-resolution and collided.

        A live acceptance run performed five writes and left four snapshots. The
        lost one belonged to an apply-then-rollback pair, which is precisely the
        sequence whose before-image you most need afterwards. Three writes in the
        same second must leave three files.
        """
        rest = FakeRest()
        for index in range(3):
            writes.apply_config_write(
                rest, "upsert_automation", {"id": "same", "config": {"alias": f"v{index}"}}
            )

        self.assertEqual(len(self.snapshots()), 3, self.snapshots())


class TestRejection(WriteTestCase):
    def test_invalid_config_is_reported_and_nothing_is_stored(self):
        path = AUTOMATION_PATH.format("bad")
        rest = FakeRest(fail_post_paths={path})

        result = writes.apply_config_write(rest, "upsert_automation", {"id": "bad", "config": {"alias": "X"}})

        self.assertFalse(result.applied)
        self.assertFalse(result.verified)
        self.assertNotIn(path, rest.store)
        self.assertIn("rejected", result.detail)

    def test_a_rejected_write_is_not_rolled_back(self):
        """Nothing was applied, so there is nothing to undo.

        Rolling back here would re-POST the before-image for no reason, turning a
        clean failure into an extra write.
        """
        path = AUTOMATION_PATH.format("bad")
        rest = FakeRest(store={path: {"alias": "Old"}}, fail_post_paths={path})

        result = writes.apply_config_write(rest, "upsert_automation", {"id": "bad", "config": {"alias": "X"}})

        self.assertFalse(result.rolled_back)
        self.assertEqual(rest.verbs().count("POST"), 1)

    def test_the_snapshot_path_is_reported_even_on_failure(self):
        path = AUTOMATION_PATH.format("bad")
        rest = FakeRest(fail_post_paths={path})
        result = writes.apply_config_write(rest, "upsert_automation", {"id": "bad", "config": {"alias": "X"}})

        self.assertTrue(result.snapshot_path)
        self.assertIn(result.snapshot_path, result.detail)


class TestVerificationAndRollback(WriteTestCase):
    def test_a_write_that_does_not_read_back_is_rolled_back(self):
        path = AUTOMATION_PATH.format("abc")

        class DriftingRest(FakeRest):
            """Accepts the write but stores something else."""

            def post(self, p, json=None, retry=True):
                self._record("POST", p, json)
                self.store[p] = {"alias": "Something Else"}
                return {"result": "ok"}

        rest = DriftingRest(store={path: {"alias": "Old"}})
        result = writes.apply_config_write(rest, "upsert_automation", {"id": "abc", "config": {"alias": "New"}})

        self.assertTrue(result.applied)
        self.assertFalse(result.verified)
        self.assertTrue(result.rolled_back)
        self.assertIn("differ", result.detail)

    def test_rollback_of_a_created_object_deletes_it(self):
        """A create that fails verification must not leave an orphan behind."""
        path = AUTOMATION_PATH.format("new")

        class DriftingRest(FakeRest):
            def post(self, p, json=None, retry=True):
                self._record("POST", p, json)
                self.store[p] = {"alias": "Wrong"}
                return {"result": "ok"}

        rest = DriftingRest()
        result = writes.apply_config_write(rest, "upsert_automation", {"id": "new", "config": {"alias": "Right"}})

        self.assertTrue(result.rolled_back)
        self.assertIn("DELETE", rest.verbs())
        self.assertNotIn(path, rest.store)

    def test_a_failed_rollback_is_reported_loudly(self):
        """The worst case: changed, not verified, and not restorable.

        This must never be reported as a quiet failure -- the house is in an
        unknown state and the message has to say so and point at the snapshot.
        """
        path = AUTOMATION_PATH.format("new")

        class DriftingRest(FakeRest):
            def post(self, p, json=None, retry=True):
                self._record("POST", p, json)
                self.store[p] = {"alias": "Wrong"}
                return {"result": "ok"}

        rest = DriftingRest(fail_delete_paths={path})
        result = writes.apply_config_write(rest, "upsert_automation", {"id": "new", "config": {"alias": "Right"}})

        self.assertTrue(result.rollback_failed)
        self.assertFalse(result.rolled_back)
        self.assertIn("ROLLBACK ALSO FAILED", result.detail)
        self.assertIn(result.snapshot_path, result.detail)

    def test_keys_home_assistant_adds_do_not_count_as_drift(self):
        """HA stores an `id` alongside the config; that is not a failed write."""
        path = AUTOMATION_PATH.format("abc")

        class AnnotatingRest(FakeRest):
            def post(self, p, json=None, retry=True):
                self._record("POST", p, json)
                self.store[p] = {**json, "id": "abc"}
                return {"result": "ok"}

        rest = AnnotatingRest()
        result = writes.apply_config_write(rest, "upsert_automation", {"id": "abc", "config": {"alias": "Dusk"}})

        self.assertTrue(result.verified)
        self.assertFalse(result.rolled_back)


class TestDelete(WriteTestCase):
    def test_deleting_an_existing_automation_verifies_absence(self):
        path = AUTOMATION_PATH.format("abc")
        rest = FakeRest(store={path: {"alias": "Old"}})

        result = writes.apply_config_write(rest, "delete_automation", {"id": "abc"})

        self.assertTrue(result.applied)
        self.assertTrue(result.verified)
        self.assertNotIn(path, rest.store)

    def test_deleting_something_that_does_not_exist_is_refused(self):
        rest = FakeRest()
        with self.assertRaises(writes.WriteError) as ctx:
            writes.apply_config_write(rest, "delete_automation", {"id": "ghost"})
        self.assertIn("nothing to delete", str(ctx.exception))

    def test_delete_requires_an_id(self):
        rest = FakeRest()
        with self.assertRaises(writes.WriteError):
            writes.apply_config_write(rest, "delete_automation", {})

    def test_delete_snapshots_the_object_first(self):
        """Deletion is the case where the before-image matters most."""
        path = AUTOMATION_PATH.format("abc")
        rest = FakeRest(store={path: {"alias": "Precious"}})

        writes.apply_config_write(rest, "delete_automation", {"id": "abc"})

        self.assertEqual(self.read_snapshot()["before"], {"alias": "Precious"})


class TestServiceCalls(unittest.TestCase):
    def test_service_calls_are_never_retried(self):
        """Not idempotent: a silent second delivery is a second change."""
        captured = {}

        class Rest(FakeRest):
            def post(self, path, json=None, retry=True):
                captured["retry"] = retry
                captured["path"] = path
                captured["body"] = json
                return [{"entity_id": "light.porch"}]

        writes.call_service(Rest(), "light", "turn_on", entity_id="light.porch")

        self.assertFalse(captured["retry"])
        self.assertEqual(captured["path"], "/api/services/light/turn_on")

    def test_entity_id_is_merged_into_the_body(self):
        captured = {}

        class Rest(FakeRest):
            def post(self, path, json=None, retry=True):
                captured["body"] = json
                return []

        writes.call_service(Rest(), "light", "turn_on", entity_id="light.porch",
                            service_data={"brightness": 180})

        self.assertEqual(captured["body"], {"brightness": 180, "entity_id": "light.porch"})

    def test_no_affected_entities_is_surfaced_rather_than_passed_off_as_success(self):
        """HA returns 200 with an empty list for a call that matched nothing."""

        class Rest(FakeRest):
            def post(self, path, json=None, retry=True):
                return []

        result = writes.call_service(Rest(), "light", "turn_on", entity_id="light.nope")

        self.assertEqual(result["changed_entities"], [])
        self.assertIn("no state change", result["note"])

    def test_affected_entities_are_reported(self):
        class Rest(FakeRest):
            def post(self, path, json=None, retry=True):
                return [{"entity_id": "light.porch"}, {"entity_id": "light.hall"}]

        result = writes.call_service(Rest(), "light", "turn_on")

        self.assertEqual(result["changed_entities"], ["light.porch", "light.hall"])
        self.assertEqual(result["note"], "")


class TestReload(unittest.TestCase):
    def test_domain_reload_uses_the_domain_service(self):
        rest = FakeRest()
        writes.reload(rest, domain="automation")
        self.assertIn(("POST", "/api/services/automation/reload"), rest.calls)

    def test_default_reload_is_reload_all(self):
        rest = FakeRest()
        writes.reload(rest)
        self.assertIn(("POST", "/api/services/homeassistant/reload_all"), rest.calls)


class TestUnknownAction(unittest.TestCase):
    def test_a_read_action_is_not_a_config_write(self):
        with self.assertRaises(writes.WriteError):
            writes.apply_config_write(FakeRest(), "get_state", {})


if __name__ == "__main__":
    unittest.main()
