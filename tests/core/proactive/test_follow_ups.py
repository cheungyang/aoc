"""Follow-up checks (core/proactive/follow_ups.py): the check, the provider that claims it, and the sweep."""
import asyncio
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace

import pytest

from core.channel.discord.ownership import PROD
from core.channel.response_parser import AgentResponse
from core.config.settings import Config
from core.proactive import follow_ups as follow_ups_module
from core.proactive.cards import PeerCard
from core.proactive.follow_ups import (
    CLAIM_S,
    LABEL,
    LATE_S,
    MAX_PER_TICK,
    ONCE_LABEL,
    REPORT_CHARS,
    DueFollowUps,
    FollowUpSchedule,
    FollowUpSweep,
    fingerprint,
    is_last_check,
    late_text,
    radio_line,
)
from core.scheduler.preconditions import PreconditionError
from core.scheduler.spec import Findings, Problems, ScheduleContext, SessionPolicy, WorkDecision
from core.storage.channel_inbox import Priority
from core.storage.follow_ups import RETRY_S, Check, Closer, FollowUpStore, Result, Status, end_of
from core.util.local_time import get_local_timezone
from tests.helpers import make_follow_up

NOW = datetime(2026, 11, 1, 9, 0, tzinfo=get_local_timezone()).timestamp()  # a Sunday
HOUR, DAY = 3600, 86400
MODEL = Check(type="model", what="new award space SFO to MAD in business")
AWARD = Check(type="award_watch", args={"origin": "SFO", "destination": "MAD"})
TURN = SimpleNamespace(source="followup")  # the check's context, as the output sees it
HOMES = {"reward-travel": "lifestyle-perk", "agent-designer": "agent-management"}
MILES = PeerCard(agent_id="reward-travel", name="Miles", emoji="✈️", home="lifestyle-perk", description="",
                 interests=(), offers=(), topics=(), derived=True)
AKI = PeerCard(agent_id="agent-designer", name="Aki", emoji="🎨", home="agent-management", description="",
               interests=(), offers=(), topics=(), derived=True)


@pytest.fixture
def store(tmp_path):
    return FollowUpStore(str(tmp_path / "memory.db"))


@pytest.fixture
def live():
    with Config().overriding(proactive_delivery=True):
        yield


@pytest.fixture(autouse=True)
def homes(monkeypatch):
    """Each agent's home channel, stood in for: no agent.json read."""
    monkeypatch.setattr(follow_ups_module, "home_channel", lambda agent_id, loader: HOMES.get(agent_id))


class Scan:
    """A scan precondition, stood in for: what it decides, and the configs it was built from."""

    def __init__(self, decision):
        self.decision = decision
        self.built = []

    def evaluate(self, ctx):
        return self.decision


@pytest.fixture
def scan(monkeypatch):
    stub = Scan(WorkDecision(False, "no award space"))

    def build(config, agent_id=None):
        stub.built.append((config, agent_id))
        return stub

    monkeypatch.setattr(follow_ups_module, "build_precondition", build)
    return stub


def _open(store, check=MODEL, agent_id="reward-travel", at=NOW - 12 * HOUR, **values):
    """A follow-up of Miles', opened in #lifestyle-perk, due at NOW by default."""
    fields = dict(goal="Business award seats SFO→MAD for the Spain trip", every_s=12 * HOUR, until="2026-11-30",
                  close_when="Alva books the flights", channel="lifestyle-perk",
                  session_id="reward-travel:discord:lifestyle-perk")
    fields.update(values)
    return store.open(agent_id=agent_id, check=check, at=at, **fields)


def _model(what):
    return Check(type="model", what=what)


def _ctx(spec):
    return ScheduleContext(schedule_id=spec.schedule_id, agent_id=spec.agent_id, now=NOW)


class Channel:
    """A Discord channel: what was posted in it, as messages 7001, 7002, ..."""

    def __init__(self):
        self.sent = []

    async def send(self, text, **kwargs):
        self.sent.append(text)
        return SimpleNamespace(id=7000 + len(self.sent))


class Holds:
    """Ownership, stood in for: whether the claims are known yet, and which agents this host holds."""

    def __init__(self, role=PROD, ready=True, held=("reward-travel",)):
        self.role, self.ready, self.held = role, ready, set(held)

    def is_ready(self):
        return self.ready

    def is_mine(self, agent_id):
        return self.ready and agent_id in self.held


