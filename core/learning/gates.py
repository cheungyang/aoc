"""The static checks on a proposal: code, before any model looks at it (proposal §5.3).

Each check reads the target file's text before and after the proposal's edits
and says pass or fail, with the reason in plain words. The reasons go back to the
analyst, which can revise and hand the proposal in again, and into the proposals
table. Only a proposal whose checks all pass is `checked` and goes on to review.

    where       the file is prompt text a proposal may change (boundaries.proposable)
    base        each edit's find occurs exactly once in the file as it is now
    size        at most MAX_CHANGED_LINES lines change, and at most MAX_REMOVED_SHARE of
                the file goes; numbers, paths and `names` it removes survive or are named
    budget      the file stays within its budget; text without one grows by GROWTH_CAP at
                most, and the templates in every agent's prompt don't grow at all
    protected   no line carrying a protected behaviour is removed or reworded, and no
                new line tells an agent to skip one (protected.py)
    capability  no new tool, path, channel, URL, email, phone number, mention or grant:
                wiring is a human's to change
    personal    outside pkm/, nothing personal: the privacy guard, and no name, number or
                RUN_WORDS-word run from the cases it cites (core/git/privacy.py)
    render      the file still loads: a template keeps its tags, a skill its frontmatter
                and sections, RULES.md its keys, FEEDBACK.md its entry lines
    lint        agent_lint, run over a copy of the repo with the change, finds nothing new
                for the agents the file belongs to

This module is the evaluator's (boundaries.EVALUATOR): the analyst never reads it.
"""
import difflib
import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from core.learning import boundaries, protected

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# Limits. Safety, not preference, so they live in code, not in a RULES.md.
MAX_CHANGED_LINES = 30        # a proposal is small (proposal §5.1)
MAX_REMOVED_SHARE = 0.25      # a bigger cut is staged as several proposals (the ACE lesson, §10)
GROWTH_CAP = 300              # characters text without a budget may grow by
ALWAYS_LOADED = "core/prompts/templates/system/"  # in every agent's prompt: may not grow
FEEDBACK_BUDGET = 2000        # core/knowledge/memory/store.py: BUDGET_PRIVATE
RUN_WORDS = 5                 # the personal check: this many words in a row from the cases
NAME_MIN = 3                  # the personal check: capitalised words at least this long
PHONE_DIGITS = 9              # the capability check: a phone number has at least this many digits

ORDER = ("where", "base", "size", "budget", "protected", "capability", "personal", "render", "lint")

_URL = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+|\bwww\.\S+", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?<![\w])\+?\d[\d ().-]{7,}\d(?![\w])")
_CHANNEL = re.compile(r"(?<![\w&#/])#[a-z0-9][a-z0-9_-]+")
_MENTION = re.compile(r"<@!?\d+>|<#\d+>|(?<![\w.`/])@[a-z][a-z0-9_-]+", re.I)
_ROOTS = r"(?:~/pkm|pkm|agents|skills|tools|tests|graphs|scripts|core|workspaces)/"
_PATH = re.compile(r"(?<![\w./~-])" + _ROOTS + r"[^\s`'\"()\[\]{},;|]*")
_TICKED = re.compile(r"`([^`\n]+)`")
_NUMBER = re.compile(r"(?<![\w.])\$?\d[\d,.:/-]*\d(?![\w])")
_WORD = re.compile(r"[a-z0-9']+")
_NAME = re.compile(r"(?<![\w'])[A-Z][a-z]{%d,}" % (NAME_MIN - 1))
_TAG = re.compile(r"\{\{\s*[#/^]?\s*[\w.]+\s*\}\}")
_RULE_LINE = re.compile(r"^- ([a-z][a-z0-9_]*):")
_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_SECTION = re.compile(r"^## (Core|Reference)\s*$", re.MULTILINE)


@dataclass
class Check:
    gate: str
    ok: bool
    detail: str = ""

    def row(self) -> Dict[str, Any]:
        return {"gate": self.gate, "ok": self.ok, "detail": self.detail}


