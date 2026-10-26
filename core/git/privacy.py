"""The privacy guard: nothing personal in what the repo makes public.

Two callers. tests/test_repo_privacy.py runs it over every file git would push
from this checkout. The coding graph runs it before anything reaches GitHub:
the push node over what a push would send, publish over the PR text an LLM
wrote. Outside the test, `check` raises NotesMissing when the notes aren't
there, because "couldn't look" must never read as "nothing found".

Two checks, neither with a list of the user's data to keep up:

1. Copied from the user's notes (needs the PKM). A run of 8 words, or 8
   Chinese characters, or a street address, that appears both in a personal
   note and in a pushed file is a hit. Personal notes are `vault/`, `wallet/`,
   `ticktick/`, `wiki/memory/` and `agents/`, less the settings files the code
   reads (RULES.md, TAGS.md). For runs, structure isn't content, so it doesn't
   count on either side: headings, table header rows and separators, code
   fences, frontmatter, code spans, URLs, paths, dotted names, HTML tags, runs
   that are mostly numbers, and any run that also appears in `templates/`
   (daily notes start from a template). A run stops at an emoji: emojis mark
   fields, like a task's 📅 date or a row's ❌ status. Addresses are read on
   every line, frontmatter and headings included.
2. Patterns (runs everywhere): emails, phone numbers, card digits, home folders,
   Discord-style ids and dynamic-DNS hosts. Placeholders are allowed: example and
   internal domains, 555 numbers, 0000 digits, one-letter home folders, and ids
   made of one repeated digit or of counting digits.
"""
import os
import re
import subprocess
from pathlib import Path
from typing import Iterable, Iterator, List, Sequence, Set, Tuple

SELF = "tests/test_repo_privacy.py"  # the guard's own tests: their examples are fake on purpose
MAX_LINES = 50
RUN_WORDS = 8
RUN_CJK = 8
MIN_LETTER_WORDS = RUN_WORDS // 2  # fewer, and the run is a row of numbers: plate sizes, prices
PERSONAL_DIRS = ("vault", "wallet", "ticktick", os.path.join("wiki", "memory"), "agents")
SETTINGS_FILES = ("RULES.md", "TAGS.md")  # thresholds and memory topics, seeded from the repo
TEMPLATES_DIR = "templates"
NOTE_SUFFIXES = (".md", ".txt", ".json", ".jsonl", ".csv")

Hit = Tuple[str, int, str]  # path, line, what matched

_WORD = re.compile(r"[a-z0-9]+")
_CJK_CHARS = r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
_CJK = re.compile(rf"[{_CJK_CHARS}]+")
_HEADING = re.compile(r"^\s*#{1,6}\s")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_CODE_SPAN = re.compile(r"`[^`]*`")
_URL = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+|\bwww\.\S+", re.IGNORECASE)
# A token holding a slash, backslash or underscore, or a dot between letters:
# paths, file names, identifiers, `module.attr`. CJK stays out of the token, so
# a Chinese sentence written against a date like 3/15 isn't dropped with it.
_PATH_LIKE = re.compile(rf"[^\s{_CJK_CHARS}]*(?:[/\\_]|[A-Za-z0-9]\.[A-Za-z])[^\s{_CJK_CHARS}]*")
_TAG = re.compile(r"</?[A-Za-z]+\s*/?>")
# Emoji, arrows and other pictographs.
_SYMBOL = re.compile(r"[\u2190-\u21ff\u2300-\u23ff\u25a0-\u27bf\u2b00-\u2bff\U0001f000-\U0001faff]")
# A street address: number, capitalized street name, street type (any case), e.g.
# "120 Maple Drive". The type is shortened so "Drive" and "Dr" match.
_STREET_TYPES = {"street": "st", "st": "st", "avenue": "ave", "ave": "ave", "road": "rd", "rd": "rd",
                 "boulevard": "blvd", "blvd": "blvd", "lane": "ln", "ln": "ln", "drive": "dr", "dr": "dr",
                 "court": "ct", "ct": "ct", "way": "way", "place": "pl", "pl": "pl", "terrace": "ter",
                 "ter": "ter", "circle": "cir", "cir": "cir", "parkway": "pkwy", "pkwy": "pkwy",
                 "highway": "hwy", "hwy": "hwy"}
_ADDRESS = re.compile(r"\b(\d{1,6})\s+((?:[A-Z0-9][\w'-]*\s+){1,4}?)((?i:" + "|".join(_STREET_TYPES) + r"))\b")


# -- Shared ------------------------------------------------------------------

def pushed_files(root: Path) -> List[str]:
    """What git would push: tracked files plus untracked ones it doesn't ignore."""
    out = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        check=True, capture_output=True,
    ).stdout
    return sorted({p for p in out.decode("utf-8", "surrogateescape").split("\0") if p})


