"""The follow_up tool (tools/follow_up.py): open, extend, close and list, for the calling agent only."""
import json
from datetime import datetime, time, timedelta

import pytest

from core.config.settings import Config
from core.storage.follow_ups import Check, Closer, FollowUpStore, Kind, Status
from core.util.local_time import get_local_timezone
from tests.helpers import execution_context, envelope
from tools import follow_up as tool_module
from tools.follow_up import MIN_AT_S, NOTES_CHARS, cadence_text, follow_up, parse_every

TODAY = datetime.now(get_local_timezone()).date()
# The tool's clock in these tests: 09:00 on TODAY, so a check due later today still comes before it ends.
NOW = datetime.combine(TODAY, time(9, 0), get_local_timezone()).timestamp()
MODEL = json.dumps({"type": "model", "what": "new award space SFO to MAD in business"})


@pytest.fixture(autouse=True)
def same_moment(monkeypatch):
    """The tool's clock is NOW, on the TODAY the cases were written from, even when a run crosses midnight."""
    monkeypatch.setattr(tool_module, "now", lambda: NOW)


@pytest.fixture
def live():
    with Config().overriding(proactive_delivery=True):
        yield


def _as(agent_id="reward-travel", channel="lifestyle-perk"):
    """A turn of `agent_id` in its channel, as the Discord runner starts one."""
    return execution_context(agent_id=agent_id, source="discord", channel=channel, stateless=False)


def _call(**args):
    return envelope(follow_up.invoke(args))


def _open(**args):
    values = dict(op="open", goal="Business award seats SFO→MAD for the Spain trip", check=MODEL, every="12h",
                  until=(TODAY + timedelta(days=30)).isoformat(), close_when="Alva books the flights")
    values.update(args)
    return _call(**values)


def test_open_stores_the_callers_follow_up_and_tells_it_to_announce_it(live):
    until = (TODAY + timedelta(days=30)).isoformat()
    with _as():
        reply = _open()
    assert reply["errors"] == "None"
    assert reply["payload"] == (f'Opened follow-up #1: "Business award seats SFO→MAD for the Spain trip", checked '
                                f"every 12h until {until}. Say in your reply that you'll keep watching, and until "
                                f"when ({until}).")
    (opened,) = FollowUpStore().list()
    assert (opened.agent_id, opened.status, opened.until, opened.close_when) == ("reward-travel", Status.OPEN, until,
                                                                                 "Alva books the flights")
    assert opened.check == Check(type="model", what="new award space SFO to MAD in business")
    assert (opened.every_s, opened.interval_s) == (43200, 43200)
    assert (opened.opened_at, opened.next_check_at) == (NOW, NOW + 43200)  # the moment `open` checked against
    assert (opened.channel, opened.session_id) == ("lifestyle-perk", "reward-travel:discord:lifestyle-perk")


def test_a_check_can_be_one_of_the_agents_own_scans(live):
    with _as():
        assert _open(check=json.dumps({"type": "credit_windows", "args": {"days": 7}}))["errors"] == "None"
    check = FollowUpStore().list()[0].check
    assert check == Check(type="credit_windows", args={"days": 7})
    assert check.spec() == {"type": "credit_windows", "days": 7}  # the schedule form, flattened


def test_an_award_watch_check_is_built_when_it_opens(live):
    watch = {"origins": ["SFO", "OAK"], "destinations": ["MAD"], "depart": ["2026-12-01", "2026-12-07"],
             "seats": 4, "connections": "us_only"}
    with _as():
        assert _open(check=json.dumps({"type": "award_watch", **watch}))["errors"] == "None"
    assert FollowUpStore().list()[0].check == Check(type="award_watch", args=watch)