class TestTheCheck:

    def test_it_is_the_agents_one_shot_turn_in_its_home_channel(self, store):
        spec = FollowUpSchedule(_open(store), store=store)
        assert (spec.agent_id, spec.channel, spec.source, spec.label) == ("reward-travel", "lifestyle-perk",
                                                                          "followup", LABEL)
        assert spec.schedule_id == "followup:1"  # a scan's last_success_at is this follow-up's own
        assert spec.session_policy is SessionPolicy.STATELESS and spec.thinking_level is None
        assert spec.has_work(_ctx(spec))  # a model check runs at every due check

    def test_without_a_home_channel_it_posts_where_it_was_opened(self, store):
        opened = _open(store, agent_id="excursion-planner", channel="weekend-planning")
        assert FollowUpSchedule(opened, store=store).channel == "weekend-planning"

    def test_a_scan_check_runs_only_on_findings_new_since_the_last_check(self, store, scan):
        found = Findings("award_watch", "Iberia, Dec 10: 2 seats at 70k", ())
        scan.decision = WorkDecision(True, "new award space", detail=found)
        opened = _open(store, check=AWARD)
        spec = FollowUpSchedule(opened, store=store)
        assert scan.built == [({"type": "award_watch", "args": {"origin": "SFO", "destination": "MAD"}},
                               "reward-travel")]
        decision = spec.has_work(_ctx(spec))
        assert decision and decision.detail is found
        assert spec.found == fingerprint(decision)
        # The same findings at the next check are nothing new: no turn, an empty check.
        again = FollowUpSchedule(replace(opened, last_fingerprint=spec.found), store=store)
        store.claim(opened.id, NOW)
        assert tuple(again.has_work(_ctx(again))) == (False, "nothing new since the last check")
        again.on_skip(_ctx(again), "nothing new since the last check")
        assert (store.get(opened.id).checks, store.get(opened.id).empty_checks) == (1, 1)

    def test_a_scan_that_finds_nothing_is_empty_and_one_that_cant_read_is_a_problem(self, store, scan):
        opened = _open(store, check=AWARD)
        quiet = FollowUpSchedule(opened, store=store)
        assert not quiet.has_work(_ctx(quiet))
        quiet.on_skip(_ctx(quiet), "no award space")
        assert store.get(opened.id).checks == 1
        problems = Problems("award_watch", ("seats.aero refused the key (401)",))
        scan.decision = WorkDecision(False, "award_watch can't run", detail=problems)
        broken = FollowUpSchedule(opened, store=store)
        decision = broken.has_work(_ctx(broken))
        assert not decision and decision.detail is problems  # the runner posts it, once per issue a day
        broken.on_skip(_ctx(broken), decision.reason)
        assert store.get(opened.id).checks == 1  # a problem counts nothing

    def test_the_fingerprint_is_the_findings_or_the_reason(self):
        with_text = WorkDecision(True, "r", detail=Findings("s", " Iberia 70k \n", ("one issue",)))
        assert fingerprint(with_text) == fingerprint(WorkDecision(True, "other", detail=Findings("s", "Iberia 70k",
                                                                                                ("one issue",))))
        assert fingerprint(with_text) != fingerprint(WorkDecision(True, "r", detail=Findings("s", "Iberia 70k", ())))
        assert fingerprint(WorkDecision(True, "2 new")) != fingerprint(WorkDecision(True, "3 new"))

    def test_the_prompt_has_the_goal_what_it_last_said_and_what_to_check(self, store):
        watched = replace(_open(store, at=NOW - 2 * DAY), checks=2, last_report="Nothing at 80k yet; Iberia is close.",
                          last_found_at=NOW - DAY)
        spec = FollowUpSchedule(watched, store=store)
        prompt = spec.build_prompt(_ctx(spec), spec.has_work(_ctx(spec)))
        for part in ("This is your follow-up #1", "Nobody asked you for a message now", "It is Sunday 2026-11-01 09:00.",
                     "The goal: Business award seats SFO→MAD for the Spain trip",
                     "It ends early when: Alva books the flights",
                     "You opened it on Friday 2026-10-30, and you've checked it 2 times since. It runs until 2026-11-30.",
                     "What you last told the user, on Saturday 2026-10-31:",
                     '"Nothing at 80k yet; Iberia is close."',
                     "Check now: new award space SFO to MAD in business",
                     "at most 1,500 characters", "reply with exactly <silent/> and nothing else",
                     'close this follow-up (follow_up with op "close", id 1 and the reason)'):
            assert part in prompt, part
        fresh = FollowUpSchedule(_open(store, check=_model("Tahoe snow"), close_when=""), store=store)
        prompt = fresh.build_prompt(_ctx(fresh), WorkDecision(True, "due"))
        assert "you haven't checked it since" in prompt
        assert "What you last told the user" not in prompt and "It ends early when" not in prompt

    def test_a_scan_checks_findings_follow_the_prompt(self, store, scan):
        spec = FollowUpSchedule(_open(store, check=AWARD), store=store)
        decision = WorkDecision(True, "new award space", detail=Findings("award_watch", "Iberia: 2 seats at 70k"))
        prompt = spec.build_prompt(_ctx(spec), decision)
        assert "Your check found something new: below." in prompt and "Check now" not in prompt
        assert prompt.endswith('<findings source="award_watch">\nIberia: 2 seats at 70k\n</findings>')

    @pytest.mark.asyncio
    async def test_its_run_counts_the_check(self, store, scan):
        scan.decision = WorkDecision(True, "new", detail=Findings("award_watch", "Iberia: 2 seats at 70k"))
        posted, silent, nowhere, failed = (_open(store, check=replace(AWARD, args=dict(AWARD.args, n=i)))
                                           for i in range(4))
        specs = {}
        for follow_up, reply in ((posted, "Two business seats SFO→MAD on Dec 10, 70k each. " + "x" * 700),
                                 (silent, "<silent/>")):
            store.claim(follow_up.id, NOW)
            spec = specs[follow_up.id] = FollowUpSchedule(follow_up, store=store)
            spec.has_work(_ctx(spec))
            await spec.output_for(Channel()).deliver(TURN, AgentResponse.from_string(reply))
            spec.on_success(_ctx(spec))
        FollowUpSchedule(nowhere, store=store).on_success(_ctx(specs[posted.id]))  # its channel wasn't found
        FollowUpSchedule(failed, store=store).on_failure(_ctx(specs[posted.id]))

        checked = {key: store.get(follow_up.id) for key, follow_up in
                   (("posted", posted), ("silent", silent), ("nowhere", nowhere), ("failed", failed))}
        assert [(f.checks, f.finds) for f in checked.values()] == [(1, 1), (1, 0), (0, 0), (0, 0)]
        assert checked["posted"].last_report.startswith("Two business seats SFO→MAD on Dec 10, 70k each.")
        assert len(checked["posted"].last_report) <= REPORT_CHARS + 4 and checked["posted"].last_report.endswith("[…]")
        # A silent reply keeps the scan's new fingerprint: the same findings won't start a turn again.
        assert checked["silent"].last_fingerprint == checked["posted"].last_fingerprint == specs[silent.id].found
        assert all(f.claimed_at is None for f in checked.values())

    @pytest.mark.asyncio
    async def test_a_silent_reply_posts_nothing_and_a_post_goes_out(self, store):
        channel = Channel()
        spec = FollowUpSchedule(_open(store), store=store)
        output = spec.output_for(channel)
        await output.deliver(TURN, AgentResponse.from_string("<silent/>"))
        assert channel.sent == [] and output.outcome == "silent"
        await output.deliver(TURN, AgentResponse.from_string("Iberia opened two seats on Dec 10."))
        assert channel.sent == ["Iberia opened two seats on Dec 10."] and output.text == channel.sent[0]


