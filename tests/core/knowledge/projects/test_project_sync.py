import unittest
import tempfile
import os
import shutil

from core.knowledge.projects.db import (
    get_connection,
    query_projects_db,
    get_project_by_id,
)
from core.knowledge.projects.sync import sync_projects


class TestProjectSync(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_projects.db")
        self.pkm_dir = os.path.join(self.temp_dir, "pkm")
        self.projects_dir = os.path.join(self.pkm_dir, "vault", "projects")
        os.makedirs(self.projects_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_sync_workflow_and_rename_resilience(self):
        # 1. Create project without ID in frontmatter
        p1_path = os.path.join(self.projects_dir, "Agentic Workflow.md")
        with open(p1_path, "w", encoding="utf-8") as f:
            f.write("""---
title: Agentic Workflow
tags:
  - c/🔺2026
  - s/✊Executing
category: 🦄 Personal
created: 2026-01-01
reviewed: 2026-08-01
aliases:
  - "#p/agentic"
---
# Notes
Building multi-agent workflows.
""")

        # Create project 2 without ID in frontmatter
        p2_path = os.path.join(self.projects_dir, "Team Reorg.md")
        with open(p2_path, "w", encoding="utf-8") as f:
            f.write("""---
tags:
  - s/🟢Done
category: 💌 Gmail
---
# Team Reorg
Done in 2024.
""")

        # 2. Run sync
        result = sync_projects(
            pkm_dir=self.pkm_dir,
            db_path=self.db_path,
            projects_dir=self.projects_dir
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["scanned_files"], 2)
        self.assertEqual(result["modified_files"], 2)  # IDs injected into markdown files
        self.assertEqual(result["total_projects_found"], 2)
        self.assertEqual(result["inserted"], 2)
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["unchanged"], 0)
        self.assertEqual(result["pruned"], 0)

        # Verify files now contain 'id:' in frontmatter
        with open(p1_path, "r", encoding="utf-8") as f:
            p1_content = f.read()
            self.assertIn("id: ", p1_content)

        # Retrieve project 1 from DB
        conn = get_connection(self.db_path)
        all_projs = query_projects_db(conn, status="all")
        self.assertEqual(len(all_projs), 2)

        p1_db = [p for p in all_projs if p["name"] == "Agentic Workflow"][0]
        p1_uuid = p1_db["id"]
        self.assertIsNotNone(p1_uuid)
        self.assertEqual(p1_db["file_path"], "vault/projects/Agentic Workflow.md")
        self.assertEqual(p1_db["status"], "executing")
        self.assertEqual(p1_db["commitment_year"], 2026)
        conn.close()

        # 3. Rename/move file: 'Agentic Workflow.md' -> 'Renamed Agentic.md'
        renamed_p1_path = os.path.join(self.projects_dir, "Renamed Agentic.md")
        os.rename(p1_path, renamed_p1_path)

        # Run sync after rename
        result_rename = sync_projects(
            pkm_dir=self.pkm_dir,
            db_path=self.db_path,
            projects_dir=self.projects_dir
        )

        self.assertEqual(result_rename["scanned_files"], 2)
        self.assertEqual(result_rename["modified_files"], 0)  # Already has UUID
        self.assertEqual(result_rename["inserted"], 0)  # Same UUID, not a new insertion!
        self.assertEqual(result_rename["updated"], 1)   # Updated file_path!
        self.assertEqual(result_rename["pruned"], 0)    # Not pruned because UUID was preserved!

        # Verify DB updated file_path for the same UUID
        conn = get_connection(self.db_path)
        p1_renamed_db = get_project_by_id(conn, p1_uuid)
        self.assertIsNotNone(p1_renamed_db)
        self.assertEqual(p1_renamed_db["file_path"], "vault/projects/Renamed Agentic.md")
        self.assertEqual(p1_renamed_db["id"], p1_uuid)
        conn.close()

        # 4. Delete project 2 and sync -> pruned
        os.remove(p2_path)
        result_del = sync_projects(
            pkm_dir=self.pkm_dir,
            db_path=self.db_path,
            projects_dir=self.projects_dir
        )
        self.assertEqual(result_del["scanned_files"], 1)
        self.assertEqual(result_del["pruned"], 1)

    def test_sync_dry_run(self):
        p1_path = os.path.join(self.projects_dir, "Dry Run Test.md")
        with open(p1_path, "w", encoding="utf-8") as f:
            f.write("---\ntitle: Dry Run Test\n---\n")

        result = sync_projects(
            pkm_dir=self.pkm_dir,
            db_path=self.db_path,
            projects_dir=self.projects_dir,
            dry_run=True
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["total_projects_found"], 1)
        self.assertTrue(result["dry_run"])

        # Verify dry run did not write to file
        with open(p1_path, "r", encoding="utf-8") as f:
            self.assertNotIn("id: ", f.read())


if __name__ == "__main__":
    unittest.main()
