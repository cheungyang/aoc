"""Memory v2 entries: one fact per line, parsed strictly, rendered compactly.

A stored line carries everything code needs to route, rank and expire it:

    - [food] Avoiding dairy; oat milk OK. (src: meal-planner · first 2026-09-02 · seen 2026-09-18 · x3)
    - [family] Parents visiting. (src: main · first 2026-09-10 · seen 2026-09-10 · x1 · until 2026-12-05)

The prompt sees only `- Avoiding dairy; oat milk OK.`; budgets count that
rendered form. A line that doesn't parse is kept verbatim and reported, so a
hand edit in Obsidian is never silently dropped.
"""
import datetime
import re
from dataclasses import dataclass, field, replace
from typing import List, Optional, Tuple

# Scope tags. `profile` is the shared Profile; `private` and `feedback` are the
# agent's own MEMORY.md and FEEDBACK.md. Every other tag is a topic from TAGS.md.
PROFILE = "profile"
PRIVATE = "private"
FEEDBACK = "feedback"
SCOPE_TAGS = (PROFILE, PRIVATE, FEEDBACK)

SCOPE_DESCRIPTIONS = {
    PROFILE: "Stable facts about the user that every agent should know: identity, family, "
             "location, timezone, health constraints, standing preferences. Shown to every agent.",
    PRIVATE: "Your own craft: precedents, what worked, why something failed. Only you see it.",
    FEEDBACK: "A rule the user told you to follow, or a correction of your behaviour. Only you see it.",
}

TAG_NAME = re.compile(r"^[a-z][a-z-]*$")
SEPARATOR = " · "

_LINE = re.compile(
    r"^- \[(?P<tag>[a-z][a-z-]*)\] (?P<text>.+?) \((?P<meta>src: [^()]*)\)\s*$"
)
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _date(value: str) -> Optional[datetime.date]:
    if not value or not _DATE.match(value):
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        return None


def clean_text(text: str) -> str:
    """One line, no parentheses at the end that could be mistaken for metadata."""
    text = " ".join((text or "").split())
    return text.replace("(src:", "(source:")


@dataclass(frozen=True)
class Entry:
    tag: str
    text: str
    src: str
    first: datetime.date
    seen: datetime.date
    count: int = 1
    until: Optional[datetime.date] = None

    def format(self) -> str:
        meta = [f"src: {self.src}", f"first {self.first.isoformat()}",
                f"seen {self.seen.isoformat()}", f"x{self.count}"]
        if self.until:
            meta.append(f"until {self.until.isoformat()}")
        return f"- [{self.tag}] {self.text} ({SEPARATOR.join(meta)})"

    def render(self) -> str:
        return f"- {self.text}"

    def confirmed(self, today: datetime.date) -> "Entry":
        return replace(self, seen=today, count=self.count + 1)

    def expired(self, today: datetime.date) -> bool:
        return self.until is not None and self.until < today

    def days_unseen(self, today: datetime.date) -> int:
        return (today - self.seen).days


def new_entry(tag: str, text: str, src: str, today: datetime.date,
              until: Optional[datetime.date] = None) -> Entry:
    return Entry(tag=tag, text=clean_text(text), src=src, first=today, seen=today, until=until)


def parse_line(line: str) -> Optional[Entry]:
    match = _LINE.match(line.strip())
    if not match:
        return None
    fields = {"count": 1, "until": None, "first": None, "seen": None, "src": None}
    for part in match.group("meta").split(SEPARATOR.strip()):
        part = part.strip()
        if part.startswith("src:"):
            fields["src"] = part[4:].strip()
        elif part.startswith("first "):
            fields["first"] = _date(part[6:].strip())
        elif part.startswith("seen "):
            fields["seen"] = _date(part[5:].strip())
        elif part.startswith("until "):
            fields["until"] = _date(part[6:].strip())
            if fields["until"] is None:
                return None
        elif re.fullmatch(r"x\d+", part):
            fields["count"] = int(part[1:])
        else:
            return None
    if not fields["src"] or fields["first"] is None or fields["seen"] is None:
        return None
    return Entry(tag=match.group("tag"), text=match.group("text").strip(), **fields)


@dataclass
class MemoryFile:
    """Entries plus lines that didn't parse. Headings and blank lines are layout."""
    entries: List[Entry] = field(default_factory=list)
    unparsed: List[str] = field(default_factory=list)

    def rendered(self) -> str:
        return "\n".join([e.render() for e in self.entries] + self.unparsed)

    def size(self) -> int:
        return len(self.rendered())


def parse_file(text: str) -> MemoryFile:
    memory = MemoryFile()
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        entry = parse_line(line)
        if entry is None:
            memory.unparsed.append(line.strip())
        else:
            memory.entries.append(entry)
    return memory


def format_file(title: str, memory: MemoryFile) -> str:
    lines = [f"# {title}", ""]
    lines += [e.format() for e in memory.entries]
    lines += memory.unparsed
    return "\n".join(lines).rstrip() + "\n"


class TagsError(ValueError):
    """TAGS.md is malformed; the dream refuses to guess."""


def parse_tags(text: str) -> List[Tuple[str, str]]:
    """[(tag, description)] from TAGS.md: each `## name` and the paragraph under it."""
    tags: List[Tuple[str, List[str]]] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line.startswith("## "):
            tags.append((line[3:].strip(), []))
        elif line.startswith("#") or not line:
            continue
        elif tags:
            tags[-1][1].append(line)
    result = []
    seen = set()
    for name, body in tags:
        if not TAG_NAME.match(name):
            raise TagsError(f"TAGS.md: '{name}' is not a valid tag name (lowercase letters and '-')")
        if name in SCOPE_TAGS:
            raise TagsError(f"TAGS.md: '{name}' is reserved")
        if name in seen:
            raise TagsError(f"TAGS.md: duplicate tag '{name}'")
        description = " ".join(body).strip()
        if not description:
            raise TagsError(f"TAGS.md: tag '{name}' has no description")
        seen.add(name)
        result.append((name, description))
    return result


def normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", " ".join((text or "").lower().split()))