class TestTheLastCheck:
    """The check after which no other comes before `until` ends: its turn always runs, offers
    to keep watching, and its post is a find only for a scan's new findings."""

    def test_it_is_the_check_no_other_would_follow_before_until_ends(self):
        last_day = make_follow_up(until="2026-11-01", every_s=12 * HOUR, interval_s=12 * HOUR)
        ends = datetime(2026, 11, 2, tzinfo=get_local_timezone()).timestamp()  # the midnight after it
        assert not is_last_check(last_day, ends - 12 * HOUR - 1)
        assert is_last_check(last_day, ends - 12 * HOUR)  # the next would be due as it ends
        assert not is_last_check(replace(last_day, until="2026-11-02"), ends - 12 * HOUR)  # extended: not yet
        # A late check with a stretched interval isn't the last: near the end the next comes `every` on.
        assert not is_last_check(replace(last_day, interval_s=2 * DAY), ends - 30 * HOUR)

    def test_a_scan_with_nothing_new_runs_its_turn_on_the_last_check_only(self, store, scan):
        found = Findings("award_watch", "Iberia, Dec 10: 2 seats at 70k", ())
        scan.decision = WorkDecision(True, "award space", detail=found)
        seen = replace(_open(store, check=AWARD, every_s=DAY, until="2026-11-02"),
                       last_fingerprint=fingerprint(scan.decision))
        before = FollowUpSchedule(seen, store=store)  # a day before the last check
        assert tuple(before.has_work(_ctx(before))) == (False, "nothing new since the last check")
        last = FollowUpSchedule(replace(seen, until="2026-11-01"), store=store)
        decision = last.has_work(_ctx(last))
        assert decision and decision.detail is found
        prompt = last.build_prompt(_ctx(last), decision)
        assert "Your check found nothing new since the last one: what it sees now is below." in prompt
        assert prompt.endswith('<findings source="award_watch">\nIberia, Dec 10: 2 seats at 70k\n</findings>')
        # Nothing found, or nothing it could read: the turn runs all the same, and says which.
        scan.decision = WorkDecision(False, "no award space under 80k")
        quiet = FollowUpSchedule(replace(seen, until="2026-11-01"), store=store)
        decision = quiet.has_work(_ctx(quiet))
        assert decision
        assert "Your award_watch check found nothing new: no award space under 80k" in quiet.build_prompt(
            _ctx(quiet), decision)
        problems = Problems("award_watch", ("seats.aero refused the key (401)",))
        scan.decision = WorkDecision(False, "award_watch can't run", detail=problems)
        broken = FollowUpSchedule(replace(seen, until="2026-11-01"), store=store)
        decision = broken.has_work(_ctx(broken))
        assert decision and decision.detail is problems  # a turn, so the runner posts no notice: the turn says it
        assert ("Your award_watch check couldn't read what it needed: seats.aero refused the key (401)."
                in broken.build_prompt(_ctx(broken), decision))

    def test_only_the_last_checks_prompt_offers_to_keep_watching(self, store):
        opened = _open(store, every_s=DAY, until="2026-11-01")  # a model check
        last = FollowUpSchedule(opened, store=store)
        prompt = last.build_prompt(_ctx(last), last.has_work(_ctx(last)))
        for part in ("This is your follow-up #1: work you told the user you'd keep doing, and this is its last check.",
                     "It ends on 2026-11-01: no other check comes before then.",
                     "end with one short question, and no other: keep watching, and until when (a date, or stop)?",
                     "Don't extend it yourself: the user answers in the conversation, where you can.",
                     "Never reply <silent/> to this check: if nothing is new, say so in one line, then ask.",
                     "Check now: new award space SFO to MAD in business"):
            assert part in prompt, part
        assert "reply with exactly <silent/>" not in prompt and "decide whether this check is worth one" not in prompt
        earlier = FollowUpSchedule(replace(opened, until="2026-11-02"), store=store)
        prompt = earlier.build_prompt(_ctx(earlier), earlier.has_work(_ctx(earlier)))
        assert "reply with exactly <silent/> and nothing else" in prompt
        assert "decide whether this check is worth one" in prompt
        assert "last check" not in prompt and "keep watching" not in prompt

    @pytest.mark.asyncio
    async def test_its_post_is_a_find_only_for_a_scans_new_findings(self, store, scan):
        new = WorkDecision(True, "award space", detail=Findings("award_watch", "Iberia, Dec 10: 2 seats at 70k"))
        cases = {  # what the scan decides (None: a model check), and the fingerprint it had
            "new findings": (new, ""),
            "the same findings": (new, fingerprint(new)),
            "nothing found": (WorkDecision(False, "no award space"), ""),
            "a problem": (WorkDecision(False, "can't run", detail=Problems("award_watch", ("401",))), ""),
            "a model check": (None, ""),
        }
        counted = {}
        for i, (case, (decision, seen)) in enumerate(cases.items()):
            check = MODEL if decision is None else replace(AWARD, args=dict(AWARD.args, n=i))
            opened = _open(store, check=check, every_s=DAY, until="2026-11-01")
            store.update("follow_ups", {"last_fingerprint": seen, "last_report": "Nothing under 80k yet."},
                         {"id": opened.id})
            store.claim(opened.id, NOW)
            scan.decision = decision
            spec = FollowUpSchedule(store.get(opened.id), store=store)
            assert spec.has_work(_ctx(spec)), case  # the last check always runs its turn
            reply = "Still 2 seats at 70k on Dec 10. Keep watching, and until when?"
            await spec.output_for(Channel()).deliver(TURN, AgentResponse.from_string(reply))
            spec.on_success(_ctx(spec))
            counted[case] = store.get(opened.id)
        assert {case: (f.checks, f.finds, f.empty_checks) for case, f in counted.items()} == {
            "new findings": (1, 1, 0), "the same findings": (1, 0, 1), "nothing found": (1, 0, 1),
            "a problem": (0, 0, 0), "a model check": (1, 0, 1)}
        assert counted["new findings"].last_report.startswith("Still 2 seats")  # only a find keeps what it posted
        assert {f.last_report for case, f in counted.items() if case != "new findings"} == {"Nothing under 80k yet."}
        assert all(f.claimed_at is None for f in counted.values())


