"""The voice benchmark's judge (scripts/voice_judge.py), on fakes: no API, no key."""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.scheduler.script_runner import write_reason
from scripts import voice_judge as judge
from scripts.voice_dryrun import save_json


def stored(case="lights", combo="prod", reply=None, calls=2, start=400, first_token=1000, first_chunk=1300,
           audio=1800, first_reply=300, errored=False, passed=True, run=1):
    """A run as voice_dryrun.py --out stores it: its calls start at `start` ms, the first one
    streams after `first_reply` ms."""
    reply = f"{combo} says the kitchen light is on." if reply is None else reply
    return {
        "case_id": case, "run": run, "combo": combo, "room": "home-automation",
        "transcript": "Are the lights on?", "reply": reply, "spoken": [reply], "tools": ["home_assistant"] * 2,
        "agents": ["home-steward"],
        "model_calls": [{"t": start + 900 * i, "kind": "model_call", "call": i + 1, "agent": "home-steward",
                         "model": "m", "thinking": "low"} for i in range(calls)],
        "model_replies": [{"t": start + first_reply, "kind": "model_reply", "call": 1, "phase": "first_chunk"}]
        if calls else [],
        "marks": {"agent_start": 5, "first_token": first_token, "first_chunk": first_chunk, "tts_first_byte": audio},
        "errored": errored, "failures": ["agent error: [500] boom"] if errored else [], "passed": passed,
    }


def prefers(word):
    """A judge that picks the reply mentioning `word`, and calls it a tie when neither or both do."""
    def answer(prompt):
        a, b = prompt.split('<reply id="B">')
        letter = "tie" if (word in a) == (word in b) else "A" if word in a else "B"
        return json.dumps({"verdict": letter, "reason": f"{letter} has the {word}."})
    return answer


class FakeBatches:
    """client.batches: jobs stay pending until `finish` ends them."""

    def __init__(self):
        self.created, self.jobs = [], {}

    def create(self, *, model, src, config=None):
        name = f"batches/{len(self.created) + 1}"
        self.created.append(SimpleNamespace(model=model, src=src, config=config))
        self.jobs[name] = SimpleNamespace(name=name, state=SimpleNamespace(name="JOB_STATE_PENDING"), dest=None)
        return self.jobs[name]

    def get(self, *, name):
        return self.jobs[name]

    def finish(self, name, answer, state="JOB_STATE_SUCCEEDED"):
        """Ends job `name`, answering each of its requests with `answer(prompt)`."""
        src = self.created[int(name.split("/")[1]) - 1].src
        self.jobs[name].state = SimpleNamespace(name=state)
        self.jobs[name].dest = SimpleNamespace(inlined_responses=[
            SimpleNamespace(response=SimpleNamespace(text=answer(request["contents"][0]["parts"][0]["text"])),
                            metadata=request["metadata"], error=None) for request in src])


class FakeClient:
    def __init__(self, answer=None):
        self.batches = FakeBatches()
        self.models = SimpleNamespace(generate_content=MagicMock(
            side_effect=lambda model, contents, config: SimpleNamespace(text=answer(contents)) if answer else None))


def _row(report, combo):
    return next(line for line in report.splitlines() if line.startswith(f"| {combo} "))


def test_python_runs_it_without_yes():
    assert write_reason(judge.__file__, ["results.json", "--sync", "--limit", "3"]) == ""


