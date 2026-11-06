"""Unit tests for preconditions/stalled_topics.py."""

import os
import time
from pathlib import Path
from preconditions.stalled_topics import evaluate


def test_stalled_and_fresh_active_topics(tmp_path: Path):
    stalled_file = tmp_path / "astronomy-basics.md"
    fresh_file = tmp_path / "quantum-computing.md"

    stalled_file.write_text("---\nstatus: active\n---\n# Astronomy Basics")
    fresh_file.write_text("---\nstatus: active\n---\n# Quantum Computing")

    now = time.time()
    eight_days_sec = 8 * 24 * 3600
    two_days_sec = 2 * 24 * 3600

    os.utime(stalled_file, (now - eight_days_sec, now - eight_days_sec))
    os.utime(fresh_file, (now - two_days_sec, now - two_days_sec))

    is_triggered, payload = evaluate(str(tmp_path))

    assert is_triggered is True
    assert payload == {"stalled_topics": ["astronomy-basics.md"]}


def test_only_fresh_active_topics(tmp_path: Path):
    file1 = tmp_path / "rust-lang.md"
    file2 = tmp_path / "baking-recipes.md"

    file1.write_text("---\nstatus: active\n---\n# Rust Language")
    file2.write_text("---\nstatus: active\n---\n# Baking Recipes")

    now = time.time()
    two_days_sec = 2 * 24 * 3600
    three_days_sec = 3 * 24 * 3600

    os.utime(file1, (now - two_days_sec, now - two_days_sec))
    os.utime(file2, (now - three_days_sec, now - three_days_sec))

    is_triggered, payload = evaluate(str(tmp_path))

    assert is_triggered is False
    assert payload == {"stalled_topics": []}


def test_stalled_inactive_topic_ignored(tmp_path: Path):
    inactive_file = tmp_path / "ancient-history.md"
    inactive_file.write_text("---\nstatus: inactive\n---\n# Ancient History")

    now = time.time()
    ten_days_sec = 10 * 24 * 3600
    os.utime(inactive_file, (now - ten_days_sec, now - ten_days_sec))

    is_triggered, payload = evaluate(str(tmp_path))

    assert is_triggered is False
    assert payload == {"stalled_topics": []}


def test_index_md_ignored(tmp_path: Path):
    index_file = tmp_path / "index.md"
    index_file.write_text("---\nstatus: active\n---\n# Topics Index")

    now = time.time()
    ten_days_sec = 10 * 24 * 3600
    os.utime(index_file, (now - ten_days_sec, now - ten_days_sec))

    is_triggered, payload = evaluate(str(tmp_path))

    assert is_triggered is False
    assert payload == {"stalled_topics": []}


def test_missing_directory(tmp_path: Path):
    nonexistent = tmp_path / "nonexistent_dir"

    is_triggered, payload = evaluate(str(nonexistent))

    assert is_triggered is False
    assert payload == {"stalled_topics": []}


def test_missing_or_invalid_frontmatter_ignored(tmp_path: Path):
    no_fm_file = tmp_path / "no-frontmatter.md"
    draft_file = tmp_path / "draft-topic.md"

    no_fm_file.write_text("# Title without frontmatter")
    draft_file.write_text("---\nstatus: draft\n---\n# Draft Topic")

    now = time.time()
    ten_days_sec = 10 * 24 * 3600
    os.utime(no_fm_file, (now - ten_days_sec, now - ten_days_sec))
    os.utime(draft_file, (now - ten_days_sec, now - ten_days_sec))

    is_triggered, payload = evaluate(str(tmp_path))

    assert is_triggered is False
    assert payload == {"stalled_topics": []}
