#!/usr/bin/env python3
"""One-off migration of agent memory to Memory v2.

Two phases, so nothing reaches the vault without review:

    migrate_memory_v2.py export classification.json
        Parses every agent's MEMORY.md / FEEDBACK.md / CONTEXT.md into one item
        per bullet: {"agent", "file", "text", "date", "tag": null}.

    (classify: fill in each item's "tag" -- `profile`, `private`, `feedback`
     or a topic from TAGS.md -- optionally "until": "YYYY-MM-DD", or
     "drop": true for lines not worth keeping; "text" may be rewritten.)

    migrate_memory_v2.py apply classification.json [--dry-run]
        Writes wiki/memory/TAGS.md (if missing), PROFILE.md, topics/<tag>.md
        and each agent's MEMORY.md / FEEDBACK.md in the entry format, merges
        exact duplicates, enforces budgets (evictions go to the archives, never
        deleted) and removes CONTEXT.md. The vault is in git: review the diff
        before committing.

`first` / `seen` come from the bullet's own `(Ref: YYYY-MM-DD)` date when it has
one, else the file's modification date, which staggers the first staleness
review instead of putting every entry up for review on the same night.
"""
import argparse
import datetime
import json
import os
import re
import sys

script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from core.knowledge.memory import store
from core.knowledge.memory.entries import (
    FEEDBACK, PRIVATE, PROFILE, SCOPE_TAGS, Entry, MemoryFile, clean_text, normalise, parse_tags,
)
from core.util.config import Config

SOURCE_FILES = ("MEMORY.md", "FEEDBACK.md", "CONTEXT.md")

DEFAULT_TAGS = """# Memory tags

The closed list of shared memory topics. Each `## name` is a tag; the paragraph
under it is what the dream model reads to decide whether a fact belongs there.
Agents read a topic when it's in their `memory_topics` (agent.json). Add a tag
by adding a section here.

## food
Diet, allergies, cuisine and kitchen habits.

## health
Exercise, sleep and medical constraints.

## family
Childcare routines, visiting relatives and family events.

## travel
Trips and getting around: how far you like to go, favourite places, and the kinds of trips and activities you enjoy.

## finance
Cards, budgets, spending patterns and loyalty programmes.

## home
Devices, routines and household logistics.

## work
Career focus, projects and priorities.

## learning
Topics studied and reading strategy.

## personal
What matters to you: personal goals, current focus, and the values behind your priorities.
"""

_REF = re.compile(r"\(Ref:\s*([0-9, \-]+)\)\s*$")
_BULLET = re.compile(r"^\s*[-*•]\s+(.*)$")


_PREFIX = re.compile(r"^\[(\d{4}-\d{2}-\d{2})\]\s*")


def bullet_date(text, fallback):
    prefix = _PREFIX.match(text)
    if prefix:
        text = text[prefix.end():]
        try:
            fallback = datetime.date.fromisoformat(prefix.group(1))
        except ValueError:
            pass
    match = _REF.search(text)
    if match:
        dates = re.findall(r"\d{4}-\d{2}-\d{2}", match.group(1))
        valid = []
        for d in dates:
            try:
                valid.append(datetime.date.fromisoformat(d))
            except ValueError:
                pass
        if valid:
            return max(valid), _REF.sub("", text).strip()
    return fallback, text.strip()


def export(pkm_dir):
    items = []
    agents_root = os.path.join(pkm_dir, "agents")
    for agent in sorted(os.listdir(agents_root)):
        for name in SOURCE_FILES:
            path = os.path.join(agents_root, agent, name)
            if not os.path.isfile(path):
                continue
            mtime = datetime.date.fromtimestamp(os.path.getmtime(path))
            for line in store.read_text(path).splitlines():
                match = _BULLET.match(line)
                if not match:
                    continue
                date, text = bullet_date(match.group(1), mtime)
                items.append({"agent": agent, "file": name, "text": text,
                              "date": date.isoformat(), "tag": None})
    return items