@pytest.mark.parametrize("args, error", [
    (dict(goal="  "), "open needs a goal"),
    (dict(goal="x" * 301), "at most 300 characters"),
    (dict(close_when="x" * 301), "at most 300 characters"),
    (dict(check=""), "check is a JSON object with a type (model, award_watch, credit_windows)"),
    (dict(check="watch the seats"), "check is a JSON object with a type"),
    (dict(check=json.dumps({"type": "hotel_watch"})), "check type 'hotel_watch' isn't one you can use: model, "
                                                      "award_watch, credit_windows"),
    (dict(check=json.dumps({"type": "market_screen"})), "isn't one you can use"),  # another agent's scan
    # A scan check is built when it opens, so a bad arg is refused at once.
    (dict(check=json.dumps({"type": "award_watch"})), "check: 'award_watch' needs origins"),
    (dict(check=json.dumps({"type": "award_watch", "origins": ["SFO"], "destinations": ["MAD"],
                            "depart": ["2026-12-01", "2026-12-07"], "seats": 12})),
     "check: 'award_watch' seats is a whole number from 1 to 9"),
    (dict(check=json.dumps({"type": "model"})), 'a model check is {"type": "model", "what": "<what to look for>"}'),
    (dict(check=json.dumps({"type": "model", "what": "x", "when": "daily"})), "a model check is"),
    (dict(check=json.dumps({"type": "model", "what": "x" * 301})), "a model check's what is at most 300 characters"),
    (dict(every="twice a day"), 'every is like "12h" or "2d"'),
    (dict(every="5h"), "every is from 6h to 7d, not 5h"),
    (dict(every="8d"), "every is from 6h to 7d"),
    (dict(until="next week"), "until is a date, YYYY-MM-DD"),
    (dict(until=(TODAY - timedelta(days=1)).isoformat()), "has passed"),
    (dict(until=(TODAY + timedelta(days=91)).isoformat()), "until is at most 90 days out"),
    (dict(every="7d", until=(TODAY + timedelta(days=3)).isoformat()),
     f"every 7d doesn't fit before {TODAY + timedelta(days=3)} ends: check more often, or pick a later until"),
])
def test_open_refuses_what_it_cant_keep(live, args, error):
    with _as():
        reply = _open(**args)
    assert reply["errors"].startswith("Error: ") and error in reply["errors"], reply["errors"]
    assert FollowUpStore().list() == []


def test_the_edges_of_the_cadence_and_the_end_are_allowed(live):
    with _as():
        assert _open(every="6h", until=TODAY.isoformat())["errors"] == "None"
        assert _open(every="7d", until=(TODAY + timedelta(days=90)).isoformat(),
                     check=json.dumps({"type": "model", "what": "another watch"}))["errors"] == "None"
    assert (parse_every("360m"), parse_every("1.5d"), parse_every(" 12H ")) == (21600, 129600, 43200)
    assert (cadence_text(21600), cadence_text(172800), cadence_text(23400)) == ("6h", "2d", "390m")


def test_its_description_says_both_forms_and_stays_short():
    """Every prompt that has the tool carries this, so it stays within 650 characters: it was
    617 before the own-tools rule, and `extend` and the one-off fit in the same budget. The
    one-off took "never reminders" out: a topic the user asks to come back to is one."""
    description = follow_up.description
    words = " ".join(description.split())
    assert "A watch: a check you run with your own tools on a cadence until an end date." in words
    assert "A one-off (at): a topic the user asks you to bring back at a set time." in words
    assert "at, goal, notes: a one-off. at: YYYY-MM-DD HH:MM." in words
    assert "op: open, extend, close, or list yours." in description
    assert "reminder" not in description
    assert len(description) <= 650


def test_extend_moves_the_callers_until_later_and_tells_it_to_say_so(live):
    later = (TODAY + timedelta(days=60)).isoformat()
    with _as():
        _open()
        reply = _call(op="extend", id=1, until=later)
    assert reply["errors"] == "None"
    assert reply["payload"] == f"Extended follow-up #1 until {later}. Say so in your reply."
    extended = FollowUpStore().get(1)
    assert (extended.until, extended.status, extended.agent_id) == (later, Status.OPEN, "reward-travel")


@pytest.mark.parametrize("args, error", [
    (dict(until=(TODAY + timedelta(days=60)).isoformat()), "extend needs the follow-up's id (list shows them)"),
    (dict(id=1, until="next month"), "until is a date, YYYY-MM-DD"),
    (dict(id=1, until=(TODAY - timedelta(days=1)).isoformat()), "has passed"),
    (dict(id=1, until=(TODAY + timedelta(days=91)).isoformat()), "until is at most 90 days out"),
    (dict(id=1, until=(TODAY + timedelta(days=30)).isoformat()),
     f"until is after {TODAY + timedelta(days=30)}: follow-up #1 already runs until then"),
    (dict(id=1, until=(TODAY + timedelta(days=20)).isoformat()), "until is after"),
    (dict(id=2, until=(TODAY + timedelta(days=60)).isoformat()), "you have no open follow-up #2"),
])
def test_extend_refuses_what_it_cant_keep(live, args, error):
    with _as():
        _open()
        reply = _call(op="extend", **args)
    assert reply["errors"].startswith("Error: ") and error in reply["errors"], reply["errors"]
    assert FollowUpStore().get(1).until == (TODAY + timedelta(days=30)).isoformat()


