"""Tests for scripts/tts_benchmark.py: the scoring math and the offline plumbing.

No test touches the network: providers are replaced with fakes, and the Chirp
client is only exercised up to the point where it would need credentials.
"""
import asyncio
import base64
import csv
import json
import math
import os

import numpy as np
import pytest

from scripts import tts_benchmark as tb


# ---------------------------------------------------------------------------
# Number words and normalization
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("n, words", [
    (0, "zero"), (7, "seven"), (19, "nineteen"), (20, "twenty"), (45, "forty five"),
    (100, "one hundred"), (305, "three hundred five"), (1080, "one thousand eighty"),
    (60000, "sixty thousand"), (1234567, "one million two hundred thirty four thousand five hundred sixty seven"),
])
def test_number_words(n, words):
    assert tb.number_words(n) == words


@pytest.mark.parametrize("n, words", [
    (1, "first"), (2, "second"), (3, "third"), (12, "twelfth"), (15, "fifteenth"),
    (20, "twentieth"), (22, "twenty second"), (31, "thirty first"),
])
def test_ordinal_words(n, words):
    assert tb.ordinal_words(n) == words


@pytest.mark.parametrize("n, words", [
    (2027, "twenty twenty seven"), (2005, "two thousand five"), (1999, "nineteen ninety nine"),
    (1905, "nineteen oh five"), (1900, "nineteen hundred"),
])
def test_year_words(n, words):
    assert tb.year_words(n) == words


def test_decimal_words_reads_digits_after_the_point():
    assert tb.decimal_words("21.5") == "twenty one point five"
    assert tb.decimal_words("1,234") == "one thousand two hundred thirty four"
    assert tb.decimal_words("1.65") == "one point six five"


@pytest.mark.parametrize("written, spoken", [
    ("$1,234.56", "one thousand two hundred thirty four dollars fifty six cents"),
    ("1,234.56 dollars", "one thousand two hundred thirty four dollars fifty six cents"),
    ("$87.20", "eighty seven dollars twenty cents"),
    ("$1", "one dollar"),
    ("$1.65 million", "one point six five million dollars"),
    ("$26M", "twenty six million dollars"),
    ("6.2%", "six point two percent"),
    ("21.5°C", "twenty one point five degrees celsius"),
    ("21.5 degrees Celsius", "twenty one point five degrees celsius"),
    ("7:45 PM", "seven forty five pm"),
    ("7:45 p.m.", "seven forty five pm"),
    ("9:30", "nine thirty"),
    ("7:05", "seven oh five"),
    ("8:00 a.m.", "eight am"),
    ("October 22nd", "october twenty second"),
    ("March 15th, 2027", "march fifteenth twenty twenty seven"),
    ("25 lb", "twenty five pounds"),
    ("1 lb", "one pound"),
    ("20-day average", "twenty day average"),
    ("Reykjavík", "reykjavik"),
    ("C.K.A. labs", "cka labs"),
    ("Here's the plan, um, today.", "heres the plan today"),
    ("one hundred and five", "one hundred five"),
])
def test_normalize_words(written, spoken):
    assert " ".join(tb.normalize_words(written)) == spoken


def test_normalize_words_leaves_ordinary_words_alone():
    # "a m" inside "a man" and the verb "am" must not be taken for a clock suffix.
    assert tb.normalize_words("a man said I am here") == ["a", "man", "said", "i", "am", "here"]


def test_transcriber_formatting_matches_the_spoken_input():
    """The transcriber's inverse text normalization must not count as errors."""
    reference = "Logged. Next up, three sets of ten goblet squats at thirty-five pounds at 7:45 PM."
    transcript = "Logged. Next up, three sets of 10 goblet squats at 35 lb at 7:45 p.m."
    assert tb.normalize_words(reference) == tb.normalize_words(transcript)


def test_normalize_words_empty():
    assert tb.normalize_words("") == []


# ---------------------------------------------------------------------------
# Word error rate and term matching
# ---------------------------------------------------------------------------

def test_word_errors_identical():
    errs = tb.word_errors(["a", "b", "c"], ["a", "b", "c"])
    assert (errs.errors, errs.wer) == (0, 0.0)


def test_word_errors_counts_each_kind():
    # Each example has exactly one minimal alignment, so the breakdown is fixed.
    sub_ins = tb.word_errors("a b c d".split(), "a x c d e".split())
    assert (sub_ins.substitutions, sub_ins.deletions, sub_ins.insertions) == (1, 0, 1)
    assert sub_ins.wer == pytest.approx(2 / 4)
    deletion = tb.word_errors("three sets of ten".split(), "three sets ten".split())
    assert (deletion.substitutions, deletion.deletions, deletion.insertions) == (0, 1, 0)
    assert deletion.ref_words == 4


