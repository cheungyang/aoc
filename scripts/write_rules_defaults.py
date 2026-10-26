#!/usr/bin/env python3
"""Writes each agent's RULES.md defaults into the user's PKM, never over a file that's there.

For every agent with `agents/<agent_id>/lib/rules.py` (a `RULES` tuple of
core.tooling.Rule):

- no `pkm/agents/<agent_id>/RULES.md` yet: prints the file it would write, or
  writes it with --write;
- a RULES.md already there: leaves it as it is, and lists the keys it lacks
  (their defaults apply) and the keys the code doesn't know.

    .venv/bin/python scripts/write_rules_defaults.py            # dry run: shows what it would write
    .venv/bin/python scripts/write_rules_defaults.py --write

The rules modules are found by folder and imported from their path; nothing
here imports an agent's code by name.
"""
import argparse
import importlib.util
import os
import sys
from dataclasses import dataclass, field
from typing import List, Tuple

script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from core.tooling.rules import Rule, render_rules_file, rule_keys, rules_path  # noqa: E402

AGENTS_DIR = os.path.join(project_root, "agents")


@dataclass
class Outcome:
    """What the script found, or did, for one agent."""
    agent_id: str
    path: str  # the agent's RULES.md
    rules: int = 0
    text: str = ""  # the defaults file, when the agent had none
    written: bool = False
    missing: List[str] = field(default_factory=list)  # keys an existing file lacks
    unknown: List[str] = field(default_factory=list)  # keys an existing file sets that the code doesn't know
    error: str = ""

    def render(self, pkm_dir: str) -> str:
        shown = "pkm/" + os.path.relpath(self.path, pkm_dir).replace(os.sep, "/")
        if self.error:
            return f"{self.agent_id}: {self.error}"
        if self.written:
            return f"{self.agent_id}: wrote {shown} ({self.rules} rules)"
        if self.text:
            body = "\n".join(f"    {line}" if line else "" for line in self.text.splitlines())
            return f"{self.agent_id}: would write {shown}:\n{body}"
        if not (self.missing or self.unknown):
            return f"{self.agent_id}: {shown} is there and sets every rule"
        parts = [f"{self.agent_id}: {shown} is there; left as it is."]
        if self.missing:
            parts.append(f"Not in it (their defaults apply): {', '.join(self.missing)}.")
        if self.unknown:
            parts.append(f"Unknown to the code: {', '.join(self.unknown)}.")
        return " ".join(parts)


def rules_modules(agents_dir: str = AGENTS_DIR) -> List[Tuple[str, str]]:
    """(agent_id, path) for each `agents/<agent_id>/lib/rules.py`, by id."""
    if not os.path.isdir(agents_dir):
        return []
    found = []
    for agent_id in sorted(os.listdir(agents_dir)):
        path = os.path.join(agents_dir, agent_id, "lib", "rules.py")
        if os.path.isfile(path):
            found.append((agent_id, path))
    return found


def load_rules_tuple(agent_id: str, path: str) -> tuple:
    """The `RULES` of an agent's lib/rules.py, imported from its path."""
    spec = importlib.util.spec_from_file_location(f"_rules_of_{agent_id.replace('-', '_')}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rules = getattr(module, "RULES", None)
    if not isinstance(rules, tuple) or not rules or not all(isinstance(rule, Rule) for rule in rules):
        raise ValueError("RULES must be a non-empty tuple of Rule")
    return rules


def write_defaults(agents_dir: str, pkm_dir: str, write: bool = False) -> List[Outcome]:
    """One Outcome per agent with lib/rules.py. Writes only missing files, and only with `write`."""
    outcomes = []
    for agent_id, module_path in rules_modules(agents_dir):
        outcome = Outcome(agent_id, rules_path(agent_id, pkm_dir))
        outcomes.append(outcome)
        try:
            rules = load_rules_tuple(agent_id, module_path)
        except Exception as e:
            outcome.error = f"can't read lib/rules.py ({type(e).__name__}: {e})"
            continue
        outcome.rules = len(rules)
        if os.path.exists(outcome.path):
            with open(outcome.path, "r", encoding="utf-8") as f:
                found = rule_keys(f.read())
            known = [rule.key for rule in rules]
            outcome.missing = [key for key in known if key not in found]
            outcome.unknown = [key for key in found if key not in known]
            continue
        outcome.text = render_rules_file(agent_id, rules)
        if write:
            os.makedirs(os.path.dirname(outcome.path), exist_ok=True)
            with open(outcome.path, "x", encoding="utf-8") as f:  # "x": never over a file that appeared meanwhile
                f.write(outcome.text)
            outcome.written = True
    return outcomes


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Write each agent's RULES.md defaults into the PKM, never over an existing file.")
    parser.add_argument("--write", action="store_true", help="write the missing files (default: only show them)")
    parser.add_argument("--pkm-dir", default=None, help="the PKM to write into (default: the configured one)")
    parser.add_argument("--agents-dir", default=AGENTS_DIR, help="where the agents live (default: this repo's)")
    args = parser.parse_args(argv)
    if args.pkm_dir is None:
        from core.config.settings import Config

        args.pkm_dir = Config().pkm_dir

    outcomes = write_defaults(args.agents_dir, args.pkm_dir, write=args.write)
    if not outcomes:
        print("No agent declares rules yet (agents/<agent_id>/lib/rules.py).")
    for outcome in outcomes:
        print(outcome.render(args.pkm_dir))
    pending = sum(1 for outcome in outcomes if outcome.text and not outcome.written)
    if pending:
        print(f"\nDry run: nothing written. Run with --write to write {pending} file{'s' if pending > 1 else ''}.")
    return 1 if any(outcome.error for outcome in outcomes) else 0


if __name__ == "__main__":
    sys.exit(main())