def pushed_texts(root: Path, paths: Sequence[str]) -> Iterator[Tuple[str, str]]:
    """(path, text) for each pushed text file. Skips the pkm link, files that are
    gone and binaries; any other symlink is read as the link text git stores."""
    for path in paths:
        if path == "pkm" or path.startswith("pkm/"):
            continue
        full = root / path
        if full.is_symlink():
            yield path, os.readlink(full)
        elif full.is_file():
            data = full.read_bytes()
            if b"\0" not in data[:8000]:
                yield path, data.decode("utf-8", "replace")


def render(title: str, hits: Sequence[Hit], limit: int = MAX_LINES) -> str:
    head = f"{len(hits)} {title}" + (f"; the first {limit}" if len(hits) > limit else "")
    return "\n".join([head + ":"] + [f"{path}:{line}: {what}" for path, line, what in hits[:limit]])


# -- Check 1: copied from the user's notes ----------------------------------

def content_lines(text: str) -> Iterator[Tuple[int, str]]:
    """(line number, line) for the lines that carry content, not structure."""
    lines = text.splitlines()
    in_frontmatter = bool(lines) and lines[0].strip() == "---"
    for i, line in enumerate(lines):
        if in_frontmatter:
            if i > 0 and line.strip() == "---":
                in_frontmatter = False
            continue
        if _HEADING.match(line) or _TABLE_SEPARATOR.match(line) or _FENCE.match(line):
            continue
        if line.lstrip().startswith("|") and i + 1 < len(lines) and _TABLE_SEPARATOR.match(lines[i + 1]):
            continue  # a table's header row
        yield i + 1, line


def prose(line: str) -> str:
    """The line without code spans, URLs, path-like tokens and HTML tags. Those
    name the system, not the user's life, and notes and code share plenty of them."""
    for pattern in (_CODE_SPAN, _URL, _PATH_LIKE, _TAG):
        line = pattern.sub(" ", line)
    return line


def runs(line: str) -> Iterator[str]:
    """Every run of RUN_WORDS words, at least MIN_LETTER_WORDS of them with a
    letter, and of RUN_CJK Chinese characters in the line's prose, normalized.
    No run crosses an emoji."""
    for field in _SYMBOL.split(prose(line)):
        words = _WORD.findall(field.casefold())
        for i in range(len(words) - RUN_WORDS + 1):
            run = words[i:i + RUN_WORDS]
            if sum(not word.isdigit() for word in run) >= MIN_LETTER_WORDS:
                yield " ".join(run)
        for chunk in _CJK.findall(field):
            for i in range(len(chunk) - RUN_CJK + 1):
                yield chunk[i:i + RUN_CJK]


def addresses(line: str) -> Iterator[str]:
    """Each street address in the line, as `address: <number> <name> <type>`."""
    for m in _ADDRESS.finditer(line):
        yield f"address: {m.group(1)} {' '.join(m.group(2).casefold().split())} {_STREET_TYPES[m.group(3).casefold()]}"


def keys(text: str) -> Iterator[Tuple[int, List[str]]]:
    """(line number, what to compare) per line: its runs when the line carries
    content, and its addresses on any line, since a note's frontmatter or heading
    is where an address usually sits."""
    content = dict(content_lines(text))
    for number, line in enumerate(text.splitlines(), 1):
        found = list(runs(content[number])) if number in content else []
        found.extend(addresses(line))
        if found:
            yield number, found


def note_files(folder: Path) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            if name.endswith(NOTE_SUFFIXES) and name not in SETTINGS_FILES:
                yield Path(dirpath) / name


def runs_in(files: Iterator[Path]) -> Set[str]:
    found: Set[str] = set()
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for _, line_keys in keys(text):
            found.update(line_keys)
    return found


def personal_runs(pkm: Path) -> Set[str]:
    """The runs in the user's personal notes, less those that also appear in a template."""
    found = set()
    for folder in PERSONAL_DIRS:
        if (pkm / folder).is_dir():
            found |= runs_in(note_files(pkm / folder))
    if (pkm / TEMPLATES_DIR).is_dir():
        found -= runs_in(note_files(pkm / TEMPLATES_DIR))
    return found


def copied_lines(texts: Iterator[Tuple[str, str]], personal: Set[str]) -> List[Hit]:
    """Each pushed line holding a run or an address from the notes, once per line."""
    hits = []
    for path, text in texts:
        for number, line_keys in keys(text):
            match = next((key for key in line_keys if key in personal), None)
            if match:
                hits.append((path, number, f"\u201c{match}\u201d"))
    return hits


# -- Check 2: patterns ---------------------------------------------------------

_EMAIL = re.compile(r"(?<![\w.%+-])([A-Za-z0-9._%+-]+)@((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})\b")
_PLACEHOLDER_DOMAIN = re.compile(r"(^|\.)(example\.(com|org|net)|example|test|invalid|localhost|internal|local|"
                                 r"lan|users\.noreply\.github\.com)$", re.IGNORECASE)