def test_word_errors_empty_reference():
    assert tb.word_errors([], []).wer == 0.0
    assert tb.word_errors([], ["extra"]).wer == 1.0
    assert tb.word_errors(["a", "b"], []).deletions == 2


def test_term_found_ignores_spacing_both_ways():
    assert tb.term_found(["xiaolongbao"], "some xiao long bao please".split())
    assert tb.term_found(["hong shao rou"], "make hongshaorou tomorrow".split())
    assert tb.term_found(["Fitbod"], "fit bod has todays workout".split())


def test_term_found_uses_alternates_and_rejects_absent_terms():
    hyp = tb.normalize_words("Nvidia is down six point two percent")
    assert not tb.term_found(["NVDA"], hyp)
    assert tb.term_found(["NVDA", "Nvidia"], hyp)
    assert not tb.term_found(["gai lan"], tb.normalize_words("garlic guy lawn"))


def test_term_found_matches_whole_words_only():
    assert not tb.term_found(["Hue"], tb.normalize_words("the hues in the den"))


def test_score_pronunciation_reports_wer_and_missed_terms():
    score = tb.score_pronunciation(
        "Tonight, try mapo tofu with garlic gai lan.",
        "Tonight try mapo tofu with garlic guy lawn.",
        ["mapo tofu", "gai lan"],
        {"gai lan": ["kai lan"]},
    )
    assert score["missed_terms"] == ["gai lan"]
    assert score["terms"] == 2
    assert score["substitutions"] == 2 and score["insertions"] == 0
    assert score["wer"] == pytest.approx(2 / 8)


def test_rescore_run_applies_new_alternates(tmp_path):
    text = "Daisy has your briefing, and Ramsay is planning dinner."
    transcript = "Daisy has your briefing and Ramsey is planning dinner."
    utterance = {"id": "names-agents", "category": "names", "text": text, "terms": ["Daisy", "Ramsay"]}
    before = tb.score_pronunciation(text, transcript, utterance["terms"])
    assert before["missed_terms"] == ["Ramsay"]
    results = {
        "started": "t", "ran": ["gemini"], "skipped": {}, "utterances": [utterance], "aggregates": {},
        "host": {"platform": "p", "machine": "m", "cpus": 1, "python": "3"},
        "config": tb.Options().public(), "cold_start_ms": {}, "reliability": None, "cost": {}, "records": [],
        "pronunciation": {"model": "m", "usage": {}, "candidates": tb.summarize_pronunciation(
            {"gemini": [{"utterance": "names-agents", "transcript": transcript, **before}]})},
    }
    (tmp_path / "results.json").write_text(json.dumps(results))

    test_set = {"utterances": [utterance], "term_alternates": {"Ramsay": ["Ramsey"]}}
    rescored = tb.rescore_run(str(tmp_path), test_set)

    gemini = rescored["pronunciation"]["candidates"]["gemini"]
    assert gemini["term_misses"] == 0 and gemini["missed"] == []
    assert json.loads((tmp_path / "results.json").read_text())["pronunciation"]["candidates"]["gemini"]["term_misses"] == 0
    assert (tmp_path / "summary.md").is_file()


# ---------------------------------------------------------------------------
# Percentiles, playback, chunking, selection
# ---------------------------------------------------------------------------

def test_percentile_matches_numpy_linear():
    values = [120.0, 80.0, 300.0, 95.0, 410.0, 150.0]
    for q in (50, 90, 10, 100, 0):
        assert tb.percentile(values, q) == pytest.approx(float(np.percentile(values, q)))


def test_percentile_edge_cases():
    assert tb.percentile([], 50) is None
    assert tb.percentile([None, None], 50) is None
    assert tb.percentile([42], 90) == 42.0
    assert tb.percentile([1, None, 3], 50) == 2.0


def test_playback_without_gaps_has_no_underruns():
    chunks = [tb.AudioChunk(0.2, 0.5), tb.AudioChunk(0.4, 0.5), tb.AudioChunk(0.9, 0.5)]
    result = tb.simulate_playback(chunks)
    assert result.underruns == 0
    assert result.stall_s == 0.0
    assert result.finish_s == pytest.approx(1.7)


def test_playback_counts_a_late_chunk_as_an_underrun():
    # The first chunk plays 0.2-0.7; the second only arrives at 1.0.
    chunks = [tb.AudioChunk(0.2, 0.5), tb.AudioChunk(1.0, 0.5), tb.AudioChunk(1.2, 0.5)]
    result = tb.simulate_playback(chunks)
    assert result.underruns == 1
    assert result.stall_s == pytest.approx(0.3)
    assert result.finish_s == pytest.approx(2.0)


