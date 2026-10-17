"""Preconditions over files, and the filesystem helpers scripts reuse.

Relative paths resolve against the repository root, the same way the agents'
`filesystem` tool sees `pkm/...`.
"""
import glob
import json
import os
import re
from typing import Iterable, Optional

from core.scheduler.preconditions.base import Precondition, PreconditionError
from core.scheduler.spec import WorkDecision

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


# --------------------------------------------------------------------------
# Filesystem helpers. Public because scripts reuse them in their own has_work().
# --------------------------------------------------------------------------

def resolve_path(path: str, root: str = PROJECT_ROOT) -> str:
    expanded = os.path.expanduser(str(path))
    return expanded if os.path.isabs(expanded) else os.path.join(root, expanded)


def _is_hidden(name: str) -> bool:
    return name.startswith(".")


def _iter_matches(patterns: Iterable[str], root: str = PROJECT_ROOT):
    for pattern in patterns:
        for match in sorted(glob.glob(resolve_path(pattern, root), recursive=True)):
            yield match


def _walk_entries(path: str):
    """Yields (path, is_dir) for `path` and, if a directory, everything under it.

    Directories are yielded too: deleting a file changes its parent's mtime and
    nothing else, and a deletion is a change a sync has to see.
    """
    if os.path.isdir(path):
        yield path, True
        for current, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not _is_hidden(d)]
            for d in dirs:
                yield os.path.join(current, d), True
            for f in files:
                if not _is_hidden(f):
                    yield os.path.join(current, f), False
    elif os.path.exists(path):
        yield path, False


def first_changed_path(
    patterns: Iterable[str],
    since: Optional[float],
    root: str = PROJECT_ROOT,
    suffixes: Optional[Iterable[str]] = None,
) -> Optional[str]:
    """The first matching file or directory modified after `since`, else None.

    `suffixes` restricts which *files* count (directories always do, since a
    deletion shows up only as its parent's mtime).
    """
    wanted = tuple(suffixes) if suffixes else None
    for match in _iter_matches(patterns, root):
        for path, is_dir in _walk_entries(match):
            if wanted and not is_dir and not path.endswith(wanted):
                continue
            try:
                if os.path.getmtime(path) > since:
                    return path
            except OSError:
                continue
    return None


def first_existing_file(patterns: Iterable[str], root: str = PROJECT_ROOT) -> Optional[str]:
    """The first non-hidden regular file matching `patterns`, else None."""
    for match in _iter_matches(patterns, root):
        for path, is_dir in _walk_entries(match):
            if not is_dir and not _is_hidden(os.path.basename(path)):
                return path
    return None


def _rel(path: str, root: str = PROJECT_ROOT) -> str:
    try:
        return os.path.relpath(path, root)
    except ValueError:
        return path


class FilesChangedSince(Precondition):
    """A matching file or directory changed since the last successful run."""
    type_name = "files_changed_since"

    def validate(self):
        self._check_keys(["paths"])
        self.paths = self._require_str_list("paths")

    def evaluate(self, ctx):
        if ctx.last_success_at is None:
            return WorkDecision(True, "no previous successful run")
        changed = first_changed_path(self.paths, ctx.last_success_at)
        if changed:
            return WorkDecision(True, f"{_rel(changed)} changed since last success")
        return WorkDecision(False, f"nothing under {', '.join(self.paths)} changed since last success")


class FilesExist(Precondition):
    """At least one (non-hidden) file matches — e.g. an inbox that is not empty."""
    type_name = "files_exist"

    def validate(self):
        self._check_keys(["paths"])
        self.paths = self._require_str_list("paths")

    def evaluate(self, ctx):
        found = first_existing_file(self.paths)
        if found:
            return WorkDecision(True, f"{_rel(found)} exists")
        return WorkDecision(False, f"no files match {', '.join(self.paths)}")


class FileMatches(Precondition):
    """A file exists and contains a regex match — e.g. a queued request block."""
    type_name = "file_matches"

    def validate(self):
        self._check_keys(["path", "pattern"])
        self.path = self._require_str("path")
        pattern = self._require_str("pattern")
        try:
            self.regex = re.compile(pattern)
        except re.error as e:
            raise PreconditionError(f"'file_matches' pattern does not compile: {e}")

    def evaluate(self, ctx):
        full = resolve_path(self.path)
        if not os.path.isfile(full):
            return WorkDecision(False, f"{self.path} does not exist")
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        if self.regex.search(content):
            return WorkDecision(True, f"{self.path} matches /{self.regex.pattern}/")
        return WorkDecision(False, f"{self.path} has no match for /{self.regex.pattern}/")


class JsonNonEmpty(Precondition):
    """A JSON file has at least one non-empty value under the listed keys."""
    type_name = "json_non_empty"

    def validate(self):
        self._check_keys(["path", "keys"])
        self.path = self._require_str("path")
        self.keys = self._require_str_list("keys")

    def evaluate(self, ctx):
        full = resolve_path(self.path)
        if not os.path.isfile(full):
            return WorkDecision(False, f"{self.path} does not exist")
        with open(full, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return WorkDecision(True, f"{self.path} is not an object; letting the agent judge")
        filled = [k for k in self.keys if data.get(k)]
        if filled:
            return WorkDecision(True, f"{self.path} has entries under {', '.join(filled)}")
        return WorkDecision(False, f"{self.path} is empty under {', '.join(self.keys)}")
