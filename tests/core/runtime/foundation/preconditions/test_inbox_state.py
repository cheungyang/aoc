"""Unit tests for preconditions/inbox_state.py."""

import os
import time
from pathlib import Path
from preconditions.inbox_state import evaluate


def test_evaluate_multiple_items(tmp_path: Path):
    file1 = tmp_path / "alpha_notes.md"
    file2 = tmp_path / "beta_notes.md"
    file1.write_text("Test content for file 1")
    file2.write_text("Test content for file 2")

    is_triggered, payload = evaluate(str(tmp_path))

    assert is_triggered is True
    assert payload["status"] == "multiple_items"
    assert sorted(payload["items"]) == ["alpha_notes.md", "beta_notes.md"]


def test_evaluate_new_item(tmp_path: Path):
    file1 = tmp_path / "quantum_project.md"
    file1.write_text("Recent note created 1 hour ago")

    now = time.time()
    os.utime(file1, (now - 3600, now - 3600))  # 1 hour ago

    is_triggered, payload = evaluate(str(tmp_path))

    assert is_triggered is True
    assert payload["status"] == "new_item"
    assert payload["items"] == ["quantum_project.md"]


def test_evaluate_stale_3_days(tmp_path: Path):
    file1 = tmp_path / "archived_ideas.md"
    file1.write_text("Old note created 4 days ago")

    now = time.time()
    four_days_sec = 4 * 24 * 3600
    os.utime(file1, (now - four_days_sec, now - four_days_sec))  # 4 days ago

    is_triggered, payload = evaluate(str(tmp_path))

    assert is_triggered is True
    assert payload["status"] == "stale_3_days"
    assert payload["items"] == ["archived_ideas.md"]


def test_evaluate_pending_stale(tmp_path: Path):
    file1 = tmp_path / "midway_report.md"
    file1.write_text("Note modified 48 hours ago")

    now = time.time()
    forty_eight_hours_sec = 48 * 3600
    os.utime(file1, (now - forty_eight_hours_sec, now - forty_eight_hours_sec))

    is_triggered, payload = evaluate(str(tmp_path))

    assert is_triggered is False
    assert payload["status"] == "pending_stale"
    assert payload["items"] == ["midway_report.md"]


def test_evaluate_empty_directory(tmp_path: Path):
    is_triggered, payload = evaluate(str(tmp_path))

    assert is_triggered is False
    assert payload == {"status": "empty", "items": []}


def test_evaluate_nonexistent_directory(tmp_path: Path):
    missing_dir = tmp_path / "nonexistent_folder"

    is_triggered, payload = evaluate(str(missing_dir))

    assert is_triggered is False
    assert payload == {"status": "empty", "items": []}