def test_prebuffer_absorbs_a_gap():
    chunks = [tb.AudioChunk(0.2, 0.5), tb.AudioChunk(1.0, 0.5)]
    assert tb.simulate_playback(chunks, prebuffer_s=0.4).underruns == 0


def test_tiny_gaps_are_tolerated():
    chunks = [tb.AudioChunk(0.0, 0.5), tb.AudioChunk(0.505, 0.5)]
    assert tb.simulate_playback(chunks).underruns == 0
    assert tb.simulate_playback(chunks, tolerance_s=0.001).underruns == 1


def test_playback_of_nothing():
    assert tb.simulate_playback([]) == tb.Playback(0, 0.0, 0.0)


def test_group_into_sentences_waits_for_the_last_clause():
    pieces = ["Nice work today.", " You finished all four,", " and your squats", " went up.", " Rest now"]
    schedule = [0.0, 0.15, 0.30, 0.45, 0.60]
    assert tb.group_into_sentences(pieces, schedule) == [
        ("Nice work today.", 0.0),
        ("You finished all four, and your squats went up.", 0.45),
        ("Rest now", 0.60),
    ]


def test_utterance_pieces_schedules_chunks():
    pieces, schedule = tb.utterance_pieces({"chunks": ["a.", " b.", " c."], "text": "a. b. c."}, 0.2)
    assert pieces == ["a.", " b.", " c."]
    assert schedule == pytest.approx([0.0, 0.2, 0.4])
    assert tb.utterance_pieces({"text": "Hello."}, 0.2) == (["Hello."], [0.0])


def test_stratified_selection_covers_every_category_in_file_order():
    utterances = [{"id": f"{c}{i}", "category": c} for c in ("ack", "num", "names") for i in range(4)]
    utterances.append({"id": "long0", "category": "long"})
    picked = tb.stratified_selection(utterances, 5)
    assert [u["id"] for u in picked] == ["ack0", "ack1", "num0", "names0", "long0"]
    assert tb.stratified_selection(utterances, None) == utterances
    assert tb.stratified_selection(utterances, 100) == utterances


def test_select_utterances_filters_and_rejects_unknown_ids():
    utterances = [{"id": "a", "category": "ack"}, {"id": "b", "category": "long"}]
    assert tb.select_utterances(utterances, ids=["b"]) == [utterances[1]]
    assert tb.select_utterances(utterances, categories=["ack"]) == [utterances[0]]
    with pytest.raises(ValueError):
        tb.select_utterances(utterances, ids=["nope"])


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------

def test_chirp_cost_is_free_inside_the_free_tier():
    assert tb.chirp_monthly_cost(840_000) == 0.0
    assert tb.chirp_monthly_cost(1_000_000) == 0.0
    assert tb.chirp_monthly_cost(1_500_000) == pytest.approx(15.0)
    assert tb.chirp_monthly_cost(840_000, free_chars=0) == pytest.approx(25.2)


def test_gemini_cost_from_audio_and_text_tokens():
    # 840k chars at 15 chars per second is 56,000 s of audio: 1.4M audio tokens.
    cost = tb.gemini_monthly_cost(840_000, 1 / 15, 0.25, 0.50, 6.00)
    assert cost == pytest.approx(1_400_000 * 6.00 / 1e6 + 210_000 * 0.50 / 1e6)


def test_cost_estimates_use_measured_rates():
    records = [{"candidate": "gemini", "ok": True, "chars": 150, "audio_s": 10.0,
                "usage": {"input_tokens": 50, "audio_tokens": 250}},
               {"candidate": "edge", "ok": True, "chars": 150, "audio_s": 10.0}]
    costs = tb.cost_estimates(["edge", "chirp-stream", "gemini"], records)
    assert costs["edge"]["monthly_usd"] == 0.0
    assert costs["chirp-stream"]["monthly_usd"] == 0.0
    gemini = costs["gemini"]
    assert gemini["basis"] == "measured"
    expected = tb.gemini_monthly_cost(tb.MONTHLY_SPOKEN_CHARS, 10.0 / 150, 50 / 150, 0.50, 6.00)
    assert gemini["monthly_usd"] == pytest.approx(expected, abs=0.01)
    assert gemini["monthly_usd_by_period"]["from 2027-01-01"] == pytest.approx(2 * expected, abs=0.02)


def test_cost_estimates_fall_back_to_nominal_rates():
    gemini = tb.cost_estimates(["gemini"], [])["gemini"]
    assert gemini["basis"] == "nominal"
    assert gemini["monthly_usd"] > 0


# ---------------------------------------------------------------------------
# Blind pairs
# ---------------------------------------------------------------------------

ITEMS = [(f"u{i}", c) for c in ("chirp-stream", "gemini") for i in range(20)]


