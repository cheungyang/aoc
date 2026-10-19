# goal-setter: how its tools work

Both tools read one snapshot of the project and task stores, plus Grace's own coaching records. They never write. With `explain=true`, they also list the files they read and how each number was reached.

## Sources

- **The stores**, `projects.db` and `tasks.db`, which the project and task syncs build from the vault. Both are required: a store that is missing or can't be read fails the call, and the issue names it, so run its sync. They are opened read-only.
- **The dossier**, `pkm/wiki/project-coaching/<project>.md`. It belongs to the project whose name, or note file name, matches its `project_name` or its own file name. Its frontmatter's `last_coached` is a date; its body, without the `**Status:**` line, is the dossier notes.
- **The project note.** Each agreed session adds a `### YYYY-MM-DD from Grace` heading. The latest session is the newest date, wherever it sits in the note; its text runs to the next heading of the same or a higher level.
- **Last coached** is the later of the dossier's `last_coached` and the latest session.
- Notes are cut at 1,200 characters, at a word break, and end with "…"; Obsidian `%% … %%` comments are dropped.

Nothing is guessed. Each of these makes the answer `partial`, and the lines come back raw: a dossier with no `last_coached`, or one that doesn't read as a date; a session heading whose date isn't YYYY-MM-DD; a task date that isn't a date; a note that can't be opened.

A dossier and a note, read. The heading dated `2026-9-30` isn't YYYY-MM-DD, so it comes back raw, and the newest readable session counts:

```python
>>> from datetime import date
>>> from agents.goal_setter.lib.coaching import read_dossier, read_sessions
>>> dossier = read_dossier("---\nproject_name: Garden makeover\nlast_coached: 2026-10-03\n---\n**Status:** executing\n## Coaching Strategy\nStart with the raised beds.\n")
>>> dossier.last_coached, dossier.strategy
(datetime.date(2026, 10, 3), '## Coaching Strategy\nStart with the raised beds.')
>>> read_dossier("---\nlast_coached: soon\n---\n").unreadable
('last_coached: soon',)
>>> sessions = read_sessions("### 2026-05-12 from Grace\nold plan\n### 2026-09-23 from Grace 🚀\nnew plan %% aoc_id 22 %%\n### 2026-9-30 from Grace\n")
>>> sessions.latest, sessions.notes, sessions.unreadable
(datetime.date(2026, 9, 23), 'new plan', ('### 2026-9-30 from Grace',))
```

## project_context

- Finds the project by its exact name, id, note file name or alias tag (`#p/10k` or `p/10k`), ignoring case; then by a unique part of its name, note file name or alias, ignoring spaces and punctuation (speech says "10 k"). Of several matches, a single executing one wins; otherwise the call fails and lists the candidates.
- A task belongs to the project when it carries one of the project's alias tags or a subtag (`#p/10k/race`), or sits in the project's note. `#p/10km` is another project's tag.
- Counts: open tasks; overdue (open, due before today); due in the next 14 days, today included; completed; dropped.
- Velocity counts completions by their completion date: in the last 7, 30 and 90 days, today included, and in the 30 days before the last 30. Per week is the 90-day count × 7 / 90, to one decimal. The trend compares the last 30 days with the 30 before: `up`, `down` or `steady`, and `stalled` when both are 0.
- A completed task without a completion date counts as completed but in no window; `completed_without_date` says how many.
- Open tasks come most urgent first (priority, due date, scheduled date, title), 10 at most, with how many weren't shown; recent completions are the last 30 days', newest first, 5 at most.

Three dated completions, 1, 15 and 36 days ago, and one without a date. The last 30 days hold 2 and the 30 before hold 1, so the trend is up; 3 × 7 / 90 rounds to 0.2 a week:

```python
>>> from agents.goal_setter.lib.velocity import task_stats
>>> today = date(2026, 10, 16)
>>> done = [{"status": "completed", "completed_date": day} for day in ("2026-10-15", "2026-10-01", "2026-09-10")]
>>> stats = task_stats(done + [{"status": "completed"}], today)
>>> stats.done_7d, stats.done_30d, stats.done_prev_30d, stats.done_90d, stats.per_week, stats.trend
(1, 2, 1, 3, 0.2, 'up')
>>> stats.completed, stats.undated
(4, 1)
```

A spoken name finds the project; a name that matches nothing lists the executing projects:

```python
>>> from agents.goal_setter.lib.attention import find_project
>>> projects = [
...     {"id": "4c36", "name": "Finish a 10k by EoY 2026", "status": "executing", "aliases": ["#p/10k"],
...      "file_path": "vault/projects/Finish a 10k by June 2026.md"},
...     {"id": "8150", "name": "Project Alpha launch", "status": "executing", "aliases": ["#p/alpha"]},
... ]
>>> find_project("10 k", projects)["name"]
'Finish a 10k by EoY 2026'
>>> try:
...     find_project("running goal", projects)
... except LookupError as e:
...     print(e)
No project matches 'running goal'. Executing projects: Finish a 10k by EoY 2026, Project Alpha launch.
```

