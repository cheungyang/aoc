"""MEAL_LOGS.md: what the family ate, and the dinner ideas proposed.

    ### 2026-10-11
    - **晚餐**：香菇雞湯麵（Shiitake chicken noodle soup）
    - **消耗紀錄**：香菇（全完食扣除）、雞腿（消耗 1 份，剩餘 1 份）

    ### 2026-10-12 晚餐提案 (Dinner Recommendations)
    - **提案一（清爽家常）**：清蒸鱈魚 ＋ 蒜炒菠菜 ＋ 味噌豆腐湯

Sections are dated headings; entries are '- **label**：text' lines. The file is
mostly chronological but not strictly (a date can appear twice, ideas can come
before or after that day's meals), so readers sort by the heading's date.

Under an entry, indented bullets are its details (an idea's ingredients and
steps); notes ('> ...') and blank lines are skipped. `unrecognized` lists the
other lines in the days a reader covers, so a tool can return them raw.
"""
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, Dict, List, Optional

from pydantic import BaseModel, Field

TITLE = "# 膳食日誌 (Meal Logs)"
IDEAS_HEADING = "晚餐提案 (Dinner Recommendations)"
CONSUMED_LABEL = "消耗紀錄"
DINNER = "晚餐"
# Entry labels that are meals (晚餐, 午餐, 備餐, 料理紀錄, ...), not stock records.
_MEAL_WORDS = ("餐", "料理", "外食")
# A meal line's trailing note about stock ('（扣減排骨 1 份）'): noise in meal history.
_STOCK_NOTE = re.compile(r"\s*[（(][^（）()]*(?:剩餘|扣減|扣除|消耗|完食|用罄|食用完畢)[^（）()]*[）)]\s*$")
_MAX_MEAL_CHARS = 80

_HEADER = re.compile(r"^#{2,6}\s*(\d{4})-(\d{1,2})-(\d{1,2})(.*?)\s*$")
_ANY_HEADING = re.compile(r"^ {0,3}(#{1,6})\s")
_ENTRY = re.compile(r"^\s*[-*]\s+\*\*(.+?)\*\*\s*[：:]\s*(.*?)\s*$")
_DETAIL = re.compile(r"^\s+[-*+]\s")
_NOTE = re.compile(r"^\s*(?:>|<!--)")
# '###2026-10-15': a date heading without its space, which no reader sees as one.
_BROKEN_HEADING = re.compile(r"^ {0,3}#{1,6}\d")
_CHINESE_DIGITS = "零一二三四五六七八九"


class MealLog(BaseModel):
    """A meal to record in MEAL_LOGS.md."""

    dishes: str = Field(description=(
        "What was eaten, in Traditional Chinese, e.g. '番茄炒蛋、蒜炒青菜' or '外食（拉麵）'."))
    label: str = Field("晚餐", description="晚餐, 午餐, 早餐, 點心 or 備餐 (meal prep).")
    days_ago: int = Field(0, ge=0, le=7, description="0 for today, 1 for yesterday.")


class MealIdea(BaseModel):
    """A dinner idea being proposed, for MEAL_LOGS.md."""

    theme: str = Field(description="A few words in Traditional Chinese, e.g. '清爽海味'.")
    dishes: List[str] = Field(description="Dish names in Traditional Chinese.")


@dataclass
class _Section:
    day: date
    rest: str    # the heading after its date
    start: int   # index of the heading line
    end: int     # index after the section's last line

    @property
    def is_ideas(self) -> bool:
        return "提案" in self.rest or "recommend" in self.rest.casefold()


def recent_meals(text: str, today: date, days: int = 14) -> List[dict]:
    """Meals of the last `days` days (today included), newest first:
    [{"date", "days_ago", "meals": ["晚餐：…", …]}]."""
    lines = text.splitlines()
    since = today - timedelta(days=days - 1)
    by_day: Dict[date, List[str]] = {}
    for section in _sections(lines):
        if section.is_ideas or not since <= section.day <= today:
            continue
        meals = by_day.setdefault(section.day, [])
        for label, body in _entries(lines, section):
            entry = f"{label}：{_compact(body)}"
            if any(word in label for word in _MEAL_WORDS) and entry not in meals:
                meals.append(entry)
    return [{"date": d.isoformat(), "days_ago": (today - d).days, "meals": by_day[d]}
            for d in sorted(by_day, reverse=True) if by_day[d]]


def last_ideas(text: str, today: date, days: int = 14) -> Optional[dict]:
    """The latest dinner ideas logged in the last `days` days: {"date", "days_ago", "ideas"}."""
    lines = text.splitlines()
    since = today - timedelta(days=days - 1)
    latest = None
    for section in _sections(lines):
        if section.is_ideas and since <= section.day <= today and (latest is None or section.day >= latest.day):
            latest = section
    if latest is None:
        return None
    ideas = [body for _, body in _entries(lines, latest)]
    return {"date": latest.day.isoformat(), "days_ago": (today - latest.day).days, "ideas": ideas} if ideas else None


def logged_meal(text: str, day: date, label: str = DINNER) -> Optional[str]:
    """`day`'s first `label` entry (e.g. tonight's dinner) as '晚餐：…', or None."""
    lines = text.splitlines()
    for section in _sections(lines):
        if section.day == day and not section.is_ideas:
            for entry_label, body in _entries(lines, section):
                if label in entry_label:
                    return f"{entry_label}：{_compact(body)}"
    return None