def test_extend_is_only_for_the_callers_own_open_follow_up(live):
    later = (TODAY + timedelta(days=60)).isoformat()
    with _as():
        _open()
    with _as("excursion-planner", "weekend-planning"):
        assert "you have no open follow-up #1" in _call(op="extend", id=1, until=later)["errors"]  # not its own
    with _as():
        _call(op="close", id=1)
        assert "you have no open follow-up #1" in _call(op="extend", id=1, until=later)["errors"]  # closed
    assert FollowUpStore().get(1).until == (TODAY + timedelta(days=30)).isoformat()


def test_a_second_open_follow_up_with_the_same_check_is_refused(live):
    with _as():
        _open()
        reply = _open(goal="Seats to Madrid")
        assert "follow-up #1 already has this check" in reply["errors"] and "Close it first" in reply["errors"]
        _call(op="close", id=1)
        assert _open(goal="Seats to Madrid")["errors"] == "None"


def test_list_shows_the_callers_open_follow_ups(live):
    with _as():
        assert _call(op="list")["payload"] == "You have no open follow-ups."
        _open()
        _open(check=json.dumps({"type": "credit_windows"}), goal="Use the Amex hotel credit", every="2d",
              close_when="")
    with _as("excursion-planner", "weekend-planning"):
        _open(goal="Tahoe cabin under $400")
        assert _call(op="list")["payload"].startswith('#3 · "Tahoe cabin under $400"')
    with _as():
        lines = _call(op="list")["payload"].splitlines()
    until = (TODAY + timedelta(days=30)).isoformat()
    assert lines == [
        f'#1 · "Business award seats SFO→MAD for the Spain trip" · every 12h until {until} · check: model, '
        f'"new award space SFO to MAD in business" · ends early when: Alva books the flights',
        f'#2 · "Use the Amex hotel credit" · every 2d until {until} · check: credit_windows',
    ]


def test_close_ends_only_the_callers_own_open_follow_up(live):
    with _as():
        _open()
    with _as("excursion-planner", "weekend-planning"):
        assert "you have no open follow-up #1" in _call(op="close", id=1)["errors"]  # not its own
    with _as():
        assert "close needs the follow-up's id" in _call(op="close")["errors"]
        reply = _call(op="close", id=1, reason="booked on Iberia")
        assert reply["payload"] == 'Closed follow-up #1: "Business award seats SFO→MAD for the Spain trip".'
        assert "you have no open follow-up #1" in _call(op="close", id=1)["errors"]  # already closed
    closed = FollowUpStore().get(1)
    assert (closed.status, closed.close_reason, closed.closed_by) == (Status.CLOSED, "booked on Iberia", Closer.AGENT)


@pytest.mark.parametrize("op", ["open", "extend", "close", "list"])
def test_with_the_switch_off_every_op_is_refused(op):
    with _as():
        reply = _open(op=op, id=1)
    assert reply["errors"] == "Error: follow-ups are off: nothing would check them"
    assert FollowUpStore().list() == []


def test_it_needs_an_agent_run(live):
    assert "no active execution context" in _call(op="list")["errors"]


@pytest.mark.parametrize("source", ["scheduled", "proactive", "followup"])
def test_open_and_extend_are_refused_on_the_scheduled_surface(live, source):
    until = (TODAY + timedelta(days=30)).isoformat()
    refused = ("Error: open or extend a follow-up in a conversation, after the user agreed; you can offer it in "
               "your message")
    with _as():
        _open()
    with execution_context(agent_id="reward-travel", source=source, channel="lifestyle-perk", stateless=False):
        assert _open(check=json.dumps({"type": "model", "what": "seats to Lisbon"}))["errors"] == refused
        assert _call(op="extend", id=1, until=(TODAY + timedelta(days=60)).isoformat())["errors"] == refused
    assert [(f.id, f.until) for f in FollowUpStore().list()] == [(1, until)]


