"""Tests for the addressed-agent rule (core/runtime/foundation/addressing.py).

The positives include messages from the real session history, where agents
were addressed by name in their own channels; the negatives are the ways a
message names an agent without speaking to it.
"""
import os
import sys
import unittest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))

from core.runtime.foundation.addressing import (
    DIRECTIVE,
    LEADING,
    MENTION,
    TRAILING,
    addressed_agent,
    aliases_for,
    message_text,
)

AGENTS = {
    "topic-researcher": {"name": "Ted"},
    "wiki-gardener": {"name": "William"},
    "day-planner": {"name": "Daisy"},
    "meal-planner": {"name": "Ramsay"},
    "reward-travel": {"name": "Miles"},
    "software-planner": {"name": "Elephant"},
    "goal-setter": {"name": "Grace"},
    "receipt-processor": {"name": "Receipt Processor"},
}
ALIASES = aliases_for(AGENTS)


def who(prompt, aliases=ALIASES):
    found = addressed_agent(prompt, aliases)
    return found.agent_id if found else None


class TestVocativeForms(unittest.TestCase):

    def test_the_name_first_set_off_by_punctuation(self):
        for text in ("Ted, create a syllabus on THIS topic", "Ted: what about this?", "Ted - take this",
                     "Ted—take this", "Ted! look", "Ted? are you there", "Ted. Make it short"):
            with self.subTest(text=text):
                self.assertEqual(who(text), "topic-researcher")

    def test_the_name_first_then_the_request(self):
        """The form from the proposal's example: no comma."""
        found = addressed_agent("Ted create a syllabus on THIS topic", ALIASES)
        self.assertEqual((found.agent_id, found.form, found.name), ("topic-researcher", LEADING, "Ted"))

    def test_how_agents_were_addressed_in_the_real_history(self):
        cases = {
            "Ted let's continue with the learning challenge topic": "topic-researcher",
            "Miles can you confirm that you have access to pkm/wallet/travel/": "reward-travel",
            "ramsay did you update the inventory?": "meal-planner",
            "ramsay, i cooked spinach and oyster fried egg yesterday": "meal-planner",
            "daisy morning update": "day-planner",
            "grace, help me to continue to plan for the GLM project": "goal-setter",
            "elephant that are currently in the queue": "software-planner",
            "elephant, new project idea: help me design a french words app": "software-planner",
            "ask ted to show us a list of current topics": "topic-researcher",
        }
        for text, agent_id in cases.items():
            with self.subTest(text=text):
                self.assertEqual(who(text), agent_id)

    def test_openers_before_the_name(self):
        for text in ("hey Ted, take this", "ok so ted what now", "thanks Ted!", "hi @Ted"):
            with self.subTest(text=text):
                self.assertEqual(who(text), "topic-researcher")

    def test_a_handover_verb_before_the_name(self):
        for text in ("ask Ted to build a syllabus", "tell Daisy that I'm in Tahoe", "have Ted look at this",
                     "let Ramsay know we ate the fish", "get Ted to dig in"):
            with self.subTest(text=text):
                self.assertIsNotNone(who(text))
        self.assertEqual(addressed_agent("ask Ted to build it", ALIASES).form, DIRECTIVE)

    def test_the_name_last_after_a_comma(self):
        found = addressed_agent("what do you make of this, Ted?", ALIASES)
        self.assertEqual((found.agent_id, found.form), ("topic-researcher", TRAILING))
        self.assertEqual(who("can you put it on the list, Daisy 🙏"), "day-planner")

    def test_an_at_mention_anywhere(self):
        found = addressed_agent("can you build a syllabus for this @Ted", ALIASES)
        self.assertEqual((found.agent_id, found.form), ("topic-researcher", MENTION))

    def test_an_at_mention_first_addresses_whatever_follows(self):
        self.assertEqual(who("@Ted said what?"), "topic-researcher")

    def test_the_name_alone(self):
        self.assertEqual(who("Ted"), "topic-researcher")
        self.assertEqual(who("Ted?"), "topic-researcher")

    def test_a_question_after_an_auxiliary_is_to_the_agent(self):
        for text in ("Ted can you", "Ted would you mind", "Ted is this right?", "Ted did that work",
                     "Ted should we start"):
            with self.subTest(text=text):
                self.assertEqual(who(text), "topic-researcher")

    def test_an_adverb_before_the_request_is_skipped(self):
        self.assertEqual(who("Ted just start it"), "topic-researcher")
        self.assertEqual(who("Ted also add the paper"), "topic-researcher")

    def test_ids_and_multi_word_names(self):
        self.assertEqual(who("topic-researcher, take this"), "topic-researcher")
        self.assertEqual(who("topic researcher, take this"), "topic-researcher")
        self.assertEqual(who("receipt processor, file this"), "receipt-processor")

    def test_case_does_not_matter_for_a_name(self):
        self.assertEqual(who("TED, take this"), None)  # all caps: an acronym
        self.assertEqual(who("ted, take this"), "topic-researcher")
        self.assertEqual(who("@TED take this"), "topic-researcher")

    def test_a_multimodal_prompt_reads_its_text(self):
        prompt = [{"type": "text", "text": "Ramsay, add these"}, {"type": "image_url", "image_url": {"url": "data:x"}}]
        self.assertEqual(who(prompt), "meal-planner")


