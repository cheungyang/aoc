"""Voice prompts (core/prompts/voice.py): <voice_mode>, the speech writer, the near-miss resolver."""
import unittest

from core.channel.response_parser import AgentResponse
from core.prompts.base import load_template, placeholders
from core.prompts.system import get_formatting_prompt
from core.prompts.voice import (
    NearMissPrompt,
    ScheduledVoiceModePrompt,
    SpeechWriterPrompt,
    VoiceModePrompt,
    get_voice_mode_prompt,
    parse_speech,
    scheduled_speech_scope,
)

SPEECH_RULES = load_template("voice/_speech_rules")
QUESTION_RULE = load_template("voice/_question_rule")
VOICE_MODE = VoiceModePrompt().render()
SCHEDULED_VOICE_MODE = ScheduledVoiceModePrompt().render()


class TestVoiceModePrompt(unittest.TestCase):
    """<voice_mode> reaches exactly the turns the user hears."""

    def _prompt_in(self, **session_kwargs):
        from core.runtime.foundation.context import current_execution_context
        from core.runtime.foundation.sessions import SessionManager
        token = current_execution_context.set(SessionManager.get_session(**session_kwargs))
        try:
            return get_voice_mode_prompt()
        finally:
            current_execution_context.reset(token)

    def test_a_voice_turn_gets_the_block(self):
        prompt = self._prompt_in(agent_id="main", source="discord", channel="general", surface="voice")
        self.assertEqual(prompt, VOICE_MODE)
        self.assertIn("Begin each response with a <speech>...</speech> block", prompt)
        self.assertIn("Before calling tools, write a one-sentence <speech> block", prompt)

    def test_the_block_says_the_words_were_heard_and_may_be_misheard(self):
        """The prod logs: "recipe tree" read right and said aloud; "Frankie." from
        324 ms of audio cost two model calls and a poll to ask what was meant."""
        self.assertIn("The user's words come from speech recognition", VOICE_MODE)
        self.assertIn("say so first in <speech>", VOICE_MODE)
        self.assertIn("ask for a quick yes", VOICE_MODE, "no write on the strength of a guess")
        self.assertIn("no poll and no tool call", VOICE_MODE)
        self.assertNotIn("speech recognition", SCHEDULED_VOICE_MODE, "a scheduled post has no words of yours")

    def test_a_typed_turn_does_not(self):
        self.assertEqual(self._prompt_in(agent_id="main", source="discord", channel="general"), "")

    def test_a_delegate_inherits_it_but_a_stateless_worker_never_gets_it(self):
        from core.runtime.foundation.context import current_execution_context
        from core.runtime.foundation.sessions import SessionManager
        voice_turn = SessionManager.get_session(agent_id="main", source="discord", channel="general", surface="voice")
        token = current_execution_context.set(voice_turn)
        try:
            delegate = SessionManager.get_session(agent_id="day-planner", source="tool", channel="day-planning")
            worker = SessionManager.get_session(agent_id="graph-worker-low", source="tool", channel="general",
                                                stateless=True)
        finally:
            current_execution_context.reset(token)
        self.assertEqual((delegate.get_surface(), worker.get_surface()), ("voice", "voice"))
        for session, expected in ((delegate, VOICE_MODE), (worker, "")):
            token = current_execution_context.set(session)
            try:
                self.assertEqual(get_voice_mode_prompt(), expected, session.agent_id)
            finally:
                current_execution_context.reset(token)

    def test_no_context_no_block(self):
        from core.runtime.foundation.context import current_execution_context
        token = current_execution_context.set(None)
        try:
            self.assertEqual(get_voice_mode_prompt(), "")
        finally:
            current_execution_context.reset(token)

    def test_the_formatting_rules_allow_speech_under_voice_mode(self):
        """Otherwise they forbid the very block <voice_mode> asks for."""
        self.assertIn("<speech> when <voice_mode> asks for it", get_formatting_prompt())

    def test_a_scheduled_run_gets_the_scheduled_block_only_inside_its_scope(self):
        scheduled = dict(agent_id="day-planner", source="scheduled", channel="day-planning")
        self.assertEqual(self._prompt_in(**scheduled), "", "an IGNORE schedule runs outside the scope")
        with scheduled_speech_scope("day-planner"):
            self.assertEqual(self._prompt_in(**scheduled), SCHEDULED_VOICE_MODE)
            # An agent the run calls writes for its caller, not for the channel.
            self.assertEqual(self._prompt_in(agent_id="graph-worker", source="scheduled", channel="day-planning"), "")
            # A live voice turn meanwhile keeps the live block.
            self.assertEqual(self._prompt_in(agent_id="main", source="discord", channel="general", surface="voice"),
                             VOICE_MODE)

    def test_a_proactive_delivery_gets_the_scheduled_block_inside_its_scope(self):
        proactive = dict(agent_id="meal-planner", source="proactive", channel="meal-planning")
        self.assertEqual(self._prompt_in(**proactive), "")
        with scheduled_speech_scope("meal-planner"):
            self.assertEqual(self._prompt_in(**proactive), SCHEDULED_VOICE_MODE)

    def test_the_scheduled_block_marks_questions_and_has_no_pre_tool_line(self):
        self.assertIn(SPEECH_RULES, SCHEDULED_VOICE_MODE)
        self.assertIn(SPEECH_RULES, VOICE_MODE, "one set of rules for both")
        self.assertIn(QUESTION_RULE, SCHEDULED_VOICE_MODE)
        self.assertNotIn("<question>", VOICE_MODE)
        self.assertNotIn("Before calling tools", SCHEDULED_VOICE_MODE)
        self.assertIn("Begin your final response with a <speech>", SCHEDULED_VOICE_MODE)
        self.assertIn("don't introduce yourself", SCHEDULED_VOICE_MODE)


