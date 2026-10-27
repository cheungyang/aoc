"""Outcomes (core/proactive/outcomes.py) and their lines (core/knowledge/memory/outcomes.py)."""
import asyncio
import sqlite3

import pytest

from core.channel.discord.radio import clear_listeners, on_reaction, reaction_added
from core.knowledge.memory.outcomes import outcome_line, route_line
from core.proactive import radio as radio_module
from core.proactive.outcomes import BAD, GOOD, OUTCOME_S, Outcomes, declined_with
from core.proactive.radio import TeamRadio
from core.storage.signals import SignalStore

POSTED = 1_793_000_000.0  # when the proactive post went out
KIND = "meal-planner/artifact/food"


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def store(tmp_path):
    return SignalStore(str(tmp_path / "memory.db"))


@pytest.fixture
def lines():
    """What was written to main's memory log."""
    return []


def _route(store, urgency="now", to_agent="meal-planner", at=POSTED):
    """A live route of the 99 Ranch receipt to `to_agent`, decided 30 s before `at`."""
    decision_id = store.start_decision(tap="effect", session_id="receipt-processor:tool:receipts",
                                       first_turn_at=at - 40, mode="live", at=at - 30)
    signal_id = store.add_signal({"decision_id": decision_id, "at": at - 30, "kind": "artifact",
                                  "gist": "Groceries from 99 Ranch", "tags": "food", "source_agent": "receipt-processor"})
    return store.add_route({"signal_id": signal_id, "decision_id": decision_id, "to_agent": to_agent,
                            "offer": "update the kitchen inventory", "asked": urgency, "urgency": urgency,
                            "verdict": "deliver", "kind_key": f"{to_agent}/artifact/food"})


def _post(store, status="posted", message_ids=("7001",), to_agent="meal-planner", urgency="now", at=POSTED):
    """`to_agent`'s proactive turn for a new route, finished `status` at `at`, with the messages it posted."""
    route_id = _route(store, urgency=urgency, to_agent=to_agent, at=at)
    store.approve_delivery(route_id, to_agent=to_agent, channel="meal-planning", deliver_at=at - 30, at=at - 30)
    store.claim_delivery(route_id, now=at - 20)
    store.finish_delivery(route_id, status, at=at, message_ids=message_ids)
    return route_id


def _handed(store, to_agent="meal-planner", at=POSTED):
    """A live `next` route, handed to `to_agent`'s scheduled run, which posted at `at`."""
    route_id = _route(store, urgency="next", to_agent=to_agent, at=at)
    store.hand_over([route_id], to_agent=to_agent, channel="meal-planning", now=at - 60)
    store.finish_delivery(route_id, "posted", at=at)  # as SignalsForMe finishes it: no message ids
    return route_id


def _you(store, text, at, **turn):
    """Your turn with Ramsay in #meal-planning, ended at `at`."""
    values = {"at": at, "agent_id": "meal-planner", "channel": "meal-planning",
              "session_id": "meal-planner:discord:meal-planning", "source": "discord", "surface": "text",
              "caller": "main", "tap": "chat", "user_text": text, "reply_text": "On it."}
    values.update(turn)
    return store.record_turn(values)


@pytest.mark.parametrize("reply, phrase", [
    # The whole reply is one of DECLINE, whatever its case and punctuation.
    ("no", "no"), ("Nope.", "nope"), ("No, thanks!", "no thanks"), ("not now", "not now"),
    ("Not interested.", "not interested"), ("STOP", "stop"), ("skip", "skip"), ("pass", "pass"),
    ("Don't", "don't"), ("don’t", "don't"), ("leave it", "leave it"),
    # At most four words, starting with one.
    ("Not now, maybe later", "not now"), ("no thanks, I'm good", "no thanks"), ("stop sending these", "stop"),
    # Longer replies that start with one: an answer to the suggestion.
    ("skip the eggs, add the rest", None), ("don't add the milk, rest is fine", None),
    ("not now, maybe tomorrow evening", None),
    # Ones that start like a no, and aren't one.
    ("no problem, add them all", None), ("No problem!", None), ("no worries", None), ("no rush", None),
    ("no doubt", None),
    # Anything else.
    ("noted", None), ("nothing new?", None), ("Add them all", None), ("", None),
])
def test_a_short_no(reply, phrase):
    assert declined_with(reply) == phrase


