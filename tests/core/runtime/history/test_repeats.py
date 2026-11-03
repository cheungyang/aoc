"""The copies of your words a cancelled voice turn left unanswered in history
(core/runtime/history/repeats.py): which ones the turn that repeats them drops.

The histories below are shaped like the ones the Nov 2 snapshot holds, where a
turn cancelled while you went on talking left its words behind, once or twice.
"""
import unittest

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from core.runtime.history.repeats import unanswered_repeats


def human(text, id_):
    return HumanMessage(content=text, id=id_)


# How the agent a voice turn is routed to sees your words: main stamped them,
# the delegation put its <caller> line in front, and the agent stamped them again.
def routed(words, minute="17:00"):
    return f"[Mon {minute}] <caller>main</caller>\n[Mon {minute}] {words}"


class TestUnansweredRepeats(unittest.TestCase):

    def test_a_cancelled_turns_copy_is_dropped(self):
        history = [
            AIMessage(content="Earlier reply.", id="a0"),
            human("[Mon 17:00] Like to", "h1"),
            human("[Mon 17:00] Like to I would like you to assess the plan.", "h2"),
        ]
        self.assertEqual(unanswered_repeats(history), ["h1"])

    def test_two_cancelled_turns_leave_two_copies_and_both_go(self):
        """agent-designer's history on Nov 2: the same words three times."""
        history = [
            AIMessage(content="Earlier reply.", id="a0"),
            human(routed("네. 그럼 이따가 칭찬 한번 해 줘.", "17:15"), "h1"),
            human(routed("네. 그럼 이따가 칭찬 한번 해 줘. Hello.", "17:15"), "h2"),
            human(routed("네. 그럼 이따가 칭찬 한번 해 줘. Hello. Hi.", "17:15"), "h3"),
        ]
        self.assertEqual(unanswered_repeats(history), ["h1", "h2"])

    def test_stamps_and_caller_lines_are_not_the_words(self):
        """The turn that answers can come a minute later, and the agent's copy
        carries main's stamp, the <caller> line and its own stamp."""
        history = [
            human(routed("How do I do this? I would like you", "16:57"), "h1"),
            human(routed("How do I do this? I would like you To help me with my projects.", "16:58"), "h2"),
        ]
        self.assertEqual(unanswered_repeats(history), ["h1"])

    def test_an_answered_message_is_left_alone(self):
        history = [
            human("[Mon 17:00] Like to", "h1"),
            AIMessage(content="<speech>Like to what?</speech>", id="a1"),
            human("[Mon 17:01] Like to I would like you to assess the plan.", "h2"),
        ]
        self.assertEqual(unanswered_repeats(history), [])

    def test_only_the_run_right_before_the_newest_counts(self):
        """An older unanswered message, before a reply, is another turn's business."""
        history = [
            human("[Mon 16:00] Like to", "h0"),
            AIMessage(content="", tool_calls=[{"name": "t", "args": {}, "id": "c1"}], id="a1"),
            ToolMessage(content="done", tool_call_id="c1", id="t1"),
            human("[Mon 17:00] Like to I would like you to assess the plan.", "h2"),
        ]
        self.assertEqual(unanswered_repeats(history), [])

    def test_words_the_newest_message_doesnt_start_with_stay(self):
        """A typed message after a turn that failed doesn't repeat it, so the
        failed turn's message keeps its place; so does one whose words
        expired from the voice room's unanswered ones."""
        history = [
            human("[Mon 17:00] What's on my calendar?", "h1"),
            human("[Mon 17:02] try again", "h2"),
        ]
        self.assertEqual(unanswered_repeats(history), [])

    def test_the_match_ends_where_a_word_ends(self):
        history = [human("[Mon 17:00] Hi", "h1"), human("[Mon 17:00] Hiking this weekend?", "h2")]
        self.assertEqual(unanswered_repeats(history), [])

    def test_the_same_words_again_are_a_repeat(self):
        history = [human("[Mon 16:52] This is one of the directions.", "h1"),
                   human("[Mon 16:52] This is one of the directions.", "h2")]
        self.assertEqual(unanswered_repeats(history), ["h1"])

    def test_a_copy_carrying_a_hand_off_capsule_stays(self):
        """The cancelled turn claimed the capsule. Dropping its copy would lose
        it, and the copy doesn't match anyway: the capsule follows the words."""
        history = [
            human(routed("Like to") + '\n\n<handoff_context from="wiki-gardener">...</handoff_context>', "h1"),
            human(routed("Like to I would like you to assess the plan."), "h2"),
        ]
        self.assertEqual(unanswered_repeats(history), [])

    def test_the_run_stops_at_the_first_message_that_isnt_a_repeat(self):
        history = [
            human("[Mon 17:00] Something else entirely.", "h0"),
            human("[Mon 17:01] Like to", "h1"),
            human("[Mon 17:01] Like to I would like you to assess the plan.", "h2"),
        ]
        self.assertEqual(unanswered_repeats(history), ["h1"])

    def test_an_older_repeat_behind_one_that_isnt_stays(self):
        history = [
            human("[Mon 17:00] Like to", "h0"),
            human("[Mon 17:01] Something else entirely.", "h1"),
            human("[Mon 17:01] Like to I would like you to assess the plan.", "h2"),
        ]
        self.assertEqual(unanswered_repeats(history), [])

    def test_nothing_to_drop_without_a_newest_human_message_or_ids(self):
        self.assertEqual(unanswered_repeats([]), [])
        self.assertEqual(unanswered_repeats([human("[Mon 17:00] Hello.", "h1")]), [])
        self.assertEqual(unanswered_repeats([human("[Mon 17:00] Like to", "h1"),
                                             AIMessage(content="Like to what?", id="a1")]), [])
        self.assertEqual(unanswered_repeats([HumanMessage(content="[Mon 17:00] Like to"),
                                             human("[Mon 17:00] Like to go.", "h2")]), [])
        self.assertEqual(unanswered_repeats([human("[Mon 17:00] Like to", "h1"), human("[Mon 17:00]", "h2")]), [])


if __name__ == "__main__":
    unittest.main()