class TestSpeechWriterPrompt(unittest.TestCase):

    def test_same_rules_as_voice_mode_question_included(self):
        prompt = SpeechWriterPrompt(name="William", channel="pkm-wiki",
                                    post="  Two notes overlap: {habits} and {routines}. Merge?  ").render()
        self.assertIn(SPEECH_RULES, prompt)
        self.assertIn(QUESTION_RULE, prompt)
        self.assertIn("<post>\nTwo notes overlap: {habits} and {routines}. Merge?\n</post>", prompt,
                      "a post's braces are text, and its edges are trimmed")
        self.assertIn("Author: William", prompt)
        self.assertIn("#pkm-wiki", prompt)
        self.assertIn("<assigned_task>", prompt, "in the worker's playbook format")
        self.assertNotIn("{{", prompt, "every tag filled")

    def test_the_template_takes_the_author_the_channel_and_the_post(self):
        self.assertEqual(placeholders("voice/speech_writer"), {"name", "channel", "post"})
        self.assertEqual((SpeechWriterPrompt.caller, SpeechWriterPrompt.timeout), ("speech-writer", 20.0))


class TestParseSpeech(unittest.TestCase):

    def test_the_speech_block_and_its_question(self):
        speech, question = parse_speech(
            "<speech>William here. <question>Should the two notes be merged?</question></speech>")
        self.assertEqual(speech, "William here. Should the two notes be merged?")
        self.assertEqual(question, "Should the two notes be merged?")

    def test_the_workers_parsed_reply(self):
        response = AgentResponse.from_string("<speech>Daisy here. <question>Energy, one to ten?</question></speech>")
        self.assertEqual(parse_speech(response), ("Daisy here. Energy, one to ten?", "Energy, one to ten?"))
        self.assertEqual(parse_speech(AgentResponse.from_string("<payload>Plain words.</payload>")),
                         ("Plain words.", None))

    def test_a_reply_without_tags_is_taken_whole(self):
        self.assertEqual(parse_speech("Ted here. Your syllabus is ready."), ("Ted here. Your syllabus is ready.", None))
        self.assertEqual(parse_speech([{"type": "text", "text": "<speech>Hi.</speech>"}]), ("Hi.", None))
        self.assertEqual(parse_speech(""), (None, None))
        self.assertEqual(parse_speech(None), (None, None))


class TestNearMissPrompt(unittest.TestCase):

    def test_the_phrase_and_the_targets_in_the_workers_playbook_format(self):
        prompt = NearMissPrompt(phrase="exercise", targets="fitness-coach | fitness coach Frankie | #fitness-coach").render()
        self.assertIn('The user asked to be switched to: "exercise"', prompt)
        self.assertIn("fitness-coach | fitness coach Frankie | #fitness-coach", prompt)
        self.assertIn("<assigned_task>", prompt)
        self.assertIn("<payload>none</payload>", prompt)

    def test_it_is_kept_short_on_the_voice_budget(self):
        self.assertEqual((NearMissPrompt.caller, NearMissPrompt.timeout), ("near-miss", 4.0))


if __name__ == "__main__":
    unittest.main()
