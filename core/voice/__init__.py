"""Voice: talking to the agents in a Discord voice room.

    voice_manager.py  one bot's voice: wires listening to speaking, and runs turns
    listening/        the user's side: utterances, turn-taking, the voiceprint gate
    engines/          speech-to-text and text-to-speech
    speech/           what the agent says on a voice turn, and how it is played
    room/             joining the room, and the focus: which agent the next turn is for
    hooks.py          how features built on the room join in, without the voice core knowing them
    briefing/         one such feature: the channel inbox, and voice briefings of it
    settings.py       voice settings shared by every agent
    patches.py        fixes to discord.py and voice_recv, applied when the room connects

Importing this package does nothing: the voice room loads libopus and patches
the Discord libraries itself, just before it connects.
"""