@dataclass
class Draft:
    """What the checks look at: the file, its text, the edits, and the cases they cite."""
    path: str                       # repo-relative; pkm/agents/<id>/... for the vault
    before: str                     # the file's text now
    edits: List[Dict[str, str]]     # [{find, replace}]
    dropped: List[str] = field(default_factory=list)
    cases: str = ""                 # the cited cards' evidence and episodes (digests.as_text)
    root: str = REPO
    pkm: Optional[str] = None       # the vault; the privacy guard reads its notes
    after: str = ""                 # set by `apply` once the edits apply


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def apply(text: str, edits: Sequence[Dict[str, str]]) -> Tuple[str, List[str]]:
    """`text` with each edit applied in turn, and what kept an edit from applying."""
    problems = []
    for n, edit in enumerate(edits, 1):
        find, replace = edit.get("find", ""), edit.get("replace", "")
        if not find:
            problems.append(f"edit {n} has an empty find")
            continue
        count = text.count(find)
        if count != 1:
            hint = "copy it exactly from the file" if count == 0 else "add text around it so it's unique"
            problems.append(f"edit {n}'s find occurs {count} times, not once: {hint}")
            continue
        text = text.replace(find, replace, 1)
    return text, problems


# -- helpers ------------------------------------------------------------------

def _lines(text: str) -> List[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def removed_lines(before: str, after: str) -> List[str]:
    """Lines of `before` that `after` no longer has, word for word."""
    kept = set(_lines(after))
    return [line for line in _lines(before) if line not in kept]


def added_lines(before: str, after: str) -> List[str]:
    """Lines of `after` that `before` didn't have, word for word."""
    return removed_lines(after, before)


def changed_lines(before: str, after: str) -> int:
    diff = difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0)
    return sum(1 for line in diff if line[:1] in "+-" and not line.startswith(("+++", "---")))


def removed_chars(before: str, after: str) -> int:
    matcher = difflib.SequenceMatcher(None, before, after, autojunk=False)
    return sum(i2 - i1 for op, i1, i2, _, _ in matcher.get_opcodes() if op in ("delete", "replace"))


def _clip(text: str, limit: int = 80) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _runs(text: str, n: int = RUN_WORDS) -> Set[Tuple[str, ...]]:
    words = _WORD.findall(text.lower())
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


def _names(text: str) -> Set[str]:
    """Capitalised words that don't start a sentence or a line: likely names."""
    names = set()
    for line in text.splitlines():
        for match in _NAME.finditer(line):
            head = line[:match.start()].rstrip()
            if not head or head[-1] in ".!?:-*#>|•" or head.endswith(("**", "`")):
                continue
            names.add(match.group(0))
    return names


def _agent_names(root: str) -> Set[str]:
    """Every agent's name (Aki, Kaizen...): they're the team's, not personal."""
    names = set()
    agents_dir = os.path.join(root, "agents")
    for agent in os.listdir(agents_dir) if os.path.isdir(agents_dir) else []:
        try:
            with open(os.path.join(agents_dir, agent, "agent.json"), encoding="utf-8") as f:
                name = json.load(f).get("name")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(name, str):
            names.update(name.split())
    return names


def _tool_names(root: str) -> Set[str]:
    """Every tool module's name: the shared tools/ and each agent's tools/."""
    names = set()
    folders = [os.path.join(root, "tools")]
    agents_dir = os.path.join(root, "agents")
    if os.path.isdir(agents_dir):
        folders += [os.path.join(agents_dir, a, "tools") for a in os.listdir(agents_dir)]
    for folder in folders:
        if os.path.isdir(folder):
            names |= {f[:-3] for f in os.listdir(folder) if f.endswith(".py") and not f.startswith("_")}
    return names


# -- the checks -----------------------------------------------------------------

def check_where(draft: Draft) -> Check:
    reason = boundaries.why_not_proposable(draft.path)
    return Check("where", not reason, reason or "prompt text a proposal may change")


