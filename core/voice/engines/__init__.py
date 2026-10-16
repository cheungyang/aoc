"""Speech engines.

    stt.py         SpeechRecognizer: what the voice pipeline transcribes with
    live_stt.py    streaming speech-to-text over the Gemini Live API
    stt_engine.py  local Whisper, the fallback
    tts.py              TTSEngine: what the voice speaks with, in whose voice
    chirp_tts.py        Chirp 3: HD, streamed both ways
    edge_tts_stream.py  edge-tts, the fallback
"""
