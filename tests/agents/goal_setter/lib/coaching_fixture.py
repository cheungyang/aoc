"""A small PKM for the coaching tests: projects.db, tasks.db, dossiers and notes
in a temp dir, in the real formats with made-up projects. Today is Fri 2026-10-16.

- "Finish a 10k by EoY 2026": ⏫ 2026, its note still named "...June 2026.md";
  coached 2026-09-23 (dossier and note); nothing done since 2026-07-03.
- "Project Alpha launch": 🔺 2026; dossier says coached 2026-10-11.
- "Guitar lessons": ⏫ 2026; never coached; its only completed task has no date.
- "Spanish lessons": ⏫ but committed for 2025; never coached; 1 overdue.
- "Start a podcast": paused.

Every project has its note, so a read of this PKM is complete (status ok).
`RULES` holds goal-setter's default rules, as the tools read them with no RULES.md.
"""
import os
import shutil
import tempfile
from datetime import date

from core.storage.projects import ProjectsStore
from core.storage.tasks import TasksStore
from core.tooling import Report
from agents.goal_setter.lib.rules import RULES as RULE_DECLARATIONS

TODAY = date(2026, 10, 16)
NOTE_RUN = "vault/projects/Finish a 10k by June 2026.md"
NOTE_ALPHA = "vault/projects/Project Alpha launch.md"
NOTE_GUITAR = "vault/projects/Guitar lessons.md"
NOTE_SPANISH = "vault/projects/Spanish lessons.md"
NOTE_PODCAST = "vault/projects/Start a podcast.md"
RULES = {rule.key: rule.default for rule in RULE_DECLARATIONS}

PROJECTS = [
    {"id": "4c36ea3c2444", "name": "Finish a 10k by EoY 2026", "file_path": NOTE_RUN, "status": "executing",
     "raw_status": "s/✊Executing", "commitment_year": 2026, "priority": "⏫", "priority_rank": 2,
     "start_date": "2025-03-01", "last_reviewed": "2026-09-23", "category": "🏃 Health",
     "aliases": ["#p/10k"], "tags": ["s/✊Executing", "c/⏫2026"]},
    {"id": "8150fa0422fa", "name": "Project Alpha launch", "file_path": NOTE_ALPHA, "status": "executing",
     "commitment_year": 2026, "priority": "🔺", "priority_rank": 1, "category": "💼 Work",
     "aliases": ["#p/alpha"], "tags": ["c/🔺2026"]},
    {"id": "7c63ffacf2a5", "name": "Guitar lessons",
     "file_path": NOTE_GUITAR, "status": "executing",
     "commitment_year": 2026, "priority": "⏫", "priority_rank": 2, "category": "🎨 Hobbies", "aliases": ["#p/music"]},
    {"id": "e3b24e07518d", "name": "Spanish lessons", "file_path": NOTE_SPANISH,
     "status": "executing", "commitment_year": 2025, "priority": "⏫", "priority_rank": 2, "aliases": ["#p/spanish"]},
    {"id": "77aa00bb11cc", "name": "Start a podcast", "file_path": NOTE_PODCAST,
     "status": "paused", "aliases": ["#p/podcast"]},
]


def _task(tid, title, status="todo", tags=(), priority=None, rank=99, done=None, dropped=None, due=None,
          source="ticktick/Inbox.md"):
    return {"id": tid, "title": title, "raw_title": title, "status": status, "priority": priority,
            "priority_rank": rank, "tags": list(tags), "due_date": due, "completed_date": done,
            "dropped_date": dropped, "source": source, "source_type": "vault" if source.startswith("vault") else "ticktick",
            "line_number": 1, "raw_line": f"- [ ] {title}"}


TASKS = [
    _task("r1", "Run a 5k time trial", tags=["a/run", "p/10k"], priority="🔼", rank=3, source=NOTE_RUN),
    _task("r2", "Easy runs: 3x weekly", tags=["p/10k"], source=NOTE_RUN),
    _task("r3", "Stretch after every run", "completed", ["p/10k"], done="2026-07-03", source=NOTE_RUN),
    _task("r4", "Buy running shoes", "completed", ["p/10k"], done="2026-03-20", source=NOTE_RUN),
    _task("r5", "Join a running club", "dropped", ["p/10k"], dropped="2026-03-20", source=NOTE_RUN),
    _task("r6", "Bike commute log", tags=["p/10km"]),  # a different tag, not 10k's
    _task("a1", "Write the launch checklist", tags=["p/alpha"], priority="🔺", rank=1, due="2026-10-22"),
    _task("a2", "Draft the rollout plan", "completed", ["p/alpha"], done="2026-10-02"),
    _task("a3", "Follow up with Pat on the spec", "completed", ["p/alpha/spec"], done="2026-10-08"),
    _task("a4", "Team kickoff", "completed", done="2026-09-04", source=NOTE_ALPHA),  # in the note, untagged
    _task("g1", "Book a trial lesson", tags=["p/music"], priority="⏫", rank=2),
    _task("g2", "Learn three chords", tags=["p/music"]),
    _task("g3", "Buy a guitar", "completed", ["p/music"]),
    _task("s1", "Book the placement test", tags=["p/spanish"], due="2026-10-06"),
    _task("s2", "Finish unit 3", "completed", ["p/spanish"], done="2026-09-07"),
]