def check_base(draft: Draft) -> Check:
    after, problems = apply(draft.before, draft.edits)
    if problems:
        return Check("base", False, "; ".join(problems))
    if after == draft.before:
        return Check("base", False, "the edits change nothing")
    draft.after = after
    return Check("base", True, f"{len(draft.edits)} edit(s) apply to the file as it is now")


def check_size(draft: Draft) -> Check:
    problems = []
    changed = changed_lines(draft.before, draft.after)
    if changed > MAX_CHANGED_LINES:
        problems.append(f"{changed} lines change, more than {MAX_CHANGED_LINES}: split it")
    cut = removed_chars(draft.before, draft.after)
    if draft.before and cut > MAX_REMOVED_SHARE * len(draft.before):
        problems.append(f"it removes {cut / len(draft.before):.0%} of the file, more than "
                        f"{MAX_REMOVED_SHARE:.0%}: stage the cut as several proposals")
    gone_text = "\n".join(removed_lines(draft.before, draft.after))
    keepers = set(_NUMBER.findall(gone_text)) | set(_PATH.findall(gone_text)) | set(_TICKED.findall(gone_text))
    named = " ".join(draft.dropped)
    lost = sorted(k for k in keepers if k not in draft.after and k not in named)
    if lost:
        problems.append("it drops " + ", ".join(f"`{_clip(k, 40)}`" for k in lost[:6])
                        + ": keep each elsewhere in the file, or name it in `dropped`")
    return Check("size", not problems, "; ".join(problems) or f"{changed} line(s) change")


def check_budget(draft: Draft) -> Check:
    path, old, new = boundaries.repo_path(draft.path), len(draft.before), len(draft.after)
    delta = new - old
    name = os.path.basename(path)
    if path.startswith("agents/"):
        from agents.agent_designer.lib.lint import PERSONA_LIMITS
        if name not in PERSONA_LIMITS:
            return Check("budget", False, f"{name} has no persona budget")
        target, ceiling = PERSONA_LIMITS[name]
        if new > ceiling:
            return Check("budget", False, f"{name} would be {new:,} characters, over its {ceiling:,} ceiling")
        if new > target and delta > 0:
            return Check("budget", False, f"{name} is over its {target:,}-character target: "
                                          f"an addition must delete at least as much ({delta:+,})")
        return Check("budget", True, f"{name} {old:,} → {new:,} characters")
    if name == "FEEDBACK.md" and delta > 0 and new > FEEDBACK_BUDGET:
        return Check("budget", False, f"FEEDBACK.md would be {new:,} characters, over its {FEEDBACK_BUDGET:,} budget")
    if path.startswith(ALWAYS_LOADED) and delta > 0:
        return Check("budget", False, f"{path} is in every agent's prompt on every turn: it may not grow ({delta:+,})")
    if name != "FEEDBACK.md" and delta > GROWTH_CAP:
        return Check("budget", False, f"it grows the file by {delta:,} characters, more than {GROWTH_CAP}: "
                                      "delete as much elsewhere, or find a cheaper home")
    return Check("budget", True, f"{delta:+,} characters")


def check_protected(draft: Draft) -> Check:
    problems = []
    for line in removed_lines(draft.before, draft.after):
        for behaviour in protected.marked(line):
            problems.append(f"it removes or rewords a line about {behaviour.name}: \"{_clip(line)}\"")
    for line in added_lines(draft.before, draft.after):
        for behaviour in protected.skipped(line):
            problems.append(f"a new line skips {behaviour.name}: \"{_clip(line)}\"")
    return Check("protected", not problems, "; ".join(problems[:4]) or "no protected behaviour touched")


