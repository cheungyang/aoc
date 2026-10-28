"""The Governor (core/proactive/governor.py): what each route may become, and when."""
import random
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from core.proactive.cards import PeerCard
from core.proactive.governor import (
    ABSORB,
    DELIVER,
    DROP,
    WORTH_POSTING,
    Governor,
    evidence_found,
    kind_key,
    quiet_stretch,
    resolve_times,
)
from core.proactive.switchboard import ProposedRoute, ProposedSignal
from core.storage.signals import SignalStore
from core.util.local_time import get_local_timezone

NOW = datetime(2026, 10, 24, 9, 0, tzinfo=get_local_timezone()).timestamp()


def _card(agent_id, name, offers=("update the kitchen inventory",), topics=("food",)):
    return PeerCard(agent_id=agent_id, name=name, emoji="🙂", home=agent_id, description="", interests=(),
                    offers=tuple(offers), topics=tuple(topics), derived=not offers)


TEAM = {
    "meal-planner": _card("meal-planner", "Ramsay", topics=("food", "health", "family")),
    "excursion-planner": _card("excursion-planner", "Eric", offers=("activity ideas",), topics=("travel", "family")),
    "reward-travel": _card("reward-travel", "Miles", offers=("track card credits",), topics=("travel", "finance")),
}


def _signal(kind="artifact", tags=("food",), until=None, sensitivity="normal", evidence="Saved: 99 Ranch"):
    return ProposedSignal(kind=kind, gist="Groceries from 99 Ranch", evidence=evidence, tags=list(tags),
                          until=until, sensitivity=sensitivity, payload={})


def _route(to="meal-planner", offer="update kitchen inventory", urgency="now", window=(None, None)):
    return ProposedRoute(to=to, offer=offer, urgency=urgency, window_start=window[0], window_end=window[1])


class Draw(random.Random):
    """A random source whose acceptance draw is fixed."""

    def __init__(self, value):
        super().__init__(0)
        self.value = value

    def betavariate(self, alpha, beta):
        self.last = (alpha, beta)
        return self.value


@pytest.fixture
def store(tmp_path):
    return SignalStore(str(tmp_path / "memory.db"))


def _governor(store, draw=0.9, owns=lambda agent_id: True):
    return Governor(store, TEAM, owns=owns, rng=Draw(draw), clock=lambda: NOW)


class TestEvidence:

    def test_word_for_word_ignoring_case_spacing_and_quotes(self):
        exchange = "User: We\u2019re in Tahoe with the family\n  until Tuesday"
        assert evidence_found("we're in tahoe with the family until Tuesday", exchange)
        assert evidence_found('"We\'re in Tahoe."', exchange)
        assert not evidence_found("We're in Reno", exchange)
        assert not evidence_found("in", exchange)

    def test_an_ellipsis_joins_pieces_in_order(self):
        exchange = "Saved: 99 Ranch, 18 items, Groceries, $86.40"
        assert evidence_found("99 Ranch … $86.40", exchange)
        assert not evidence_found("$86.40 ... 99 Ranch", exchange)


class TestSignalProblems:

    def test_evidence_until_and_past(self, store):
        governor = _governor(store)
        exchange = "Saved: 99 Ranch"
        assert governor.signal_problem(_signal(), exchange) is None
        assert governor.signal_problem(_signal(evidence="Costco"), exchange) == "evidence not found in the exchange"
        assert governor.signal_problem(_signal(kind="situation"), exchange) == "a situation needs an until date"
        assert governor.signal_problem(_signal(kind="situation", until="2026-10-01"), exchange) == "until 2026-10-01 is past"
        assert governor.signal_problem(_signal(kind="situation", until="2026-10-27"), exchange) is None


