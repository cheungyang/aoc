import unittest
import tempfile
import os
import shutil
import sqlite3

from core.knowledge.tasks.db import (
    get_connection,
    init_db,
    upsert_tasks,
    prune_deleted_tasks,
    query_tasks_db,
    get_task_by_id,
    get_task_stats,
    execute_read_sql
)
from core.knowledge.tasks.sync import sync_tasks, process_file


class TestTaskSyncAndDb(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_tasks.db")
        self.pkm_dir = os.path.join(self.temp_dir, "pkm")
        os.makedirs(os.path.join(self.pkm_dir, "ticktick"), exist_ok=True)
        os.makedirs(os.path.join(self.pkm_dir, "vault", "projects"), exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_db_init_and_crud(self):
        conn = get_connection(self.db_path)
        init_db(conn)

        # Upsert 2 tasks
        tasks = [
            {
                "id": "t1",
                "title": "Task 1",
                "raw_title": "Task 1 🔺",
                "status": "todo",
                "priority": "🔺",
                "priority_rank": 1,
                "tags": ["p/aoc"],
                "scheduled_date": "2026-08-10",
                "due_date": "2026-08-15",
                "completed_date": None,
                "dropped_date": None,
                "created_date": "2026-08-01",
                "source": "vault/projects/AOC.md",
                "source_type": "vault",
                "line_number": 5,
                "raw_line": "- [ ] Task 1 🔺"
            },
            {
                "id": "t2",
                "title": "Task 2",
                "raw_title": "Task 2 🔽",
                "status": "completed",
                "priority": "🔽",
                "priority_rank": 4,
                "tags": ["a/learn"],
                "scheduled_date": None,
                "due_date": None,
                "completed_date": "2026-08-05",
                "dropped_date": None,
                "created_date": None,
                "source": "ticktick/Inbox.md",
                "source_type": "ticktick",
                "line_number": 12,
                "raw_line": "- [x] Task 2 🔽 ✅ 2026-08-05"
            }
        ]

        stats = upsert_tasks(conn, tasks)
        self.assertEqual(stats["inserted"], 2)
        self.assertEqual(stats["updated"], 0)
        self.assertEqual(stats["unchanged"], 0)

        # Fetch by ID
        t1 = get_task_by_id(conn, "t1")
        self.assertIsNotNone(t1)
        self.assertEqual(t1["title"], "Task 1")
        self.assertEqual(t1["priority"], "🔺")
        self.assertEqual(t1["tags"], ["p/aoc"])

        # Query filter by tag
        res = query_tasks_db(conn, status="todo", tags=["p/aoc"])
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["id"], "t1")

        # Query filter by priority rank
        res_p = query_tasks_db(conn, min_priority="🔼", status="all")
        self.assertEqual(len(res_p), 1)
        self.assertEqual(res_p[0]["id"], "t1")

        # Stats check
        s = get_task_stats(conn)
        self.assertEqual(s["total_tasks"], 2)
        self.assertEqual(s["status"]["todo"], 1)
        self.assertEqual(s["status"]["completed"], 1)

        # Update a task
        tasks[0]["title"] = "Task 1 Updated"
        stats2 = upsert_tasks(conn, tasks)
        self.assertEqual(stats2["inserted"], 0)
        self.assertEqual(stats2["updated"], 1)
        self.assertEqual(stats2["unchanged"], 1)

        # Prune deleted tasks
        pruned = prune_deleted_tasks(conn, ["t1"]) # t2 deleted
        self.assertEqual(pruned, 1)
        self.assertIsNone(get_task_by_id(conn, "t2"))

        conn.close()

    def test_execute_read_sql(self):
        conn = get_connection(self.db_path)
        init_db(conn)

        # Valid SELECT
        res, err = execute_read_sql(conn, "SELECT count(*) as count FROM tasks")
        self.assertIsNone(err)
        self.assertEqual(res[0]["count"], 0)

        # Reject mutation statements
        res_del, err_del = execute_read_sql(conn, "DELETE FROM tasks")
        self.assertIn("Error: Only read-only SELECT queries are permitted.", err_del)

        conn.close()

    def test_full_sync_workflow(self):
        # Create ticktick file
        tt_file = os.path.join(self.pkm_dir, "ticktick", "Inbox.md")
        with open(tt_file, "w") as f:
            f.write("# Inbox\n\n- [ ] [Doc Title](http://...) #a/learn [link](https://ticktick...) %%[ticktick_id:: tt_100]%% ⏫\n")

        # Create vault file without ID
        vt_file = os.path.join(self.pkm_dir, "vault", "projects", "Home.md")
        with open(vt_file, "w") as f:
            f.write("# Home\n\n- [ ] Fix leaky faucet #p/home 🔺 📅 2026-08-20\n")

        # Run sync
        result = sync_tasks(pkm_dir=self.pkm_dir, db_path=self.db_path)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["scanned_files"], 2)
        self.assertEqual(result["modified_markdown_files"], 1) # Vault file was modified to inject ID
        self.assertEqual(result["total_tasks_found"], 2)
        self.assertEqual(result["inserted"], 2)

        # Verify vault file now contains aoc_id
        with open(vt_file, "r") as f:
            content = f.read()
            self.assertIn("%% aoc_id ", content)

        # Verify database contents
        conn = get_connection(self.db_path)
        all_tasks = query_tasks_db(conn, status="all")
        self.assertEqual(len(all_tasks), 2)
        
        titles = {t["title"] for t in all_tasks}
        self.assertIn("Doc Title", titles)
        self.assertIn("Fix leaky faucet", titles)
        conn.close()


if __name__ == "__main__":
    unittest.main()
