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
    MAX_PER_TICK,
    REPORT_CHARS,
    DueFollowUps,
    FollowUpSchedule,
    FollowUpSweep,
    fingerprint,
    radio_line,
)
from core.scheduler.preconditions import PreconditionError
from core.scheduler.spec import Findings, Problems, ScheduleContext, SessionPolicy, WorkDecision
from core.storage.follow_ups import Check, Closer, FollowUpStore, Result, Status
from core.util.local_time import get_local_timezone

NOW = datetime(2026, 11, 1, 9, 0, tzinfo=get_local_timezone()).timestamp()  # a Sunday
HOUR, DAY = 3600, 86400
MODEL = Check(type="model", what="new award space SFO to MAD in business")
AWARD = Check(type="award_watch", args={"origin": "SFO", "destination": "MAD"})
TURN = SimpleNamespace(source="followup")  # the check's context, as the output sees it
HOMES = {"reward-travel": "lifestyle-perk"}
MILES = PeerCard(agent_id="reward-travel", name="Miles", emoji="✈️", home="lifestyle-perk", description="",
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
    return FollowUpSweep(radio, store=store, team=lambda: {"reward-travel": MILES}, clock=lambda: now)


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
        assert radio.lines[-1] == ("📻 🔁 ✈️ Miles closed follow-up #1 · Business award seats SFO→MAD for the Spain "
                                   "trip · you cancelled it: too many pings · 1 check, 1 find", ())

    def test_why_it_closed_and_who_closed_it(self, store):
        base = replace(_open(store), status=Status.CLOSED, checks=6, finds=1)
        assert radio_line(replace(base, closed_by=Closer.AGENT, close_reason="booked on Iberia"),
                          {"reward-travel": MILES}).endswith("· booked on Iberia · 6 checks, 1 find")
        assert radio_line(replace(base, closed_by=Closer.AGENT), {"reward-travel": MILES}) \
            .endswith("· Miles closed it · 6 checks, 1 find")
        assert radio_line(replace(base, closed_by=Closer.UNTIL, close_reason="until passed"), {}) == (
            "📻 🔁 🤖 reward-travel closed follow-up #1 · Business award seats SFO→MAD for the Spain trip · "
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