class TestVerdicts:

    def judge(self, store, signal=None, route=None, **kwargs):
        governor = kwargs.pop("governor", None) or _governor(store)
        kwargs.setdefault("source_agent", "receipt-processor")
        return governor.judge(signal or _signal(), route or _route(), **kwargs)

    def test_an_artifact_goes_now_to_the_teammate_it_names(self, store):
        verdict = self.judge(store)
        assert (verdict.verdict, verdict.urgency, verdict.offer) == (DELIVER, "now", "update the kitchen inventory")
        assert verdict.deliver_at == NOW and verdict.kind_key == "meal-planner/artifact/food"

    def test_a_signal_problem_drops_every_route(self, store):
        verdict = self.judge(store, signal_problem="evidence not found in the exchange")
        assert (verdict.verdict, verdict.reason) == (DROP, "evidence not found in the exchange")

    @pytest.mark.parametrize("route, source, reason", [
        (_route(to="stranger"), "receipt-processor", "stranger is not a teammate with a card"),
        (_route(), "meal-planner", "the exchange was with that teammate already"),
        (_route(offer="book a table"), "receipt-processor", "'book a table' isn't one of Ramsay's offers"),
        (_route(window=("2026-12-01", "2026-12-03")), "receipt-processor", "its window isn't in the next 7 days"),
        (_route(window=("2026-10-01", "2026-10-02")), "receipt-processor", "its window isn't in the next 7 days"),
    ])
    def test_drops(self, store, route, source, reason):
        verdict = self.judge(store, route=route, source_agent=source)
        assert (verdict.verdict, verdict.reason) == (DROP, reason)

    def test_a_sensitive_fact_reaches_only_a_teammate_who_reads_its_tag(self, store):
        money = _signal(tags=("finance", "food"))
        assert self.judge(store, signal=money).reason == "tagged finance, which Ramsay doesn't read"
        to_miles = self.judge(store, signal=money, route=_route(to="reward-travel", offer="track card credits"))
        assert to_miles.verdict == DELIVER
        marked = _signal(sensitivity="sensitive")
        assert self.judge(store, signal=marked).verdict == DELIVER  # Ramsay reads health
        to_eric = self.judge(store, signal=marked, route=_route(to="excursion-planner", offer="activity ideas"))
        assert to_eric.reason == "sensitive, and Eric reads neither health nor finance"

    def test_another_hosts_teammate(self, store):
        verdict = self.judge(store, governor=_governor(store, owns=lambda agent_id: False))
        assert verdict.reason == "Ramsay is answered by another host"

    def test_the_same_fact_twice_within_72_hours(self, store):
        decision = store.start_decision(tap="effect", session_id="s", first_turn_at=NOW, mode="shadow", at=NOW)
        signal_id = store.add_signal({"decision_id": decision, "at": NOW - 3600, "kind": "artifact", "gist": "g",
                                      "dedupe_key": "artifact:abc"})
        store.add_route({"signal_id": signal_id, "decision_id": decision, "to_agent": "meal-planner", "asked": "now",
                         "urgency": "now", "verdict": "deliver"})
        verdict = self.judge(store, dedupe_key="artifact:abc")
        assert verdict.reason == "Ramsay already had this within 72 h"
        assert self.judge(store, dedupe_key="artifact:other").verdict == DELIVER

    def test_absorbs(self, store):
        assert self.judge(store, route=_route(urgency="absorb")).verdict == ABSORB
        cascade = self.judge(store, hops=1)
        assert (cascade.verdict, cascade.reason) == (ABSORB, "it came from a proactive turn, which can't start another")

    def test_talking_to_the_recipient_now_means_no_post(self, store):
        store.record_turn({"at": NOW - 120, "agent_id": "meal-planner", "caller": "main", "tap": "chat"})
        verdict = self.judge(store)
        assert (verdict.verdict, verdict.reason) == (ABSORB, "you're talking to Ramsay right now")

    def test_now_is_capped_to_today_unless_it_is_an_artifact_a_request_or_imminent(self, store):
        situation = _signal(kind="situation", tags=("travel",), until="2026-10-27")
        eric = _route(to="excursion-planner", offer="activity ideas", urgency="now")
        verdict = self.judge(store, signal=situation, route=eric)
        assert (verdict.verdict, verdict.urgency, verdict.deliver_at) == (DELIVER, "today", None)
        assert verdict.reason.startswith("now only for")
        imminent = _route(to="excursion-planner", offer="activity ideas", urgency="now",
                          window=("2026-10-24", "2026-10-24"))
        assert self.judge(store, signal=situation, route=imminent).urgency == "now"

    def test_next_becomes_today_for_a_teammate_with_no_scheduled_run(self, store):
        """Live, a `next` route rides along on a scheduled run: without one it would never land."""
        gap = _signal(kind="request", tags=("travel",))
        eric_next = _route(to="excursion-planner", offer="activity ideas", urgency="next")
        assert self.judge(store, signal=gap, route=eric_next).urgency == "next"  # his card has a run
        team = dict(TEAM, **{"excursion-planner": replace(TEAM["excursion-planner"], scheduled=False)})
        governor = Governor(store, team, owns=lambda agent_id: True, rng=Draw(0.9), clock=lambda: NOW)
        verdict = self.judge(store, signal=gap, route=eric_next, governor=governor)
        assert (verdict.verdict, verdict.urgency, verdict.reason) == (
            DELIVER, "today", "Eric has no scheduled run to wait for")

    def test_worth_posting_draws_from_the_kinds_acceptance(self, store):
        low = self.judge(store, governor=_governor(store, draw=WORTH_POSTING - 0.01))
        assert low.verdict == ABSORB and low.reason.startswith("not worth a post yet")
        governor = _governor(store, draw=0.9)
        self.judge(store, governor=governor)
        assert governor.rng.last == (2, 1)  # a new kind gets the benefit of the doubt
        decision = store.start_decision(tap="effect", session_id="s", first_turn_at=NOW, mode="shadow", at=NOW)
        signal_id = store.add_signal({"decision_id": decision, "at": NOW, "kind": "artifact", "gist": "g"})
        route_id = store.add_route({"signal_id": signal_id, "decision_id": decision, "to_agent": "meal-planner",
                                    "asked": "now", "urgency": "now", "verdict": "deliver",
                                    "kind_key": kind_key("meal-planner", _signal())})
        store.label_routes([route_id], "bad")
        self.judge(store, governor=governor)
        assert governor.rng.last == (2, 2)  # a declined kind fades