def test_a_check_can_list_and_close_its_own_follow_up(live):
    with _as():
        _open()
    with execution_context(agent_id="reward-travel", source="followup", channel="lifestyle-perk", stateless=False):
        assert _call(op="list")["payload"].startswith('#1 · "Business award seats SFO→MAD for the Spain trip"')
        reply = _call(op="close", id=1, reason="found two seats, and Alva booked them")
    assert reply["payload"] == 'Closed follow-up #1: "Business award seats SFO→MAD for the Spain trip".'
    closed = FollowUpStore().get(1)
    assert (closed.status, closed.closed_by, closed.close_reason) == (Status.CLOSED, Closer.AGENT,
                                                                      "found two seats, and Alva booked them")


# -- one-offs: a topic the user asks to come back to at a set time ------------------------

TOMORROW_9 = datetime.combine(TODAY + timedelta(days=1), time(9, 0), get_local_timezone())
LOGO = "The logo redesign: pick between the two directions"
NOTES = "We narrowed it to the serif and the geometric mark; Alva wanted to sleep on it."


def _at(moment: float) -> str:
    """A moment as `at` takes it: YYYY-MM-DD HH:MM on the user's clock."""
    return f"{datetime.fromtimestamp(moment, get_local_timezone()):%Y-%m-%d %H:%M}"


def _aki():
    """A turn of Aki's, in its channel: where the user asked for this."""
    return _as("agent-designer", "agent-management")


def _open_once(**args):
    values = dict(op="open", at=_at(TOMORROW_9.timestamp()), goal=LOGO, notes=NOTES)
    values.update(args)
    return _call(**values)


def test_a_one_off_is_stored_due_at_its_time_and_the_reply_says_what_and_when(live):
    with _aki():
        reply = _open_once()
    when = f"{TOMORROW_9:%A %Y-%m-%d %H:%M}"
    assert reply["errors"] == "None"
    assert reply["payload"] == (f'Opened follow-up #1: you\'ll bring up "{LOGO}" on {when}. Say in your reply what '
                                f"you'll bring up, and when ({when}).")
    (opened,) = FollowUpStore().list()
    due = TOMORROW_9.timestamp()
    assert (opened.kind, opened.is_once, opened.agent_id, opened.status) == (Kind.ONCE, True, "agent-designer",
                                                                             Status.OPEN)
    assert (opened.goal, opened.notes, opened.check) == (LOGO, NOTES, Check(type="model", what=LOGO))
    assert (opened.due_at, opened.next_check_at, opened.opened_at) == (due, due, NOW)
    assert (opened.every_s, opened.interval_s) == (0, 0)
    assert opened.until == (TODAY + timedelta(days=2)).isoformat()  # a grace day after its own
    assert opened.channel == "agent-management"


@pytest.mark.parametrize("at", ["{day} 09:00", "{day}T09:00", " {day} 9:00 "])
def test_at_is_a_time_on_the_users_clock_with_a_space_or_a_t(live, at):
    with _aki():
        assert _open_once(at=at.format(day=TOMORROW_9.date().isoformat()))["errors"] == "None"
    assert FollowUpStore().get(1).due_at == TOMORROW_9.timestamp()


@pytest.mark.parametrize("args, error", [
    (dict(at="tomorrow at 9"), 'at is a time on the user\'s clock, "YYYY-MM-DD HH:MM"'),
    (dict(at=(TODAY + timedelta(days=1)).isoformat()), "at is a time on the user's clock"),  # a day, but no time
    (dict(at="2026-13-40 09:00"), "at is a time on the user's clock"),
    (dict(at=_at(NOW - 3600)), f"at {_at(NOW - 3600)} has passed: it's {_at(NOW)}"),
    (dict(at=_at(NOW + MIN_AT_S - 60)), f"at is at least 5 minutes out: it's {_at(NOW)}"),
    (dict(at=_at(NOW + 90 * 86400 + 60)), f"at is at most 90 days out: {_at(NOW + 90 * 86400)} or sooner"),
    (dict(every="12h"), "at can't go with every: a one-off is brought up once, at its time"),
    (dict(until=(TODAY + timedelta(days=3)).isoformat()), "at can't go with until"),
    (dict(check=MODEL), "at can't go with check"),
    (dict(close_when="the user brings it up"), "at can't go with close_when"),
    (dict(every="12h", until=(TODAY + timedelta(days=3)).isoformat()), "at can't go with every or until"),
    (dict(goal="  "), "open needs a goal: what to bring up"),
    (dict(goal="x" * 301), "goal is at most 300 characters"),
    (dict(notes="x" * (NOTES_CHARS + 1)), "notes are at most 1000 characters"),
])
def test_a_one_off_is_refused_what_it_cant_keep(live, args, error):
    with _aki():
        reply = _open_once(**args)
    assert reply["errors"].startswith("Error: ") and error in reply["errors"], reply["errors"]
    assert FollowUpStore().list() == []


