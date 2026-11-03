"""Speech engines.

    stt.py         SpeechRecognizer: what the voice pipeline transcribes with
    live_stt.py    streaming speech-to-text over the Gemini Live API
    cloud_stt.py   batch speech-to-text, when a live session fails
    vocabulary_echo.py  the live recognizer reciting its word list, not what was said
    tts.py              TTSEngine: what the voice speaks with, in whose voice
    chirp_tts.py        Chirp 3: HD, streamed both ways
    edge_tts_stream.py  edge-tts, the fallback
"""