@pytest.mark.asyncio
@pytest.mark.parametrize("urgency", ["now", "today"])
async def test_your_reply_is_engaged(store, lines, urgency):
    route_id = _post(store, urgency=urgency)
    _you(store, "Which of them go in the freezer?", POSTED + 300)
    await Outcomes(store, clock=Clock(POSTED + 360), record=lines.append).settle()
    route = store.route(route_id)
    assert (route["outcome"], route["outcome_at"]) == ("engaged", POSTED + 300)
    assert store.acceptance(KIND) == {"engaged": 1, "ignored": 0, "declined": 0}
    assert lines == ['[MEMORY] Switchboard #1: "Groceries from 99 Ranch" from receipt-processor to meal-planner '
                     f'(deliver, {urgency}, offer: update the kitchen inventory) was posted, and the user engaged '
                     'with it (they replied).']


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [
    "Add them all",                       # a poll vote: it reaches Ramsay as your message
    "skip the eggs, add the rest",
    "don't add the milk, rest is fine",
    "no problem, add them all",
])
async def test_a_poll_vote_or_an_answer_that_starts_like_a_no_is_engaged(store, lines, reply):
    route_id = _post(store)
    _you(store, reply, POSTED + 60)
    await Outcomes(store, clock=Clock(POSTED + 120), record=lines.append).settle()
    assert store.route(route_id)["outcome"] == "engaged"


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", ["no", "Nope.", "No thanks!", "not now", "Not interested.", "STOP", "skip", "pass",
                                   "Don't", "leave it", "Not now, maybe later"])
async def test_a_short_no_is_declined(store, lines, reply):
    route_id = _post(store)
    _you(store, reply, POSTED + 60)
    await Outcomes(store, clock=Clock(POSTED + 120), record=lines.append).settle()
    route = store.route(route_id)
    assert (route["outcome"], route["outcome_at"]) == ("declined", POSTED + 60)
    assert store.acceptance(KIND) == {"engaged": 0, "ignored": 0, "declined": 1}
    (line,) = lines
    assert line.startswith("[FEEDBACK] Switchboard #1:")
    assert line.endswith(f'was posted, and the user declined it (they said "{reply}").')


@pytest.mark.asyncio
async def test_only_your_first_turn_with_the_recipient_after_the_post_counts(store, lines):
    route_id = _post(store)
    _you(store, "nope", POSTED - 5)                                  # before the post
    _you(store, "nope", POSTED + 10, agent_id="day-planner")         # with another agent
    _you(store, "nope", POSTED + 20, caller="")                      # an agent asked, not you
    _you(store, "nope", POSTED + 30, tap="scheduled")                # a scheduled run
    _you(store, "", POSTED + 35, caller="", tap="scheduled", source="proactive")  # a proactive turn
    _you(store, "Add them all", POSTED + 40)                         # yours: the first
    _you(store, "nope", POSTED + 50)                                 # and the later ones change nothing
    await Outcomes(store, clock=Clock(POSTED + 60), record=lines.append).settle()
    route = store.route(route_id)
    assert (route["outcome"], route["outcome_at"]) == ("engaged", POSTED + 40)


@pytest.mark.asyncio
async def test_a_check_mark_is_engaged_and_a_cross_declined(store, lines):
    liked = _post(store, message_ids=("7001", "7002"))  # a post split in two messages: either one counts
    disliked = _post(store, message_ids=("7003",), to_agent="excursion-planner")
    outcomes = Outcomes(store, clock=Clock(POSTED + 90), record=lines.append)
    assert not await outcomes.reacted("7002", GOOD)  # always False: the runner goes on
    assert not await outcomes.reacted("7003", BAD)
    assert (store.route(liked)["outcome"], store.route(liked)["outcome_at"]) == ("engaged", POSTED + 90)
    assert store.route(disliked)["outcome"] == "declined"
    assert lines[0].endswith("engaged with it (they reacted ✅).")
    assert lines[1].endswith("declined it (they reacted ❌).")


@pytest.mark.asyncio
async def test_a_day_with_no_reply_and_no_reaction_is_ignored(store, lines):
    route_id = _post(store)
    clock = Clock(POSTED + OUTCOME_S - 1)
    outcomes = Outcomes(store, clock=clock, record=lines.append)
    await outcomes.settle()
    assert store.route(route_id)["outcome"] is None and lines == []
    clock.now = POSTED + OUTCOME_S
    await outcomes.settle()
    route = store.route(route_id)
    assert (route["outcome"], route["outcome_at"]) == ("ignored", POSTED + OUTCOME_S)
    assert store.acceptance(KIND) == {"engaged": 0, "ignored": 1, "declined": 0}
    (line,) = lines
    assert line.startswith("[FEEDBACK]") and line.endswith("ignored it (no reply or reaction within a day).")


@pytest.mark.asyncio
async def test_a_reply_after_the_day_is_too_late(store, lines):
    route_id = _post(store)
    _you(store, "Add them all", POSTED + OUTCOME_S + 60)
    await Outcomes(store, clock=Clock(POSTED + OUTCOME_S + 120), record=lines.append).settle()
    assert store.route(route_id)["outcome"] == "ignored"