DOSSIER_RUN = """---
project_name: Finish a 10k by June 2026
last_coached: 2026-09-23
---
**Status:** executing | **Priority:** ⏫ | **Year:** 2026 | **Category:** 🏃 Health

## Coaching Strategy
The 5k time trial is still unticked. Ask what got in the way.

## Knowledge Graph Context
- **Behaviors:** runs before work, rests on Sundays.
"""

DOSSIER_ALPHA = """---
project_name: Project Alpha launch
last_coached: 2026-10-11
---
## Coaching Strategy
Agree on the launch date first.
"""

NOTE_RUN_TEXT = """---
id: 4c36ea3c2444
title: Finish a 10k by EoY 2026
aliases:
  - "#p/10k"
---
# Tasks with \\#p/10k tags
```dataview
TASK WHERE contains(text, this.aliases[0])
```

### 2026-09-23 from Grace
**Shift to the year-end plan**
- **Milestone 1: A steady base** - three easy runs a week.
- [ ] #a/run Run a 5k time trial 🔼 #p/10k %% aoc_id 164c286e0deb %%

### 2026-05-12 from Grace
🚀 **The Spring Plan Kicks Off**

# was: Finish a 10k by June 2026
"""

NOTE_ALPHA_TEXT = """---
id: 8150fa0422fa
---
### 2026-09-14 from Grace
Sprint wrap-up.
"""


class CoachingPkm:
    """Builds the PKM in a temp dir; call cleanup() when done."""

    def __init__(self):
        self.root = tempfile.mkdtemp()
        self.pkm_dir = os.path.join(self.root, "pkm")
        self.projects_db = os.path.join(self.pkm_dir, "projects.db")
        self.tasks_db = os.path.join(self.pkm_dir, "tasks.db")
        projects = ProjectsStore(self.projects_db)
        projects.init()
        projects.upsert_projects(PROJECTS)
        tasks = TasksStore(self.tasks_db)
        tasks.init()
        tasks.upsert_tasks(TASKS)
        self.write("wiki/project-coaching/Finish a 10k by June 2026.md", DOSSIER_RUN)
        self.write("wiki/project-coaching/Project Alpha launch.md", DOSSIER_ALPHA)
        self.write(NOTE_RUN, NOTE_RUN_TEXT)
        self.write(NOTE_ALPHA, NOTE_ALPHA_TEXT)
        self.write(NOTE_GUITAR, "---\nid: 7c63ffacf2a5\n---\n# Guitar lessons\n")
        self.write(NOTE_SPANISH, "---\nid: e3b24e07518d\n---\n")
        self.write(NOTE_PODCAST, "---\nid: 77aa00bb11cc\n---\n")

    def write(self, rel_path, text):
        path = os.path.join(self.pkm_dir, rel_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def write_rules(self, text):
        """goal-setter's RULES.md in this PKM, which the tools read once use_as_config() points there."""
        return self.write("agents/goal-setter/RULES.md", text)

    def snapshot(self, report=None):
        from agents.goal_setter.lib.snapshot import load_snapshot

        return load_snapshot(report or Report(), pkm_dir=self.pkm_dir, projects_db=self.projects_db,
                             tasks_db=self.tasks_db)

    def use_as_config(self):
        """Points Config at this PKM; undo with reset_config()."""
        from core.util.config import Config

        config = Config()
        config.pkm_dir, config.projects_db_path, config.tasks_db_path = self.pkm_dir, self.projects_db, self.tasks_db

    @staticmethod
    def reset_config():
        from core.util.config import Config

        config = Config()
        config.pkm_dir = config.projects_db_path = config.tasks_db_path = None

    def cleanup(self):
        self.reset_config()
        shutil.rmtree(self.root, ignore_errors=True)