def _entry(item, tag):
    date = datetime.date.fromisoformat(item["date"])
    until = datetime.date.fromisoformat(item["until"]) if item.get("until") else None
    return Entry(tag=tag, text=clean_text(item["text"]), src=item["agent"],
                 first=date, seen=date, count=1, until=until)


def plan(items, tag_names, pkm_dir=None):
    """{scope path: (Scope, MemoryFile)} built from a classification."""
    problems = []
    files = {}
    for i, item in enumerate(items):
        if item.get("drop"):
            continue
        tag = item.get("tag")
        if not tag:
            problems.append(f"item {i} ({item['agent']}/{item['file']}): no tag")
            continue
        if tag not in SCOPE_TAGS and tag not in tag_names:
            problems.append(f"item {i}: unknown tag '{tag}'")
            continue
        scope = store.scope_for(tag, item["agent"], pkm_dir)
        _, memory = files.setdefault(scope.path, (scope, MemoryFile()))
        entry = _entry(item, tag)
        same = next((j for j, e in enumerate(memory.entries) if normalise(e.text) == normalise(entry.text)), None)
        if same is not None:
            old = memory.entries[same]
            memory.entries[same] = Entry(tag, old.text, old.src, min(old.first, entry.first),
                                         max(old.seen, entry.seen), old.count + 1, old.until or entry.until)
        else:
            memory.entries.append(entry)
    return files, problems


def apply(items, pkm_dir, dry_run=False, today=None):
    today = today or datetime.date.today()
    tags_file = store.tags_path(pkm_dir)
    tags_text = store.read_text(tags_file) or DEFAULT_TAGS
    tag_names = [name for name, _ in parse_tags(tags_text)]
    files, problems = plan(items, tag_names, pkm_dir)
    if problems:
        return problems, []

    report = []
    if not dry_run and not os.path.isfile(tags_file):
        store.write_atomic(tags_file, DEFAULT_TAGS)
        report.append(f"wrote {os.path.relpath(tags_file, pkm_dir)}")

    agents = sorted({item["agent"] for item in items})
    # Private files are rewritten from scratch: every old line was classified.
    for agent in agents:
        for tag in (PRIVATE, FEEDBACK):
            scope = store.private_scope(agent, tag, pkm_dir)
            files.setdefault(scope.path, (scope, MemoryFile()))

    for path, (scope, memory) in sorted(files.items()):
        if dry_run:
            kept, evicted, expired = store.enforce(memory, scope.budget, today)
            counts = {"budget": len(evicted), "expired": len(expired)}
        else:
            counts = store.commit(scope, memory, today)
        rel = os.path.relpath(path, pkm_dir)
        note = ", ".join(f"{k} {v}" for k, v in counts.items() if v)
        report.append(f"{rel}: {len(memory.entries)} entries" + (f" (archived: {note})" if note else ""))

    for agent in agents:
        context = os.path.join(pkm_dir, "agents", agent, "CONTEXT.md")
        if os.path.isfile(context):
            if not dry_run:
                os.remove(context)
            report.append(f"removed {os.path.relpath(context, pkm_dir)}")
    return [], report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Migrate agent memory to Memory v2.")
    sub = parser.add_subparsers(dest="command", required=True)
    exp = sub.add_parser("export")
    exp.add_argument("output")
    app = sub.add_parser("apply")
    app.add_argument("classification")
    app.add_argument("--dry-run", action="store_true")
    parser.add_argument("--pkm-dir", default=None)
    args = parser.parse_args(argv)
    pkm_dir = args.pkm_dir or Config().pkm_dir

    if args.command == "export":
        items = export(pkm_dir)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2, ensure_ascii=False)
        print(f"Exported {len(items)} items to {args.output}")
        return 0

    with open(args.classification, encoding="utf-8") as f:
        items = json.load(f)
    problems, report = apply(items, pkm_dir, dry_run=args.dry_run)
    if problems:
        print("Not applied:\n  " + "\n  ".join(problems))
        return 1
    print(("Dry run:\n  " if args.dry_run else "Applied:\n  ") + "\n  ".join(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
