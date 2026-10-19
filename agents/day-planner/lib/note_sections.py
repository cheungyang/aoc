"""A note's sections by heading: find them and put lines under them, every other line as written.

A section is the lines under a heading, up to the next heading of any level, so
a `##` heading's own lines never take its `###` subsections with them. Lines in
the frontmatter and in fenced code blocks aren't headings. A heading is named
whatever its case, emoji, punctuation and spaces: "Next week's focus" finds
"### 🌱 Next week's focus".

A section is written only while it is empty or still the template's A, B, C
(`journal_notes.is_template_focus`). Other text there is refused unless the
caller says to replace it.
"""
import re
from dataclasses import dataclass, field
from typing import List, Sequence, Tuple

from agents.day_planner.lib.journal_notes import is_template_focus

_HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*$")
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")


class SectionError(ValueError):
    """The note can't take the sections as asked; nothing was changed. One line per problem."""


@dataclass
class Filled:
    """A section written."""
    heading: str  # as the note writes it
    lines: int  # lines written; 0 when the text given was empty
    replaced: List[str] = field(default_factory=list)  # the text it replaced, unless empty or the template's


def fill_sections(text: str, sections: Sequence[Tuple[str, str]], replace: bool = False) -> Tuple[str, List[Filled]]:
    """`text` with each (heading, lines) in place of what was under that heading, and what was written.

    Blank lines at either end of a section stay, and so does the spacing before
    the next heading. Raises SectionError, changing nothing, for a heading the
    note doesn't have or has twice, and for a section holding other text, unless
    `replace`.
    """
    lines = text.split("\n")
    heads = _headings(lines)
    problems, edits, filled, given = [], [], [], set()
    for wanted, body in sections:
        key = _key(wanted)
        if key in given:
            problems.append(f"'{wanted}' is given twice")
            continue
        given.add(key)
        matches = [n for n, (_, title) in enumerate(heads) if _key(title) == key]
        if len(matches) != 1:
            problems.append(f"'{wanted}' matches {len(matches)} headings" if matches else f"no heading '{wanted}'")
            continue
        n = matches[0]
        index, title = heads[n]
        end = heads[n + 1][0] if n + 1 < len(heads) else len(lines)
        content = [j for j in range(index + 1, end) if lines[j].strip()]
        first, last = (content[0], content[-1] + 1) if content else (index + 1, index + 1)
        old, new = lines[first:last], _block(body)
        written = bool(old) and not is_template_focus([line for line in old if line.strip()])
        if not new:
            filled.append(Filled(title, 0))
            continue
        if written and not replace:
            problems.append(f"'{title}' already has text:\n" + "\n".join(f"    {line}" for line in old))
            continue
        trail = lines[last:end] or ([""] if end < len(lines) else [])
        edits.append((first, end, new + trail))
        filled.append(Filled(title, len(new), old if written else []))
    if problems:
        if any(problem.startswith("no heading") for problem in problems):
            problems.append("the note's headings: " + "; ".join(title for _, title in heads))
        raise SectionError("\n".join(f"- {problem}" for problem in problems))
    for first, end, replacement in sorted(edits, reverse=True):
        lines[first:end] = replacement
    return "\n".join(lines), filled


def _headings(lines: List[str]) -> List[Tuple[int, str]]:
    """(index, title) of each heading line, past the frontmatter and outside fenced code blocks."""
    start = 0
    if lines[0].strip() == "---":
        start = next((j + 1 for j in range(1, len(lines)) if lines[j].strip() == "---"), 0)
    found, fence = [], ""
    for index in range(start, len(lines)):
        mark = _FENCE.match(lines[index])
        if mark and (not fence or mark.group(1).startswith(fence)):
            fence = "" if fence else mark.group(1)
            continue
        heading = None if fence else _HEADING.match(lines[index])
        if heading:
            found.append((index, heading.group(1)))
    return found


def _key(title: str) -> str:
    """A heading's letters and digits, lowercased: what names it."""
    return re.sub(r"[\W_]+", "", title.casefold())


def _block(text: str) -> List[str]:
    """`text`'s lines without trailing spaces, and without blank lines at either end."""
    lines = [line.rstrip() for line in (text or "").split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return lines
