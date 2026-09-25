import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

from core.util.config import Config
from scripts import wiki_scanner as ws

NOW = datetime(2026, 9, 24)


def _row(path, vector, text="shared words here", tags='["concept"]', updated_at="2026-09-01T00:00:00"):
    return {"file_path": path, "vector": vector, "text": text, "tags": tags, "updated_at": updated_at}


class ScannerMixin:
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.pkm = os.path.join(self.tmp, "pkm")
        self.wiki = os.path.join(self.pkm, "wiki")
        Config().pkm_dir = self.pkm
        Config().knowledge_db_path = os.path.join(self.tmp, ".lancedb")

    def tearDown(self):
        Config().reset()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def note(self, rel, body="# Note"):
        full = os.path.join(self.pkm, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(body)
        return rel

    def folder(self, name, indexed=True):
        os.makedirs(os.path.join(self.wiki, name), exist_ok=True)
        if indexed:
            self.note(f"wiki/{name}/index.md", "# Index")

    def run_with(self, rows):
        with patch.object(ws, "init_knowledge_db", return_value=object()), \
                patch.object(ws, "scan_by_category", return_value=rows):
            ws.run_scanner(now=NOW)
        with open(os.path.join(self.pkm, ws.PENDING_FILE)) as f:
            return json.load(f)

    def state(self):
        with open(os.path.join(self.pkm, ws.STATE_FILE)) as f:
            return json.load(f)


class TestHasWork(unittest.TestCase):
    def test_always_runs(self):
        ok, _ = ws.has_work(None)
        self.assertTrue(ok)


class TestScope(ScannerMixin, unittest.TestCase):
    def test_rows_outside_marked_folders_and_index_files_dropped(self):
        self.folder("concepts")
        self.folder("software", indexed=False)
        from core.knowledge.vector.sync import wiki_scope
        in_scope, _ = wiki_scope(self.wiki)
        rows = [_row("wiki/concepts/a.md", [1, 0]), _row("wiki/concepts/index.md", [1, 0]),
                _row("wiki/software/spec.md", [1, 0])]
        kept = ws.in_scope_rows(rows, self.pkm, in_scope)
        self.assertEqual([r["file_path"] for r in kept], ["wiki/concepts/a.md"])


class TestDuplicates(unittest.TestCase):
    def test_same_folder_pair_found(self):
        rows = [_row("wiki/concepts/a.md", [1, 0]), _row("wiki/concepts/b.md", [1, 0.01])]
        self.assertEqual(ws.find_duplicate_pairs(rows), {("wiki/concepts/a.md", "wiki/concepts/b.md")})

    def test_cross_folder_pair_ignored(self):
        rows = [_row("wiki/concepts/a.md", [1, 0]), _row("wiki/summaries/a.md", [1, 0])]
        self.assertEqual(ws.find_duplicate_pairs(rows), set())

    def test_dissimilar_vectors_ignored(self):
        rows = [_row("wiki/concepts/a.md", [1, 0]), _row("wiki/concepts/b.md", [0, 1])]
        self.assertEqual(ws.find_duplicate_pairs(rows), set())

    def test_low_word_overlap_ignored(self):
        rows = [_row("wiki/concepts/a.md", [1, 0], text="alpha beta gamma delta", tags=""),
                _row("wiki/concepts/b.md", [1, 0], text="epsilon zeta eta theta", tags="")]
        self.assertEqual(ws.find_duplicate_pairs(rows), set())

    def test_chunks_averaged_per_file(self):
        rows = [_row("wiki/concepts/a.md", [1, 0]), _row("wiki/concepts/a.md", [0, 1]),
                _row("wiki/concepts/b.md", [1, 1])]
        self.assertEqual(len(ws.find_duplicate_pairs(rows)), 1)


class TestStaleStubs(unittest.TestCase):
    def test_old_stub_found(self):
        rows = [_row("wiki/concepts/s.md", [1], tags='["stub"]', updated_at="2026-01-01T00:00:00Z")]
        self.assertEqual(ws.find_stale_stubs(rows, NOW), {"wiki/concepts/s.md"})

    def test_recent_stub_and_old_non_stub_ignored(self):
        rows = [_row("wiki/concepts/s.md", [1], tags='["stub"]', updated_at="2026-09-01T00:00:00"),
                _row("wiki/concepts/n.md", [1], tags='["concept"]', updated_at="2025-01-01T00:00:00")]
        self.assertEqual(ws.find_stale_stubs(rows, NOW), set())

    def test_non_json_tags(self):
        rows = [_row("wiki/concepts/s.md", [1], tags="#stub", updated_at="2025-01-01T00:00:00")]
        self.assertEqual(ws.find_stale_stubs(rows, NOW), {"wiki/concepts/s.md"})


class TestDismissals(ScannerMixin, unittest.TestCase):
    def test_new_dismissal_stamped(self):
        a, b = self.note("wiki/concepts/a.md"), self.note("wiki/concepts/b.md")
        kept = ws.refresh_dismissed(self.pkm, [{"type": ws.DUPLICATE, "files": [a, b]}])
        self.assertEqual(kept[0]["fingerprint"], ws.fingerprint(self.pkm, [a, b]))

    def test_changed_file_drops_dismissal(self):
        a, b = self.note("wiki/concepts/a.md"), self.note("wiki/concepts/b.md")
        stamped = ws.refresh_dismissed(self.pkm, [{"type": ws.DUPLICATE, "files": [a, b]}])
        self.note(a, "# Edited")
        self.assertEqual(ws.refresh_dismissed(self.pkm, stamped), [])

    def test_deleted_file_drops_dismissal(self):
        a = self.note("wiki/concepts/a.md")
        self.assertEqual(ws.refresh_dismissed(self.pkm, [{"files": [a, "wiki/concepts/gone.md"]}]), [])

    def test_malformed_entries_dropped(self):
        self.assertEqual(ws.refresh_dismissed(self.pkm, ["x", {"files": "a.md"}]), [])

    def test_queue_skips_dismissed_in_any_order(self):
        pairs = {("wiki/c/a.md", "wiki/c/b.md"), ("wiki/c/a.md", "wiki/c/z.md")}
        queue = ws.build_queue(set(), pairs, [{"type": ws.DUPLICATE, "files": ["wiki/c/b.md", "wiki/c/a.md"]}])
        self.assertEqual(queue, [{"type": ws.DUPLICATE, "files": ["wiki/c/a.md", "wiki/c/z.md"]}])


class TestRunScanner(ScannerMixin, unittest.TestCase):
    def test_writes_review_queue(self):
        self.folder("concepts")
        a, b = self.note("wiki/concepts/a.md"), self.note("wiki/concepts/b.md")
        out = self.run_with([_row(a, [1, 0]), _row(b, [1, 0])])
        self.assertEqual(out, {"review_queue": [{"type": ws.DUPLICATE, "files": [a, b]}]})

    def test_dismissed_pair_stays_out_until_edited(self):
        self.folder("concepts")
        a, b = self.note("wiki/concepts/a.md"), self.note("wiki/concepts/b.md")
        rows = [_row(a, [1, 0]), _row(b, [1, 0])]
        os.makedirs(self.wiki, exist_ok=True)
        ws.write_json(os.path.join(self.pkm, ws.STATE_FILE),
                      {"dismissed": [{"type": ws.DUPLICATE, "files": [a, b], "date": "2026-09-20"}]})
        self.assertEqual(self.run_with(rows), {"review_queue": []})
        self.assertIn("fingerprint", self.state()["dismissed"][0])
        self.note(b, "# Edited")
        self.assertEqual(len(self.run_with(rows)["review_queue"]), 1)
        self.assertEqual(self.state()["dismissed"], [])

    def test_unindexed_folder_reported_once(self):
        self.folder("concepts")
        self.folder("raw", indexed=False)
        with patch("builtins.print") as printed:
            self.run_with([])
        self.assertTrue(any("raw/" in str(c) for c in printed.call_args_list))
        self.assertEqual(self.state()["unindexed_folders"], ["raw"])
        with patch("builtins.print") as printed:
            self.run_with([])
        self.assertFalse(any("raw/" in str(c) for c in printed.call_args_list))

    def test_other_state_keys_preserved(self):
        self.folder("concepts")
        os.makedirs(self.wiki, exist_ok=True)
        ws.write_json(os.path.join(self.pkm, ws.STATE_FILE), {"note": "keep me"})
        self.run_with([])
        self.assertEqual(self.state()["note"], "keep me")

    def test_unreadable_state_treated_as_empty(self):
        self.folder("concepts")
        self.note(ws.STATE_FILE, "{not json")
        self.assertEqual(self.run_with([]), {"review_queue": []})

    def test_store_failure_writes_nothing(self):
        with patch.object(ws, "init_knowledge_db", side_effect=RuntimeError("boom")):
            ws.run_scanner(now=NOW)
        self.assertFalse(os.path.exists(os.path.join(self.pkm, ws.PENDING_FILE)))


if __name__ == "__main__":
    unittest.main()