def check_capability(draft: Draft, tools: Optional[Set[str]] = None) -> Check:
    tools = _tool_names(draft.root) if tools is None else tools
    tool_word = re.compile(r"`(%s)`|\b(%s)\(|\b(%s) tool\b" % ((("|".join(map(re.escape, sorted(tools)))) or "(?!)",) * 3))

    def found(text: str) -> Dict[str, Set[str]]:
        return {
            "URL": set(_URL.findall(text)), "email": set(_EMAIL.findall(text)),
            "phone number": {p for p in _PHONE.findall(text) if sum(c.isdigit() for c in p) >= PHONE_DIGITS},
            "channel": set(_CHANNEL.findall(text)), "mention or grant": set(_MENTION.findall(text)),
            "path": set(_PATH.findall(text)),
            "tool": {next(g for g in m.groups() if g) for m in tool_word.finditer(text)},
        }

    old, new = found(draft.before), found(draft.after)
    problems = [f"a new {kind}: {', '.join(_clip(v, 50) for v in sorted(new[kind] - old[kind])[:3])}"
                for kind in new if new[kind] - old[kind]]
    return Check("capability", not problems,
                 ("; ".join(problems) + " — wiring is a human's to change") if problems else "no new wiring")


def check_personal(draft: Draft, notes_check: Optional[Callable[[str, str], List[str]]] = None,
                   agent_names: Optional[Set[str]] = None) -> Check:
    if boundaries.in_vault(draft.path):
        return Check("personal", True, "the vault: personal detail belongs here")
    added = "\n".join(added_lines(draft.before, draft.after))
    problems = []
    try:
        problems += notes_check(draft.path, added) if notes_check else _notes_check(draft.path, added, draft.pkm)
    except Exception as e:  # "couldn't look" must never read as "nothing found"
        return Check("personal", False, f"the privacy guard couldn't run: {type(e).__name__}: {e}")
    if draft.cases:
        runs = (_runs(added) & _runs(draft.cases)) - _runs(draft.before)
        if runs:
            problems.append(f"{len(runs)} run(s) of {RUN_WORDS} words from the cases it cites")
        numbers = (set(_NUMBER.findall(added)) & set(_NUMBER.findall(draft.cases))) - set(_NUMBER.findall(draft.before))
        if numbers:
            problems.append(f"{len(numbers)} number(s) from the cases it cites")
        known = _agent_names(draft.root) if agent_names is None else agent_names
        names = (_names(added) & set(re.findall(r"[A-Z][a-z]+", draft.cases))) - set(
            re.findall(r"[A-Z][a-z]+", draft.before)) - known
        if names:
            problems.append(f"{len(names)} name(s) from the cases it cites")
    return Check("personal", not problems,
                 ("; ".join(problems) + ": the repo is public, so a lesson that needs it goes to pkm/")
                 if problems else "nothing personal found")


def _default_pkm() -> str:
    from core.tooling import pkm_path
    return str(pkm_path())


def _notes_check(path: str, added: str, pkm: Optional[str] = None) -> List[str]:
    """The privacy guard over the added text: what it found, by kind, never the words."""
    from core.git.privacy import check, describe
    hits = check([(path, added)], Path(pkm or _default_pkm()))
    return sorted({describe(hit) for hit in hits})


def check_render(draft: Draft) -> Check:
    path, before, after = boundaries.repo_path(draft.path), draft.before, draft.after
    name = os.path.basename(path)
    problems = []
    if not after.strip():
        problems.append("the file would be empty")
    if path.startswith(boundaries.TEMPLATES):
        if sorted(_TAG.findall(before)) != sorted(_TAG.findall(after)):
            problems.append("the template's {{tags}} change, so it would no longer render as the code fills it")
    elif name == "SKILL.md":
        old_front, new_front = _FRONTMATTER.match(before), _FRONTMATTER.match(after)
        if old_front and (not new_front or _keys(old_front.group(1)) != _keys(new_front.group(1))):
            problems.append("the skill's frontmatter changes")
        if sorted(_SECTION.findall(before)) != sorted(_SECTION.findall(after)):
            problems.append("the skill's `## Core` / `## Reference` sections change, which the loader splits on")
    elif name == "RULES.md":
        old_keys = [m.group(1) for m in map(_RULE_LINE.match, before.splitlines()) if m]
        new_keys = [m.group(1) for m in map(_RULE_LINE.match, after.splitlines()) if m]
        if sorted(old_keys) != sorted(new_keys):
            problems.append("RULES.md's keys change: only a value may (the code reads the keys)")
    elif name == "FEEDBACK.md":
        from core.knowledge.memory.entries import parse_line
        broken = [line for line in added_lines(before, after) if line.startswith("- ") and parse_line(line) is None]
        if broken:
            problems.append(f"{len(broken)} FEEDBACK line(s) wouldn't parse as entries: \"{_clip(broken[0])}\"")
    return Check("render", not problems, "; ".join(problems) or "it still loads")