class TestTheProvider:

    @pytest.mark.asyncio
    async def test_with_the_switch_off_nothing_is_checked(self, store):
        opened = _open(store)
        assert await DueFollowUps(store)(NOW) == []
        assert store.get(opened.id).claimed_at is None

    @pytest.mark.asyncio
    async def test_two_calls_claim_a_check_once(self, store, live):
        opened = _open(store)
        provider = DueFollowUps(store, ownership=Holds())
        first, second = await provider(NOW), await provider(NOW + 1)
        assert [(type(s), s.follow_up_id) for s in first] == [(FollowUpSchedule, opened.id)] and second == []
        assert store.get(opened.id).claimed_at == NOW

    @pytest.mark.asyncio
    async def test_calls_at_once_claim_it_once(self, store, live):
        _open(store)
        provider = DueFollowUps(store, ownership=Holds())
        claimed = await asyncio.gather(*(provider(NOW) for _ in range(3)))
        assert sorted(len(specs) for specs in claimed) == [0, 0, 1]

    @pytest.mark.asyncio
    async def test_at_most_five_a_tick(self, store, live):
        for i in range(MAX_PER_TICK + 2):
            _open(store, check=_model(f"watch {i}"), at=NOW - 12 * HOUR - i)
        provider = DueFollowUps(store, ownership=Holds())
        first, second = await provider(NOW), await provider(NOW + 30)
        assert (len(first), len(second)) == (MAX_PER_TICK, 2)
        assert [s.follow_up_id for s in first] == [7, 6, 5, 4, 3]  # the longest due first

    @pytest.mark.asyncio
    async def test_a_follow_up_whose_agent_another_host_holds_waits(self, store, live, capsys):
        opened = _open(store)
        holds = Holds(held=())
        provider = DueFollowUps(store, ownership=holds)
        assert await provider(NOW) == [] and await provider(NOW + 30) == []
        assert store.get(opened.id).claimed_at is None  # never claimed: nothing to free later
        assert capsys.readouterr().out.count("waits") == 1  # said once, not every tick
        holds.held.add("reward-travel")
        assert [s.follow_up_id for s in await provider(NOW + 60)] == [opened.id]

    @pytest.mark.asyncio
    async def test_held_elsewhere_doesnt_take_the_places_of_the_rest(self, store, live):
        for i in range(MAX_PER_TICK):
            _open(store, check=_model(f"away {i}"), agent_id="excursion-planner", at=NOW - 13 * HOUR)
        mine = _open(store)
        assert [s.follow_up_id for s in await DueFollowUps(store, ownership=Holds())(NOW)] == [mine.id]

    @pytest.mark.asyncio
    async def test_a_check_that_cant_be_made_is_failed_at_once(self, store, live, monkeypatch, capsys):
        opened = _open(store, check=AWARD)

        def unknown(config, agent_id=None):
            raise PreconditionError("unknown precondition type 'award_watch'")

        monkeypatch.setattr(follow_ups_module, "build_precondition", unknown)
        assert await DueFollowUps(store, ownership=Holds())(NOW) == []
        failed = store.get(opened.id)
        assert (failed.claimed_at, failed.checks, failed.next_check_at) == (None, 0, NOW + 12 * HOUR)
        assert "Could not make the check of follow-up #1: PreconditionError" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_a_claim_that_never_finished_is_freed_and_taken_again(self, store, live, capsys):
        opened = _open(store)
        store.claim(opened.id, NOW)  # a run that never finished
        provider = DueFollowUps(store, ownership=Holds())
        assert await provider(NOW + CLAIM_S - 1) == []
        assert [s.follow_up_id for s in await provider(NOW + CLAIM_S + 1)] == [opened.id]
        assert "never finished" in capsys.readouterr().out


