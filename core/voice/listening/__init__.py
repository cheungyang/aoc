"""The user's side of the conversation: hearing them, and taking turns.

    vad_sink.py     cuts the speaker's audio into utterances, streamed as they talk
    utterance.py    one stretch of the user's speech: its audio and its transcript
    floor.py        what an utterance means -- a new turn, an interruption, or
                    listening noise -- and which words are still unanswered
    turn_taking.py  the rules both decide by: when a turn has ended, and when
                    speech over the agent interrupts it
"""
