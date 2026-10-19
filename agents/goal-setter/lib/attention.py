"""Which executing projects need coaching, and everything a session on one needs.

`project_context(name, ...)` gathers one project's details, task stats and
velocity, open and recently done tasks, and coaching history (see coaching.py)
in one read. `projects_needing_attention(...)` ranks the executing projects by
the signals `assess` finds, weighted by the rules (lib/rules.py over RULES.md).
Plain functions over a read-only snapshot of projects.db and tasks.db; what
they couldn't read goes in the `Report` they are handed. The reasons are facts;
which project to coach, and how, is the coach_project skill's call.
"""
import os
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.tooling import Report, rules_path
from agents.goal_setter.lib.coaching import CoachingHistory, coaching_history, load_dossiers
from agents.goal_setter.lib.snapshot import Snapshot
from agents.goal_setter.lib.velocity import (
    DUE_SOON_DAYS,
    TaskStats,
    alias_tags,
    open_task_lines,
    project_tasks,
    recent_completion_lines,
    task_stats,
    unreadable_dates,
)

AGENT_ID = "goal-setter"
# The Tasks plugin's priorities by rank, as RULES.md's `priority_points` names them.
PRIORITY_SIGNS = {1: "🔺", 2: "⏫", 3: "🔼", 4: "🔽", 5: "⏬"}
NEVER = 10**6  # days since coached, for a project never coached: it sorts first among ties


class ProjectNotFound(LookupError):
    """No single project matches the name; the message lists what does."""


@dataclass(frozen=True)
class Attention:
    project: Dict[str, Any]
    score: int
    reasons: Tuple[str, ...]
    points: Tuple[str, ...]  # what the score adds up, e.g. ("never_coached 3", "⏫ 2")
    stats: TaskStats
    last_coached: Optional[date]
    rank: int  # the priority rank that counts this year: 99 once the commitment lapsed

    def as_dict(self) -> Dict[str, Any]:
        last = self.last_coached
        return {
            "name": self.project["name"],
            "priority": self.project.get("priority"),
            "score": self.score,
            "reasons": list(self.reasons),
            "last_coached": last.isoformat() if last else None,
            "open_tasks": self.stats.open,
            "done_last_30d": self.stats.done_30d,
        }


def project_context(name: str, today: date, snapshot: Snapshot, report: Report) -> Dict[str, Any]:
    """One project's details, task stats and velocity, tasks, and coaching history.
    Raises ProjectNotFound when `name` matches no project, or several."""
    project = find_project(name, snapshot.projects)
    tasks = project_tasks(project, snapshot.tasks)
    report_unreadable_dates(tasks, snapshot.pkm_dir, report)
    history = coaching_history(project, snapshot.pkm_dir, load_dossiers(snapshot.pkm_dir, report), report)
    stats = task_stats(tasks, today)
    open_lines, hidden = open_task_lines(tasks, today)
    context = {
        "project": _details(project),
        "coaching": history.as_dict(today),
        "tasks": stats.as_dict(today),
        "open_tasks": open_lines,
    }
    if hidden:
        context["open_tasks_not_shown"] = hidden
    recent = recent_completion_lines(tasks, today)
    if recent:
        context["recently_completed"] = recent
    _trace_context(history, stats, today, report)
    return context


def projects_needing_attention(today: date, rules: Dict[str, Any], snapshot: Snapshot, report: Report,
                               limit: int = 5) -> Dict[str, Any]:
    """The executing projects that need coaching, most first, each with its reasons."""
    ranked, resting = rank_projects(snapshot, today, rules, report)
    result = {
        "executing": sum(1 for p in snapshot.projects if p.get("status") == "executing"),
        "projects": [a.as_dict() for a in ranked[:limit]],
    }
    if len(ranked) > limit:
        result["more"] = [a.project["name"] for a in ranked[limit:]]
    if resting:
        result[f"coached_in_last_{rules['rest_days']}d"] = resting
    return result


