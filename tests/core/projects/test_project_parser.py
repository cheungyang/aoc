import unittest
import os
import tempfile
from core.projects.parser import (
    extract_frontmatter,
    extract_id_from_frontmatter,
    inject_frontmatter_id,
    generate_project_id,
    extract_tags,
    extract_commitments,
    resolve_primary_commitment,
    extract_status,
    extract_dates,
    extract_project_name,
    parse_project_content,
    parse_project_file,
    PRIORITY_MAP,
    STATUS_MAP,
)


class TestProjectParser(unittest.TestCase):

    def test_extract_frontmatter(self):
        content = "---\ntitle: Sample Project\nstatus: active\n---\n# Content header\nSome notes."
        fm, body = extract_frontmatter(content)
        self.assertEqual(fm.get("title"), "Sample Project")
        self.assertEqual(fm.get("status"), "active")
        self.assertIn("# Content header", body)

        # No frontmatter
        no_fm_content = "# Just plain markdown\nNo frontmatter here."
        fm2, body2 = extract_frontmatter(no_fm_content)
        self.assertEqual(fm2, {})
        self.assertEqual(body2, no_fm_content)

    def test_id_extraction_and_injection(self):
        # File with existing frontmatter and existing ID
        content_with_id = "---\nid: custom_id_123\ntitle: Custom ID Project\n---\nBody"
        fm, _ = extract_frontmatter(content_with_id)
        extracted = extract_id_from_frontmatter(fm, content_with_id)
        self.assertEqual(extracted, "custom_id_123")

        same_content, assigned = inject_frontmatter_id(content_with_id)
        self.assertEqual(assigned, "custom_id_123")
        self.assertEqual(same_content, content_with_id)

        # File with frontmatter but NO ID -> inject ID
        content_no_id = "---\ntitle: No ID Project\ntags:\n  - c/🔺2026\n---\nBody text"
        updated_content, new_id = inject_frontmatter_id(content_no_id, new_id="testid123456")
        self.assertEqual(new_id, "testid123456")
        self.assertIn("id: testid123456\n", updated_content)
        self.assertTrue(updated_content.startswith("---\nid: testid123456\n"))

        # File with NO frontmatter at all -> create frontmatter with ID
        plain_content = "# Plain Note\nJust content here."
        updated_plain, new_id_plain = inject_frontmatter_id(plain_content, new_id="plainid12345")
        self.assertEqual(new_id_plain, "plainid12345")
        self.assertTrue(updated_plain.startswith("---\nid: plainid12345\n---\n"))
        self.assertIn("# Plain Note", updated_plain)

    def test_extract_tags(self):
        fm = {"tags": ["s/✊Executing", "c/🔺2026", "#t/🍄Google"]}
        body = "Here is an inline tag #p/aoc and another #urgent/high."
        tags = extract_tags(fm, body)
        self.assertIn("s/✊Executing", tags)
        self.assertIn("c/🔺2026", tags)
        self.assertIn("#t/🍄Google", tags)
        self.assertIn("#p/aoc", tags)
        self.assertIn("#urgent/high", tags)

    def test_extract_commitments(self):
        tags = ["s/✊Executing", "c/🔺2025", "c/⏫2026", "c/2027", "random_tag"]
        commitments = extract_commitments(tags)
        self.assertEqual(len(commitments), 3)
        self.assertEqual(commitments[0], {"year": 2025, "priority": "🔺", "priority_rank": 1})
        self.assertEqual(commitments[1], {"year": 2026, "priority": "⏫", "priority_rank": 2})
        self.assertEqual(commitments[2], {"year": 2027, "priority": None, "priority_rank": 99})

    def test_resolve_primary_commitment(self):
        commitments = [
            {"year": 2025, "priority": "🔺", "priority_rank": 1},
            {"year": 2026, "priority": "⏫", "priority_rank": 2},
        ]
        # Target year matching
        y, p, r = resolve_primary_commitment(commitments, {}, target_year=2026)
        self.assertEqual(y, 2026)
        self.assertEqual(p, "⏫")
        self.assertEqual(r, 2)

        # Fallback to latest year
        y2, p2, r2 = resolve_primary_commitment(commitments, {}, target_year=2030)
        self.assertEqual(y2, 2026)
        self.assertEqual(p2, "⏫")
        self.assertEqual(r2, 2)

        # Explicit frontmatter priority
        fm = {"priority": "🔺", "commitment_year": 2026}
        y3, p3, r3 = resolve_primary_commitment([], fm)
        self.assertEqual(y3, 2026)
        self.assertEqual(p3, "🔺")
        self.assertEqual(r3, 1)

    def test_extract_status(self):
        # Tag based status
        status, raw_st = extract_status({}, ["s/✊Executing", "c/🔺2026"])
        self.assertEqual(status, "executing")
        self.assertEqual(raw_st, "s/✊Executing")

        status_pause, _ = extract_status({}, ["s/⏸️Pause"])
        self.assertEqual(status_pause, "paused")

        status_done, _ = extract_status({}, ["s/🟢Done"])
        self.assertEqual(status_done, "done")

        status_disc, _ = extract_status({}, ["s/🛑Discontinued"])
        self.assertEqual(status_disc, "discontinued")

        status_plan, _ = extract_status({}, ["s/🐙Planning"])
        self.assertEqual(status_plan, "planning")

        status_cons, _ = extract_status({}, ["s/💭Considering"])
        self.assertEqual(status_cons, "considering")

        # Frontmatter status
        fm_st, _ = extract_status({"status": "in-progress"}, [])
        self.assertEqual(fm_st, "executing")

        # Implied active when commitment exists but no status tag
        implied_st, implied_raw = extract_status({}, ["c/🔺2026"], has_commitments=True)
        self.assertEqual(implied_st, "executing")
        self.assertEqual(implied_raw, "implied_active")

    def test_extract_dates_and_project_name(self):
        fm = {
            "title": "My Awesome Project",
            "created": "2026-01-15",
            "reviewed": "2026-06-01",
            "updated": "2026-08-01"
        }
        dates = extract_dates(fm)
        self.assertEqual(dates["start_date"], "2026-01-15")
        self.assertEqual(dates["last_reviewed"], "2026-06-01")
        self.assertEqual(dates["last_updated"], "2026-08-01")

        name = extract_project_name(fm, "vault/projects/My Awesome Project.md")
        self.assertEqual(name, "My Awesome Project")

        # Fallback to filename without title
        name_fb = extract_project_name({}, "vault/projects/Uncharted Territory.md")
        self.assertEqual(name_fb, "Uncharted Territory")

    def test_parse_project_content_full(self):
        content = """---
id: proj_uuid_999
title: Multi-Agent Workflow
tags:
  - c/🔺2025
  - c/⏫2026
  - s/✊Executing
category: 🦄 Personal
type: project
created: 2025-04-02
reviewed: 2026-05-07
aliases:
  - "#p/agentic"
---
# Notes
Here are notes with #p/multiagent.
"""
        project, updated = parse_project_content(
            content=content,
            rel_path="vault/projects/Multi-Agent Workflow.md",
            auto_assign_id=True,
            target_year=2026
        )

        self.assertIsNone(updated)  # Already had ID
        self.assertEqual(project["id"], "proj_uuid_999")
        self.assertEqual(project["name"], "Multi-Agent Workflow")
        self.assertEqual(project["file_path"], "vault/projects/Multi-Agent Workflow.md")
        self.assertEqual(project["status"], "executing")
        self.assertEqual(project["commitment_year"], 2026)
        self.assertEqual(project["priority"], "⏫")
        self.assertEqual(project["priority_rank"], 2)
        self.assertEqual(len(project["commitments"]), 2)
        self.assertEqual(project["start_date"], "2025-04-02")
        self.assertEqual(project["last_reviewed"], "2026-05-07")
        self.assertEqual(project["category"], "🦄 Personal")
        self.assertEqual(project["type"], "project")
        self.assertEqual(project["aliases"], ["#p/agentic"])
        self.assertIn("#p/multiagent", project["tags"])

    def test_parse_project_file_auto_assign(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as tf:
            tf.write("""---
title: File Test Project
tags:
  - s/🟢Done
category: 💌 Gmail
---
Body text.
""")
            temp_path = tf.name

        try:
            proj, updated = parse_project_file(temp_path, rel_path="vault/projects/File Test Project.md", auto_assign_id=True)
            self.assertIsNotNone(proj["id"])
            self.assertIsNotNone(updated)
            self.assertIn(f"id: {proj['id']}", updated)
            self.assertEqual(proj["name"], "File Test Project")
            self.assertEqual(proj["status"], "done")
            self.assertEqual(proj["category"], "💌 Gmail")
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)


if __name__ == "__main__":
    unittest.main()
