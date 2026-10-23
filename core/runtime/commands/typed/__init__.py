"""Typed commands: `!name args`, typed to the Discord bot. The Discord runner
asks TYPED (../registry.py) about every message that starts with "!".

In a channel the bot answers (ChannelCommand), answered by the bot that
answers messages there:

    new.py         !new            clears this channel's session
    new_all.py     !newall         clears every session
    compact.py     !compact        compacts this channel's session history now
    restart.py     !restart        restarts the process
    help.py        !help           every command, typed or spoken

For the voice room (VoiceRoomCommand), through VoiceControls (../ports.py):

    join.py        !join           joins, talking in this channel's conversation
    leave.py       !leave          leaves the voice room
    inbox.py       !inbox          the channel inbox that voice briefings read
    enroll.py      !enroll         takes your voiceprint
    voiceprint.py  !voiceprint     the voiceprint gate, and your voiceprint
    gate.py        !gate           the utterances worth a check
"""