@pytest.mark.asyncio
async def test_the_first_outcome_sticks(store, lines):
    clock = Clock(POSTED + 60)
    outcomes = Outcomes(store, clock=clock, record=lines.append)
    liked = _post(store, message_ids=("7001",))
    declined = _post(store, message_ids=("7002",), to_agent="excursion-planner")
    ignored = _post(store, message_ids=("7003",), to_agent="day-planner")

    await outcomes.reacted("7001", GOOD)
    _you(store, "no", POSTED + 120)                                    # a no after your ✅
    _you(store, "nope", POSTED + 120, agent_id="excursion-planner")
    clock.now = POSTED + 180
    await outcomes.settle()
    await outcomes.reacted("7002", GOOD)                               # a ✅ after your no
    clock.now = POSTED + OUTCOME_S + 5
    for emoji in (GOOD, BAD):                                          # a mark after the day
        await outcomes.reacted("7003", emoji)

    assert [store.route(r)["outcome"] for r in (liked, declined, ignored)] == ["engaged", "declined", "ignored"]
    assert len(lines) == 3


@pytest.mark.asyncio
async def test_a_cross_within_the_day_turns_engaged_into_declined(store, lines):
    route_id = _post(store)
    _you(store, "Which ones are frozen?", POSTED + 60)
    outcomes = Outcomes(store, clock=Clock(POSTED + 3600), record=lines.append)
    await outcomes.settle()
    assert store.acceptance(KIND) == {"engaged": 1, "ignored": 0, "declined": 0}

    assert not await outcomes.reacted("7001", BAD)
    route = store.route(route_id)
    assert (route["outcome"], route["outcome_at"]) == ("declined", POSTED + 3600)
    assert store.acceptance(KIND) == {"engaged": 0, "ignored": 0, "declined": 1}  # moved, not added
    assert [line.split(" ")[0] for line in lines] == ["[MEMORY]", "[FEEDBACK]"]
    await outcomes.reacted("7001", GOOD)  # and a ✅ never turns it back
    assert store.route(route_id)["outcome"] == "declined"


@pytest.mark.asyncio
async def test_a_cross_after_the_day_leaves_engaged_alone(store, lines):
    route_id = _post(store)
    _you(store, "Which ones are frozen?", POSTED + 60)
    assert not await Outcomes(store, clock=Clock(POSTED + OUTCOME_S + 1), record=lines.append).reacted("7001", BAD)
    assert store.route(route_id)["outcome"] == "engaged"


@pytest.mark.asyncio
async def test_the_same_mark_from_every_bot_runner_counts_once(store, lines):
    route_id = _post(store)
    outcomes = Outcomes(store, clock=Clock(POSTED + 60), record=lines.append)
    for emoji in (GOOD, BAD):
        assert await asyncio.gather(*(outcomes.reacted("7001", emoji) for _ in range(3))) == [False] * 3
    assert store.route(route_id)["outcome"] == "declined"
    assert store.acceptance(KIND) == {"engaged": 0, "ignored": 0, "declined": 1}
    assert len(lines) == 2  # one per outcome: engaged, then declined
    await outcomes.settle()
    assert len(lines) == 2


@pytest.mark.asyncio
async def test_what_the_ledger_says_comes_before_a_mark(store, lines):
    route_id = _post(store)
    _you(store, "nope", POSTED + 60)
    # The ✅ comes before the sweep has seen your reply: the reply still decides.
    assert not await Outcomes(store, clock=Clock(POSTED + 120), record=lines.append).reacted("7001", GOOD)
    route = store.route(route_id)
    assert (route["outcome"], route["outcome_at"]) == ("declined", POSTED + 60)
    assert len(lines) == 1 and lines[0].endswith('declined it (they said "nope").')


@pytest.mark.asyncio
async def test_a_handed_silent_or_failed_delivery_gets_no_outcome(store, lines):
    handed = _handed(store)
    silent = _post(store, status="silent", message_ids=(), to_agent="excursion-planner")
    failed = _post(store, status="failed", message_ids=("7003",), to_agent="day-planner")  # it posted, then failed
    for agent_id in ("meal-planner", "excursion-planner", "day-planner"):
        _you(store, "nope", POSTED + 60, agent_id=agent_id)
    outcomes = Outcomes(store, clock=Clock(POSTED + OUTCOME_S + 60), record=lines.append)
    await outcomes.settle()
    assert not await outcomes.reacted("7003", BAD)
    assert [store.route(r)["outcome"] for r in (handed, silent, failed)] == [None, None, None]
    assert lines == [] and store.unsettled_posts() == []


