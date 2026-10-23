"""Spoken commands: said in the voice room, recognized by their phrases. The
voice turn (core/voice/turn.py) asks SPOKEN (../registry.py) about each
finished utterance, in order, before the agent hears it.

    words.py       how spoken words are compared: the forms an utterance is
                   reduced to before a phrase is matched
    stop.py        "stop", "never mind": stops the reply and drops the request;
                   its phrases also interrupt the agent at once (barge-in)
    wait.py        "wait", "hold on": stops the reply and keeps the request
    next_agent.py  "next agent": the next channel with something for you
    where_am_i.py  "where am I": which agent and channel the room is with
    switch.py      "switch me to Daisy": moves the room's focus

What a command does to the voice room goes through VoiceControls
(../ports.py), which VoiceManager provides.
"""