def test_assign_pairs_is_deterministic_per_seed():
    assert tb.assign_pairs(ITEMS, 7) == tb.assign_pairs(ITEMS, 7)
    assert tb.assign_pairs(ITEMS, 7) != tb.assign_pairs(ITEMS, 8)


def test_assign_pairs_puts_the_baseline_on_exactly_one_side():
    pairs = tb.assign_pairs(ITEMS, 1)
    assert len(pairs) == len(ITEMS)
    assert [p["pair"] for p in pairs] == [f"{n:02d}" for n in range(1, len(ITEMS) + 1)]
    assert {(p["utterance"], p["A"] if p["A"] != tb.BASELINE else p["B"]) for p in pairs} == set(ITEMS)
    for pair in pairs:
        assert [pair["A"], pair["B"]].count(tb.BASELINE) == 1


def test_assign_pairs_randomizes_sides_and_order():
    pairs = tb.assign_pairs(ITEMS, 3)
    on_a = sum(1 for p in pairs if p["A"] == tb.BASELINE)
    assert 10 <= on_a <= 30
    assert [p["utterance"] for p in pairs] != [u for u, _ in ITEMS]


def test_score_picks_counts_preferences_ties_and_blanks():
    key = [
        {"pair": "01", "utterance": "u1", "A": "gemini", "B": "edge"},
        {"pair": "02", "utterance": "u2", "A": "edge", "B": "gemini"},
        {"pair": "03", "utterance": "u3", "A": "edge", "B": "gemini"},
        {"pair": "04", "utterance": "u4", "A": "gemini", "B": "edge"},
        {"pair": "05", "utterance": "u5", "A": "chirp-stream", "B": "edge"},
    ]
    picks = {"01": "a", "02": "B", "03": "A", "04": "=", "05": ""}
    scores = tb.score_picks(picks, key)
    gemini = scores["gemini"]
    assert (gemini["preferred"], gemini["baseline"], gemini["ties"]) == (2, 1, 1)
    assert gemini["preference"] == pytest.approx(0.5)
    assert gemini["status"] == "fail"
    assert scores["chirp-stream"]["unanswered"] == 1
    assert scores["chirp-stream"]["preference"] is None
    assert scores["chirp-stream"]["status"] == "n/a"


def test_score_picks_passes_at_eighty_percent():
    key = [{"pair": f"{i:02d}", "utterance": f"u{i}", "A": "gemini", "B": "edge"} for i in range(5)]
    picks = {f"{i:02d}": "A" for i in range(4)}
    picks["04"] = "B"
    assert tb.score_picks(picks, key)["gemini"]["status"] == "pass"


def test_pair_candidates_keeps_one_chirp():
    assert tb.pair_candidates(["edge", "chirp-unary", "chirp-stream", "gemini"]) == ["chirp-stream", "gemini"]
    assert tb.pair_candidates(["edge", "chirp-unary"]) == ["chirp-unary"]


# ---------------------------------------------------------------------------
# Gemini wire format
# ---------------------------------------------------------------------------

def _sse_lines(*events):
    lines = []
    for event in events:
        lines += [f"event: {event.get('event_type', 'x')}", "data: " + json.dumps(event), ""]
    return lines + ["event: done", "data: [DONE]", ""]


def test_sse_parser_yields_payloads_and_skips_done():
    events = [{"event_type": "step.start", "index": 0}, {"event_type": "step.stop", "index": 0}]
    parser = tb.SseParser()
    parsed = [e for e in (parser.feed(line) for line in _sse_lines(*events)) if e is not None]
    assert parsed == events
    assert parser.flush() is None


def test_sse_parser_survives_missing_blank_lines():
    parser = tb.SseParser()
    lines = ["event: a", 'data: {"event_type": "a"}', "event: b", 'data: {"event_type": "b"}']
    parsed = [e for e in (parser.feed(line) for line in lines) if e is not None]
    parsed.append(parser.flush())
    assert [e["event_type"] for e in parsed] == ["a", "b"]


def test_gemini_event_helpers():
    pcm = b"\x01\x00\x02\x00"
    delta = {"event_type": "step.delta", "index": 0, "delta": {"type": "audio", "data": base64.b64encode(pcm).decode()}}
    assert tb.gemini_audio(delta) == pcm
    wav_delta = {"event_type": "step.delta", "delta": {"type": "audio", "data": base64.b64encode(tb.pcm_to_wav(pcm)).decode()}}
    assert tb.gemini_audio(wav_delta) == pcm
    assert tb.gemini_audio({"event_type": "step.delta", "delta": {"type": "text", "text": "hi"}}) is None
    completed = {"event_type": "interaction.completed", "interaction": {"usage": {
        "total_input_tokens": 26, "output_tokens_by_modality": [{"modality": "audio", "tokens": 174}]}}}
    assert tb.gemini_usage(completed) == {"input_tokens": 26, "audio_tokens": 174}
    assert tb.gemini_usage(delta) == {}
    assert tb.gemini_error({"event_type": "error", "error": {"code": "not_found", "message": "gone"}}) == "not_found: gone"
    assert tb.gemini_error(delta) is None