def unrecognized(text: str, today: date, days: int) -> List[str]:
    """The lines a reader of the last `days` days (today included) skips, as written.

    In those days' sections: every line that isn't an entry, a detail under
    one, a note or blank. Anywhere in the file: the lines whose day can't be
    told (under a heading with no date, the heading included, or above the
    first one) and date headings missing their space ('###2026-10-15').
    """
    since = today - timedelta(days=days - 1)
    found: List[str] = []
    dated, in_window, pending = False, False, None  # pending: an undated heading not yet listed
    for line in text.splitlines():
        heading = _ANY_HEADING.match(line)
        if heading:
            day = _heading_date(line) if len(heading.group(1)) >= 2 else None
            dated, in_window = day is not None, day is not None and since <= day <= today
            pending = line if len(heading.group(1)) >= 2 and day is None else None
            continue
        if not line.strip() or _NOTE.match(line):
            continue
        if _BROKEN_HEADING.match(line):
            found.append(line)
        elif not dated:
            if pending is not None:
                found.append(pending)
                pending = None
            found.append(line)
        elif in_window and not (_ENTRY.match(line) or _DETAIL.match(line)):
            found.append(line)
    return found


def log_meal(text: str, day: date, label: str, dishes: str, consumed: List[str] = ()) -> str:
    """`text` with the meal (and what it used from stock) under `day`'s heading."""
    entries = [f"- **{_one_line(label) or '晚餐'}**：{_one_line(dishes)}"]
    if consumed:
        entries.append(f"- **{CONSUMED_LABEL}**：{'、'.join(consumed)}")
    heading = f"### {day.isoformat()}"
    return _insert(text, heading, entries, lambda s: s.day == day and not s.rest)


def log_ideas(text: str, day: date, ideas: List[MealIdea]) -> str:
    """`text` with `ideas` under `day`'s dinner-ideas heading, numbered after any already there."""
    lines = text.splitlines()
    existing = [s for s in _sections(lines) if s.day == day and s.is_ideas]
    count = sum(1 for _ in _entries(lines, existing[-1])) if existing else 0
    entries = []
    for n, idea in enumerate(ideas, start=count + 1):
        theme = _one_line(idea.theme)
        label = f"提案{_chinese_number(n)}" + (f"（{theme}）" if theme else "")
        entries.append(f"- **{label}**：{' ＋ '.join(_one_line(d) for d in idea.dishes if _one_line(d))}")
    heading = f"### {day.isoformat()} {IDEAS_HEADING}"
    return _insert(text, heading, entries, lambda s: s.day == day and s.is_ideas)


# ---------------------------------------------------------------------- internals

def _sections(lines: List[str]) -> List[_Section]:
    sections, current = [], None
    for i, line in enumerate(lines):
        if not _ANY_HEADING.match(line):
            continue
        if current:
            current.end = i
            current = None
        m = _HEADER.match(line.strip())
        if m:
            try:
                day = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                continue
            current = _Section(day, m.group(4).strip(), i, len(lines))
            sections.append(current)
    return sections


def _heading_date(line: str) -> Optional[date]:
    """The date a section heading names; None for no date or an impossible one ('2026-02-30')."""
    m = _HEADER.match(line.strip())
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _entries(lines: List[str], section: _Section):
    for line in lines[section.start + 1:section.end]:
        m = _ENTRY.match(line)
        if m:
            yield m.group(1).strip(), m.group(2)


def _insert(text: str, heading: str, entries: List[str], matches: Callable[[_Section], bool]) -> str:
    """Adds entry lines to the last section `matches` accepts, after its last line, or
    under a new `heading` at the end of the file."""
    lines = text.splitlines(keepends=True)
    newline = "\r\n" if lines and lines[0].endswith("\r\n") else "\n"
    new = [entry + newline for entry in entries]
    targets = [s for s in _sections([line.rstrip("\r\n") for line in lines]) if matches(s)]
    if targets:
        pos = targets[-1].end
        while pos > targets[-1].start + 1 and not lines[pos - 1].strip():
            pos -= 1
        lines[pos - 1] = _ended(lines[pos - 1], newline)
        lines[pos:pos] = new
        return "".join(lines)
    if not lines:
        lines = [TITLE + newline]
    lines[-1] = _ended(lines[-1], newline)
    if lines[-1].strip():
        lines.append(newline)
    return "".join(lines + [heading + newline] + new)


def _compact(body: str) -> str:
    body = _STOCK_NOTE.sub("", body).strip()
    return body if len(body) <= _MAX_MEAL_CHARS else body[:_MAX_MEAL_CHARS - 1].rstrip() + "…"


def _one_line(text: str) -> str:
    return " ".join(str(text or "").split())


def _ended(line: str, newline: str) -> str:
    return line if line.endswith("\n") else line + newline


def _chinese_number(n: int) -> str:
    """1 -> 一, 10 -> 十, 12 -> 十二, 23 -> 二十三 (digits past 99)."""
    if n < 10:
        return _CHINESE_DIGITS[n]
    if n < 100:
        tens, ones = divmod(n, 10)
        return (_CHINESE_DIGITS[tens] if tens > 1 else "") + "十" + (_CHINESE_DIGITS[ones] if ones else "")
    return str(n)
