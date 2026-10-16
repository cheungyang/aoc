"""What the agent says on a voice turn, and how it reaches the speaker.

    reply.py             the turn's reply: the speech lane and the text lane
    speech_router.py     splits the agent's stream at <speech>...</speech>
    voice_stream.py      the speech text -> sentences to synthesize one at a time,
                         through speech_util.py and sentence_chunker.py
    missing_speech.py    a reply without <speech>: the fallback and the note
    blurp_generator.py   the cue that the agent heard you
    audio_queue.py       plays it all, in order; ducks under the user's voice
"""
