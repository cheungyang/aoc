"""Words a cancelled voice turn left unanswered in history, and the turn that repeats them.

You pause mid-thought, the voice room takes the pause for the end of your turn,
and the agent starts on your words. You go on talking, so that turn is
cancelled before it said anything, and the next one answers everything still
unanswered, your earlier words first (core/channel/voice/turn.py,
`merge_transcripts`). But the cancelled turn had already put your words in the
agent's history -- LangGraph saves a run's input as it starts -- so the agent
used to read them twice: unanswered on their own, then again at the start of
the message that answers them. A turn cancelled twice left them three times.

`unanswered_repeats` finds those copies for the turn starting now: the human
messages right before its own, with no reply after them, whose words its own
message starts with. graphs/main drops them as a voice turn starts (its
`route` step), in each history the turn runs on: main's for the channel, and
the agent's.

The words are compared as `message_text` reads them, without the time stamp
and the `<caller>` line around them. A copy that carries more than the words
-- the capsule of a hand-off, which the cancelled turn already claimed --
doesn't match, and stays.
"""
from typing import List, Sequence

from langchain_core.messages import BaseMessage, HumanMessage

from core.runtime.foundation.addressing import message_text


def unanswered_repeats(messages: Sequence[BaseMessage]) -> List[str]:
    """The ids of the human messages left unanswered right before the newest
    message, whose words the newest one starts with, oldest first.

    Only the run of human messages directly before it counts: a reply in
    between means they were answered. The newest message must be a human
    one, and every copy must have an id to be dropped by.
    """
    if len(messages) < 2 or not isinstance(messages[-1], HumanMessage):
        return []
    newest = message_text(messages[-1].content)
    if not newest:
        return []
    ids: List[str] = []
    for message in reversed(messages[:-1]):
        if not isinstance(message, HumanMessage):
            break
        words = message_text(message.content)
        if not words or not message.id or not _starts_with(newest, words):
            break
        ids.append(message.id)
    return list(reversed(ids))


def _starts_with(text: str, words: str) -> bool:
    """Whether `text` opens with all of `words`, ending where a word ends:
    utterances are merged with a space between them."""
    return text == words or text.startswith(words + " ")
