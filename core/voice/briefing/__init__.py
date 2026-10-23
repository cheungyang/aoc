"""Voice briefings: the channel inbox, which channels have something for you, said in the voice room.

    model.py          an inbox row, and its enums: Source, Priority, Status, Via
    writer.py         pushes, replies and read signals write the inbox; never raises
    speech_writer.py  speech versions for posts without one, once you're in voice
    hook.py           the briefing in the voice room: the digest, "next agent",
                      what an agent left you, announcements (a voice hook)
    commands.py       `!inbox`: list the rows, and mark one done

The voice core never imports this package: the briefing joins the room as a
hook (core/voice/hooks.py). The SQLite table, one row per channel, is
core/storage/channel_inbox.py (schema: core/storage/schemas/channel_inbox.sql);
the speech writer's prompt is SpeechWriterPrompt in core/prompts/voice.py.
"""
