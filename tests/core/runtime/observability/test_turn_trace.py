"""The per-turn latency trace (core.runtime.observability.turn_trace)."""
import contextvars
import json
import os
import tempfile
import time
import unittest
import unittest.mock

from core.runtime.observability import turn_trace
from core.runtime.observability.turn_trace import STAGES, TurnTrace, current_turn_trace, detach_turn_trace, start_turn_trace
from core.storage.sessions import SessionStore, sanitize_table_name


class TestTurnTrace(unittest.TestCase):

    def test_marks_are_ms_since_the_last_voiced_frame_and_first_one_wins(self):
        t0 = time.monotonic()
        trace = TurnTrace(agent_id="main", turn_id=1, anchor=t0)
        trace.mark("stt_done", at=t0 + 1.25)
        trace.mark("stt_done", at=t0 + 9)
        self.assertEqual(trace.marks, {"speech_end": 0, "stt_done": 1250})

    def test_unknown_stage_is_an_error(self):
        with self.assertRaises(ValueError):
            TurnTrace(agent_id="main", turn_id=1, anchor=0).mark("first_tokne")

    def test_stage_deltas_follow_stage_order(self):
        t0 = time.monotonic()
        trace = TurnTrace(agent_id="main", turn_id=1, anchor=t0)
        trace.mark("vad_final", at=t0 + 0.9)
        trace.mark("stt_done", at=t0 + 2.4)
        trace.mark("audio_start", at=t0 + 5.0)
        self.assertEqual(trace.stage_deltas(), {"vad_final": 900, "stt_done": 1500, "audio_start": 2600})
        self.assertEqual(trace.summary()["ttfa_ms"], 5000)

    def test_finish_records_once_and_freezes(self):
        trace = TurnTrace(agent_id="main", turn_id=1, anchor=time.monotonic())
        first = trace.finish()
        trace.mark("audio_start")
        trace.event("tool_start", tool="late")
        self.assertIn("turn_end", first["marks"])
        self.assertNotIn("audio_start", trace.marks)
        self.assertEqual(trace.events, [])
        self.assertEqual(trace.finish(), first)

    def test_context_binding_and_detaching(self):
        def inside():
            trace = start_turn_trace("main", 3)
            self.assertIs(current_turn_trace(), trace)
            detached = contextvars.copy_context()
            detached.run(detach_turn_trace)
            self.assertIsNone(detached.run(current_turn_trace))
            self.assertIs(current_turn_trace(), trace)

        contextvars.copy_context().run(inside)
        self.assertIsNone(current_turn_trace())

    def test_stages_are_documented_in_order(self):
        self.assertEqual(STAGES[0], "speech_end")
        self.assertEqual(STAGES[-1], "turn_end")

    def test_model_calls_are_numbered_and_their_replies_carry_the_number(self):
        trace = TurnTrace(agent_id="main", turn_id=1, anchor=time.monotonic())
        first = trace.model_call("main", "flash", "low")
        trace.model_reply(first, "first_chunk")
        second = trace.model_call("day-planner", "pro", "medium")
        trace.model_reply(second, "done")
        self.assertEqual((first, second), (1, 2))
        self.assertEqual([(e["kind"], e["call"]) for e in trace.events],
                         [("model_call", 1), ("model_reply", 1), ("model_call", 2), ("model_reply", 2)])
        self.assertEqual(trace.events[0]["thinking"], "low")
        self.assertEqual(trace.events[1]["phase"], "first_chunk")

    def test_the_loop_lag_over_the_turn_is_recorded_when_the_probe_runs(self):
        trace = TurnTrace(agent_id="main", turn_id=1, anchor=time.monotonic())
        lag = {"stalls": 2, "total_ms": 310, "max_ms": 180}
        with unittest.mock.patch.object(turn_trace, "lag_between", return_value=lag) as between:
            self.assertEqual(trace.finish()["loop_lag"], lag)
        self.assertEqual(between.call_args.args[0], trace.anchor)

    def test_without_the_probe_there_is_no_loop_lag(self):
        trace = TurnTrace(agent_id="main", turn_id=1, anchor=time.monotonic())
        self.assertNotIn("loop_lag", trace.finish())


class TestStorage(unittest.TestCase):

    def test_trace_is_stored_next_to_the_token_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "memory.db")
            store = SessionStore(db)
            store.append_token_usage("main:discord:general", "gemini", 10, 5, 0.0, surface="voice")
            store.append_turn_trace("main:discord:general", {"ttfa_ms": 4200, "marks": {"audio_start": 4200}})
            store.append_turn_trace("main:discord:general", {"ttfa_ms": None, "marks": {}})

            with store.connect() as conn:
                rows = conn.execute(
                    f'SELECT entry_type, execution_time, surface, message FROM "{sanitize_table_name("main:discord:general")}" ORDER BY id'
                ).fetchall()

        self.assertEqual([r[0] for r in rows], ["token", "turn_trace", "turn_trace"])
        self.assertAlmostEqual(rows[1][1], 4.2)
        self.assertEqual(rows[1][2], "voice")
        self.assertEqual(json.loads(rows[1][3])["ttfa_ms"], 4200)
        self.assertIsNone(rows[2][1], "a turn with no audio must not count as 0 s")


if __name__ == "__main__":
    unittest.main()
