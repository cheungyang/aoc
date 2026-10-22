"""The channel inbox: which channels have something for you, for voice briefings.

    model.py          the row, and its enums: Source, Priority, Status, Via
    store.py          the SQLite table, one row per channel
    writer.py         pushes, replies and read signals write it; never raises
    speech_writer.py  speech versions for posts without one, once you're in voice
    voice_briefing.py the inbox in the voice room: the digest, "next agent",
                      what an agent left you, announcements (a voice hook)
    commands.py       `!inbox`: list the rows, and mark one done

The table's schema is core/db/channel_inbox.sql; the speech writer's prompt is
SpeechWriterPrompt in core/prompts/voice.py.
"""