def test_gemini_requests_have_the_documented_shape():
    body = tb.gemini_tts_request("Hello.", "Aoede")
    assert body["model"] == "gemini-3.8-flash-lite-tts"
    assert body["stream"] is True and body["store"] is False
    assert body["generation_config"]["speech_config"] == [{"voice": "Aoede"}]
    assert body["input"][0]["content"][0] == {"type": "text", "text": "Hello."}
    request = tb.transcription_request(b"RIFF", "audio/wav")
    assert request["model"] == "gemini-3.5-transcribe"
    assert request["input"][0]["mime_type"] == "audio/wav"
    assert request["generation_config"]["transcription_config"]["mode"] == {"type": "verbatim"}


def test_interaction_text_joins_model_output():
    interaction = {"steps": [
        {"type": "user_input", "content": [{"type": "text", "text": "ignored"}]},
        {"type": "model_output", "content": [{"type": "text", "text": "Logged. "}, {"type": "text", "text": "Next up."}]},
    ]}
    assert tb.interaction_text(interaction) == "Logged. Next up."


def test_small_helpers():
    assert tb.edge_rate(1.0) == "+0%"
    assert tb.edge_rate(1.2) == "+20%"
    assert tb.edge_rate(0.9) == "-10%"
    assert tb.voice_locale("en-GB-Chirp3-HD-Kore") == "en-GB"
    assert tb.voice_locale("weird") == "en-US"
    assert tb.gemini_voice_name("en-US-Chirp3-HD-Aoede") == "Aoede"
    assert tb.classify_error(asyncio.TimeoutError()) == "timeout"
    assert tb.classify_error(tb.ProviderHttpError(429, "slow down")) == "http_429"
    assert tb.classify_error(ValueError("x")) == "ValueError"


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------

def _tone(seconds, rate=tb.PCM_SAMPLE_RATE, amplitude=0.3):
    t = np.arange(int(seconds * rate)) / rate
    return (amplitude * 32767 * np.sin(2 * math.pi * 220 * t)).astype(np.int16)


def test_wav_round_trip_and_strip_riff():
    pcm = _tone(0.1).tobytes()
    wav = tb.pcm_to_wav(pcm)
    assert wav[:4] == b"RIFF"
    assert tb.wav_to_pcm(wav) == (pcm, tb.PCM_SAMPLE_RATE)
    assert tb.strip_riff(wav) == pcm
    assert tb.strip_riff(pcm) == pcm


def test_prepare_for_listening_trims_silence_and_levels_loudness():
    rate = tb.PCM_SAMPLE_RATE
    quiet = _tone(1.0, amplitude=0.02)
    padded = np.concatenate([np.zeros(rate, dtype=np.int16), quiet, np.zeros(rate, dtype=np.int16)])
    out = tb.prepare_for_listening(padded, rate)
    assert len(out) < len(padded) - rate  # most of the two seconds of silence is gone
    x = out.astype(np.float64) / 32768
    active = x[np.abs(x) > 10 ** (-50 / 20)]  # the leveler's own definition of speech
    rms_db = 20 * math.log10(float(np.sqrt(np.mean(active ** 2))))
    assert rms_db == pytest.approx(-20.0, abs=0.5)


def test_resample_linear_changes_length_only_when_needed():
    tone = _tone(0.5, rate=16_000)
    assert len(tb.resample_linear(tone, 16_000, 24_000)) == 12_000
    assert tb.resample_linear(tone, 16_000, 16_000) is tone


# ---------------------------------------------------------------------------
# Test set, agents, credentials
# ---------------------------------------------------------------------------

def test_the_shipped_test_set_is_valid():
    data = tb.load_test_set()
    utterances = data["utterances"]
    assert 20 <= len(utterances) <= 30
    assert {"ack", "numbers", "names", "chunked", "long"} <= {u["category"] for u in utterances}
    long_reply = next(u for u in utterances if u["category"] == "long")
    assert 100 <= len(long_reply["text"].split()) <= 140  # about 45 seconds of speech
    for utterance in utterances:
        if utterance.get("chunks"):
            assert utterance["text"] == "".join(utterance["chunks"]).strip()