class TestPairs:

    def test_what_the_judge_is_asked_about_and_what_it_isnt(self):
        results = [
            stored("lights", "PRO:low"), stored("lights", "FLASH:low"),
            stored("fixed", "PRO:low", calls=0, reply="Who's it for?"),
            stored("fixed", "FLASH:low", calls=0, reply="Who's it for?"),
            stored("same", "PRO:low", reply="It's on."), stored("same", "FLASH:low", reply="It's on."),
            stored("broke", "PRO:low"), stored("broke", "FLASH:low", errored=True),
            stored("theirs-broke", "PRO:low", errored=True), stored("theirs-broke", "FLASH:low"),
            stored("alone", "FLASH:low"),
        ]
        pairs = {p.key: p for p in judge.make_pairs(results, "PRO:low")}
        assert sorted(pairs) == [f"{case}#1 FLASH:low vs PRO:low"
                                 for case in ("broke", "fixed", "lights", "same", "theirs-broke")]
        assert pairs["lights#1 FLASH:low vs PRO:low"].prompt
        assert pairs["fixed#1 FLASH:low vs PRO:low"].skipped == "no model answered"
        assert pairs["theirs-broke#1 FLASH:low vs PRO:low"].skipped == "the reference run failed"
        same = pairs["same#1 FLASH:low vs PRO:low"]
        assert (same.outcome, same.reason, same.prompt) == ("tie", "the same reply", ""), "no judge asked"
        broke = pairs["broke#1 FLASH:low vs PRO:low"]
        assert (broke.outcome, broke.reason) == ("loss", "its run failed: agent error: [500] boom")

    def test_the_prompt_and_the_seeded_order(self):
        results = [run for i in range(20) for run in (stored(f"c{i}", "PRO:low"), stored(f"c{i}", "FLASH:low"))]
        pairs = judge.make_pairs(results, "PRO:low", seed=7)
        assert pairs == judge.make_pairs(results, "PRO:low", seed=7), "a rerun draws the same"
        assert {p.combo_first for p in pairs} == {True, False}, "both orders, so position bias cancels out"
        pair = pairs[0]
        a, b = pair.prompt.split('<reply id="B">')
        mine, theirs = (a, b) if pair.combo_first else (b, a)
        assert "FLASH:low says" in mine and "PRO:low says" in theirs
        for part in ("Room: home-automation", 'The user said: "Are the lights on?"', "Answered by: home-steward",
                     "Tools used: home_assistant\n", "Said aloud:", "Posted in the channel:"):
            assert part in pair.prompt

    def test_verdicts_map_back_to_the_combo_and_are_read_strictly(self):
        assert (judge.outcome("A", combo_first=True), judge.outcome("A", combo_first=False)) == ("win", "loss")
        assert (judge.outcome("B", combo_first=False), judge.outcome("tie", combo_first=True)) == ("win", "tie")
        assert judge.parse_verdict('{"verdict": "b", "reason": "B  names the light."}') == ("B", "B names the light.")
        assert judge.parse_verdict('```json\n{"verdict": "TIE", "reason": "Same."}\n```') == ("tie", "Same.")
        for text in ("", "A is better", '{"verdict": "C", "reason": "?"}', None):
            with pytest.raises(ValueError):
                judge.parse_verdict(text)


