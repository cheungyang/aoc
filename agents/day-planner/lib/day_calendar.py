"""Calendar facts for planning: the day, its two week numberings, and day counts.

Two week numberings are in play, and they differ on Sundays and around New Year:

- ISO weeks (Monday start), which the prompts quote ("ISO week 2026-W42").
- The vault's weeks. Obsidian's Journals plugin names weekly notes by
  formatting the week's first day as `YYYY-[W]w` in moment's "en" locale:
  weeks start on Sunday and week 1 is the week holding January 1st, unpadded
  ("2026-W6"). Templater's `tp.date.now("w")` counts the same way.
"""
from datetime import date, timedelta
from typing import List, Tuple


def week_start(day: date) -> date:
    """The Sunday on or before `day`: the first day of its vault week."""
    return day - timedelta(days=(day.weekday() + 1) % 7)


def locale_week(day: date) -> Tuple[int, int]:
    """(week-year, week) in moment's "en" locale: Sunday start, week 1 holds January 1st."""
    start = week_start(day)
    # The week holding January 1st of year Y ends in Y, so its Saturday names the week-year.
    year = (start + timedelta(days=6)).year
    return year, (start - week_start(date(year, 1, 1))).days // 7 + 1


def iso_week_label(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def vault_week_names(day: date) -> List[str]:
    """Candidate file names (no extension) of the weekly note for `day`'s vault week.

    The Journals plugin's name comes first; zero-padded and ISO-named variants
    follow, because older notes use those.
    """
    start = week_start(day)
    week_year, week = locale_week(start)
    iso_year, iso_week, _ = (start + timedelta(days=1)).isocalendar()  # that week's Monday
    names = [
        f"{start.year}-W{week}",
        f"{week_year}-W{week}",
        f"{start.year}-W{week:02d}",
        f"{week_year}-W{week:02d}",
        f"{iso_year}-W{iso_week:02d}",
    ]
    return list(dict.fromkeys(names))


def past_days(today: date, count: int = 7) -> List[date]:
    """The `count` days before `today`, oldest first."""
    return [today - timedelta(days=n) for n in range(count, 0, -1)]


def short_date(day: date, today: date) -> str:
    """'Thu 10-15', with the year when it is not `today`'s year."""
    if day.year == today.year:
        return day.strftime("%a %m-%d")
    return day.strftime("%a %Y-%m-%d")


def long_date(day: date) -> str:
    return day.strftime("%a %Y-%m-%d")


def days(n: int) -> str:
    return f"{n} day" if n == 1 else f"{n} days"


def relative_phrase(kind: str, day: date, today: date) -> str:
    """How far `day` is from `today`, ready to read out.

    `kind` is "due" or "scheduled": 'due in 3 days (Mon 10-19)',
    '2 days overdue (due Wed 10-14)', 'scheduled 4 days ago (Mon 10-12)'.
    """
    n = (day - today).days
    when = short_date(day, today)
    if kind == "due":
        if n < 0:
            return f"{days(-n)} overdue (due {when})"
        return "due today" if n == 0 else f"due in {days(n)} ({when})"
    if n < 0:
        return f"scheduled {days(-n)} ago ({when})"
    return "scheduled today" if n == 0 else f"scheduled in {days(n)} ({when})"