def test_load_test_set_rejects_terms_missing_from_the_text(tmp_path):
    path = tmp_path / "set.json"
    path.write_text(json.dumps({"utterances": [
        {"id": "x", "category": "names", "text": "Daisy has it.", "terms": ["Fitbod"]}]}))
    with pytest.raises(ValueError, match="Fitbod"):
        tb.load_test_set(str(path))


def test_load_test_set_rejects_duplicate_ids(tmp_path):
    path = tmp_path / "set.json"
    path.write_text(json.dumps({"utterances": [
        {"id": "x", "category": "ack", "text": "One."}, {"id": "x", "category": "ack", "text": "Two."}]}))
    with pytest.raises(ValueError, match="duplicated"):
        tb.load_test_set(str(path))


def _write_agent(root, agent_id, config):
    os.makedirs(root / agent_id)
    (root / agent_id / "agent.json").write_text(json.dumps(config))


def test_audition_targets_reads_voice_blocks(tmp_path):
    _write_agent(tmp_path, "day-planner", {"name": "Daisy", "voice": {"name": "en-US-Chirp3-HD-Zephyr", "rate": 1.1}})
    _write_agent(tmp_path, "main", {"name": "Concierge"})
    _write_agent(tmp_path, "typo", {"name": "Typo", "voice": {"name": "en-US-Chirp3-HD-Nobody"}})
    for excluded in ("graph-worker", "graph-worker-low", "script-executor"):
        _write_agent(tmp_path, excluded, {"name": excluded})
    os.makedirs(tmp_path / "not-an-agent")

    targets = {t["agent"]: t for t in tb.audition_targets(str(tmp_path), {"main": "Hi, I'm your Concierge."})}

    assert set(targets) == {"day-planner", "main", "typo"}
    assert targets["day-planner"]["voice"] == "en-US-Chirp3-HD-Zephyr"
    assert targets["day-planner"]["rate"] == 1.1
    assert targets["day-planner"]["source"] == "agent.json"
    assert targets["day-planner"]["line"] == "Hi, this is Daisy. This is how I sound."
    assert targets["main"]["voice"] == tb.DEFAULT_VOICE
    assert targets["main"]["source"] == "default"
    assert targets["main"]["line"] == "Hi, I'm your Concierge."
    assert targets["typo"]["voice"] == tb.DEFAULT_VOICE
    assert "Nobody" in targets["typo"]["note"]


def test_every_audition_line_belongs_to_a_real_agent():
    lines = tb.load_test_set()["audition_lines"]
    agents = {t["agent"] for t in tb.audition_targets()}
    assert set(lines) <= agents


def test_chirp_is_unavailable_without_credentials(tmp_path):
    with pytest.raises(tb.ProviderUnavailable, match=tb.CREDENTIALS_ENV_VAR):
        tb.make_chirp_client(tb.Options(credentials_file=None))
    missing = tmp_path / "nope.json"
    with pytest.raises(tb.ProviderUnavailable, match="does not exist"):
        tb.make_chirp_client(tb.Options(credentials_file=str(missing)))


def test_options_never_expose_the_api_key():
    opts = tb.Options(gemini_api_key="secret-key")
    assert "secret-key" not in json.dumps(opts.public())
    assert "secret-key" not in repr(opts)


# ---------------------------------------------------------------------------
# Chirp providers against fake clients (real request types, no network)
# ---------------------------------------------------------------------------

@pytest.fixture
def texttospeech():
    return pytest.importorskip("google.cloud.texttospeech")


class FakeChirpClient:
    def __init__(self, tts, pcm_per_call):
        self.tts = tts
        self.pcm = pcm_per_call
        self.unary_calls = []
        self.stream_requests = []

    def synthesize_speech(self, *, input, voice, audio_config, retry, timeout):
        self.unary_calls.append((input.text, voice.name, voice.language_code, audio_config, retry, timeout))
        return self.tts.SynthesizeSpeechResponse(audio_content=tb.pcm_to_wav(self.pcm))

    def streaming_synthesize(self, requests, retry, timeout):
        for request in requests:  # drained inline here; gRPC uses its own thread
            self.stream_requests.append(request)
            if request.input.text:
                yield self.tts.StreamingSynthesizeResponse(audio_content=self.pcm)


def _chirp(provider_class, client):
    opts = tb.Options(chirp_client=(client, client.tts), rate=1.2, timeout=9)
    provider = provider_class(opts)
    asyncio.run(provider.open())
    return provider