def rank_projects(snapshot: Snapshot, today: date, rules: Dict[str, Any],
                  report: Report) -> Tuple[List[Attention], List[str]]:
    """(projects that need attention, most first; names of those resting).

    Every executing project gets one explain line: its points, its rest, or no signal."""
    _check_priority_points(rules, report)
    dossiers = load_dossiers(snapshot.pkm_dir, report)
    candidates, resting, unreadable = [], [], {}
    for project in snapshot.projects:
        if project.get("status") != "executing":
            continue
        name = project["name"]
        history = coaching_history(project, snapshot.pkm_dir, dossiers, report)
        tasks = project_tasks(project, snapshot.tasks)
        unreadable.update((task.get("id") or id(task), task) for task in unreadable_dates(tasks))
        stats = task_stats(tasks, today)
        last = history.last_coached
        if is_resting(last, today, rules):
            resting.append(name)
            report.trace(f"{name}: rests, last coached {last}, {(today - last).days} days ago "
                         f"(rest_days {rules['rest_days']})")
            continue
        attention = assess(project, stats, last, today, rules)
        if attention is None:
            report.trace(f"{name}: nothing calls for coaching")
            continue
        candidates.append(attention)
        report.trace(f"{name}: {attention.score} = {' + '.join(attention.points) or 'no points'} "
                     f"(last coached {last or 'never'}; last dated completion {stats.last_done or 'none'})")
    report_unreadable_dates(unreadable.values(), snapshot.pkm_dir, report)
    return order(candidates, today), resting


def is_resting(last_coached: Optional[date], today: date, rules: Dict[str, Any]) -> bool:
    """Coached fewer than `rest_days` days ago: the project isn't ranked."""
    return last_coached is not None and (today - last_coached).days < rules["rest_days"]


def assess(project: Dict[str, Any], stats: TaskStats, last_coached: Optional[date], today: date,
           rules: Dict[str, Any]) -> Optional[Attention]:
    """Why a project needs coaching, scored with `rules`; None when nothing calls for it."""
    reasons: List[str] = []
    points: List[Tuple[str, int]] = []
    days = (today - last_coached).days if last_coached else None
    if days is None:
        reasons.append("never coached")
        points.append(("never_coached", rules["never_coached"]))
    elif days >= rules["coach_every_days"]:
        reasons.append(f"last coached {days} days ago")
        key = "long_gap" if days >= rules["long_gap_days"] else "due_again"
        points.append((key, rules[key]))
    idle = (today - stats.last_done).days if stats.last_done else None
    if stats.open and (idle is None or idle >= rules["stall_days"]):
        if idle is None:
            reasons.append(f"stalled: no dated completion, {stats.open} open")
        else:
            reasons.append(f"stalled: nothing done in {idle} days, {stats.open} open")
        points.append(("stalled", rules["stalled"]))
    if not stats.open:
        reasons.append("no open tasks")
        points.append(("no_open_tasks", rules["no_open_tasks"]))
    if stats.overdue:
        reasons.append(f"{stats.overdue} overdue task{'s' if stats.overdue > 1 else ''}")
        points.append(("overdue", rules["overdue"]))
    year = project.get("commitment_year")
    lapsed = bool(year) and year < today.year
    if lapsed:
        reasons.append(f"committed for {year}, not {today.year}")
    if not reasons:
        return None
    # A priority set for a past year's commitment doesn't rank this year.
    rank = 99 if lapsed else (project.get("priority_rank") or 99)
    sign = PRIORITY_SIGNS.get(rank)
    if sign:
        points.append((sign, _priority_points(rules).get(sign, 0)))
    score = sum(value for _, value in points)
    return Attention(project, score, tuple(reasons), tuple(f"{key} {value}" for key, value in points),
                     stats, last_coached, rank)


def order(attentions: Iterable[Attention], today: date) -> List[Attention]:
    """Most points first; then the higher priority; then the longest since coached
    (never coached first); then the name."""
    return sorted(attentions, key=lambda a: (
        -a.score,
        a.rank,
        -((today - a.last_coached).days if a.last_coached else NEVER),
        a.project["name"].lower(),
    ))


def report_unreadable_dates(tasks: Iterable[Dict[str, Any]], pkm_dir: str, report: Report) -> None:
    """Tasks whose date can't be read come back as their raw lines, by source file (partial)."""
    by_source: Dict[str, List[str]] = {}
    for task in unreadable_dates(tasks):
        by_source.setdefault(task.get("source") or "", []).append(task.get("raw_line") or task.get("title") or "")
    for source, lines in by_source.items():
        report.unparsed(os.path.join(pkm_dir, source) if source else "tasks.db", lines)


