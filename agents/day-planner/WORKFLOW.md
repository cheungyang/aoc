# day-planner: how its tools work

Three tools and one scan share this code. `day_briefing` and the 06:00 `morning_briefing` scan build the same briefing; the scan leaves out the new-note text. `recurring_chores_due` is the briefing's chore section on its own. `write_weekly_review` saves the weekly review in the week's note.

Thresholds you can change are in `pkm/agents/day-planner/RULES.md`; a key that isn't there uses the default below:

- `chore_lead_days: 3`: how many days before its date a recurring chore with no completion history is flagged as due soon.

Every example below runs in the test suite (core/tooling/workflow.py).

## day_briefing

Code computes the dates, the chores and the tasks. The notes come back as written: you read them.

**Dates.** The briefing opens with today's date, its ISO week (Monday start, zero-padded) and its vault week. Vault weeks follow the Journals plugin, which names the weekly notes: a week starts on Sunday, week 1 is the week holding January 1st, and the number isn't padded (`2026-W6`, not ISO's `2026-W06`).

On Sunday, October 18, 2026 the vault week has turned to 43, while the ISO week is still 42:

```python
>>> from datetime import date
>>> from core.tooling import Report
>>> from agents.day_planner.lib.day_calendar import iso_week_label, locale_week, vault_week_names, week_start
>>> week_start(date(2026, 10, 18)), locale_week(date(2026, 10, 18)), iso_week_label(date(2026, 10, 18))
(datetime.date(2026, 10, 18), (2026, 43), '2026-W42')
```

December 28, 2025 is in the week holding January 1st, so it is week 1 of 2026. A weekly note is looked up under the plugin's name first, then zero-padded or ISO names, which older notes use:

```python
>>> locale_week(date(2025, 12, 28))
(2026, 1)
>>> vault_week_names(date(2026, 2, 22))
['2026-W9', '2026-W09']
>>> from agents.day_planner.lib.briefing import header
>>> header(date(2026, 10, 16), Report())
'Fri 2026-10-16 · ISO week 2026-W42 · vault week 2026-W42 (Sun 10-11 to Sat 10-17)'
```

**Day counts** are calendar days between two dates, read out with the date; a date in another year shows its year:

```python
>>> from agents.day_planner.lib.day_calendar import relative_phrase
>>> relative_phrase("due", date(2026, 10, 15), date(2026, 10, 16))
'1 day overdue (due Thu 10-15)'
>>> relative_phrase("scheduled", date(2027, 1, 4), date(2026, 10, 16))
'scheduled in 80 days (Mon 2027-01-04)'
```

**Daily notes in the last 7 days** lists which of the 7 days before today have a note in `pkm/vault/journals/fleeting/`.

**This week's focus** is what last vault week's note lists under its "Next week's focus" heading: the daily template embeds that section. When that section is missing, empty or still the template's `A`, `B`, `C`, this week's own note is used, because earlier weekly reviews wrote the coming week's focus there. The lines come back as written. A weekly note without the heading is an issue (partial: the note changed shape); a weekly note that doesn't exist yet isn't.

```python
>>> from agents.day_planner.lib.journal_notes import clip, focus_section, is_template_focus, is_untouched, with_journal_keys
>>> focus_section("### 🌱 Next week's focus\n* Ship the roadmap\n  * talk to Pat\n\n### 🗓️ Book things\n* Fun things")
['* Ship the roadmap', '  * talk to Pat']
>>> is_template_focus(focus_section("### 🌱 Next week's focus\n* A\n* B\n* C\n"))
True
>>> focus_section("## ✅ Wins\n* shipped") is None
True
```

**Yesterday's and today's notes** come back as written, frontmatter and all, unless nothing is written in them yet (below). A note longer than 3,000 characters (`NOTE_CHAR_LIMIT`) is cut after its last whole line within that, and the briefing says how many lines it left out; read the note for the rest. With a limit of 30, this note keeps two lines and leaves one out:

```python
>>> clip("energy_level: 6\n#### Meeting\n* Pat owns the review\n", limit=30)
('energy_level: 6\n#### Meeting', 1)
```

**Today's note, when it doesn't exist yet**, comes with the text to create it: the daily template (`pkm/templates/Fleeting Pages.md`) exactly as written, with the Journals plugin's keys added to its frontmatter. The plugin recognizes its notes by those keys, so it won't add its template to the note a second time, as it did to the 2026-10-03 note. Templater syntax (`<% ... %>`) stays as written, as in the notes the plugin creates. A missing template is an issue (partial).

```python
>>> template = '---\nenergy_level:\n---\n![[2026-W<% tp.date.now("w")-1 %>#Focus]]\n'
>>> print(with_journal_keys(template, "fleeting", date(2026, 10, 16)), end="")
---
energy_level:
journal: fleeting
journal-date: 2026-10-16
---
![[2026-W<% tp.date.now("w")-1 %>#Focus]]
>>> with_journal_keys("### Brain dump\n", "fleeting", date(2026, 10, 16))
'---\njournal: fleeting\njournal-date: 2026-10-16\n---\n### Brain dump\n'
```

**A note nothing is written in yet**, still exactly what the plugin created from the template, comes back as one line instead of the empty template: `created from the template, nothing written yet`. Line endings, trailing whitespace and the plugin's keys, whatever their date, don't count as writing. Anything else does, a frontmatter value such as `energy_level` included, and the note comes back in full; so does every note when the template is missing.

```python
>>> new_note = with_journal_keys(template, "fleeting", date(2026, 10, 15))
>>> is_untouched(new_note, template), is_untouched(new_note.replace("\n", "\r\n") + "  \n", template)
(True, True)
>>> is_untouched(new_note.replace("energy_level:", "energy_level: 6"), template)
False
>>> is_untouched(new_note + "Call Pat\n", template)
False
```

**Recurring chores** are `recurring_chores_due` with `within_days` 0 (below).

**Open tasks due** are open tasks, recurring chores left out, that are due within 7 days (`DUE_SOON_DAYS`) or scheduled on or before today: soonest first, then by priority, at most 8 (`DUE_LIST_LIMIT`) and a count of the rest. A task shows its due date when that falls within the 7 days, else its scheduled date. A task whose date isn't a real date is left out and returned raw in the issues.

```python
>>> from agents.day_planner.lib.project_slate import project_status, task_when
>>> task_when("2026-10-21", None, horizon=date(2026, 10, 23))
('due', datetime.date(2026, 10, 21))
>>> task_when("2026-11-30", "2026-10-01", horizon=date(2026, 10, 23))
('scheduled', datetime.date(2026, 10, 1))
>>> task_when("2026-02-30", None, horizon=date(2026, 10, 23)) is None
True
```

**Active projects** are this year's projects (committed to the year in the project or in its commitments) whose status is executing or planning, by priority. Each shows its count of open tasks and the top 3 (`TASKS_PER_PROJECT`): by priority, then due date, then scheduled date. A project's tasks are those in its file or tagged with one of its aliases, chores left out, and a task kept in two files counts once. A status the sync stored with its tag prefix reads the same:

```python
>>> project_status("s/✊executing")
'executing'
```

**What can't be read.** A missing tasks.db or projects.db makes only its sections "unavailable", and the issue names the file (partial). An unexpected error in one section costs only that section, and the issue names the error.

## recurring_chores_due

A recurring chore is a pending task with 🔁 in tasks.db. Its date is its due date (📅), else its scheduled date (⏳), so a chore with only a scheduled date counts. A chore with neither is listed by name as not judged.

**The window.** Each completed copy of a chore has a lead: its date minus the day it was done, so a negative lead means it was done late. The chore's window opens at its date minus the largest lead among its last 8 completions (`HISTORY_LIMIT`). A chore with no completions opens `chore_lead_days` before its date. No window opens before the chore's start date (🛫).

A credit due October 25, done 21, 20 and 11 days early before, opens on October 4, so on October 16 it is due soon:

```python
>>> from agents.day_planner.lib.recurring_chores import ChoresDue, assess, format_chores, same_chore
>>> today = date(2026, 10, 16)
>>> chore = assess("Dining credit", "⏫", "due", date(2026, 10, 25), leads=[21, 20, 11], today=today, lead_days=3)
>>> chore.opens, chore.state, chore.days_left
(datetime.date(2026, 10, 4), 'soon', 9)
```

With no history and `chore_lead_days` 3, a chore due October 20 opens on October 17, so on October 16 it is quiet:

```python
>>> chore = assess("Water the plants", "", "due", date(2026, 10, 20), leads=[], today=today, lead_days=3)
>>> chore.opens, chore.state
(datetime.date(2026, 10, 17), 'quiet')
```

A start date holds the window shut: scheduled October 18 with no history, it would open on October 15, but it starts on October 17, so on October 16 it isn't due yet:

```python
>>> chore = assess("Book piano lessons", "🔽", "scheduled", date(2026, 10, 18), leads=[], today=today, lead_days=3, start=date(2026, 10, 17))
>>> chore.opens, chore.state
(datetime.date(2026, 10, 17), 'quiet')
```

**States.** Overdue: its date has passed. Due today. Due soon: its window is open. Quiet: not yet. Overdue and due today don't depend on the window: done 18 days late last time and due yesterday, a chore is overdue.

```python
>>> assess("Pay the water bill", "⏫", "due", date(2026, 10, 15), leads=[-18], today=today, lead_days=3).state
'overdue'
```

**Which copies count.** A completed copy belongs to a pending chore when their titles match once lowercased and reduced to letters and digits, or when both recur on the same 🔁 rule and their titles are at least 85% alike (`MATCH_RATIO`), because titles drift. Dropped copies don't count.

```python
>>> same_chore("Pay the water bill every 15th of the month ⏫ 🔁 every month 📅 2026-10-15",
...            "Pay the water bill every 5th of the month (summer) ⏫ 🔁 every month 📅 2026-08-22 ✅ 2026-09-09")
True
>>> same_chore("$15 dining credit monthly 🔁 every month 📅 2026-10-25",
...            "$10 movie credit monthly 🔁 every month 📅 2026-10-25 ✅ 2026-09-01")
False
```

**The lines.** Overdue, due today and due soon are listed with their day counts and how early or late the chore is usually done. Quiet chores are counted, with the next one to open. `within_days` also lists the quiet chores whose date is within that many days:

```python
>>> chores = ChoresDue(today=today, lead_days=3,
...     soon=[assess("Water the plants", "", "due", date(2026, 10, 18), [], today, 3),
...           assess("Dining credit", "⏫", "due", date(2026, 10, 25), [21, 20, 11], today, 3)],
...     quiet=[assess("Book piano lessons", "🔽", "scheduled", date(2026, 10, 21), [], today, 3)])
>>> print(format_chores(chores))
Overdue: none
Due today: none
Due soon:
- Water the plants: due in 2 days (Sun 10-18); no completion history, so flagged 3 days ahead
- Dining credit ⏫: due in 9 days (Sun 10-25); usually done 11–21 days early (3 past completions)
Quiet until their usual window: 1 (next: Book piano lessons, from Sun 10-18)
>>> print(format_chores(chores, within_days=7))
Overdue: none
Due today: none
Due soon:
- Water the plants: due in 2 days (Sun 10-18); no completion history, so flagged 3 days ahead
- Dining credit ⏫: due in 9 days (Sun 10-25); usually done 11–21 days early (3 past completions)
Also due within 7 days, before their usual window:
- Book piano lessons 🔽: scheduled in 5 days (Wed 10-21)
```

**What can't be read.** A missing tasks.db fails the call. A task whose date isn't a real date (2026-02-30) is left out and returned raw in the issues (partial).

## write_weekly_review

The agent passes the approved wins and next week's focus; code picks the note and writes them in.

**Which week.** The review covers the vault week holding the latest Friday, today included: on its Friday run and on Saturday that's the current week; from Sunday to Thursday, the week before. Its wins and focus go into that week's note, since the daily notes embed last week's "Next week's focus". The note has the Journals plugin's name (the first of `vault_week_names`), never an ISO one.

On Friday, October 23, 2026, and still on Sunday the 25th, when week 44 has begun, the review covers week 43, from Sunday the 18th:

```python
>>> from agents.day_planner.lib.journal_notes import weekly_note
>>> from agents.day_planner.lib.weekly_review import review_week
>>> review_week(date(2026, 10, 23)), review_week(date(2026, 10, 25))
(datetime.date(2026, 10, 18), datetime.date(2026, 10, 18))
>>> weekly_note(date(2026, 10, 18))
'vault/journals/weekly/2026-W43.md'
```

**A new note** is the weekly template (`pkm/templates/Weekly Review.md`) exactly as written, Templater syntax included, with the plugin's keys added to its frontmatter: `journal: weekly` and `journal-date`, the week's Sunday. With those keys the plugin takes the note as its own instead of adding its template under it. A note that exists gets the keys if it lacks them. A missing template fails the call.

```python
>>> template = '---\ncssclass: hide-embedded-header\n---\n<< [[<% tp.date.now("gggg-[W]ww", -1, tp.file.title, "gggg-[W]ww") %>]] >>\n'
>>> print(with_journal_keys(template, "weekly", date(2026, 10, 18)), end="")
---
cssclass: hide-embedded-header
journal: weekly
journal-date: 2026-10-18
---
<< [[<% tp.date.now("gggg-[W]ww", -1, tp.file.title, "gggg-[W]ww") %>]] >>
```

**The sections.** Each heading the call names gets its lines in place of the text under it, up to the next heading of any level. A heading is named whatever its case, emoji and punctuation. A section is filled only while it is empty or still the template's `A`, `B`, `C`: one with other text fails the whole call, writing nothing, and the error shows that text, unless `replace` is set. A heading the note doesn't have fails the call too, and the error lists the note's headings. Every other line stays as written.

```python
>>> from agents.day_planner.lib.note_sections import fill_sections
>>> note = "### 🏆 Works wins\n\n### 🌱 Next week's focus\n* A\n* B\n* C\n\n### 🗓️ Book things\n* Fun things\n"
>>> text, filled = fill_sections(note, [("Works wins", "* Shipped Project A"),
...                                     ("Next week's focus", "* Project A\n* Project B\n* Rest")])
>>> print(text, end="")
### 🏆 Works wins
* Shipped Project A
<BLANKLINE>
### 🌱 Next week's focus
* Project A
* Project B
* Rest
<BLANKLINE>
### 🗓️ Book things
* Fun things
>>> fill_sections(text, [("🌱 Next week's focus", "* Project C")])
Traceback (most recent call last):
...
agents.day_planner.lib.note_sections.SectionError: - '🌱 Next week's focus' already has text:
    * Project A
    * Project B
    * Rest
>>> fill_sections(text, [("Wins", "* Shipped")])
Traceback (most recent call last):
...
agents.day_planner.lib.note_sections.SectionError: - no heading 'Wins'
- the note's headings: 🏆 Works wins; 🌱 Next week's focus; 🗓️ Book things
```