class TestNamingIsNotAddressing(unittest.TestCase):
    """The reporting-verb stoplist: the name is the subject, not the listener."""

    def test_reporting_verbs(self):
        for text in ("William said it leans on caching", "Ted thinks so", "Ted told me", "Daisy wants a review",
                     "Ted mentioned that", "So William said that"):
            with self.subTest(text=text):
                self.assertIsNone(who(text))

    def test_a_past_tense(self):
        for text in ("Ted posted it", "Ted created a syllabus", "Ted finished"):
            with self.subTest(text=text):
                self.assertIsNone(who(text))

    def test_an_auxiliary_without_a_question(self):
        for text in ("Ted is great", "Ted can do it", "Ted will handle it", "Ted has a syllabus",
                     "Ted won't mind"):
            with self.subTest(text=text):
                self.assertIsNone(who(text))

    def test_an_adverb_then_a_reporting_verb(self):
        self.assertIsNone(who("Ted just said it"))
        self.assertIsNone(who("Ted already posted"))

    def test_a_possessive(self):
        self.assertIsNone(who("Ted's syllabus is great"))
        self.assertIsNone(who("Ted’s syllabus is great"))
        self.assertIsNone(who("ask Ted's opinion"))

    def test_joining_words_and_prepositions(self):
        for text in ("Ted and William, look at this", "Miles to go", "Elephant in the room"):
            with self.subTest(text=text):
                self.assertIsNone(who(text))

    def test_an_acronym(self):
        self.assertIsNone(who("TED talk on caching was great"))

    def test_a_name_inside_the_message(self):
        self.assertIsNone(who("I asked Ted about this yesterday"))
        self.assertIsNone(who("Ted-like answers please"))

    def test_mentions_of_two_agents_address_neither(self):
        self.assertIsNone(who("I think @Ted and @William would know"))

    def test_an_agent_not_in_the_aliases(self):
        """The router leaves out the agent that answers the channel: naming it
        is a message for it, not a hand-off."""
        without_william = aliases_for({k: v for k, v in AGENTS.items() if k != "wiki-gardener"})
        self.assertIsNone(who("William, what about this?", without_william))

    def test_no_aliases_or_no_text(self):
        self.assertIsNone(who("Ted, go", {}))
        self.assertIsNone(who(""))
        self.assertIsNone(who(None))


class TestWhatTheRunnerAdds(unittest.TestCase):
    """The words around yours, which the Discord runner and delegation add."""

    def test_the_replied_to_message_is_ignored(self):
        text = 'Ted, go\n\n<replying_to author="William" posted="2026-10-24 07:00">Ingested, Ted</replying_to>'
        self.assertEqual(message_text(text), "Ted, go")
        self.assertIsNone(who('looks good\n\n<replying_to author="William">thanks, Ted</replying_to>'))

    def test_a_new_threads_starter_is_ignored(self):
        text = '[Thread starter message from Alva: "the plan"]\n\nTed, take this'
        self.assertEqual(who(text), "topic-researcher")

    def test_a_caller_tag_is_ignored(self):
        self.assertEqual(who("<caller>main</caller>\nTed, go"), "topic-researcher")


class TestAliases(unittest.TestCase):

    def test_names_and_ids_both_lead_to_the_agent(self):
        self.assertEqual(ALIASES["ted"], "topic-researcher")
        self.assertEqual(ALIASES["topic researcher"], "topic-researcher")
        self.assertEqual(ALIASES["receipt processor"], "receipt-processor")

    def test_a_shared_alias_is_dropped(self):
        aliases = aliases_for({"a": {"name": "Sam"}, "b": {"name": "Sam"}})
        self.assertNotIn("sam", aliases)
        self.assertEqual(aliases["a"], "a")


if __name__ == "__main__":
    unittest.main()