class Radio:
    """Team radio, stood in for: the lines posted, with their reactions. `posts` is False on a
    host that doesn't post (None back), and an exception to raise one."""

    def __init__(self, posts=True):
        self.posts = posts
        self.lines = []

    async def post(self, text, reactions=()):
        if isinstance(self.posts, Exception):
            raise self.posts
        self.lines.append((text, tuple(reactions)))
        return "8001" if self.posts else None


def _sweep(store, radio, now=NOW):
    return FollowUpSweep(radio, store=store, team=lambda: {"reward-travel": MILES, "agent-designer": AKI},
                         clock=lambda: now)


class TestTheSweep:

    @pytest.mark.asyncio
    async def test_it_ends_the_follow_ups_past_their_until_with_the_switch_off(self, store):
        ended, today = _open(store, until="2026-10-31"), _open(store, check=AWARD, until="2026-11-01")
        await _sweep(store, None)()
        closed = store.get(ended.id)
        assert (closed.status, closed.closed_by, closed.close_reason, closed.closed_at) == (Status.CLOSED, Closer.UNTIL,
                                                                                           "until passed", NOW)
        assert store.get(today.id).is_open  # its last day isn't over
        assert closed.radio_state is None  # no radio: nothing posted, nothing marked

    @pytest.mark.asyncio
    async def test_a_line_when_one_opens_and_when_it_closes(self, store):
        radio = Radio()
        opened = _open(store, at=NOW - 60)
        await _sweep(store, radio)()
        await _sweep(store, radio)()  # once
        assert radio.lines == [("📻 🔁 ✈️ Miles opened follow-up #1 · Business award seats SFO→MAD for the Spain trip "
                                "· every 12h until 2026-11-30 · check: model, \"new award space SFO to MAD in "
                                "business\"", ())]
        store.claim(opened.id, NOW)
        store.finish_check(opened.id, Result.FOUND, NOW)
        store.close(opened.id, reason="too many pings", by=Closer.YOU, at=NOW)
        await _sweep(store, radio)()
        assert radio.lines[-1] == ("📻 🔁 ✈️ Miles' follow-up #1 closed · Business award seats SFO→MAD for the Spain "
                                   "trip · you cancelled it: too many pings · 1 check, 1 find", ())

    def test_why_it_closed_and_who_closed_it(self, store):
        base = replace(_open(store), status=Status.CLOSED, checks=6, finds=1)
        assert radio_line(replace(base, closed_by=Closer.AGENT, close_reason="booked on Iberia"),
                          {"reward-travel": MILES}).endswith("· booked on Iberia · 6 checks, 1 find")
        assert radio_line(replace(base, closed_by=Closer.AGENT), {"reward-travel": MILES}) \
            .endswith("· Miles closed it · 6 checks, 1 find")
        assert radio_line(replace(base, closed_by=Closer.SWITCHBOARD, close_reason="booked UA 990"),
                          {"reward-travel": MILES}) == (
            "📻 🔁 ✈️ Miles' follow-up #1 closed · Business award seats SFO→MAD for the Spain trip · "
            "you said it's done: booked UA 990 · 6 checks, 1 find")  # your words closed it, not Miles
        assert radio_line(replace(base, closed_by=Closer.UNTIL, close_reason="until passed"), {}) == (
            "📻 🔁 🤖 reward-travel's follow-up #1 closed · Business award seats SFO→MAD for the Spain trip · "
            "until passed · 6 checks, 1 find")  # no card: the robot and the id

    @pytest.mark.asyncio
    @pytest.mark.parametrize("posts", [False, ConnectionError("Discord is down")], ids=["another host", "an error"])
    async def test_each_line_gets_one_attempt(self, store, posts, capsys):
        opened = _open(store, at=NOW - 60)
        await _sweep(store, Radio(posts))()
        assert store.get(opened.id).radio_state is Status.OPEN  # marked, posted here or not
        assert store.pending_lines() == []
        if isinstance(posts, Exception):
            assert "Team radio missed follow-up #1's open line: ConnectionError" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_old_events_are_marked_without_a_line(self, store):
        radio = Radio()
        old = _open(store, at=NOW - 2 * DAY)
        await _sweep(store, radio)()
        assert radio.lines == [] and store.get(old.id).radio_state is Status.OPEN


