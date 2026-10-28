"""Follow-ups in memory.db (core/storage/follow_ups.py)."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, datetime

import pytest

from core.storage.follow_ups import (
    Check,
    Closer,
    DuplicateFollowUp,
    FollowUp,
    FollowUpStore,
    Result,
    Status,
    cadence_text,
    check_key,
    end_of,
    ensure_first_check,
    parse_until,
)
from core.util.local_time import get_local_timezone
from tests.helpers import make_follow_up

AWARD = Check(type="award_watch", args={"origin": "SFO", "destination": "MAD", "cabin": "business"})


def _model(what):
    return Check(type="model", what=what)


@pytest.fixture
def store(tmp_path):
    return FollowUpStore(str(tmp_path / "memory.db"))


def _open(store, agent_id="reward-travel", check=AWARD, at=1000.0, **values):
    fields = dict(goal="Business award seats SFO→MAD, 2 seats", every_s=12 * 3600, until="2026-11-30",
                  close_when="Alva books the flights", channel="lifestyle-perk",
                  session_id="reward-travel:discord:lifestyle-perk")
    fields.update(values)
    return store.open(agent_id=agent_id, check=check, at=at, **fields)


def test_a_follow_up_opens_with_its_cadence_and_first_check(store):
    opened = _open(store)
    assert isinstance(opened, FollowUp) and opened.is_open
    assert (opened.status, opened.agent_id, opened.check) == (Status.OPEN, "reward-travel", AWARD)
    assert (opened.every_s, opened.interval_s, opened.opened_at, opened.next_check_at) == (43200, 43200, 1000.0,
                                                                                          44200.0)
    assert (opened.until, opened.close_when) == ("2026-11-30", "Alva books the flights")
    assert (opened.channel, opened.session_id) == ("lifestyle-perk", "reward-travel:discord:lifestyle-perk")
    assert (opened.checks, opened.empty_checks, opened.last_fingerprint, opened.claimed_at) == (0, 0, "", None)
    assert (opened.finds, opened.last_report, opened.closed_by, opened.radio_state) == (0, "", None, None)
    assert store.get(opened.id) == opened


def test_a_check_is_stored_in_its_json_form_with_its_key(store):
    # Both columns are the store's own, so read from the table: the form and the key don't change.
    award, model = _open(store), _open(store, check=_model("Tahoe snow forecast"))
    stored = store.select_one("follow_ups", {"id": award.id})
    assert stored["check_spec"] == '{"type": "award_watch", "origin": "SFO", "destination": "MAD", "cabin": "business"}'
    assert stored["check_key"] == ('reward-travel:{"cabin": "business", "destination": "mad", "origin": "sfo", '
                                   '"type": "award_watch"}')
    assert store.select_one("follow_ups", {"id": model.id})["check_spec"] == ('{"type": "model", '
                                                                             '"what": "Tahoe snow forecast"}')


def test_a_check_reads_and_writes_its_stored_form():
    for spec in ({"type": "model", "what": "Tahoe snow"}, {"type": "award_watch", "origin": "SFO"},
                 {"type": "credit_windows"}):
        assert Check.from_spec(spec).spec() == spec
    assert Check.from_spec({"type": "model", "what": "Tahoe snow"}) == _model("Tahoe snow")
    assert Check.from_spec({"type": "award_watch", "origin": "SFO"}) == Check(type="award_watch",
                                                                              args={"origin": "SFO"})
    assert _model("x").is_model and not AWARD.is_model


def test_a_check_that_cant_be_read_has_no_type(store):
    opened = _open(store)
    for unreadable in ("not json", "[1, 2]", "{}"):
        store.update("follow_ups", {"check_spec": unreadable}, {"id": opened.id})
        assert store.get(opened.id).check == Check(type="")


def test_lists_are_by_agent_and_status_oldest_first(store):
    first, other = _open(store), _open(store, agent_id="excursion-planner")
    second = _open(store, check=_model("Tahoe snow forecast"))
    assert [f.id for f in store.list(agent_id="reward-travel")] == [first.id, second.id]
    store.close(first.id)
    assert [f.id for f in store.list(agent_id="reward-travel", status=Status.OPEN)] == [second.id]
    assert [f.id for f in store.list(status=Status.CLOSED)] == [first.id]
    assert [f.id for f in store.list()] == [first.id, other.id, second.id]


def test_the_same_check_is_refused_while_one_is_open(store):
    open_one = _open(store)
    same = replace(AWARD, args=dict(AWARD.args, cabin="  Business ", origin="sfo"))  # the same check, written differently
    for check in (AWARD, same):
        with pytest.raises(DuplicateFollowUp) as refused:
            _open(store, check=check, goal="Seats to Madrid")
        assert refused.value.existing == open_one
        assert f"#{open_one.id}" in str(refused.value)
    _open(store, agent_id="excursion-planner")  # another agent's check is its own
    store.close(open_one.id, reason="booked")
    assert _open(store, goal="Seats to Madrid, again").is_open  # closed ones don't count


def test_two_opens_at_once_open_one(store):
    def attempt(_):
        try:
            return _open(store).id
        except DuplicateFollowUp:
            return None

    store.list()  # the table exists first: the race is over the open alone
    with ThreadPoolExecutor(max_workers=4) as pool:
        opened = [i for i in pool.map(attempt, range(4)) if i is not None]
    assert len(opened) == 1 and len(store.list(status=Status.OPEN)) == 1


def test_only_an_open_follow_up_closes_and_only_its_owners(store):
    opened = _open(store)
    assert store.close(opened.id, agent_id="excursion-planner") is None  # not its own
    assert store.get(opened.id).is_open
    closed = store.close(opened.id, agent_id="reward-travel", reason="booked", at=2000.0)
    assert (closed.status, closed.closed_at, closed.close_reason) == (Status.CLOSED, 2000.0, "booked")
    assert closed.closed_by is Closer.AGENT  # the default: the follow_up tool closes as the agent
    assert store.close(opened.id) is None  # already closed
    assert store.close(999) is None
    assert store.close(_open(store, goal="again").id, by=Closer.YOU).closed_by is Closer.YOU
    with pytest.raises(ValueError):
        store.close(opened.id, by="someone")


def test_a_check_key_is_the_agent_and_its_normalised_check():
    assert check_key("a", _model("Tahoe  Snow")) == check_key("a", Check.from_spec({"what": "tahoe snow",
                                                                                     "type": "MODEL"}))
    assert check_key("a", AWARD) != check_key("b", AWARD)
    assert check_key("a", AWARD) != check_key("a", replace(AWARD, args=dict(AWARD.args, destination="LIS")))


# -- the checks, expiry and team radio's outbox ----------------------------------------

TZ = get_local_timezone()
NOV_1 = datetime(2026, 11, 1, 9, 0, tzinfo=TZ).timestamp()
HOUR, DAY = 3600, 86400


def test_due_checks_are_open_unclaimed_due_and_within_until(store):
    later = _open(store, check=_model("later"), at=NOV_1 - 11 * HOUR)        # due at NOV_1 + 1 h
    first = _open(store, check=_model("first"), at=NOV_1 - 13 * HOUR)        # due an hour ago
    second = _open(store, check=_model("second"), at=NOV_1 - 12 * HOUR)      # due now
    claimed = _open(store, check=_model("claimed"), at=NOV_1 - 13 * HOUR)
    closed = _open(store, check=_model("closed"), at=NOV_1 - 13 * HOUR)
    ended = _open(store, check=_model("ended"), at=NOV_1 - 13 * HOUR, until="2026-10-31")
    store.claim(claimed.id, NOV_1)
    store.close(closed.id)
    due = store.due(NOV_1, "2026-11-01")
    assert [f.id for f in due] == [first.id, second.id]  # the longest due first
    assert [f.id for f in store.due(NOV_1, "2026-11-01", limit=1)] == [first.id]
    assert later.id not in [f.id for f in store.due(NOV_1 + 30 * 60, "2026-11-01")]
    assert ended.id in [f.id for f in store.due(NOV_1, "2026-10-31")]  # its last day still counts


def test_a_check_is_claimed_once(store):
    opened = _open(store)
    assert store.claim(opened.id, NOV_1) is True
    assert store.claim(opened.id, NOV_1 + 1) is False
    assert store.get(opened.id).claimed_at == NOV_1
    closed = _open(store, check=_model("closed"))
    store.close(closed.id)
    assert store.claim(closed.id, NOV_1) is False


def test_a_claim_that_never_finished_is_freed(store):
    stuck, fresh = _open(store), _open(store, check=_model("fresh"))
    store.claim(stuck.id, NOV_1)
    store.claim(fresh.id, NOV_1 + 30 * 60)
    assert store.release_stale(NOV_1 + HOUR - 1, HOUR) == []
    assert store.release_stale(NOV_1 + HOUR + 1, HOUR) == [stuck.id]
    assert store.get(stuck.id).claimed_at is None and store.get(fresh.id).claimed_at is not None


def _checked(store, follow_up_id, *results, now=NOV_1, **kwargs):
    checked = None
    for result in results:
        store.claim(follow_up_id, now)
        checked = store.finish_check(follow_up_id, result, now, **kwargs)
    return checked


def test_a_find_counts_and_starts_the_cadence_over(store):
    opened = _open(store, until="2026-12-31")
    _checked(store, opened.id, Result.EMPTY, Result.EMPTY, Result.EMPTY)  # doubled: 24 h
    found = _checked(store, opened.id, Result.FOUND, fingerprint="f1", report="Two seats on Iberia, 70k each.")
    assert (found.checks, found.finds, found.empty_checks) == (4, 1, 0)
    assert (found.interval_s, found.next_check_at) == (12 * HOUR, NOV_1 + 12 * HOUR)
    assert (found.last_found_at, found.last_checked_at, found.claimed_at) == (NOV_1, NOV_1, None)
    assert (found.last_fingerprint, found.last_report) == ("f1", "Two seats on Iberia, 70k each.")


def test_every_third_empty_check_in_a_row_doubles_the_interval_up_to_7_days(store):
    opened = _open(store, until="2026-12-31")
    intervals = [_checked(store, opened.id, Result.EMPTY).interval_s // HOUR for _ in range(6)]
    assert intervals == [12, 12, 24, 24, 24, 48]  # x2 at the 3rd and the 6th
    slow = _open(store, check=_model("slow"), every_s=3 * DAY, until="2026-12-31")
    assert _checked(store, slow.id, *[Result.EMPTY] * 3).interval_s == 6 * DAY
    capped = _checked(store, slow.id, *[Result.EMPTY] * 3)
    assert (capped.interval_s, capped.empty_checks, capped.checks) == (7 * DAY, 6, 6)
    assert capped.next_check_at == NOV_1 + 7 * DAY


def test_an_empty_check_keeps_a_fingerprint_given_but_no_report(store):
    opened = _open(store, until="2026-12-31")
    checked = _checked(store, opened.id, Result.EMPTY, fingerprint="seen", report="not posted")
    assert (checked.last_fingerprint, checked.last_report) == ("seen", "")


@pytest.mark.parametrize("result", [Result.PROBLEM, Result.FAILED])
def test_a_problem_or_a_failure_counts_nothing_but_waits_an_interval(store, result):
    opened = _open(store, until="2026-12-31")
    checked = _checked(store, opened.id, result, fingerprint="ignored")
    assert (checked.checks, checked.finds, checked.empty_checks, checked.last_fingerprint) == (0, 0, 0, "")
    assert (checked.next_check_at, checked.last_checked_at, checked.claimed_at) == (NOV_1 + 12 * HOUR, NOV_1, None)


def test_as_until_nears_the_interval_is_back_to_every(store):
    # Nov 2 ends 39 h after Nov 1, 09:00: a doubled 24 h interval would leave one check before the end.
    opened = _open(store, until="2026-11-02")
    assert _checked(store, opened.id, Result.EMPTY, Result.EMPTY).interval_s == 12 * HOUR
    third = _checked(store, opened.id, Result.EMPTY)
    assert (third.interval_s, third.next_check_at, third.empty_checks) == (12 * HOUR, NOV_1 + 12 * HOUR, 3)


def test_a_check_that_closed_its_own_follow_up_still_counts(store):
    opened = _open(store, until="2026-12-31")
    store.claim(opened.id, NOV_1)
    store.close(opened.id, agent_id="reward-travel", reason="booked on Iberia")  # in the check's own turn
    finished = store.finish_check(opened.id, Result.FOUND, NOV_1 + 60, report="Booked: Iberia, Dec 10.")
    assert (finished.status, finished.checks, finished.finds, finished.claimed_at) == (Status.CLOSED, 1, 1, None)
    assert finished.last_report == "Booked: Iberia, Dec 10."
    assert finished.next_check_at == opened.next_check_at  # it isn't due again
    assert store.finish_check(999, Result.FOUND, NOV_1) is None
    with pytest.raises(ValueError):
        store.finish_check(opened.id, "maybe", NOV_1)


def test_expire_closes_the_ones_whose_until_passed(store):
    ended, last_day = _open(store, until="2026-10-31"), _open(store, check=_model("x"), until="2026-11-01")
    closed = store.expire("2026-11-01", NOV_1)
    assert [f.id for f in closed] == [ended.id]
    assert (closed[0].status, closed[0].close_reason, closed[0].closed_by, closed[0].closed_at) == (
        Status.CLOSED, "until passed", Closer.UNTIL, NOV_1)
    assert store.get(last_day.id).is_open
    assert store.expire("2026-11-01", NOV_1) == []


def test_extend_moves_until_later_and_keeps_the_history(store):
    opened = _open(store)
    _checked(store, opened.id, Result.FOUND, fingerprint="f1", report="Two seats on Iberia, 70k each.")
    checked = _checked(store, opened.id, Result.EMPTY)
    extended = store.extend(opened.id, "2026-12-31")
    assert extended.until == "2026-12-31"
    assert replace(extended, until=checked.until) == checked  # the counts, fingerprint and report stay
    assert (extended.checks, extended.finds, extended.last_fingerprint) == (2, 1, "f1")
    for not_later in ("2026-12-31", "2026-12-15"):
        assert store.extend(opened.id, not_later) is None
    assert store.get(opened.id).until == "2026-12-31"
    # The new until is the one that counts: due on it, and not expired the day after the old one.
    assert opened.id in [f.id for f in store.due(NOV_1 + 60 * DAY, "2026-12-31")]
    assert store.expire("2026-12-01", NOV_1 + 30 * DAY) == []


def test_only_an_open_follow_up_extends_and_only_its_owners(store):
    opened = _open(store)
    assert store.extend(opened.id, "2026-12-31", agent_id="excursion-planner") is None  # not its own
    assert store.extend(opened.id, "2026-12-15", agent_id="reward-travel").until == "2026-12-15"
    assert store.extend(opened.id, "2026-12-31").until == "2026-12-31"  # no agent given: anyone's (`!followups`)
    store.close(opened.id)
    assert store.extend(opened.id, "2027-01-15") is None  # closed
    assert store.extend(999, "2027-01-15") is None
    with pytest.raises(ValueError):
        store.extend(opened.id, "next month")


def test_until_is_a_date_from_today_to_90_days_out_and_ends_at_the_midnight_after():
    today = date(2026, 11, 1)
    assert (parse_until(" 2026-11-01 ", today), parse_until("2027-01-30", today)) == ("2026-11-01", "2027-01-30")
    for text, error in (("next week", "until is a date, YYYY-MM-DD"),
                        ("2026-10-31", "until 2026-10-31 has passed: it's 2026-11-01 today"),
                        ("2027-01-31", "until is at most 90 days out: 2027-01-30 or sooner")):
        with pytest.raises(ValueError) as refused:
            parse_until(text, today)
        assert str(refused.value) == error
    assert end_of("2026-11-30") == datetime(2026, 12, 1, tzinfo=TZ).timestamp()
    assert end_of("someday") == 0.0  # unreadable: ending now


def test_the_first_check_must_come_before_until_ends():
    ends = datetime(2026, 11, 11, tzinfo=TZ).timestamp()  # 2026-11-10 ends at the midnight after it
    ensure_first_check(12 * HOUR, "2026-11-10", ends - 12 * HOUR - 1)  # due a second before the end: it fits
    with pytest.raises(ValueError) as refused:
        ensure_first_check(12 * HOUR, "2026-11-10", ends - 12 * HOUR)  # due as it ends: it would never run
    assert str(refused.value) == ("every 12h doesn't fit before 2026-11-10 ends: check more often, "
                                  "or pick a later until")


def test_you_close_any_agents_follow_up_and_the_last_closed_come_newest_first(store):
    first, second = _open(store), _open(store, agent_id="excursion-planner")
    cancelled = store.close(first.id, reason="booked already", by=Closer.YOU, at=NOV_1)
    assert (cancelled.closed_by, cancelled.close_reason) == (Closer.YOU, "booked already")
    store.close(second.id, by=Closer.YOU, at=NOV_1 + 60)
    assert [f.id for f in store.closed(10)] == [second.id, first.id]
    assert [f.id for f in store.closed(1)] == [second.id]


def test_the_radio_outbox_has_each_line_once(store):
    opened, watched, quick = _open(store), _open(store, check=_model("watched")), _open(store, check=_model("quick"))
    assert [f.id for f in store.pending_lines()] == [opened.id, watched.id, quick.id]
    store.mark_line(opened.id, Status.OPEN)
    store.mark_line(watched.id, Status.OPEN)
    assert store.get(opened.id).radio_state is Status.OPEN
    store.close(quick.id)  # closed before its opened line went out: one line, the closed one
    assert [(f.id, f.status) for f in store.pending_lines()] == [(quick.id, Status.CLOSED)]
    store.mark_line(quick.id, Status.CLOSED)
    # A follow-up closed by its own check waits for the check to finish, so its line counts it.
    store.claim(watched.id, NOV_1)
    store.close(watched.id, agent_id="reward-travel", reason="found it")
    assert store.pending_lines() == []
    store.finish_check(watched.id, Result.FOUND, NOV_1)
    assert [(f.id, f.finds) for f in store.pending_lines()] == [(watched.id, 1)]
    store.mark_line(watched.id, Status.CLOSED)
    assert store.pending_lines() == []
    with pytest.raises(ValueError):
        store.mark_line(opened.id, "posted")


def test_the_words_the_tool_followups_and_the_radio_share():
    assert (cadence_text(21600), cadence_text(172800), cadence_text(23400)) == ("6h", "2d", "390m")
    assert _model("seats to Madrid").text() == 'model, "seats to Madrid"'
    assert (AWARD.text(), Check(type="").text()) == ("award_watch", "?")
    assert make_follow_up(checks=6, finds=1).tally() == "6 checks, 1 find"
    assert make_follow_up(checks=1, finds=0).tally() == "1 check, 0 finds"
    closed = make_follow_up(status=Status.CLOSED, closed_at=NOV_1)
    assert not closed.is_open
    assert replace(closed, closed_by=Closer.AGENT, close_reason="booked on Iberia").closed_why("Miles") == \
        "booked on Iberia"
    assert replace(closed, closed_by=Closer.AGENT).closed_why("Miles") == "Miles closed it"
    assert replace(closed, closed_by=Closer.YOU, close_reason="too many").closed_why("Miles") == \
        "you cancelled it: too many"
    assert replace(closed, closed_by=Closer.YOU).closed_why("Miles") == "you cancelled it"
    assert replace(closed, closed_by=Closer.UNTIL, close_reason="until passed").closed_why("Miles") == "until passed"
    assert replace(closed, closed_by=Closer.SWITCHBOARD, close_reason="booked UA 990").closed_why("Miles") == \
        "you said it's done: booked UA 990"
    assert replace(closed, closed_by=Closer.SWITCHBOARD).closed_why("Miles") == "you said it's done"
