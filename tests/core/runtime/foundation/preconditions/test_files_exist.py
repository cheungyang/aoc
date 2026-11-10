"""Unit tests for preconditions/files_exist.py."""

from pathlib import Path
from preconditions.files_exist import evaluate


def test_files_exist_default_behavior_file_present(tmp_path: Path):
    sample_file = tmp_path / "sample.txt"
    sample_file.write_text("Hello World")

    config = {
        "type": "files_exist",
        "paths": [str(sample_file)],
    }
    assert evaluate(config) is True


def test_files_exist_default_behavior_file_missing(tmp_path: Path):
    missing_file = tmp_path / "missing.txt"

    config = {
        "type": "files_exist",
        "paths": [str(missing_file)],
    }
    assert evaluate(config) is False


def test_files_exist_inverted_file_present(tmp_path: Path):
    sample_file = tmp_path / "sample.txt"
    sample_file.write_text("Hello World")

    config = {
        "type": "files_exist",
        "paths": [str(sample_file)],
        "invert": True,
    }
    assert evaluate(config) is False


def test_files_exist_inverted_file_missing(tmp_path: Path):
    missing_file = tmp_path / "missing.txt"

    config = {
        "type": "files_exist",
        "paths": [str(missing_file)],
        "invert": True,
    }
    assert evaluate(config) is True


def test_files_exist_direct_args_inverted(tmp_path: Path):
    data_file = tmp_path / "data.txt"
    data_file.write_text("Test contents")

    missing_file = tmp_path / "nonexistent.txt"

    assert evaluate(str(data_file), invert=True) is False
    assert evaluate(str(missing_file), invert=True) is True
