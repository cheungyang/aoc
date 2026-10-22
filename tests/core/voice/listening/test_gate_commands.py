"""What the voiceprint gate answers in Discord: `!voiceprint`, `!gate review`, and ✅/❌ marks."""
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.voice import settings
from core.voice.listening import gate_commands as gc
from core.voice.listening import speaker_gate as sg
from core.voice.listening.gate_log import Effect, GateRecord, Label, Mode
from tests.helpers import FAKE_VOICEPRINT, FakeSpeakerModel


def gate(saved=True, model=True, **config):
    g = sg.SpeakerGate(model=FakeSpeakerModel() if model else None)
    if not model:
        g._model = None
    if saved:
        sg.Voiceprint(embedding=FAKE_VOICEPRINT, model=FakeSpeakerModel.name, enrolled_at=time.time(),
                      readings=4, consistency=0.82, user_id="42").save(sg.voiceprint_path())
    g.configure({"speaker_gate": "shadow", **config})
    return g


def record(at=None, verdict="NOT_YOU", score=0.31, **kw):
    base = dict(at=time.time() - 60 if at is None else at, mode=Mode.SHADOW, model="fake.onnx", duration_ms=1500,
                verdict=verdict, score=score, voiced_share=0.62, effect=Effect.TURN, transcript="turn left here")
    base.update(kw)
    return GateRecord(**base)


def channel():
    ch = MagicMock()
    messages = []

    async def send(text):
        message = MagicMock(id=1000 + len(messages), content=text)
        message.add_reaction = AsyncMock()
        messages.append(message)
        return message

    ch.send = AsyncMock(side_effect=send)
    ch.messages = messages
    return ch


# --- !voiceprint ------------------------------------------------------------------

def test_status_shows_the_mode_the_voiceprint_who_is_heard_and_the_counts():
    g = gate()
    g.log.add(record(verdict="YOU", score=0.9, compute_ms=12))
    text = gc.voiceprint_command(g, "")
    assert text.startswith("🎙️ **Voiceprint gate:** `shadow`")
    assert "4 readings, consistency 0.82, account 42" in text
    assert "Heard: only account 42." in text
    assert f"yours at {settings.SPEAKER_ACCEPT:.2f} or above" in text
    assert "Last day: 1 scored · you 1 · not you 0 · undecided 0 · median scoring 12 ms." in text


def test_status_says_what_is_missing():
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(settings, "speaker_model_path", lambda: None)
        text = gc.status(gate(saved=False, model=False))
    assert "isn't installed" in text and "--only speaker" in text
    assert "No voiceprint yet: join the voice channel and type `!enroll`." in text
    assert "Heard: everyone in the voice channel." in text
    assert "Nothing scored in the last day." in text


def test_status_flags_a_voiceprint_from_another_model():
    g = gate()

    class Other(FakeSpeakerModel):
        name = "other.onnx"

    g._model = Other()
    assert "enrolled with `fake.onnx`, but the model is now `other.onnx`" in gc.status(g)


def test_voiceprint_reset_and_unknown_words():
    g = gate()
    assert gc.voiceprint_command(g, "reset") == "🗑️ Voiceprint deleted, with the kept clips."
    assert gc.voiceprint_command(g, "reset") == "There was no voiceprint to delete."
    assert gc.voiceprint_command(g, "please") == gc.VOICEPRINT_USAGE


# --- !gate review -------------------------------------------------------------------

def test_a_review_line_says_what_the_gate_heard_and_what_it_did():
    line = gc.review_line(record(at=time.mktime((2026, 10, 22, 10, 31, 0, 0, 0, -1)), effect=Effect.BARGE_IN, id=12))
    assert line == "#12 · Thu 10:31 · **not you** 0.31 · voiced 0.62 · it stopped the reply\n> turn left here"
    assert gc.review_line(record(score=None, transcript="", voiced_share=None, effect=None, id=3)).endswith(
        "**not you** no score\n> (no words)")


@pytest.mark.asyncio
async def test_review_posts_each_doubtful_utterance_with_both_marks_to_tap():
    g = gate()
    rejected = g.log.add(record(verdict="NOT_YOU", score=0.2))
    g.log.add(record(verdict="YOU", score=0.95))  # confidently you: not asked
    ch = channel()
    await gc.gate_command(g, "review", ch)

    header, item = ch.messages
    assert header.content.startswith("🗣️ **1 to check.**")
    assert item.content.startswith(f"#{rejected} ")
    assert [c.args[0] for c in item.add_reaction.await_args_list] == ["✅", "❌"]
    assert g.log.get(rejected).review_message_id == str(item.id)

    again = channel()
    await gc.gate_command(g, "review", again)
    assert [m.content for m in again.messages] == ["🗣️ Nothing to review from the last day."], "posted once"


@pytest.mark.asyncio
async def test_review_all_takes_confident_ones_too():
    g = gate()
    g.log.add(record(verdict="YOU", score=0.95))
    ch = channel()
    await gc.gate_command(g, "review all", ch)
    assert len(ch.messages) == 2


@pytest.mark.asyncio
async def test_gate_alone_says_how_with_the_counts():
    ch = channel()
    await gc.gate_command(gate(), "", ch)
    assert ch.messages[0].content == gc.GATE_USAGE + "\nNothing scored in the last day."


def test_a_mark_on_a_review_message_labels_it():
    g = gate()
    record_id = g.log.add(record())
    g.log.set_review_message(record_id, 4242)
    assert gc.mark_from_reaction(g, 4242, "❌") is True
    assert g.log.get(record_id).label is Label.NOT_YOU
    assert gc.mark_from_reaction(g, 4242, "✅") is True
    assert g.log.get(record_id).label is Label.YOU, "the latest mark wins"
    assert gc.mark_from_reaction(g, 4242, "👍") is False
    assert gc.mark_from_reaction(g, 9999, "✅") is False, "not a review message"