class TestBatches:

    def results(self, path, flash_kitchen=("lights", "thermostat")):
        runs = []
        for case in ("lights", "thermostat"):
            runs.append(stored(case, "PRO:low", reply=f"The {case} is fine."))
            word = "kitchen" if case in flash_kitchen else "hall"
            runs.append(stored(case, "FLASH:low", reply=f"The {case} in the {word} is fine."))
        save_json(str(path), runs)

    def test_submitted_once_then_collected_and_then_no_api_at_all(self, tmp_path, capsys):
        path = tmp_path / "results.json"
        self.results(path)
        client = FakeClient()

        assert judge.main([str(path)], client=client) == 0
        [job] = client.batches.created
        assert job.model == judge.JUDGE_MODEL and len(job.src) == 2
        assert job.src[0]["config"] == judge.judge_config()
        assert job.src[0]["metadata"] == {"key": "lights#1 FLASH:low vs PRO:low"}
        state = json.loads((tmp_path / "results.judge.json").read_text())
        assert state["batches"][0]["name"] == "batches/1"
        report = capsys.readouterr().out
        assert "Waiting on batches/1 (JOB_STATE_PENDING, 2 pairs)" in report
        assert _row(report, "FLASH:low").endswith("| waiting |")

        assert judge.main([str(path)], client=client) == 0
        assert len(client.batches.created) == 1, "a job still running isn't submitted again"
        assert _row(capsys.readouterr().out, "FLASH:low").endswith("| waiting |")

        client.batches.finish("batches/1", prefers("kitchen"))
        assert judge.main([str(path)], client=client) == 0
        assert len(client.batches.created) == 1, "collected, not submitted again"
        row = _row(capsys.readouterr().out, "FLASH:low")
        assert row.startswith("| FLASH:low | 2 | 2 / 0 / 0 | 100% | 0.60 / 0.60 s |") and row.endswith("| PASS |")

        with patch.object(judge, "make_client", side_effect=AssertionError("no API needed")):
            assert judge.main([str(path)]) == 0
        assert _row(capsys.readouterr().out, "FLASH:low").endswith("| PASS |")

    def test_a_job_that_ended_without_verdicts_is_submitted_again(self, tmp_path):
        path = tmp_path / "results.json"
        self.results(path)
        client = FakeClient()
        judge.main([str(path)], client=client)
        client.batches.finish("batches/1", prefers("kitchen"), state="JOB_STATE_EXPIRED")
        judge.main([str(path)], client=client)
        assert len(client.batches.created) == 2 and len(client.batches.created[1].src) == 2
        state = json.loads((tmp_path / "results.judge.json").read_text())
        assert [(b["name"], b["state"]) for b in state["batches"]] == [
            ("batches/1", "JOB_STATE_EXPIRED"), ("batches/2", "JOB_STATE_PENDING")]

    def test_a_done_job_without_answers_inline_stays_open_rather_than_paid_for_again(self, tmp_path, capsys):
        path = tmp_path / "results.json"
        self.results(path)
        client = FakeClient()
        judge.main([str(path)], client=client)
        client.batches.jobs["batches/1"].state = SimpleNamespace(name="JOB_STATE_SUCCEEDED")  # no dest
        capsys.readouterr()
        judge.main([str(path)], client=client)
        assert len(client.batches.created) == 1
        assert "Waiting on batches/1 (JOB_STATE_SUCCEEDED, 2 pairs)" in capsys.readouterr().out

    def test_replies_that_changed_since_are_judged_again(self, tmp_path, capsys):
        path = tmp_path / "results.json"
        self.results(path)
        client = FakeClient()
        judge.main([str(path)], client=client)
        client.batches.finish("batches/1", prefers("kitchen"))
        judge.main([str(path)], client=client)
        self.results(path, flash_kitchen=("lights",))  # thermostat's FLASH:low run was redone
        capsys.readouterr()
        judge.main([str(path)], client=client)
        assert [r["metadata"]["key"] for r in client.batches.created[1].src] == ["thermostat#1 FLASH:low vs PRO:low"]
        assert "| FLASH:low | 1 (+1 waiting) |" in capsys.readouterr().out

    def test_limit_asks_about_only_so_many_new_pairs(self, tmp_path, capsys):
        path = tmp_path / "results.json"
        self.results(path)
        client = FakeClient()
        judge.main([str(path), "--limit", "1"], client=client)
        assert len(client.batches.created[0].src) == 1
        assert "Not asked yet: 1 pair. Run this again to ask." in capsys.readouterr().out


class TestSync:

    def test_asks_directly_and_waits_out_a_429(self, tmp_path, monkeypatch, capsys):
        path = tmp_path / "results.json"
        save_json(str(path), [stored("lights", "PRO:low"), stored("lights", "FLASH:low")])
        client = FakeClient()
        client.models.generate_content.side_effect = [
            RuntimeError("429 RESOURCE_EXHAUSTED. Please retry in 3s."),
            SimpleNamespace(text='{"verdict": "tie", "reason": "Both say it is on."}')]
        waits = []
        monkeypatch.setattr(judge, "_sleep", waits.append)

        assert judge.main([str(path), "--sync"], client=client) == 0
        assert waits == [30], "the API asked for less than the first wait"
        assert client.batches.created == []
        assert _row(capsys.readouterr().out, "FLASH:low").startswith("| FLASH:low | 1 | 0 / 1 / 0 | 100% |")
        verdict = json.loads((tmp_path / "results.judge.json").read_text())["verdicts"]["lights#1 FLASH:low vs PRO:low"]
        assert (verdict["outcome"], verdict["reason"]) == ("tie", "Both say it is on.")

    def test_an_error_that_isnt_a_rate_limit_stops_it(self, tmp_path, monkeypatch):
        path = tmp_path / "results.json"
        save_json(str(path), [stored("lights", "PRO:low"), stored("lights", "FLASH:low")])
        client = FakeClient()
        client.models.generate_content.side_effect = RuntimeError("400 INVALID_ARGUMENT")
        monkeypatch.setattr(judge, "_sleep", lambda seconds: pytest.fail("not a rate limit"))
        with pytest.raises(RuntimeError):
            judge.main([str(path), "--sync"], client=client)


