"""Team radio (core/proactive/radio.py) and its outcome lines (core/knowledge/memory/outcomes.py)."""
import pytest

from core.knowledge.memory.outcomes import REASONS, missed_line, route_line
from core.proactive import radio as radio_module
from core.proactive.cards import PeerCard
from core.proactive.radio import BAD, GOOD, TeamRadio, line
from core.storage.signals import SignalStore

TEAM = {
    "receipt-processor": PeerCard("receipt-processor", "Receipt Processor", "🧾", "receipts", "", (), (), (), True),
    "meal-planner": PeerCard("meal-planner", "Ramsay", "🧑‍🍳", "meal-planning", "", (), ("update the kitchen inventory",),
                             ("food",), False),
}


class FakeChannel:
    """The Discord side: what was posted, and the ids it was given."""

    def __init__(self):
        self.posts, self.polls = [], []

    async def post(self, text, reactions=()):
        self.posts.append((text, tuple(reactions)))
        return str(1000 + len(self.posts))

    async def poll(self, reply_to, question, answers):
        self.polls.append((reply_to, question, tuple(answers)))
        return str(9000 + len(self.polls))


@pytest.fixture
def store(tmp_path):
    return SignalStore(str(tmp_path / "memory.db"))


@pytest.fixture
def outcomes(monkeypatch):
    lines = []
    monkeypatch.setattr(radio_module, "record_outcome", lines.append)
    return lines


def _decision(store, verdicts=("deliver", "drop")):
    decision_id = store.start_decision(tap="effect", session_id="receipt-processor:tool:receipts", first_turn_at=1.0,
                                       mode="shadow", at=2.0)
    signal_id = store.add_signal({"decision_id": decision_id, "at": 2.0, "kind": "artifact",
                                  "gist": "Groceries from 99 Ranch", "source_agent": "receipt-processor"})
    for verdict in verdicts:
        store.add_route({"signal_id": signal_id, "decision_id": decision_id, "to_agent": "meal-planner",
                         "offer": "update the kitchen inventory", "asked": "now", "urgency": "now",
                         "verdict": verdict, "reason": "evidence not found in the exchange" if verdict == "drop" else "",
                         "kind_key": "meal-planner/artifact/food"})
    return store.decision(decision_id)


def test_a_line_says_who_to_whom_how_urgent_what_and_why():
    decision = {"id": 42, "mode": "shadow"}
    route = {"source_agent": "receipt-processor", "to_agent": "meal-planner", "verdict": "deliver", "urgency": "now",
             "gist": "Groceries from 99 Ranch", "reason": ""}
    assert line(decision, route, TEAM) == "📻 #42 🧾→🧑‍🍳 now · Groceries from 99 Ranch · would deliver at once"
    held = line(decision, dict(route, urgency="today"), TEAM)
    assert held.endswith("today · Groceries from 99 Ranch · held for your first quiet stretch")
    dropped = line(decision, dict(route, verdict="drop", reason="evidence not found in the exchange"), TEAM)
    assert dropped == "📻 #42 🧾→🧑‍🍳 dropped · Groceries from 99 Ranch · evidence not found in the exchange"
    stranger = line(decision, dict(route, to_agent="stranger", verdict="drop", reason="x"), TEAM)
    assert "🧾→🤖 dropped" in stranger


def test_a_next_line_says_where_it_lands():
    """Shadow times it at the first touchpoint; live, it rides along on a scheduled run."""
    route = {"source_agent": "receipt-processor", "to_agent": "meal-planner", "verdict": "deliver", "urgency": "next",
             "gist": "Freezer restocked", "reason": ""}
    shadow = line({"id": 7, "mode": "shadow"}, route, TEAM)
    assert shadow.endswith("next · Freezer restocked · waits for Ramsay's next run or your next conversation with them")
    live = line({"id": 7, "mode": "live"}, route, TEAM)
    assert live.endswith("next · Freezer restocked · rides along on Ramsay's next scheduled run")