LOGO = "The logo redesign: pick between the two directions"
NOTES = "We narrowed it to the serif and the geometric mark; Alva wanted to sleep on it."


def _once(store, due_at=NOW, agent_id="agent-designer", at=NOW - DAY, **values):
    """A one-off of Aki's, opened yesterday in #agent-management, due at NOW by default."""
    fields = dict(goal=LOGO, notes=NOTES, channel="agent-management",
                  session_id="agent-designer:discord:agent-management")
    fields.update(values)
    return store.open_once(agent_id=agent_id, due_at=due_at, at=at, **fields)


class TestTheOneOff:
    """A topic the user asked to come back to at a set time: its turn always runs, announced live,
    and its own run closes it."""

    def test_it_always_runs_announced_live_with_its_own_label(self, store):
        spec = FollowUpSchedule(_once(store), store=store)
        assert (spec.agent_id, spec.channel, spec.source, spec.schedule_id) == ("agent-designer", "agent-management",
                                                                                "followup", "followup:1")
        assert (spec.priority, spec.label, spec.precondition) == (Priority.HIGHEST, ONCE_LABEL, None)
        assert ONCE_LABEL == "a reminder"  # a voice briefing: "Aki has a reminder."
        assert spec.has_work(_ctx(spec))
        watch = FollowUpSchedule(_open(store), store=store)
        assert (watch.priority, watch.label) == (Priority.DEFAULT, LABEL)  # a watch's, as before

    def test_it_never_has_a_last_check_nor_a_scan(self, store, scan):
        opened = _once(store)
        for now in (NOW, end_of(opened.until) - 1, end_of(opened.until) + DAY):
            assert not is_last_check(opened, now)
        # Its check is a model check by construction; read as anything else, it's still never scanned.
        spec = FollowUpSchedule(replace(opened, check=AWARD), store=store)
        assert (scan.built, spec.precondition) == ([], None) and spec.has_work(_ctx(spec))

    def test_its_prompt_has_when_you_asked_the_goal_the_notes_and_the_marked_question(self, store):
        spec = FollowUpSchedule(_once(store), store=store)
        prompt = spec.build_prompt(_ctx(spec), spec.has_work(_ctx(spec)))
        for part in ("This is your follow-up #1: on Saturday 2026-10-31, the user asked you to bring this back to them "
                     "on Sunday 2026-11-01 at 09:00.",
                     "It is Sunday 2026-11-01 09:00.",
                     f"Bring up: {LOGO}",
                     f'Your notes from then:\n"{NOTES}"',
                     "End with one question, and no other: whether to get into it now.",
                     "mark it in your <speech> with <question>...</question>, where it stands, without repeating it",
                     "Only if your notes make bringing it up depend on something that doesn't hold now, reply with "
                     "exactly <silent/> and nothing else.",
                     "Don't close this follow-up yourself: your reply closes it."):
            assert part in prompt, part
        for absent in ("You're bringing it up", "keep watching", "Check now", "<findings"):
            assert absent not in prompt, absent
        bare = FollowUpSchedule(_once(store, due_at=NOW + 60, notes=""), store=store)
        assert "Your notes from then" not in bare.build_prompt(_ctx(bare))

    def test_its_prompt_says_so_when_it_runs_more_than_half_an_hour_late(self, store):
        spec = FollowUpSchedule(_once(store), store=store)

        def prompt_at(late):
            return spec.build_prompt(ScheduleContext(schedule_id=spec.schedule_id, agent_id=spec.agent_id,
                                                     now=NOW + late))

        assert "You're bringing it up" not in prompt_at(LATE_S)  # half an hour is still on time
        assert ("It is Sunday 2026-11-01 09:45.\nYou're bringing it up 45 minutes late: say so in a few words."
                in prompt_at(45 * 60))
        assert "You're bringing it up about 3 hours late: say so in a few words." in prompt_at(3 * HOUR)
        assert (late_text(31 * 60), late_text(89 * 60), late_text(26 * HOUR)) == ("31 minutes", "89 minutes",
                                                                                  "about 26 hours")

    @pytest.mark.asyncio
    async def test_a_post_or_a_silent_reply_closes_it_and_a_failure_is_tried_again(self, store):
        posted, silent, failed, nowhere = (_once(store, due_at=NOW - i * 60) for i in range(4))
        for follow_up, reply in ((posted, "Morning! Yesterday we got the logo down to two directions. Want to get "
                                          "into it now?"),
                                 (silent, "<silent/>")):
            store.claim(follow_up.id, NOW)
            spec = FollowUpSchedule(follow_up, store=store)
            assert spec.has_work(_ctx(spec))
            await spec.output_for(Channel()).deliver(TURN, AgentResponse.from_string(reply))
            spec.on_success(_ctx(spec))
        for follow_up in (failed, nowhere):
            store.claim(follow_up.id, NOW)
        FollowUpSchedule(failed, store=store).on_failure(_ctx(spec))
        FollowUpSchedule(nowhere, store=store).on_success(_ctx(spec))  # its channel wasn't found: nothing posted

        checked = {name: store.get(f.id) for name, f in
                   (("posted", posted), ("silent", silent), ("failed", failed), ("nowhere", nowhere))}
        assert {name: (f.status, f.closed_by, f.close_reason) for name, f in checked.items()} == {
            "posted": (Status.CLOSED, Closer.FIRED, "brought up"),
            "silent": (Status.CLOSED, Closer.FIRED, "nothing to bring up"),
            "failed": (Status.OPEN, None, ""), "nowhere": (Status.OPEN, None, "")}
        assert checked["posted"].last_report.startswith("Morning! Yesterday we got the logo down to two directions.")
        for name in ("failed", "nowhere"):  # one failed attempt each: tried again a quarter of an hour on
            retry = checked[name]
            assert (retry.checks, retry.claimed_at, retry.next_check_at - retry.last_checked_at) == (1, None, RETRY_S)
        assert all(f.claimed_at is None for f in checked.values())

    @pytest.mark.asyncio
    async def test_the_provider_claims_it_at_its_time(self, store, live):
        opened = _once(store, due_at=NOW + 60)
        provider = DueFollowUps(store, ownership=Holds(held=("agent-designer",)))
        assert await provider(NOW) == []
        (spec,) = await provider(NOW + 60)
        assert (spec.follow_up_id, spec.priority, spec.label) == (opened.id, Priority.HIGHEST, ONCE_LABEL)
        assert store.get(opened.id).claimed_at == NOW + 60

    def test_its_radio_lines_say_what_and_when_and_how_it_ended(self, store):
        opened = _once(store, due_at=NOW + 2 * DAY, at=NOW)  # Tuesday, 09:00
        team = {"agent-designer": AKI}
        assert radio_line(opened, team) == f'📻 ⏰ 🎨 Aki will bring up "{LOGO}" at Tue 09:00 · follow-up #1'
        far = replace(opened, due_at=NOW + 20 * DAY)
        assert radio_line(far, team).endswith(" at Nov 21 09:00 · follow-up #1")  # a weekday would mislead
        closed = replace(opened, status=Status.CLOSED, closed_at=NOW + 2 * DAY, closed_by=Closer.FIRED,
                         close_reason="brought up", checks=1, finds=1)
        assert radio_line(closed, team) == f"📻 ⏰ 🎨 Aki's follow-up #1 closed · {LOGO} · brought up"
        assert radio_line(replace(closed, close_reason="couldn't bring it up", checks=3, finds=0), team) == (
            f"📻 ⏰ 🎨 Aki's follow-up #1 closed · {LOGO} · couldn't bring it up · 3 attempts")
        assert radio_line(replace(closed, closed_by=Closer.UNTIL, close_reason="until passed", checks=0), {}) == (
            f"📻 ⏰ 🤖 agent-designer's follow-up #1 closed · {LOGO} · never brought up")
        assert radio_line(replace(closed, closed_by=Closer.YOU, close_reason=""), team).endswith(" · you cancelled it")
        assert "every" not in radio_line(opened, team) + radio_line(closed, team)

    @pytest.mark.asyncio
    async def test_the_sweep_announces_it_and_then_its_end(self, store):
        radio = Radio()
        opened = _once(store, due_at=NOW + 2 * DAY, at=NOW - 60)
        await _sweep(store, radio)()
        await _sweep(store, radio)()  # once
        assert radio.lines == [(f'📻 ⏰ 🎨 Aki will bring up "{LOGO}" at Tue 09:00 · follow-up #1', ())]
        store.claim(opened.id, NOW + 2 * DAY)
        store.finish_check(opened.id, Result.FOUND, NOW + 2 * DAY + 60)
        await _sweep(store, radio, now=NOW + 2 * DAY + 120)()
        assert radio.lines[-1] == (f"📻 ⏰ 🎨 Aki's follow-up #1 closed · {LOGO} · brought up", ())
