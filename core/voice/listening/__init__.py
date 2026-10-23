"""The user's side of the conversation: hearing them, and taking turns.

    vad_sink.py       cuts the speaker's audio into utterances, streamed as they
                      talk; only your account's audio is heard (Part 4, L1)
    floor.py          what an utterance means -- a new turn, an interruption, or
                      listening noise -- and which words are still unanswered
    turn_taking.py    the rules both decide by: when a turn has ended, and when
                      speech over the agent interrupts it

An utterance -- one stretch of the user's speech, its audio and its transcript
-- is core/voice/foundation/utterance.py: the engines transcribe it too.

Hearing only you (Part 4):

    speaker_gate.py   does an utterance sound like you? Your voiceprint, and a
                      verdict per utterance (shadow mode: scored and recorded only)
    enrollment.py     `!enroll`: your voiceprint, from four sentences read aloud
    gate_commands.py  `!voiceprint` and `!gate review` in Discord

The gate's records, and your ✅/❌ marks on them, are in memory.db
(core/storage/speaker_gate_log.py).
"""
