"""Voice briefings: the channel inbox, which channels have something for you, said in the voice room.

    hook.py           the briefing in the voice room: the digest, "next agent",
                      what an agent left you, announcements (a voice hook)
    speech_writer.py  speech versions for posts without one, once you're in voice
    commands.py       `!inbox`: list the rows, and mark one done

The voice core never imports this package: the briefing joins the room as a
hook (core/voice/hooks.py). The inbox itself sits below every channel that
writes it: its rows and table are core/storage/channel_inbox.py (schema:
core/storage/schemas/channel_inbox.sql), and pushes, replies and read signals
write it through core/channel/inbox.py. The speech writer's prompt is
SpeechWriterPrompt in core/prompts/voice.py.
"""