def test_chirp_unary_sends_one_request_per_sentence(texttospeech):
    pcm = _tone(0.5).tobytes()
    client = FakeChirpClient(texttospeech, pcm)
    provider = _chirp(tb.ChirpUnaryProvider, client)
    synth = asyncio.run(provider.synthesize(["Nice work.", " Rest up,", " then sleep."], [0.0, 0.01, 0.02]))
    assert [call[0] for call in client.unary_calls] == ["Nice work.", "Rest up, then sleep."]
    text, voice, locale, audio_config, retry, timeout = client.unary_calls[0]
    assert (voice, locale) == (tb.DEFAULT_VOICE, "en-US")
    assert audio_config.audio_encoding == texttospeech.AudioEncoding.LINEAR16
    assert audio_config.speaking_rate == pytest.approx(1.2)
    assert retry is None and timeout == 9
    assert bytes(synth.audio) == pcm * 2  # WAV headers stripped
    assert len(synth.chunks) == 2 and synth.audio_s == pytest.approx(1.0)


def test_chirp_stream_sends_config_then_clauses(texttospeech):
    pcm = _tone(0.25).tobytes()
    client = FakeChirpClient(texttospeech, pcm)
    provider = _chirp(tb.ChirpStreamProvider, client)
    synth = asyncio.run(provider.synthesize(["Nice work today.", " You finished,", " all four."], [0.0, 0.0, 0.0]))
    first, *rest = client.stream_requests
    config = first.streaming_config
    assert config.voice.name == tb.DEFAULT_VOICE
    assert config.streaming_audio_config.audio_encoding == texttospeech.AudioEncoding.PCM
    assert config.streaming_audio_config.speaking_rate == pytest.approx(1.2)
    assert [r.input.text for r in rest] == ["Nice work today.", " You finished,", " all four."]
    assert synth.requests == 1
    assert len(synth.chunks) == 3
    assert synth.audio_s == pytest.approx(0.75)
    assert synth.ttfa_s is not None and synth.ttfa_s >= 0


# ---------------------------------------------------------------------------
# End to end, with fake engines
# ---------------------------------------------------------------------------

