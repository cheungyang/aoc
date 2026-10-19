# excursion-planner: how its tools work

## weekend_availability

`weekend_availability(weeks)` reads your primary Google Calendar once and returns
the next weekends: each day's events, its free windows and its free hours, in
local time. It doesn't pick what to pitch; that's the agent's call (AGENTS.md).
When free time counts is set in RULES.md: `free_from`, `free_until` and
`min_gap_hours`.

### Which weekends

This weekend counts until its Saturday is over: from Monday to Saturday it comes
first, and on a Sunday next weekend does. `weeks` asks for 1 to 8 weekends
(default 3); a number outside that range is brought inside it. Each weekend is
labelled by calendar week, Monday to Sunday: "this weekend", "next weekend",
"in 2 weeks".

On Friday Oct 16, the next 3 weekends start Oct 17, 24 and 31. On Sunday Oct 18,
the first one starts Oct 24:

```python
>>> from datetime import date, datetime
>>> from zoneinfo import ZoneInfo
>>> from core.tooling import Report
>>> from agents.excursion_planner.lib.events import parse_events
>>> from agents.excursion_planner.lib.weekends import (
...     day_availability, free_time, query_range, upcoming_saturdays, weekend_availability)
>>> upcoming_saturdays(date(2026, 10, 16), 3)
[datetime.date(2026, 10, 17), datetime.date(2026, 10, 24), datetime.date(2026, 10, 31)]
>>> upcoming_saturdays(date(2026, 10, 18), 1)
[datetime.date(2026, 10, 24)]
```

### What it reads

One gog call for the primary calendar, from the first Saturday 00:00 to the
Monday 00:00 after the last weekend, local time, for up to 100 events per
weekend. In Los Angeles, 3 weekends from Friday Oct 16 run from Oct 17 07:00 UTC
to Nov 2 08:00 UTC; the clocks fall back in between:

```python
>>> la = ZoneInfo("America/Los_Angeles")
>>> start, end = query_range(date(2026, 10, 16), 3, la)
>>> print(start, end)
2026-10-17 07:00:00+00:00 2026-11-02 08:00:00+00:00
```

What it can't read, it says:
- The calendar can't be read (gog fails, or its reply holds no event list): the
  reply is `failed` and its issues say why. There is nothing to plan from.
- An event it can't read (no start, a time it can't parse, an unknown time zone)
  is left out of the free windows and comes back raw in the issues: `partial`.
- The calendar holds more events than were fetched: `partial`, since some may be
  missing.

A dinner whose time reads "Saturday 7pm" can't be placed, so it comes back raw:

```python
>>> report = Report()
>>> parse_events({"events": [{"summary": "Dinner", "start": {"dateTime": "Saturday 7pm"}}]}, la, report)
[]
>>> report.status
'partial'
>>> print(report.render_issues())
- Google Calendar (primary): 1 line not recognized
    {"summary": "Dinner", "start": {"dateTime": "Saturday 7pm"}}
```

A reply that isn't an event list fails; a reply with a next page is partial:

```python
>>> report = Report()
>>> parse_events("Calendar not found", la, report)
[]
>>> print(report.render_issues())
- Google Calendar (primary): gog's reply holds no event list: "Calendar not found" (required)
>>> report = Report()
>>> parse_events({"events": [], "nextPageToken": "next"}, la, report, limit=300)
[]
>>> print(report.status, report.issue_lines())
partial ['Google Calendar (primary): it holds more than the 300 events fetched; some may be missing']
```

### Which events count

Cancelled events, invitations you declined and working-location markers
("working from home") are left out; explain mode lists them. An event with no
title shows as "(busy)": calendars shared as free/busy only send none. A time
without an offset reads in the event's own time zone, else in yours. An event
with no end counts as a moment (timed) or a single day (all-day).