class TestReport:

    def test_latency_is_timed_from_the_turns_first_model_call(self):
        run = stored(start=400, first_token=1000, first_chunk=1300, audio=1800, first_reply=250)
        assert judge.since_first_call(run, "first_token") == 600
        assert judge.since_first_call(run, "tts_first_byte") == 1400
        assert judge.first_call_reply(run) == 250
        assert judge.since_first_call(stored(calls=0), "first_token") is None, "the fixed line isn't timed"
        assert judge.first_call_reply({**run, "model_replies": []}) is None, "results from before the reply events"

    def test_the_bar_needs_both_the_win_rate_and_the_first_token(self):
        assert judge.bar(0.90, 700) == "PASS"
        assert judge.bar(26 / 29, 650) == "FAIL: wins or ties 89.7%"
        assert judge.bar(1.0, 701) == "FAIL: first token 701 ms"
        assert judge.bar(0.5, None) == "FAIL: wins or ties 50.0%, no first token"

    def test_each_combo_is_held_to_the_bar(self, tmp_path, capsys):
        runs = []
        for i in range(10):
            runs.append(stored(f"c{i}", "PRO:low", reply=f"Answer {i}.", first_token=3400))
            runs.append(stored(f"c{i}", "FLASH:low", reply=f"Answer {i}, {'bad' if i == 9 else 'good'}."))
            runs.append(stored(f"c{i}", "FLASH_LITE:minimal", reply=f"Answer {i}, bad." if i < 2 else f"Answer {i}!"))
            runs.append(stored(f"c{i}", "PRO:medium", reply=f"Answer {i}, good.", first_token=2000))
        runs += [stored("fixed", combo, calls=0, reply="Who's it for?")
                 for combo in ("PRO:low", "FLASH:low", "FLASH_LITE:minimal", "PRO:medium")]
        path = tmp_path / "results.json"
        save_json(str(path), runs)

        def answer(prompt):
            a, b = prompt.split('<reply id="B">')
            letter = "A" if "good" in a or "bad" in b else "B" if "good" in b or "bad" in a else "tie"
            return json.dumps({"verdict": letter, "reason": f"{letter} is better."})

        assert judge.main([str(path), "--sync"], client=FakeClient(answer)) == 0
        report = capsys.readouterr().out
        assert _row(report, "FLASH:low").startswith("| FLASH:low | 10 | 9 / 0 / 1 | 90% |")
        assert _row(report, "FLASH:low").endswith("| PASS |")
        assert _row(report, "FLASH_LITE:minimal").endswith("| FAIL: wins or ties 80.0% |")
        assert _row(report, "PRO:medium").endswith("| FAIL: first token 1600 ms |")
        assert _row(report, "PRO:low").startswith("| PRO:low (reference) | - | - | - | 3.00 / 3.00 s |")
        assert _row(report, "PRO:low").endswith("| 11/11 | reference |")
        assert "Not counted: 3 pairs where no model answered." in report
        assert "- c9#1 FLASH:low vs PRO:low (it was " in report

    def test_without_runs_on_the_reference_it_says_which_combos_there_are(self, tmp_path, capsys):
        path = tmp_path / "results.json"
        save_json(str(path), [stored("lights", "FLASH:low"), stored("lights", "prod")])
        assert judge.main([str(path)], client=FakeClient()) == 1
        assert "pass --reference with one of: FLASH:low, prod" in capsys.readouterr().out
        assert judge.main([str(path), "--reference", "prod", "--sync"],
                          client=FakeClient(prefers("FLASH"))) == 0