class FakeProvider(tb.Provider):
    """Streams a tone in 0.2 s chunks, 50 ms apart: fast enough never to stall."""

    def __init__(self, opts, name, fail_first=0):
        super().__init__(opts)
        self.name = name
        self.fail_first = fail_first
        self.calls = 0

    async def synthesize(self, pieces, schedule, voice=None, rate=None):
        self.calls += 1
        if self.calls <= self.fail_first:
            raise tb.ProviderHttpError(503, "unavailable")
        synth = tb.Synthesis()
        text = "".join(pieces)
        chunk = _tone(0.2).tobytes()
        for i in range(max(1, len(text) // 15)):
            synth.chunks.append(tb.AudioChunk(0.1 + 0.05 * i, 0.2))
            synth.audio.extend(chunk)
        synth.total_s = synth.chunks[-1].arrival_s
        synth.requests = 1
        synth.usage = {"input_tokens": len(text) // 4, "audio_tokens": 5 * len(synth.chunks)}
        return synth


class UnavailableProvider(tb.Provider):
    name = "chirp-stream"

    async def open(self):
        raise tb.ProviderUnavailable("GOOGLE_TTS_CREDENTIALS_FILE is not set")


@pytest.fixture
def fake_engines(monkeypatch):
    made = {}

    def factory(name, fail_first=0):
        def make(opts):
            made[name] = FakeProvider(opts, name, fail_first)
            return made[name]
        return make

    monkeypatch.setattr(tb, "PROVIDER_CLASSES", {
        "edge": factory("edge"),
        "gemini": factory("gemini", fail_first=1),  # the warm-up fails, nothing else
        "chirp-stream": UnavailableProvider,
    })
    return made


@pytest.mark.asyncio
async def test_run_benchmark_end_to_end(tmp_path, fake_engines):
    test_set = tb.load_test_set()
    utterances = tb.select_utterances(test_set["utterances"], limit=5)
    opts = tb.Options(candidates=["edge", "chirp-stream", "gemini"], runs=2, reliability=3,
                      pronunciation=False, chunk_interval_s=0.0)

    results = await tb.run_benchmark(opts, test_set, utterances, str(tmp_path), ab_pairs=True, pairs_seed=5)

    assert results["ran"] == ["edge", "gemini"]
    assert "GOOGLE_TTS_CREDENTIALS_FILE" in results["skipped"]["chirp-stream"]
    assert results["cold_start_ms"]["gemini"] is None  # its warm-up was the failure
    for name in ("edge", "gemini"):
        agg = results["aggregates"][name]
        assert agg["syntheses"] == 2 * len(utterances)
        assert agg["failures"] == 0
        assert agg["underruns"] == 0
        assert agg["ttfa_ms"]["p50"] == pytest.approx(100.0)
        assert results["reliability"][name]["failures"] == 0
    assert results["cost"]["gemini"]["basis"] == "measured"

    saved = json.loads((tmp_path / "results.json").read_text())
    assert saved["config"]["runs"] == 2
    assert len(saved["records"]) == 2 * 2 * len(utterances)
    for name, files in saved["audio_files"].items():
        assert set(files) == {u["id"] for u in utterances}
        for relative in files.values():
            assert (tmp_path / relative).is_file()
    summary = (tmp_path / "summary.md").read_text()
    assert "Time to first audio" in summary and "awaiting your picks" in summary

    pairs_dir = tmp_path / "pairs"
    with open(pairs_dir / "picks.csv", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == len(utterances)  # gemini vs edge, once per utterance
    for row in rows:
        assert (pairs_dir / f"pair_{row['pair']}_A.wav").is_file()
        assert (pairs_dir / f"pair_{row['pair']}_B.wav").is_file()
    key = json.loads((tmp_path / "ab_key" / "answer_key.json").read_text())
    assert key["seed"] == 5 and len(key["pairs"]) == len(rows)

    # The listener prefers gemini every time.
    picks = {p["pair"]: ("A" if p["A"] == "gemini" else "B") for p in key["pairs"]}
    with open(pairs_dir / "picks.csv", "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["pair", "utterance", "text", "pick", "notes"])
        for row in rows:
            writer.writerow([row["pair"], row["utterance"], row["text"], picks[row["pair"]], ""])
    scores = tb.score_pairs_dir(str(pairs_dir))
    assert scores["gemini"]["preference"] == 1.0
    rescored = json.loads((tmp_path / "results.json").read_text())
    assert rescored["naturalness"]["gemini"]["status"] == "pass"
    assert "100% of" in (tmp_path / "summary.md").read_text()


@pytest.mark.asyncio
async def test_timed_synthesis_reports_timeouts(tmp_path):
    class Slow(tb.Provider):
        name = "slow"

        async def synthesize(self, pieces, schedule, voice=None, rate=None):
            await asyncio.sleep(1)

    synth, exc = await tb.timed_synthesis(Slow(tb.Options()), ["Hi."], [0.0], None, None, timeout=0.01)
    assert synth is None
    assert tb.classify_error(exc) == "timeout"


@pytest.mark.asyncio
async def test_sentence_provider_paces_requests_by_schedule():
    sent = []

    class Recorder(tb.SentenceProvider):
        name = "recorder"

        async def _request(self, text, voice, rate, t0, synth):
            sent.append((text, round(tb.time.perf_counter() - t0, 2)))
            synth.chunks.append(tb.AudioChunk(tb.time.perf_counter() - t0, 1.0))

    pieces = ["One.", " Two,", " still two."]
    synth = await Recorder(tb.Options()).synthesize(pieces, [0.0, 0.05, 0.1])
    assert [t for t, _ in sent] == ["One.", "Two, still two."]
    assert sent[1][1] >= 0.09  # sent only once its last clause had arrived
    assert synth.requests == 0 and len(synth.chunks) == 2


def test_evaluate_marks_targets():
    results = {
        "ran": ["edge", "gemini"],
        "aggregates": {
            "edge": {"ttfa_ms": {"p50": 650.0, "p90": 900.0, "n": 4}, "underruns": 2, "syntheses_with_underruns": 1,
                     "rtf": {"p50": 0.2, "p90": 0.3, "max": 0.4}},
            "gemini": {"ttfa_ms": {"p50": 380.0, "p90": 500.0, "n": 4}, "underruns": 0, "syntheses_with_underruns": 0,
                       "rtf": {"p50": 0.3, "p90": 0.6, "max": 0.9}},
        },
        "pronunciation": {"candidates": {"gemini": {"utterances": 4, "terms": 6, "term_misses": 0,
                                                     "term_misses_per_25": 0.0, "wer": 0.02}}},
        "reliability": {"edge": {"failures": 1, "requests": 100, "timeouts": 1}},
        "cost": {"edge": {"monthly_usd": 0.0}, "gemini": {"monthly_usd": 8.4, "monthly_usd_by_period": {"now": 8.4}}},
        "naturalness": None,
    }
    table = tb.evaluate(results)
    assert table["ttfa"]["edge"]["status"] == "fail"
    assert table["ttfa"]["gemini"]["status"] == "pass"
    assert table["underruns"]["edge"]["status"] == "fail"
    assert table["rtf"]["gemini"]["status"] == "fail"
    assert table["terms"]["gemini"]["status"] == "pass"
    assert table["terms"]["edge"]["status"] == "n/a"
    assert table["reliability"]["edge"]["status"] == "fail"
    assert table["reliability"]["gemini"]["status"] == "n/a"
    assert table["cost"]["edge"]["status"] == "pass"
    assert table["cost"]["gemini"]["status"] == "fail"
    assert table["naturalness"]["edge"]["value"] == "baseline"
