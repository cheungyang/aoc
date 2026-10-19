"""Thresholds an agent's tools read from the user's PKM: `pkm/agents/<agent_id>/RULES.md`.

Code owns the mechanism and the defaults; the user owns the numbers. An agent
declares its thresholds once, in `agents/<agent_id>/lib/rules.py`, as a tuple
named `RULES` (the module imports only `core.tooling`):

    from core.tooling import Rule

    RULES = (
        Rule("move_pct", 4.0, "price move, in %, that triggers research"),
        Rule("earnings_days", 7, "days ahead to flag earnings"),
        Rule("shelf_life_days", {"草莓 (Strawberries)": 3}, "days an item keeps, by name"),
    )

Its tools and scans read them with `load_rules("market-scout", RULES, report)`:
the user's values from RULES.md over these defaults. RULES.md is a plain list,
one rule per line, editable in Obsidian; a table rule lists its entries
indented below it:

    - move_pct: 4  # price move, in %, that triggers research
    - earnings_days: 7
    - shelf_life_days:  # days an item keeps, by name
      - 草莓 (Strawberries): 3

- A missing file or line means the default applies; a table's entries go over
  the default table one by one.
- A value reads as its default's type: yes/no for a boolean, a number may
  carry `%` or `,`, a list is comma-separated.
- An unknown key, or a value that doesn't read, is an issue in the report and
  the default applies. Nothing is dropped silently.

The agent may change a value once the user agrees. scripts/write_rules_defaults.py
writes RULES.md with every default for an agent that has none.
"""
import copy
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from core.tooling.report import Report

RULES_FILE = "RULES.md"

_KEY = re.compile(r"^[a-z][a-z0-9_]*$")
# "- key: value  # comment", or an indented "- entry name: value" under a table rule.
# The key ends at the first colon followed by a space or the end of the line, so
# a value may hold colons ("08:00").
_ITEM = re.compile(r"^(?P<indent>[ \t]*)[-*][ \t]+`?(?P<key>[^`]+?)`?[ \t]*:(?P<value>(?:[ \t].*)?)$")
_COMMENT = re.compile(r"\s+#(?:\s|$)")
_SCALARS = (bool, int, float, str)
TRUE_WORDS = {"true", "yes", "on", "1"}
FALSE_WORDS = {"false", "no", "off", "0"}


@dataclass(frozen=True)
class Rule:
    """One threshold: its key in RULES.md, its default, and what it means in plain words.

    The default is a bool, int, float or str; a list or tuple of those; or a
    dict of names to one of those (a table).
    """
    key: str
    default: Any
    description: str

    def __post_init__(self):
        if not _KEY.match(self.key or ""):
            raise ValueError(f"rule key {self.key!r}: use lowercase letters, digits and _")
        if not (self.description or "").strip():
            raise ValueError(f"rule {self.key!r} needs a description in plain words")
        default = self.default
        if isinstance(default, dict):
            readable = all(isinstance(k, str) and isinstance(v, _SCALARS) for k, v in default.items())
        elif isinstance(default, (list, tuple)):
            readable = all(isinstance(item, _SCALARS) for item in default)
        else:
            readable = isinstance(default, _SCALARS)
        if not readable:
            raise TypeError(f"rule {self.key!r}: the default must be a bool, int, float or str, "
                            f"a list of those, or a dict of names to one of those")


def rules_path(agent_id: str, pkm_dir: Optional[str] = None) -> str:
    """Where `agent_id`'s RULES.md lives: `pkm/agents/<agent_id>/RULES.md`."""
    if pkm_dir is None:
        from core.util.config import Config

        pkm_dir = Config().pkm_dir
    return os.path.join(pkm_dir, "agents", agent_id, RULES_FILE)


def coerce(raw: str, default: Any) -> Any:
    """Reads `raw` as the type of `default`. Raises ValueError if it can't."""
    text = raw.strip().strip("`").strip()
    if isinstance(default, bool):
        word = text.lower()
        if word in TRUE_WORDS:
            return True
        if word in FALSE_WORDS:
            return False
        raise ValueError(f"expected yes or no, got '{text}'")
    if isinstance(default, int):
        number = text.rstrip("%").replace(",", "").strip()
        try:
            return int(number)
        except ValueError:
            raise ValueError(f"expected a whole number, got '{text}'") from None
    if isinstance(default, float):
        number = text.rstrip("%").replace(",", "").strip()
        try:
            return float(number)
        except ValueError:
            raise ValueError(f"expected a number, got '{text}'") from None
    if isinstance(default, (list, tuple)):
        items = [item.strip() for item in text.split(",") if item.strip()]
        if default:
            items = [coerce(item, default[0]) for item in items]
        return type(default)(items)
    if isinstance(default, str):
        return text
    raise ValueError(f"no reader for {type(default).__name__}")