@pytest.mark.asyncio
async def test_your_label_wins_and_takes_over_the_outcomes_count(store, lines):
    relabeled = _post(store, message_ids=("7001",))
    labeled_first = _post(store, message_ids=("7002",), to_agent="excursion-planner")
    outcomes = Outcomes(store, clock=Clock(POSTED + 60), record=lines.append)
    await outcomes.reacted("7001", GOOD)
    store.label_routes([relabeled], "bad")  # you said it was wrong after all
    assert store.acceptance(KIND) == {"engaged": 0, "ignored": 0, "declined": 1}

    store.label_routes([labeled_first], "good")
    await outcomes.reacted("7002", BAD)  # the outcome is kept on the route; the label counts
    assert store.route(labeled_first)["outcome"] == "declined"
    assert store.acceptance("excursion-planner/artifact/food") == {"engaged": 1, "ignored": 0, "declined": 0}


@pytest.mark.asyncio
async def test_other_messages_and_other_emoji_are_left_alone(store, lines):
    route_id = _post(store)
    outcomes = Outcomes(store, clock=Clock(POSTED + 60), record=lines.append)
    assert not await outcomes.reacted("999", GOOD)
    assert not await outcomes.reacted("7001", "👍")
    assert not await outcomes.reacted("700", GOOD)  # part of an id isn't the id
    assert store.route(route_id)["outcome"] is None and lines == []


@pytest.mark.asyncio
async def test_a_board_error_is_logged_not_raised(lines, capsys):
    class Locked:
        def __getattr__(self, name):
            def locked(*args, **kwargs):
                raise sqlite3.OperationalError("database is locked")
            return locked

    outcomes = Outcomes(Locked(), clock=Clock(POSTED), record=lines.append)
    await outcomes.settle()
    assert not await outcomes.reacted("7001", GOOD)
    out = capsys.readouterr().out
    assert "Could not settle the posts' outcomes" in out and "Could not take ✅ on message 7001" in out


@pytest.mark.asyncio
async def test_a_post_that_fails_to_settle_doesnt_hold_back_the_others(store, lines, monkeypatch, capsys):
    broken, fine = _post(store, message_ids=("7001",)), _post(store, message_ids=("7002",), to_agent="excursion-planner")
    first_user_turn = store.first_user_turn

    def locked_for_ramsay(agent_id, **window):
        if agent_id == "meal-planner":
            raise sqlite3.OperationalError("database is locked")
        return first_user_turn(agent_id, **window)

    monkeypatch.setattr(store, "first_user_turn", locked_for_ramsay)
    await Outcomes(store, clock=Clock(POSTED + OUTCOME_S), record=lines.append).settle()
    assert (store.route(broken)["outcome"], store.route(fine)["outcome"]) == (None, "ignored")
    assert f"Could not settle the post of route #{broken}" in capsys.readouterr().out


@pytest.fixture
def listeners(monkeypatch):
    monkeypatch.setattr(radio_module, "record_outcome", lambda line: None)
    yield
    clear_listeners()


class RadioThread:
    """The team-radio thread, stood in for."""

    async def post(self, text, reactions=()):
        return None

    async def poll(self, reply_to, question, answers):
        return None


@pytest.mark.asyncio
async def test_a_mark_on_a_radio_line_stays_a_label_and_one_on_a_post_goes_on_to_the_inbox(store, lines, listeners):
    """Registered as install() registers them: team radio first."""
    route_id = _post(store)
    store.set_radio_message(route_id, "1001")  # its team radio line
    on_reaction(TeamRadio(RadioThread(), store, team=dict).reacted)
    on_reaction(Outcomes(store, clock=Clock(POSTED + 60), record=lines.append).reacted)

    assert await reaction_added("1001", GOOD)  # team radio took it: a label, not an outcome
    assert (store.route(route_id)["label"], store.route(route_id)["outcome"]) == ("good", None)
    assert not await reaction_added("7001", GOOD)  # not taken: the runner goes on, and finishes the inbox row
    assert store.route(route_id)["outcome"] == "engaged"
    assert store.acceptance(KIND) == {"engaged": 1, "ignored": 0, "declined": 0}  # the label's count, once


def test_an_outcomes_line_is_a_precedent_or_feedback():
    route = {"decision_id": 42, "gist": "Groceries from 99 Ranch", "source_agent": "receipt-processor",
             "to_agent": "meal-planner", "verdict": "deliver", "urgency": "now", "offer": "update the kitchen inventory"}
    what = ('Switchboard #42: "Groceries from 99 Ranch" from receipt-processor to meal-planner '
            '(deliver, now, offer: update the kitchen inventory)')
    assert outcome_line(route, "engaged", "they replied") == (
        f"[MEMORY] {what} was posted, and the user engaged with it (they replied).")
    assert outcome_line(route, "declined", 'they said "not now"') == (
        f'[FEEDBACK] {what} was posted, and the user declined it (they said "not now").')
    assert outcome_line(route, "ignored") == f"[FEEDBACK] {what} was posted, and the user ignored it."
    assert route_line(route, "good") == f"[MEMORY] {what} was right, the user said."  # the same shape