def _keys(frontmatter: str) -> List[str]:
    return sorted(line.split(":", 1)[0].strip() for line in frontmatter.splitlines() if ":" in line)


def owners(path: str, root: str = REPO) -> List[str]:
    """The agents a file is part of: an agent's own file, or every agent granted the skill."""
    parts = boundaries.repo_path(path).split("/")
    if parts[0] == "agents" and len(parts) >= 2:
        return [parts[1]]
    if parts[0] == "skills" and len(parts) >= 2:
        skill, found = parts[1], []
        agents_dir = os.path.join(root, "agents")
        for agent in sorted(os.listdir(agents_dir)) if os.path.isdir(agents_dir) else []:
            try:
                with open(os.path.join(agents_dir, agent, "agent.json"), encoding="utf-8") as f:
                    skills = json.load(f).get("skills") or {}
            except (OSError, ValueError, AttributeError):
                continue
            if isinstance(skills, dict) and skill in skills:
                found.append(agent)
        return found
    return []


def overlay(root: str, path: str, text: str) -> str:
    """A temporary copy of `root` with `path` holding `text`: every other entry is a
    symlink to the real one, so it costs a few directories. The caller removes it."""
    parts = boundaries.repo_path(path).split("/")
    copy = tempfile.mkdtemp(prefix="aoc-proposal-")
    source, target = root, copy
    for depth, part in enumerate(parts):
        for entry in os.listdir(source):
            if entry != part:
                os.symlink(os.path.join(source, entry), os.path.join(target, entry))
        if depth == len(parts) - 1:
            with open(os.path.join(target, part), "w", encoding="utf-8") as f:
                f.write(text)
        else:
            source, target = os.path.join(source, part), os.path.join(target, part)
            os.mkdir(target)
    return copy


def check_lint(draft: Draft, lint: Optional[Callable[[str, str], Iterable[Tuple[str, str]]]] = None) -> Check:
    if boundaries.in_vault(draft.path):
        return Check("lint", True, "the vault: agent_lint doesn't read these files")
    agents = owners(draft.path, draft.root)
    if not agents:
        return Check("lint", True, "no agent's files: agent_lint doesn't apply")
    lint = lint or _lint
    copy = overlay(draft.root, draft.path, draft.after)
    try:
        problems = []
        for agent in agents:
            new = set(lint(agent, copy)) - set(lint(agent, draft.root))
            problems += [f"{agent}: {kind}: {_clip(message, 120)}" for kind, message in sorted(new)]
    finally:
        shutil.rmtree(copy, ignore_errors=True)
    return Check("lint", not problems, "; ".join(problems[:4]) or f"nothing new for {', '.join(agents)}")


def _lint(agent: str, root: str) -> List[Tuple[str, str]]:
    from agents.agent_designer.lib.lint import lint_agent
    report = lint_agent(agent, root=root, measure_schemas=False)
    return [(f.kind, f.message.replace(root, "")) for f in report.findings]


def run(draft: Draft, **overrides: Any) -> List[Check]:
    """Every check, in ORDER. Once `where` or `base` fails the rest can't run, and are left out.
    `overrides` (tests): `notes_check`, `agent_names`, `tools` and `lint` stand-ins."""
    checks = [check_where(draft)]
    if not checks[-1].ok:
        return checks
    checks.append(check_base(draft))
    if not checks[-1].ok:
        return checks
    checks += [
        check_size(draft),
        check_budget(draft),
        check_protected(draft),
        check_capability(draft, overrides.get("tools")),
        check_personal(draft, overrides.get("notes_check"), overrides.get("agent_names")),
        check_render(draft),
        check_lint(draft, overrides.get("lint")),
    ]
    return checks
