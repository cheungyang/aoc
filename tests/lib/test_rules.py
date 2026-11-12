"""Unit tests for lib/rules.py rules parser."""

from pathlib import Path
from lib.rules import get_threshold


def test_get_threshold_bold_format(tmp_path: Path) -> None:
    rules_file = tmp_path / "RULES.md"
    rules_file.write_text("**max_retries**: 3\n**timeout**: 45\n", encoding="utf-8")

    assert get_threshold("max_retries", str(rules_file)) == "3"
    assert get_threshold("timeout", str(rules_file)) == "45"


def test_get_threshold_plain_format(tmp_path: Path) -> None:
    rules_file = tmp_path / "RULES.md"
    rules_file.write_text("max_retries: 5\napi_host: example.com\n", encoding="utf-8")

    assert get_threshold("max_retries", str(rules_file)) == "5"
    assert get_threshold("api_host", str(rules_file)) == "example.com"


def test_get_threshold_missing_file() -> None:
    assert get_threshold("max_retries", "non_existent_rules.md") is None


def test_get_threshold_missing_key(tmp_path: Path) -> None:
    rules_file = tmp_path / "RULES.md"
    rules_file.write_text("**max_retries**: 3\n", encoding="utf-8")

    assert get_threshold("unknown_key", str(rules_file)) is None


def test_get_threshold_empty_key_or_file(tmp_path: Path) -> None:
    rules_file = tmp_path / "RULES.md"
    rules_file.write_text("", encoding="utf-8")

    assert get_threshold("", str(rules_file)) is None
    assert get_threshold("max_retries", str(rules_file)) is None