def test_the_prior_posts_a_new_kind_about_nine_times_in_ten():
    from core.proactive.governor import PRIOR_DECLINED, PRIOR_ENGAGED
    rng = random.Random(7)
    draws = [rng.betavariate(PRIOR_ENGAGED, PRIOR_DECLINED) for _ in range(20_000)]
    posted = sum(d >= WORTH_POSTING for d in draws) / len(draws)
    assert 0.88 < posted < 0.94
    declined = [rng.betavariate(PRIOR_ENGAGED, PRIOR_DECLINED + 6) for _ in range(20_000)]
    assert sum(d >= WORTH_POSTING for d in declined) / len(declined) < 0.35


class TestWhenItWouldBeDelivered:

    def test_a_quiet_stretch(self):
        minute = 60
        since = 10_000.0
        # The chat that led to the decision, then turns at +5 and +20 minutes, then
        # quiet: the stretch ends 30 minutes after +20.
        activity = [since - 90, since + 5 * minute, since + 20 * minute]
        assert quiet_stretch(activity, since, now=since + 49 * minute, quiet_s=30 * minute) is None
        assert quiet_stretch(activity, since, now=since + 60 * minute, quiet_s=30 * minute) == since + 50 * minute
        # Already quiet for 30 minutes when it was decided (a schedule's post): at once.
        assert quiet_stretch([since - 40 * minute], since, now=since + 1, quiet_s=30 * minute) == since

    def _route(self, store, urgency, at, window_end=None, to="excursion-planner"):
        decision = store.start_decision(tap="chat", session_id="s", first_turn_at=at, mode="shadow", at=at)
        signal_id = store.add_signal({"decision_id": decision, "at": at, "kind": "situation", "gist": "g"})
        return store.add_route({"signal_id": signal_id, "decision_id": decision, "to_agent": to, "asked": urgency,
                                "urgency": urgency, "verdict": "deliver", "window_end": window_end})

    def test_today_waits_for_the_quiet_and_next_for_a_touchpoint(self, store):
        store.record_turn({"at": NOW - 90, "agent_id": "day-planner", "caller": "main", "tap": "chat"})
        today = self._route(store, "today", NOW)
        nxt = self._route(store, "next", NOW)
        store.record_turn({"at": NOW + 600, "agent_id": "day-planner", "caller": "main", "tap": "chat"})
        assert resolve_times(store, now=NOW + 1200) == []
        assert resolve_times(store, now=NOW + 600 + 1800) == [today]
        assert store.route(today)["deliver_at"] == NOW + 600 + 1800
        store.record_turn({"at": NOW + 7200, "agent_id": "excursion-planner", "caller": "", "tap": "scheduled"})
        assert resolve_times(store, now=NOW + 7300) == [nxt]
        assert store.route(nxt)["deliver_at"] == NOW + 7200

    def test_a_today_whose_window_closed_waits_for_the_next_touchpoint(self, store):
        route_id = self._route(store, "today", NOW, window_end="2026-10-24")
        busy = [NOW - 90] + [NOW + i * 600 for i in range(1, 100)]
        for at in busy:
            store.record_turn({"at": at, "agent_id": "day-planner", "caller": "main", "tap": "chat"})
        resolve_times(store, now=NOW + timedelta(days=1).total_seconds())
        route = store.route(route_id)
        assert (route["urgency"], route["deliver_at"]) == ("next", None)
        assert route["reason"] == "no quiet stretch before its window closed"
