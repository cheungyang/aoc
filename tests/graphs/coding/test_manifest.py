"""The manifest is the only durable record of what the pipeline is doing.

Every tick reads it, mutates one task and writes the whole document back, so
the contract is that a write never loses a concurrent write, a lease is never
held by two owners at once, and a crashed tick's work returns to the queue at
the stage it reached. Break any of these and tasks either run twice or vanish
from the queue with no error anywhere.
"""
import json
import os
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from graphs.coding.utils.manifest import (
    DEFAULT_LEASE_SECONDS,
    MANIFEST_VERSION,
    acquire_lease,
    bump_attempt,
    lease_is_active,
    load_manifest,
    locked_manifest,
    migrate_manifest,
    next_stage,
    persist_task,
    reclaim_expired_leases,
    release_lease,
    save_manifest,
    stage_at_or_past,
    yield_task,
)


def _manifest(tasks):
    return {
        "version": MANIFEST_VERSION,
        "project_name": "proj",
        "max_concurrency": 1,
        "repo": {"slug": "owner/repo"},
        "queue": tasks,
    }


class ManifestTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = os.path.join(self.tmpdir, "build_request.json")

    def write(self, data):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def read(self):
        with open(self.path, "r", encoding="utf-8") as f:
            return json.load(f)


class TestMigration(ManifestTestCase):
    def test_v2_statuses_are_mapped(self):
        migrated = migrate_manifest({
            "version": "2.0",
            "queue": [
                {"task_id": "A", "status": "pending"},
                {"task_id": "B", "status": "in_progress"},
                {"task_id": "C", "status": "in_review"},
                {"task_id": "D", "status": "completed"},
                {"task_id": "E", "status": "rejected"},
            ]
        })
        statuses = {t["task_id"]: t["status"] for t in migrated["queue"]}
        self.assertEqual(statuses, {
            "A": "queued", "B": "active", "C": "awaiting_review",
            "D": "done", "E": "failed"
        })
        self.assertEqual(migrated["version"], MANIFEST_VERSION)

    def test_in_flight_task_resumes_from_the_start_not_mid_stage(self):
        # A v2 manifest records no stage, so claiming the task was "implemented"
        # would skip work that may never have happened.
        migrated = migrate_manifest({"queue": [{"task_id": "B", "status": "in_progress"}]})
        self.assertEqual(migrated["queue"][0]["stage"], "queued")

    def test_is_idempotent(self):
        once = migrate_manifest({"queue": [{"task_id": "A", "status": "pending"}]})
        twice = migrate_manifest(once)
        self.assertEqual(once["queue"][0]["status"], twice["queue"][0]["status"])
        self.assertEqual(twice["queue"][0]["stage"], "queued")

    def test_unknown_keys_survive(self):
        migrated = migrate_manifest({"queue": [], "reviewers": ["me"], "repo": {"slug": "o/r"}})
        self.assertEqual(migrated["reviewers"], ["me"])
        self.assertEqual(migrated["repo"], {"slug": "o/r"})

    def test_corrupt_manifest_raises_instead_of_looking_empty(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{not json")
        # Returning an empty queue here would read as "everything finished".
        with self.assertRaises(ValueError):
            load_manifest(self.path)

    def test_missing_file_is_an_empty_manifest(self):
        data = load_manifest(os.path.join(self.tmpdir, "nope.json"))
        self.assertEqual(data["queue"], [])
        self.assertEqual(data["version"], MANIFEST_VERSION)


class TestAtomicAndLockedWrites(ManifestTestCase):
    def test_save_leaves_no_temp_files(self):
        save_manifest(self.path, _manifest([]))
        leftovers = [f for f in os.listdir(self.tmpdir) if f.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_persist_task_preserves_other_keys_and_tasks(self):
        self.write(_manifest([
            {"task_id": "A", "status": "queued", "pr_url": "https://x/1"},
            {"task_id": "B", "status": "queued"},
        ]))

        persist_task(self.path, "B", status="active", stage="provisioned")

        data = self.read()
        self.assertEqual(data["repo"], {"slug": "owner/repo"})
        self.assertEqual(data["queue"][0]["pr_url"], "https://x/1")
        self.assertEqual(data["queue"][1]["status"], "active")
        self.assertEqual(data["queue"][1]["stage"], "provisioned")
        self.assertIn("updated_at", data["queue"][1])

    def test_persist_task_unknown_id_returns_none(self):
        self.write(_manifest([{"task_id": "A", "status": "queued"}]))
        self.assertIsNone(persist_task(self.path, "ZZZ", status="done"))

    def test_locked_manifest_writes_back_mutations(self):
        self.write(_manifest([{"task_id": "A", "status": "queued"}]))
        with locked_manifest(self.path) as manifest:
            manifest["queue"].append({"task_id": "B", "status": "queued"})
        self.assertEqual(len(self.read()["queue"]), 2)

    def test_concurrent_writers_do_not_lose_updates(self):
        # The failure this prevents: two nodes each rewrite the whole document
        # from their own snapshot, and the second silently discards the first.
        self.write(_manifest([{"task_id": f"T{i}", "status": "queued"} for i in range(8)]))

        def claim(i):
            return persist_task(self.path, f"T{i}", status="active", run_id=f"run_{i}")

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(claim, range(8)))

        data = self.read()
        self.assertEqual([t["status"] for t in data["queue"]], ["active"] * 8)
        self.assertEqual([t["run_id"] for t in data["queue"]], [f"run_{i}" for i in range(8)])


class TestAttempts(ManifestTestCase):
    def test_attempts_are_per_stage(self):
        self.write(_manifest([{"task_id": "A", "status": "queued"}]))

        self.assertEqual(bump_attempt(self.path, "A", "publish"), 1)
        self.assertEqual(bump_attempt(self.path, "A", "publish"), 2)
        # A flaky publish must not consume the implement budget (B1).
        self.assertEqual(bump_attempt(self.path, "A", "implement"), 1)

        attempts = self.read()["queue"][0]["attempts"]
        self.assertEqual(attempts, {"publish": 2, "implement": 1})


class TestLeases(ManifestTestCase):
    def setUp(self):
        super().setUp()
        self.write(_manifest([{"task_id": "A", "status": "queued"}]))

    def test_second_owner_is_refused_while_the_lease_is_live(self):
        self.assertTrue(acquire_lease(self.path, "A", owner="tick-1"))
        self.assertFalse(acquire_lease(self.path, "A", owner="tick-2"))
        self.assertEqual(self.read()["queue"][0]["lease_owner"], "tick-1")

    def test_owner_can_extend_its_own_lease(self):
        now = time.time()
        acquire_lease(self.path, "A", owner="tick-1", ttl_seconds=60, now=now)
        self.assertTrue(acquire_lease(self.path, "A", owner="tick-1", ttl_seconds=600, now=now))
        self.assertAlmostEqual(self.read()["queue"][0]["lease_expires_at"], now + 600, places=3)

    def test_expired_lease_can_be_taken_over(self):
        past = time.time() - DEFAULT_LEASE_SECONDS - 10
        acquire_lease(self.path, "A", owner="dead-tick", now=past)
        self.assertTrue(acquire_lease(self.path, "A", owner="tick-2"))

    def test_release_refuses_to_steal(self):
        acquire_lease(self.path, "A", owner="tick-1")
        self.assertFalse(release_lease(self.path, "A", owner="tick-2"))
        self.assertTrue(release_lease(self.path, "A", owner="tick-1"))
        self.assertIsNone(self.read()["queue"][0]["lease_owner"])

    def test_crash_recovery_requeues_an_abandoned_active_task(self):
        past = time.time() - DEFAULT_LEASE_SECONDS - 10
        acquire_lease(self.path, "A", owner="dead-tick", now=past)
        persist_task(self.path, "A", status="active", stage="implemented")

        reclaimed = reclaim_expired_leases(self.path)

        self.assertEqual(reclaimed, ["A"])
        task = self.read()["queue"][0]
        self.assertEqual(task["status"], "queued")
        self.assertIsNone(task["lease_owner"])
        # It resumes where it got to rather than starting over.
        self.assertEqual(task["stage"], "implemented")

    def test_live_lease_is_not_reclaimed(self):
        acquire_lease(self.path, "A", owner="tick-1")
        self.assertEqual(reclaim_expired_leases(self.path), [])

    def test_lease_is_active_helper(self):
        self.assertFalse(lease_is_active({}))
        self.assertFalse(lease_is_active({"lease_owner": "x", "lease_expires_at": time.time() - 1}))
        self.assertTrue(lease_is_active({"lease_owner": "x", "lease_expires_at": time.time() + 60}))


class TestYieldTask(ManifestTestCase):
    """A tick that stops mid-task must leave it visible to the next tick."""

    def setUp(self):
        super().setUp()
        self.write(_manifest([{"task_id": "A", "status": "queued"}]))

    def test_yield_clears_the_lease_and_requeues_at_the_same_stage(self):
        acquire_lease(self.path, "A", owner="tick-1")
        persist_task(self.path, "A", status="active", stage="audited")

        yield_task(self.path, "A")

        task = self.read()["queue"][0]
        self.assertIsNone(task["lease_owner"])
        self.assertIsNone(task["lease_expires_at"])
        # Runnable again — `active` without a lease would be invisible forever.
        self.assertEqual(task["status"], "queued")
        # And resumable — publish re-enters at publish, not at the LLM.
        self.assertEqual(task["stage"], "audited")

    def test_yield_carries_the_fields_it_is_given(self):
        yield_task(self.path, "A", last_error={"kind": "github"}, head_sha="abc")

        task = self.read()["queue"][0]
        self.assertEqual(task["last_error"]["kind"], "github")
        self.assertEqual(task["head_sha"], "abc")

    def test_an_explicit_status_still_wins(self):
        yield_task(self.path, "A", status="blocked")
        self.assertEqual(self.read()["queue"][0]["status"], "blocked")


class TestStageHelpers(unittest.TestCase):
    def test_stage_at_or_past(self):
        self.assertTrue(stage_at_or_past({"stage": "verified"}, "implemented"))
        self.assertTrue(stage_at_or_past({"stage": "verified"}, "verified"))
        self.assertFalse(stage_at_or_past({"stage": "implemented"}, "verified"))
        self.assertFalse(stage_at_or_past({}, "implemented"))
        self.assertFalse(stage_at_or_past({"stage": "nonsense"}, "implemented"))

    def test_next_stage(self):
        self.assertEqual(next_stage("queued"), "provisioned")
        self.assertIsNone(next_stage("done"))
        self.assertIsNone(next_stage("nonsense"))


if __name__ == "__main__":
    unittest.main()