def load_rules(agent_id: str, rules: Sequence[Rule], report: Report) -> Dict[str, Any]:
    """{key: value} for `rules`: the user's values from RULES.md over the defaults.

    What can't be read goes in `report`, and its default applies.
    """
    path = rules_path(agent_id)
    if not os.path.isfile(path):
        return {rule.key: copy.deepcopy(rule.default) for rule in rules}
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    values, found = read_rules_text(text, rules, path, report)
    report.read(path, records=found)
    return values


def read_rules_text(text: str, rules: Sequence[Rule], source: str, report: Report):
    """(values, rule lines read) for a RULES.md's text. Issues go in `report`, named after `source`."""
    values = {rule.key: copy.deepcopy(rule.default) for rule in rules}
    by_key = {rule.key: rule for rule in rules}
    seen = set()
    table: Optional[Rule] = None
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        indented = line[:1] in (" ", "\t")
        if not indented:
            table = None
        match = _ITEM.match(line)
        if not match:
            continue
        name, raw = match.group("key").strip(), _strip_comment(match.group("value"))
        if indented:
            if table is None:
                report.problem(source, f"line {number}: '{line.strip()}' isn't under a table rule; ignored")
                continue
            entry_default = next(iter(table.default.values()), "")
            try:
                values[table.key][name] = coerce(raw, entry_default)
            except ValueError as e:
                report.problem(source, f"line {number}: '{name}: {raw}' can't be read ({e}); "
                                       f"{table.key} keeps its default for it")
            continue

        key = name.lower()
        rule = by_key.get(key)
        if rule is None:
            report.problem(source, f"line {number}: unknown rule '{name}'; ignored")
            continue
        if key in seen:
            report.problem(source, f"line {number}: '{key}' is set again; this line wins")
        seen.add(key)
        if isinstance(rule.default, dict):
            values[key] = copy.deepcopy(rule.default)
            if raw:
                report.problem(source, f"line {number}: '{key}' is a table: list its entries below it, "
                                       f"one per line; using the default")
            else:
                table = rule
            continue
        try:
            values[key] = coerce(raw, rule.default)
        except ValueError as e:
            values[key] = copy.deepcopy(rule.default)
            report.problem(source, f"line {number}: '{key}: {raw}' can't be read ({e}); "
                                   f"using the default {_format(rule.default)}")
    return values, len(seen)


def rule_keys(text: str) -> List[str]:
    """The rule keys a RULES.md sets, in file order: its unindented `- key: value` lines."""
    keys = []
    for line in text.splitlines():
        match = _ITEM.match(line)
        if match and not match.group("indent"):
            key = match.group("key").strip().lower()
            if key not in keys:
                keys.append(key)
    return keys


def render_rules_file(agent_id: str, rules: Sequence[Rule]) -> str:
    """A RULES.md holding every default, for an agent that has none yet. Reads back as the defaults."""
    lines = [
        f"# {agent_id}: rules",
        "",
        f"The thresholds {agent_id}'s tools use. Change a value, or delete its line to go back to the default.",
        "You can also ask the agent to change one; it confirms with you before saving.",
        "",
    ]
    for rule in rules:
        if isinstance(rule.default, dict):
            lines.append(f"- {rule.key}:  # {rule.description}")
            lines += [f"  - {name}: {_format(value)}" for name, value in rule.default.items()]
        else:
            lines.append(f"- {rule.key}: {_format(rule.default)}  # {rule.description}")
    return "\n".join(lines) + "\n"


def _strip_comment(raw: str) -> str:
    return _COMMENT.split(" " + raw, maxsplit=1)[0].strip()


def _format(value: Any) -> str:
    """A value as RULES.md writes it."""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        return ", ".join(_format(item) for item in value)
    return str(value)
