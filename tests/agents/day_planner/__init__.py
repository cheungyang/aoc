"""Tests for core/knowledge/planning, with made-up fixtures in the real vault formats.

`PlanningCase` builds a throwaway pkm folder (daily and weekly notes, the
fleeting template) and throwaway tasks.db / projects.db caches, and points
Config at them. Nothing here touches the real ~/pkm.
"""
import json
import os
import shutil
import tempfile
import unittest
from datetime import date

from core.util.config import Config

TODAY = date(2026, 10, 16)  # a Friday in ISO week 42 and vault week 42

FLEETING_TEMPLATE = """---
energy_level:
---
>[!tip]+ ![[2026-W<% tp.date.now("w")-1 %>#🌱 Next week's focus]]

### Brain dump 🧠🗑️


### Today's intention and commitment (max 3)

```dataviewjs
const thisFilename = dv.current().file.name;
const doneToday = dv.pages().file.tasks
\t.where(t => 
\t\t(t.completed || t.status =="-") 
\t\t&& (t.text.includes(thisFilename))
\t);
if (doneToday.length > 0) {
\tdv.header(3, "✅ Done today");
\tdv.taskList(doneToday, false);
}
```

### Sparks & Ideas 🌟
"""

DAILY_NOTE = """---
energy_level: 6
journal: fleeting
journal-date: {day}
---
>[!tip]+ ![[2026-W<% tp.date.now("w")-1 %>#🌱 Next week's focus]]

### Brain dump 🧠🗑️

#### Meeting notes
* Pat owns the review, Sam is coming back and may pick up the follow-up
\t* agenda, next steps (action items)
![[Pasted image 20260101120000.png]]

### Today's intention and commitment (max 3)
- [x] Review the weekly doc ✅ {day}
- [ ] say hi to Pat
- [/] Draft the Project A roadmap %% aoc_id 1a2b3c4d5e6f %%

code freeze on Friday, launch on 11/9 looks realistic

```dataviewjs
const thisFilename = dv.current().file.name;
```

### Sparks & Ideas 🌟


#### Agent's recommendation
- **Energy Level**: 6/10 (steady)
- [ ] Ship the roadmap draft
"""

# The note the Journals plugin creates: the template with its keys, nothing written in it yet.
NEW_DAILY_NOTE = FLEETING_TEMPLATE.replace("energy_level:\n", "energy_level:\njournal: fleeting\njournal-date: {day}\n")

WEEKLY_TEMPLATE_NOTE = """---
cssclass: hide-embedded-header
journal: weekly
journal-date: {day}
---
## 🔍 Weekly Review: Insights & Actions 
![[vault/journals/fleeting/<% tp.date.weekday("YYYY-MM-DD", -7, tp.file.title, "gggg-[W]ww") %>### Sparks & Ideas 🌟]]

### 🌱 Next week's focus
* A
* B
* C


### 🗓️ Book things for the next X weeks:
* Fun things to book & Weekend planning
"""

WEEKLY_FOCUS_NOTE = WEEKLY_TEMPLATE_NOTE.replace(
    "* A\n* B\n* C\n",
    "* **🚀 Project A push**: Ship the Project A roadmap %% aoc_id 0f1e2d3c4b5a %%\n"
    "* **Garden makeover**:\n\t* plants - talk to Pat for a quote\n"
    "* Plan the next quarter\n",
)

# templates/Weekly Review.md, trimmed: the note the plugin creates is this plus its keys.
WEEKLY_TEMPLATE = """---
cssclass: hide-embedded-header 
---
<< [[<% tp.date.now("gggg-[W]ww", -1, tp.file.title, "gggg-[W]ww") %>]] | [[<% tp.date.now("gggg-[W]ww", 1, tp.file.title, "gggg-[W]ww") %>]] >>

## 🔍 Weekly Review: Insights & Actions 
![[vault/journals/fleeting/<% tp.date.weekday("YYYY-MM-DD", -7, tp.file.title, "gggg-[W]ww") %>### Sparks & Ideas 🌟]]

## ✅ Wins with 2026 commitments
Send out gratitude and wins



### 🏆 Works wins

### 🏆 Family wins

### 🏆 Personal development wins

## 🎈Projects review
```dataview
TABLE category FROM "vault/projects"
# not a heading
```

### 🌱 Next week's focus
* A
* B
* C


### 🗓️ Book things for the next X weeks:
* Fun things to book & Weekend planning

"""


class PlanningCase(unittest.TestCase):
    """A temp pkm folder and task caches, with Config pointed at them."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.pkm = os.path.join(self.root, "pkm")
        os.makedirs(self.pkm)
        config = Config()
        config.pkm_dir = self.pkm
        config.tasks_db_path = os.path.join(self.root, "tasks.db")
        config.projects_db_path = os.path.join(self.root, "projects.db")
        self.addCleanup(self._cleanup)
        self.write("templates/Fleeting Pages.md", FLEETING_TEMPLATE)
        self._init_caches()

    def _cleanup(self):
        config = Config()
        config.pkm_dir = None
        config.tasks_db_path = None
        config.projects_db_path = None
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, rel, text):
        path = os.path.join(self.pkm, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def daily(self, day, text=DAILY_NOTE):
        return self.write(f"vault/journals/fleeting/{day.isoformat()}.md", text.replace("{day}", day.isoformat()))

    def weekly(self, name, text=WEEKLY_TEMPLATE_NOTE, day="2026-10-04"):
        return self.write(f"vault/journals/weekly/{name}.md", text.replace("{day}", day))

    def _init_caches(self):
        from core.knowledge.projects.db import get_connection as projects_connection
        from core.knowledge.projects.db import init_db as init_projects
        from core.knowledge.tasks.db import get_connection as tasks_connection
        from core.knowledge.tasks.db import init_db as init_tasks

        conn = tasks_connection(Config().tasks_db_path)
        init_tasks(conn)
        conn.close()
        conn = projects_connection(Config().projects_db_path)
        init_projects(conn)
        conn.close()

    def task(self, line, source="vault/projects/Chores.md"):
        """Adds one task, parsed from its markdown line by the real parser."""
        from core.knowledge.tasks.db import get_connection, upsert_tasks
        from core.knowledge.tasks.parser import parse_task_line

        self._lines = getattr(self, "_lines", 0) + 1
        parsed, _ = parse_task_line(line, self._lines, source)
        conn = get_connection(Config().tasks_db_path)
        upsert_tasks(conn, [parsed])
        conn.close()
        return parsed

    def project(self, name, status="executing", year=2026, priority="⏫", rank=2, aliases=(), category=None,
                file_name=None, commitments=()):
        from core.knowledge.projects.db import get_connection

        conn = get_connection(Config().projects_db_path)
        pid = f"id-{name}"
        conn.execute(
            "INSERT INTO projects (id, name, file_path, status, raw_status, commitment_year, priority, "
            "priority_rank, category, aliases, tags, content_hash, updated_date) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', 'h', '2026-10-01')",
            (pid, name, f"vault/projects/{file_name or name + '.md'}", status, status, year, priority, rank,
             category, json.dumps(list(aliases))),
        )
        for y in commitments:
            conn.execute("INSERT INTO project_commitments (id, year, priority, priority_rank) VALUES (?, ?, ?, ?)",
                         (pid, y, priority, rank))
        conn.commit()
        conn.close()