_PHONE = re.compile(r"(?<![\w.])(?:\+?1[-. ])?\(?(\d{3})\)?[-. ](\d{3})[-. ]\d{4}(?![\w.])")
_CARD = re.compile(r"(?:ending in|ends in|last four|last 4)\s*:?\s*(\d{3,4})\b|\*{4}\s?(\d{4})\b", re.IGNORECASE)
# Only a path that starts there: not pkm/wallet/home/x, nor a #project/home/x tag.
_HOME = re.compile(r"(?<![\w/#.~-])(/Users/|/home/)([A-Za-z0-9._-]+)")
_PLACEHOLDER_HOME = {"appuser", "user", "username", "runner", "ubuntu", "me", "you", "name", "shared", "path", "to"}
_SNOWFLAKE = re.compile(r"(?<![\w.])(\d{17,20})(?![\w.])")
_DDNS = re.compile(r"\b[\w-]+(?:\.[\w-]+)*\.(?:synology\.me|duckdns\.org|no-ip\.(?:org|com|biz)|ddns\.net|"
                   r"dyndns\.org|myqnapcloud\.com|tplinkdns\.com)\b", re.IGNORECASE)


def _placeholder_id(digits: str) -> bool:
    """One repeated digit, or digits counting up from any digit, like 234567890123456789."""
    return len(set(digits)) == 1 or all((int(b) - int(a)) % 10 == 1 for a, b in zip(digits, digits[1:]))


def pattern_hits(texts: Iterator[Tuple[str, str]]) -> List[Hit]:
    hits = []
    for path, text in texts:
        for number, line in enumerate(text.splitlines(), 1):
            for m in _EMAIL.finditer(line):
                if not _PLACEHOLDER_DOMAIN.search(m.group(2)) and m.group(0).lower() != "git@github.com":
                    hits.append((path, number, f"email {m.group(0)}"))
            for m in _PHONE.finditer(line):
                if "555" not in (m.group(1), m.group(2)):
                    hits.append((path, number, f"phone number {m.group(0)}"))
            for m in _CARD.finditer(line):
                digits = m.group(1) or m.group(2)
                if set(digits) != {"0"} and digits not in ("1234", "123"):
                    hits.append((path, number, f"card digits {m.group(0)}"))
            for m in _HOME.finditer(line):
                if len(m.group(2)) > 1 and m.group(2).lower() not in _PLACEHOLDER_HOME:
                    hits.append((path, number, f"home folder {m.group(0)}"))
            for m in _SNOWFLAKE.finditer(line):
                if not _placeholder_id(m.group(1)):
                    hits.append((path, number, f"id {m.group(1)}"))
            for m in _DDNS.finditer(line):
                hits.append((path, number, f"dynamic-DNS host {m.group(0)}"))
    return hits


# -- For callers that are about to publish -------------------------------------

class NotesMissing(Exception):
    """No personal folder under the PKM, so check 1 can't run."""


def has_notes(pkm: Path) -> bool:
    return any((pkm / folder).is_dir() for folder in PERSONAL_DIRS)


def check(texts: Iterable[Tuple[str, str]], pkm: Path) -> List[Hit]:
    """Both checks over (name, text) pairs, ordered by name and line.

    Raises NotesMissing instead of skipping check 1, as the test does: a caller
    about to publish must refuse, not go ahead with half a check.
    """
    if not has_notes(pkm):
        raise NotesMissing(f"no personal notes under {pkm} ({', '.join(PERSONAL_DIRS)})")
    texts = list(texts)
    hits = copied_lines(iter(texts), personal_runs(pkm)) + pattern_hits(iter(texts))
    return sorted(hits, key=lambda hit: (hit[0], hit[1]))


_PATTERN_KINDS = (
    ("email ", "an email address"),
    ("phone number ", "a phone number"),
    ("card digits ", "card digits"),
    ("home folder ", "a home folder path"),
    ("id ", "a Discord-style id"),
    ("dynamic-DNS host ", "a dynamic-DNS host name"),
)


def describe(hit: Hit) -> str:
    """What kind of hit it is, without the matched value.

    For places the value must not reach. The coding graph's worker is one: its
    summary becomes a public PR body, so it gets told where and what, never the
    words themselves.
    """
    what = hit[2]
    if what.startswith("\u201c"):
        match = what.strip("\u201c\u201d")
        if match.startswith("address: "):
            return "a street address from the user's notes"
        if _CJK.search(match):
            return f"{RUN_CJK} Chinese characters in a row from the user's notes"
        return f"{RUN_WORDS} words in a row from the user's notes"
    for prefix, kind in _PATTERN_KINDS:
        if what.startswith(prefix):
            return kind
    return "something that looks personal"