```python
>>> calendar = {"events": [
...     {"summary": "Soccer", "start": {"dateTime": "2026-10-24T09:00:00-07:00"},
...      "end": {"dateTime": "2026-10-24T11:00:00-07:00"}},
...     {"summary": "Brunch", "status": "cancelled", "start": {"dateTime": "2026-10-25T10:00:00-07:00"},
...      "end": {"dateTime": "2026-10-25T12:00:00-07:00"}},
...     {"summary": "Team dinner", "attendees": [{"self": True, "responseStatus": "declined"}],
...      "start": {"dateTime": "2026-10-24T18:00:00-07:00"}, "end": {"dateTime": "2026-10-24T21:00:00-07:00"}},
...     {"summary": "Home", "eventType": "workingLocation", "start": {"date": "2026-10-26"},
...      "end": {"date": "2026-10-27"}},
...     {"start": {"dateTime": "2026-10-25T14:00:00Z"}, "end": {"dateTime": "2026-10-25T15:00:00Z"}},
... ]}
>>> report = Report()
>>> [(event.title, event.busy) for event in parse_events(calendar, la, report)]
[('Soccer', True), ('(busy)', True)]
>>> report.traces
["left out: 'Brunch' (cancelled), 'Team dinner' (declined), 'Home' (working location)"]
```

### Free windows

Free time counts from `free_from` to `free_until`, local time (08:00 and 22:00
by default), in the gaps between busy events. Only gaps of at least
`min_gap_hours` count (1 by default); shorter ones are left out. An event shown
as free is listed with "(free)" and blocks nothing. Today's free time starts
from now, rounded up to the next half hour. `free_hours` adds up the free
windows, rounded to 0.1 h. The reply's `free_time` says which values were used.

Saturday Oct 24 has soccer 09:00–11:00, lunch 12:30–14:00, a market shown as
free 15:00–16:00 and a call 17:00–17:30. With the defaults, four windows make
10 h; the market blocks nothing:

```python
>>> saturday = parse_events({"events": [
...     {"summary": "Soccer", "start": {"dateTime": "2026-10-24T09:00:00-07:00"},
...      "end": {"dateTime": "2026-10-24T11:00:00-07:00"}},
...     {"summary": "Lunch", "start": {"dateTime": "2026-10-24T12:30:00-07:00"},
...      "end": {"dateTime": "2026-10-24T14:00:00-07:00"}},
...     {"summary": "Market", "transparency": "transparent", "start": {"dateTime": "2026-10-24T15:00:00-07:00"},
...      "end": {"dateTime": "2026-10-24T16:00:00-07:00"}},
...     {"summary": "Call", "start": {"dateTime": "2026-10-24T17:00:00-07:00"},
...      "end": {"dateTime": "2026-10-24T17:30:00-07:00"}},
... ]}, la, Report())
>>> friday_evening = datetime(2026, 10, 16, 18, 0, tzinfo=la)
>>> defaults = free_time({"free_from": "08:00", "free_until": "22:00", "min_gap_hours": 1.0}, Report())
>>> defaults.describe()
'08:00–22:00, gaps of 1 h or more'
>>> day_availability(date(2026, 10, 24), saturday, friday_evening, defaults, Report())
{'date': '2026-10-24', 'day': 'Sat Oct 24',
 'events': ['09:00–11:00 Soccer', '12:30–14:00 Lunch', '15:00–16:00 Market (free)', '17:00–17:30 Call'],
 'free': ['08:00–09:00', '11:00–12:30', '14:00–17:00', '17:30–22:00'], 'free_hours': 10.0}
```

With `min_gap_hours: 2`, the hour before soccer and the 90 minutes before lunch
are too short. Explain mode shows the sum and what was left out:

```python
>>> two_hours = free_time({"min_gap_hours": 2}, Report())
>>> report = Report()
>>> day_availability(date(2026, 10, 24), saturday, friday_evening, two_hours, report)["free"]
['14:00–17:00', '17:30–22:00']
>>> report.traces
['Sat Oct 24: 7.5 h free = 14:00–17:00 (3 h) + 17:30–22:00 (4.5 h), counting 08:00–22:00; under 2 h, so left out: 08:00–09:00, 11:00–12:30']
```

On that Saturday at 15:10, free time starts at 15:30:

