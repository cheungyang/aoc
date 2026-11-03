import os
import time
import pytest
from preconditions.stalled_topics import evaluate


def test_stalled_and_fresh_topics(tmp_path):
    topics_dir = tmp_path / "topics"
    topics_dir.mkdir()

    stalled_file = topics_dir / "quantum_computing.md"
    fresh_file = topics_dir / "gardening_basics.md"

    stalled_file.write_text("# Quantum Computing\n")
    fresh_file.write_text("# Gardening Basics\n")

    now = time.time()
    eight_days_ago = now - (8 * 24 * 3600)
    two_days_ago = now - (2 * 24 * 3600)

    os.utime(stalled_file, (eight_days_ago, eight_days_ago))
    os.utime(fresh_file, (two_days_ago, two_days_ago))

    has_stalled, payload = evaluate(str(topics_dir))

    assert has_stalled is True
    assert payload == {"stalled_topics": ["quantum_computing.md"]}


def test_only_fresh_topics(tmp_path):
    topics_dir = tmp_path / "topics"
    topics_dir.mkdir()

    fresh_file1 = topics_dir / "origami.md"
    fresh_file2 = topics_dir / "astronomy.md"

    fresh_file1.write_text("# Origami\n")
    fresh_file2.write_text("# Astronomy\n")

    now = time.time()
    two_days_ago = now - (2 * 24 * 3600)
    os.utime(fresh_file1, (two_days_ago, two_days_ago))
    os.utime(fresh_file2, (two_days_ago, two_days_ago))

    has_stalled, payload = evaluate(str(topics_dir))

    assert has_stalled is False
    assert payload == {"stalled_topics": []}


def test_only_stalled_index_md(tmp_path):
    topics_dir = tmp_path / "topics"
    topics_dir.mkdir()

    index_file = topics_dir / "index.md"
    index_file.write_text("# Index\n")

    now = time.time()
    ten_days_ago = now - (10 * 24 * 3600)
    os.utime(index_file, (ten_days_ago, ten_days_ago))

    has_stalled, payload = evaluate(str(topics_dir))

    assert has_stalled is False
    assert payload == {"stalled_topics": []}


def test_missing_directory(tmp_path):
    non_existent_dir = tmp_path / "non_existent_folder"

    has_stalled, payload = evaluate(str(non_existent_dir))

    assert has_stalled is False
    assert payload == {"stalled_topics": []}
