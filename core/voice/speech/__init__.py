"""What the agent says on a voice turn, and how it reaches the speaker.

    reply.py             the turn's reply: the text lane and the speech lane
    voice_stream.py      the agent's tokens -> speakable sentences
    verbalizer.py        structured output (lists, tables) rewritten for the ear,
                         with simple_structure.py, speech_util.py and prompts/
    sentence_chunker.py  text -> sentences to synthesize one at a time
    bridge_manager.py    floor-holding phrases while a tool runs
    blurp_generator.py   the cue that the agent heard you
    audio_queue.py       plays it all, in order; ducks under the user's voice
"""