```python
>>> day_availability(date(2026, 10, 24), saturday, datetime(2026, 10, 24, 15, 10, tzinfo=la), defaults, Report())["free"]
['15:30–17:00', '17:30–22:00']
```

An event that runs past midnight is cut at the day's edge; "…" marks the end
that falls on another day. Camping from Friday 18:00 to Saturday 10:00:

```python
>>> camping = parse_events([{"summary": "Camping", "start": {"dateTime": "2026-10-23T18:00:00-07:00"},
...                          "end": {"dateTime": "2026-10-24T10:00:00-07:00"}}], la, Report())
>>> day = day_availability(date(2026, 10, 24), camping, friday_evening, defaults, Report())
>>> day["events"], day["free"]
(['…–10:00 Camping'], ['10:00–22:00'])
```

### All-day events

All-day events are listed under `all_day`. One blocks the whole day only when
it's marked busy, shown as "(busy)"; one shown as free blocks nothing. An event
over several days covers each day up to the day before its end date (Google's
end date is exclusive).

A street festival over Oct 24–25 shown as free leaves Saturday free; a day trip
marked busy on Sunday leaves no free time:

```python
>>> all_day = parse_events({"events": [
...     {"summary": "Street festival", "transparency": "transparent",
...      "start": {"date": "2026-10-24"}, "end": {"date": "2026-10-26"}},
...     {"summary": "Day trip", "start": {"date": "2026-10-25"}, "end": {"date": "2026-10-26"}},
... ]}, la, Report())
>>> day_availability(date(2026, 10, 24), all_day, friday_evening, defaults, Report())
{'date': '2026-10-24', 'day': 'Sat Oct 24', 'all_day': ['Street festival'], 'free': ['08:00–22:00'], 'free_hours': 14.0}
>>> day_availability(date(2026, 10, 25), all_day, friday_evening, defaults, Report())
{'date': '2026-10-25', 'day': 'Sun Oct 25', 'all_day': ['Street festival', 'Day trip (busy)'], 'free': [], 'free_hours': 0.0}
```

### Clock changes

The arithmetic runs on UTC instants, so the 25-hour Sunday when the clocks fall
back still adds up, and that weekend gets a `note`. Brunch on Sun Nov 1,
10:00–12:00 PST:

```python
>>> brunch = parse_events([{"summary": "Brunch", "start": {"dateTime": "2026-11-01T10:00:00-08:00"},
...                         "end": {"dateTime": "2026-11-01T12:00:00-08:00"}}], la, Report())
>>> plan = weekend_availability(brunch, friday_evening, 3, defaults, Report())
>>> [(weekend["weekend"], weekend["when"]) for weekend in plan["weekends"]]
[('Oct 17–18', 'this weekend'), ('Oct 24–25', 'next weekend'), ('Oct 31–Nov 1', 'in 2 weeks')]
>>> plan["weekends"][2]["note"]
'Sun Nov 1: clocks fall back 1 h at 02:00 (PDT → PST)'
>>> sunday = plan["weekends"][2]["days"][1]
>>> sunday["free"], sunday["free_hours"]
(['08:00–10:00', '12:00–22:00'], 12.0)
```

### RULES.md values that don't read

`free_from` and `free_until` are 24-hour times ("08:00", "8:30" or "8"), and
`free_from` must come first. `min_gap_hours` is between 0 and 24. A value that
breaks this is an issue (`partial`), and its default applies:

```python
>>> report = Report()
>>> free_time({"free_from": "8am", "free_until": "22:00", "min_gap_hours": 1.0}, report).describe()
'08:00–22:00, gaps of 1 h or more'
>>> print(report.render_issues())
- RULES.md: free_from: '8am' isn't a time like 08:00; using 08:00
>>> report = Report()
>>> free_time({"free_from": "23:00", "free_until": "7"}, report).describe()
'08:00–22:00, gaps of 1 h or more'
>>> print(report.render_issues())
- RULES.md: free_from 23:00 isn't before free_until 07:00; using 08:00–22:00
```