## projects_needing_attention

Ranks the executing projects for coaching. Which one to coach, and how, is the coach_project skill's call.

- A project coached fewer than `rest_days` days ago rests: it isn't ranked, and its name is listed under `coached_in_last_<rest_days>d`.
- Every other executing project collects points for each signal that applies:

| Signal (the reason given) | When | Points |
|---|---|---|
| never coached | no `last_coached` in a dossier and no session in the note | `never_coached` |
| last coached N days ago | N is `coach_every_days` or more | `due_again`; `long_gap` instead once N is `long_gap_days` or more |
| stalled: nothing done in N days, M open | open tasks, and the last dated completion is `stall_days` or more days ago | `stalled` |
| stalled: no dated completion, M open | open tasks, and no completion has a date | `stalled` |
| no open tasks | | `no_open_tasks` |
| N overdue tasks | open tasks due before today | `overdue`, however many |
| committed for 2025, not 2026 | its commitment year is past | none |

- Its priority adds `priority_points`, by sign (🔺 ⏫ 🔼 🔽 ⏬). A priority set for a past year's commitment adds nothing, and ranks below every priority in a tie. An entry that names no priority is reported and counts for nothing.
- A project with no signal isn't ranked.
- Order: most points first; then the higher priority; then the longer since coached, never coached first; then the name.

The defaults. `pkm/agents/goal-setter/RULES.md` overrides them one line at a time:

| Key | Default |
|---|---|
| `rest_days` | 7 |
| `coach_every_days` | 14 |
| `long_gap_days` | 28 |
| `stall_days` | 30 |
| `never_coached` | 3 |
| `due_again` | 2 |
| `long_gap` | 3 |
| `stalled` | 2 |
| `no_open_tasks` | 2 |
| `overdue` | 1 |
| `priority_points` | 🔺 3, ⏫ 2, 🔼 1, 🔽 0, ⏬ 0 |

In RULES.md a weight is one line, and `priority_points` lists its entries below it; an entry not listed keeps its default:

```markdown
- stalled: 3
- priority_points:
  - ⏫: 3
```

Never coached, two open tasks and no dated completion, ⏫: 3 + 2 + 2 = 7.

```python
>>> from agents.goal_setter.lib.attention import assess, is_resting, order
>>> from agents.goal_setter.lib.rules import RULES
>>> rules = {rule.key: rule.default for rule in RULES}
>>> guitar = {"name": "Guitar lessons", "priority_rank": 2, "commitment_year": 2026}
>>> guitar_tasks = task_stats([{"status": "todo"}, {"status": "todo"}, {"status": "completed"}], today)
>>> first = assess(guitar, guitar_tasks, None, today, rules)
>>> first.score, first.reasons, first.points
(7, ('never coached', 'stalled: no dated completion, 2 open'), ('never_coached 3', 'stalled 2', '⏫ 2'))
```

"Weigh stalled projects more" is `stalled: 3`:

```python
>>> assess(guitar, guitar_tasks, None, today, dict(rules, stalled=3)).score
8
```

Last coached 23 days ago (due again, not yet a long gap), nothing done for 105 days, ⏫: 2 + 2 + 2 = 6. A 2025 commitment, never coached, idle 39 days, with an overdue task: 3 + 2 + 1 = 6, and no priority points:

```python
>>> run = {"name": "Finish a 10k by EoY 2026", "priority_rank": 2, "commitment_year": 2026}
>>> run_tasks = task_stats([{"status": "todo"}, {"status": "todo"}, {"status": "completed", "completed_date": "2026-07-03"}], today)
>>> second = assess(run, run_tasks, date(2026, 9, 23), today, rules)
>>> second.score, second.reasons
(6, ('last coached 23 days ago', 'stalled: nothing done in 105 days, 2 open'))
>>> spanish = {"name": "Spanish lessons", "priority_rank": 2, "commitment_year": 2025}
>>> spanish_tasks = task_stats([{"status": "todo", "due_date": "2026-10-06"}, {"status": "completed", "completed_date": "2026-09-07"}], today)
>>> third = assess(spanish, spanish_tasks, None, today, rules)
>>> third.score, third.points, third.reasons[-1]
(6, ('never_coached 3', 'stalled 2', 'overdue 1'), 'committed for 2025, not 2026')
```

The tie at 6 goes to this year's priority. A project coached 5 days ago rests; one coached 7 days ago doesn't:

```python
>>> [a.project["name"] for a in order([third, second, first], today)]
['Guitar lessons', 'Finish a 10k by EoY 2026', 'Spanish lessons']
>>> is_resting(date(2026, 10, 11), today, rules), is_resting(date(2026, 10, 9), today, rules)
(True, False)
```
