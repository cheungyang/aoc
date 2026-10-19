"""Task counts and velocity for one project, from tasks.db rows.

A task belongs to a project when it carries one of the project's alias tags
(#p/10k, or a subtag such as #p/10k/race) or sits in the project's own note:
the rule the project note's dataview query uses.

Velocity counts completions by their completion date. A completed task with
no readable date counts as completed and as `undated`, never in a window.
"""
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

OPEN_TASKS_SHOWN = 10
RECENT_DAYS = 30
RECENT_SHOWN = 5
DUE_SOON_DAYS = 14


def alias_tags(project: Dict[str, Any]) -> List[str]:
    """The project's tag aliases ('#p/10k' -> 'p/10k'); plain-name aliases aren't tags."""
    return [
        str(a).strip().lstrip("#").lower()
        for a in project.get("aliases") or []
        if str(a).strip().startswith("#") and len(str(a).strip()) > 1
    ]


def project_tasks(project: Dict[str, Any], tasks: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    tags = alias_tags(project)
    note = project.get("file_path")

    def belongs(task):
        if note and task.get("source") == note:
            return True
        task_tags = [str(t).lstrip("#").lower() for t in task.get("tags") or []]
        return any(t == a or t.startswith(a + "/") for t in task_tags for a in tags)

    return [t for t in tasks if belongs(t)]


@dataclass(frozen=True)
class TaskStats:
    open: int
    overdue: int
    due_soon: int
    completed: int
    dropped: int
    done_7d: int
    done_30d: int
    done_prev_30d: int  # 31-60 days ago
    done_90d: int
    last_done: Optional[date]
    last_done_title: str
    undated: int = 0  # completed, with no readable completion date

    @property
    def per_week(self) -> float:
        """Completions per week over the last 90 days, to one decimal."""
        return round(self.done_90d * 7 / 90, 1)

    @property
    def trend(self) -> str:
        if not (self.done_30d or self.done_prev_30d):
            return "stalled"
        if self.done_30d == self.done_prev_30d:
            return "steady"
        return "up" if self.done_30d > self.done_prev_30d else "down"

    def as_dict(self, today: date) -> Dict[str, Any]:
        result = {
            "open": self.open,
            "overdue": self.overdue,
            f"due_next_{DUE_SOON_DAYS}d": self.due_soon,
            "completed": self.completed,
            "dropped": self.dropped,
            "velocity": {
                "done_last_7d": self.done_7d,
                "done_last_30d": self.done_30d,
                "done_prior_30d": self.done_prev_30d,
                "done_last_90d": self.done_90d,
                "per_week_90d": self.per_week,
                "trend": self.trend,
            },
        }
        if self.undated:
            result["completed_without_date"] = self.undated
        if self.last_done:
            result["last_completed"] = {
                "date": self.last_done.isoformat(),
                "days_ago": (today - self.last_done).days,
                "title": self.last_done_title,
            }
        return result


def task_stats(tasks: Iterable[Dict[str, Any]], today: date) -> TaskStats:
    open_tasks, completed, dropped, done_dates, undated = [], 0, 0, [], 0
    last_done, last_title = None, ""
    for task in tasks:
        status = task.get("status")
        if status == "todo":
            open_tasks.append(task)
        elif status == "dropped":
            dropped += 1
        elif status == "completed":
            completed += 1
            done = parse_day(task.get("completed_date"))
            if done is None:
                undated += 1
            elif done <= today:
                done_dates.append(done)
                if last_done is None or done > last_done:
                    last_done, last_title = done, task.get("title") or ""

    def done_within(newest_days_ago: int, oldest_days_ago: int) -> int:
        return sum(1 for d in done_dates if newest_days_ago <= (today - d).days < oldest_days_ago)

    dues = [parse_day(t.get("due_date")) for t in open_tasks]
    return TaskStats(
        open=len(open_tasks),
        overdue=sum(1 for d in dues if d and d < today),
        due_soon=sum(1 for d in dues if d and today <= d <= today + timedelta(days=DUE_SOON_DAYS)),
        completed=completed,
        dropped=dropped,
        done_7d=done_within(0, 7),
        done_30d=done_within(0, 30),
        done_prev_30d=done_within(30, 60),
        done_90d=done_within(0, 90),
        last_done=last_done,
        last_done_title=last_title,
        undated=undated,
    )


def unreadable_dates(tasks: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Tasks whose due date (open) or completion date (completed) is set but isn't a date."""
    unreadable = []
    for task in tasks:
        status = task.get("status")
        field = "due_date" if status == "todo" else "completed_date" if status == "completed" else None
        value = task.get(field) if field else None
        if value and parse_day(value) is None:
            unreadable.append(task)
    return unreadable


def open_task_lines(tasks: Iterable[Dict[str, Any]], today: date, limit: int = OPEN_TASKS_SHOWN) -> Tuple[List[str], int]:
    """The open tasks, most urgent first ('⏫ Title (due 2026-10-20, overdue)'), and how many weren't shown."""
    open_tasks = sorted(
        (t for t in tasks if t.get("status") == "todo"),
        key=lambda t: (
            t.get("priority_rank") or 99,
            t.get("due_date") or "9999",
            t.get("scheduled_date") or "9999",
            t.get("title") or "",
        ),
    )
    lines = []
    for task in open_tasks[:limit]:
        line = f"{task['priority']} {task.get('title') or ''}" if task.get("priority") else task.get("title") or ""
        due = parse_day(task.get("due_date"))
        if due:
            line += f" (due {due.isoformat()}{', overdue' if due < today else ''})"
        lines.append(line)
    return lines, max(0, len(open_tasks) - limit)


def recent_completion_lines(tasks: Iterable[Dict[str, Any]], today: date) -> List[str]:
    """Tasks completed in the last RECENT_DAYS, newest first: '2026-10-10 Title'."""
    done = []
    for task in tasks:
        day = parse_day(task.get("completed_date")) if task.get("status") == "completed" else None
        if day and 0 <= (today - day).days < RECENT_DAYS:
            done.append((day, task.get("title") or ""))
    done.sort(key=lambda item: item[0], reverse=True)
    return [f"{day.isoformat()} {title}" for day, title in done[:RECENT_SHOWN]]


def parse_day(value: Any) -> Optional[date]:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None