def test_the_edges_of_a_one_offs_time_and_notes_are_allowed(live):
    with _aki():
        assert _open_once(at=_at(NOW + MIN_AT_S))["errors"] == "None"
        assert _open_once(at=_at(NOW + 90 * 86400), notes="x" * NOTES_CHARS)["errors"] == "None"
        assert _open_once(goal="Another topic", notes="  spread\n over   lines  ")["errors"] == "None"
    assert [f.due_at for f in FollowUpStore().list()] == [NOW + MIN_AT_S, NOW + 90 * 86400, TOMORROW_9.timestamp()]
    assert FollowUpStore().get(3).notes == "spread over lines"  # one line, as goal and close_when are


def test_notes_go_only_with_at(live):
    with _aki():
        reply = _open(notes=NOTES)
    assert reply["errors"] == 'Error: notes go with at, for a one-off: at is "YYYY-MM-DD HH:MM"'
    assert FollowUpStore().list() == []


def test_the_same_one_off_is_refused_but_another_time_or_a_watch_on_the_same_text_is_not(live):
    with _aki():
        assert _open_once()["errors"] == "None"
        reply = _open_once(goal="the logo redesign:  pick between the two DIRECTIONS")  # the same, normalised
        assert reply["errors"] == f'Error: follow-up #1 already brings this up then: "{LOGO}"'
        assert _open_once(at=_at(TOMORROW_9.timestamp() + 3600))["errors"] == "None"  # an hour later
        assert _open(goal=LOGO, check=json.dumps({"type": "model", "what": LOGO}))["errors"] == "None"  # a watch
    assert [(f.id, f.kind) for f in FollowUpStore().list()] == [(1, Kind.ONCE), (2, Kind.ONCE), (3, Kind.WATCH)]


@pytest.mark.parametrize("until", [(TODAY + timedelta(days=5)).isoformat(), "", "next week"])
def test_a_one_off_cant_be_extended_whatever_until_says(live, until):
    with _aki():
        _open_once()
        reply = _call(op="extend", id=1, until=until)
    assert reply["errors"] == ("Error: follow-up #1 is a one-off, brought up once: it can't be extended. Close it, "
                               "and open another at the new time")
    assert FollowUpStore().get(1).until == (TODAY + timedelta(days=2)).isoformat()


def test_list_shows_a_one_off_by_its_time_and_close_ends_it(live):
    with _aki():
        _open(goal="Agent roster review", check=json.dumps({"type": "model", "what": "roster gaps"}), close_when="")
        _open_once()
        lines = _call(op="list")["payload"].splitlines()
        assert lines[1] == f'#2 · once at {TOMORROW_9:%a %Y-%m-%d %H:%M} · "{LOGO}"'
        assert "every" in lines[0] and "every" not in lines[1]
        reply = _call(op="close", id=2, reason="the user brought it up first")
    assert reply["payload"] == f'Closed follow-up #2: "{LOGO}".'
    closed = FollowUpStore().get(2)
    assert (closed.status, closed.closed_by, closed.close_reason) == (Status.CLOSED, Closer.AGENT,
                                                                      "the user brought it up first")


@pytest.mark.parametrize("source", ["scheduled", "proactive", "followup"])
def test_a_one_off_is_refused_on_the_scheduled_surface_too(live, source):
    with execution_context(agent_id="agent-designer", source=source, channel="agent-management", stateless=False):
        reply = _open_once()
    assert reply["errors"] == ("Error: open or extend a follow-up in a conversation, after the user agreed; you can "
                               "offer it in your message")
    assert FollowUpStore().list() == []
