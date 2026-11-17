"""`!learn` (core/runtime/commands/typed/learn.py): the learning loop's view, typed in a channel."""
from unittest.mock import AsyncMock

import pytest

from core.runtime.commands.base import TypedContext
from core.runtime.commands.registry import TYPED
from core.storage.learning import LearningStore


@pytest.mark.asyncio
async def test_it_is_typed_in_a_channel_the_bot_answers():
    channel = AsyncMock()
    assert await TYPED.answer("!learn", TypedContext(channel=channel, agent_id="main", answers_here=True))
    posted = "".join(call.args[0] for call in channel.send.await_args_list)
    assert posted.startswith("🪞 **Learning loop**")
    assert "No nightly scan has run yet" in posted
    assert await TYPED.answer("!learn", TypedContext(channel=AsyncMock(), agent_id="main", answers_here=False)) is None


@pytest.mark.asyncio
async def test_a_label_typed_in_the_channel_is_recorded():
    store = LearningStore()  # this test's memory.db (tests/conftest.py)
    store.record_friction([{"at": 1.0, "detector": "refusal", "source": "learning_events", "source_id": "9"}])
    [card] = store.friction(0)
    channel = AsyncMock()
    assert await TYPED.answer(f"!learn label {card['id']} not", TypedContext(channel=channel, agent_id="main",
                                                                             answers_here=True))
    assert channel.send.await_args_list[0].args[0].startswith(f"Labeled #{card['id']} as not friction.")
    assert store.precision() == {"refusal": (0, 1)}


def test_help_lists_it_with_its_usage():
    learn = TYPED.find("learn")
    assert learn.usage == "[friction [agent] | cards [n] | label <id>… real|not | ledger]"
    assert learn.summary
