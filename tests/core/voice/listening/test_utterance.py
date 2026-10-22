"""Utterance: the PCM and text of one stretch of speech, and its WAV for Whisper."""
import io
import wave

import numpy as np

from core.voice.listening.utterance import BYTES_PER_MS, Transcript, Utterance, pcm_to_wav


def test_utterances_get_distinct_ids():
    assert Utterance().id != Utterance().id


def test_text_so_far_joins_final_segments_and_the_open_interim():
    utterance = Utterance()
    assert utterance.text_so_far() == ""
    utterance.segments += ["Add milk to the list.", "  "]
    utterance.interim = "and eggs"
    assert utterance.text_so_far() == "Add milk to the list. and eggs"
    utterance.interim = ""
    assert utterance.text_so_far() == "Add milk to the list."


def test_duration_counts_16khz_16bit_mono():
    utterance = Utterance()
    utterance.pcm += bytes(BYTES_PER_MS * 250)
    assert utterance.duration_ms() == 250


def test_transcript_records_its_provider():
    assert Transcript("hi", "whisper_fallback").provider == "whisper_fallback"


def test_pcm_to_wav_writes_16khz_mono():
    wav_bytes = pcm_to_wav(bytes(3200))  # 100 ms
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == 16000
        assert wav_file.getnframes() == 1600


def test_pcm_to_wav_boosts_quiet_speech_up_to_8x():
    quiet = (np.ones(1600, dtype=np.int16) * 1000).tobytes()
    with wave.open(io.BytesIO(pcm_to_wav(quiet)), "rb") as wav_file:
        samples = np.frombuffer(wav_file.readframes(1600), dtype=np.int16)
    assert int(samples.max()) == 8000


def test_pcm_to_wav_of_nothing_is_empty():
    assert pcm_to_wav(b"") == b""
    assert Utterance().wav() == b""


def test_the_speech_is_the_audio_between_the_onset_and_the_last_voiced_window():
    utterance = Utterance()
    utterance.pcm += bytes(BYTES_PER_MS * 300) + b"\x01\x00" * (16 * 1000) + bytes(BYTES_PER_MS * 900)
    assert utterance.speech_pcm() == bytes(utterance.pcm), "no bounds known: all of it"
    utterance.speech_start = BYTES_PER_MS * 300
    utterance.speech_end = BYTES_PER_MS * 1300
    assert utterance.speech_pcm() == b"\x01\x00" * (16 * 1000)
    assert utterance.speech_ms() == 1000