@pytest.mark.asyncio
async def test_each_route_gets_a_line_and_only_offered_ones_take_a_label(store, outcomes):
    channel = FakeChannel()
    decision = _decision(store)
    await TeamRadio(channel, store, team=lambda: TEAM).decided(decision)
    assert [reactions for _, reactions in channel.posts] == [(GOOD, BAD), ()]
    assert [store.route(r["id"])["radio_message_id"] for r in decision["routes"]] == ["1001", "1002"]


@pytest.mark.asyncio
async def test_a_decision_with_nothing_routed_posts_nothing(store):
    channel = FakeChannel()
    await TeamRadio(channel, store, team=lambda: TEAM).decided(_decision(store, verdicts=()))
    assert channel.posts == []


@pytest.mark.asyncio
async def test_a_check_mark_labels_the_route_right(store, outcomes):
    channel = FakeChannel()
    radio = TeamRadio(channel, store, team=lambda: TEAM)
    decision = _decision(store, verdicts=("deliver",))
    await radio.decided(decision)
    assert await radio.reacted("1001", GOOD)
    route = store.route(decision["routes"][0]["id"])
    assert route["label"] == "good"
    assert store.acceptance("meal-planner/artifact/food")["engaged"] == 1
    assert outcomes and outcomes[0].startswith("[MEMORY] Switchboard #")
    assert channel.polls == []


@pytest.mark.asyncio
async def test_a_cross_labels_it_wrong_and_asks_why(store, outcomes):
    channel = FakeChannel()
    radio = TeamRadio(channel, store, team=lambda: TEAM)
    decision = _decision(store, verdicts=("deliver",))
    await radio.decided(decision)
    assert await radio.reacted("1001", BAD)
    assert channel.polls == [("1001", f"What was wrong with #{decision['id']}?", REASONS)]
    route_id = decision["routes"][0]["id"]
    assert store.route(route_id)["poll_message_id"] == "9001"
    assert await radio.reacted("1001", BAD)  # again: no second poll
    assert len(channel.polls) == 1

    assert await radio.voted("9001", 2)
    assert store.route(route_id)["label_reason"] == "wrong timing"
    assert outcomes[-1].startswith("[FEEDBACK]") and "Why: wrong timing." in outcomes[-1]
    assert store.acceptance("meal-planner/artifact/food")["declined"] == 1


@pytest.mark.asyncio
async def test_other_messages_and_dropped_lines(store, outcomes):
    channel = FakeChannel()
    radio = TeamRadio(channel, store, team=lambda: TEAM)
    decision = _decision(store, verdicts=("drop",))
    await radio.decided(decision)
    assert not await radio.reacted("77", GOOD)          # not a radio line: the runner carries on
    assert not await radio.reacted("1001", "👍")
    assert await radio.reacted("1001", BAD)             # a dropped line: taken, but no label
    assert store.route(decision["routes"][0]["id"])["label"] is None
    assert outcomes == [] and channel.polls == []
    assert not await radio.voted("123", 1)


def test_outcome_lines():
    route = {"decision_id": 42, "gist": "In Tahoe", "source_agent": "day-planner", "to_agent": "excursion-planner",
             "verdict": "deliver", "urgency": "today", "offer": "activity ideas"}
    assert route_line(route, "good") == ('[MEMORY] Switchboard #42: "In Tahoe" from day-planner to excursion-planner '
                                         '(deliver, today, offer: activity ideas) was right, the user said.')
    bad = route_line(route, "bad", "wrong timing", "he already planned it")
    assert bad.startswith("[FEEDBACK] Switchboard #42")
    assert bad.endswith('was wrong, the user said. Why: wrong timing. They added: "he already planned it".')
    assert missed_line("Ramsay  should have known") == ('[FEEDBACK] The Switchboard missed a route, the user said: '
                                                        '"Ramsay should have known".')