def find_project(name: str, projects: List[Dict[str, Any]]) -> Dict[str, Any]:
    """By exact name, id, note file name or alias tag; then by a unique partial match,
    ignoring spaces and punctuation (speech gives '10 k' for '10k')."""
    query = _normalise(name).lstrip("#")
    if not query:
        raise ProjectNotFound("Give a project name.")
    for project in projects:
        keys = {_normalise(project.get("name")), _normalise(project.get("id")), _note_name(project)}
        tags = alias_tags(project)
        keys.update(tags)
        keys.update(t.split("/", 1)[1] for t in tags if "/" in t)
        if query in keys:
            return project
    squashed = _squash(query)
    matches = [
        p for p in projects
        if squashed and any(squashed in _squash(key) for key in (p.get("name"), _note_name(p), *alias_tags(p)))
    ]
    executing = [p for p in matches if p.get("status") == "executing"]
    if len(matches) == 1 or len(executing) == 1:
        return (executing or matches)[0]
    if matches:
        names = ", ".join(sorted(p["name"] for p in (executing or matches)))
        raise ProjectNotFound(f"'{name}' matches several projects: {names}. Ask which one.")
    names = ", ".join(sorted(p["name"] for p in projects if p.get("status") == "executing"))
    raise ProjectNotFound(f"No project matches '{name}'. Executing projects: {names}.")


def _priority_points(rules: Dict[str, Any]) -> Dict[str, int]:
    """`priority_points` by sign. An emoji keyboard may add a variation selector (U+FE0F) after a
    sign; it is the same sign."""
    return {_sign(name): value for name, value in rules["priority_points"].items()}


def _check_priority_points(rules: Dict[str, Any], report: Report) -> None:
    """A `priority_points` entry that names no priority counts for nothing: say so."""
    unknown = [name for name in rules["priority_points"] if _sign(name) not in PRIORITY_SIGNS.values()]
    if unknown:
        verb = "is not a priority" if len(unknown) == 1 else "are not priorities"
        report.problem(rules_path(AGENT_ID), f"priority_points: {', '.join(unknown)} {verb} "
                                             f"({' '.join(PRIORITY_SIGNS.values())}); ignored")


def _sign(name: str) -> str:
    return name.replace("\ufe0f", "").strip()


def _trace_context(history: CoachingHistory, stats: TaskStats, today: date, report: Report) -> None:
    """The inputs behind project_context's numbers, for explain mode."""
    last = history.last_coached
    if last:
        dossier = history.dossier.last_coached if history.dossier else None
        report.trace(f"last coached {last}: the later of the dossier's last_coached ({dossier or 'none'}) and "
                     f"the note's latest session ({history.session_date or 'none'}); {(today - last).days} days "
                     f"before {today}")
    else:
        report.trace("never coached: no last_coached in a dossier, no session in the note")
    report.trace(f"velocity: {stats.completed - stats.undated} of {stats.completed} completed tasks have a date; "
                 f"done in the last 7/30/90 days {stats.done_7d}/{stats.done_30d}/{stats.done_90d}, the 30 days "
                 f"before {stats.done_prev_30d}; per week {stats.done_90d} x 7 / 90 = {stats.per_week}")
    report.trace(f"overdue: {stats.overdue} of {stats.open} open tasks were due before {today}; "
                 f"{stats.due_soon} are due by {today + timedelta(days=DUE_SOON_DAYS)}")


def _details(project: Dict[str, Any]) -> Dict[str, Any]:
    tags = alias_tags(project)
    details = {
        "name": project["name"],
        "status": project.get("status"),
        "priority": project.get("priority"),
        "commitment_year": project.get("commitment_year"),
        "category": project.get("category"),
        "alias": f"#{tags[0]}" if tags else None,
        "note": f"pkm/{project['file_path']}" if project.get("file_path") else None,
        "started": project.get("start_date"),
        "last_reviewed": project.get("last_reviewed"),
    }
    return {key: value for key, value in details.items() if value is not None}


def _note_name(project: Dict[str, Any]) -> str:
    path = project.get("file_path") or ""
    base = path.rsplit("/", 1)[-1]
    return _normalise(base[:-3] if base.endswith(".md") else base)


def _normalise(value: Any) -> str:
    return " ".join(str(value or "").lower().split())


def _squash(value: Any) -> str:
    return re.sub(r"[^0-9a-z]+", "", str(value or "").lower())
