import unittest
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.channel.response_parser import AgentResponse

class TestAgentResponse(unittest.TestCase):

    def test_perfect_xml(self):
        reply_text = "Hello"
        response = AgentResponse.from_string(reply_text)
        self.assertEqual(response.text, "Hello")
        self.assertIsNone(response.poll_data)

    def test_xml_with_filler_text(self):
        reply_text = "Sure, here it is: Hello Hope that helps!"
        response = AgentResponse.from_string(reply_text)
        self.assertEqual(response.text, "Sure, here it is: Hello Hope that helps!")
        self.assertIsNone(response.poll_data)

    def test_malformed_xml_fallback(self):
        # Special characters in text should be preserved as it's not parsed as XML
        reply_text = "Hello & welcome"
        response = AgentResponse.from_string(reply_text)
        self.assertEqual(response.text, "Hello & welcome")
        self.assertIsNone(response.poll_data)

    def test_perfect_poll(self):
        reply_text = """Choose one:
<poll allow_multiple="false">
  <question>Color?</question>
  <options>
    <option><text>Red</text><emoji>🔴</emoji><response>Red</response></option>
    <option><text>Blue</text><emoji>🔵</emoji><response>Blue</response></option>
  </options>
</poll>
"""
        response = AgentResponse.from_string(reply_text)
        self.assertEqual(response.text.strip(), "Choose one:")
        self.assertIsNotNone(response.poll_data)
        self.assertEqual(response.poll_data["question"], "Color?")
        self.assertFalse(response.poll_data["allow_multiple"])
        self.assertEqual(len(response.poll_data["options"]), 2)
        self.assertEqual(response.poll_data["options"][0]["text"], "Red")
        self.assertEqual(response.poll_data["options"][0]["emoji"], "🔴")
        self.assertEqual(response.poll_data["options"][0]["response"], "Red")

    def test_malformed_poll_fallback(self):
        # & makes it invalid XML in question
        reply_text = """Choose one:
<poll allow_multiple="false">
  <question>Color & Shape?</question>
  <options>
    <option><text>Red Circle</text><emoji>🔴</emoji><response>Red Circle</response></option>
    <option><text>Blue Square</text><emoji>🟦</emoji><response>Blue Square</response></option>
  </options>
</poll>
"""
        response = AgentResponse.from_string(reply_text)
        self.assertEqual(response.text.strip(), "Choose one:")
        self.assertIsNotNone(response.poll_data)
        self.assertEqual(response.poll_data["question"], "Color & Shape?")
        self.assertFalse(response.poll_data["allow_multiple"])
        self.assertEqual(len(response.poll_data["options"]), 2)
        self.assertEqual(response.poll_data["options"][0]["text"], "Red Circle")

    def test_no_xml_block(self):
        reply_text = "This is just raw text without any tags."
        response = AgentResponse.from_string(reply_text)
        self.assertEqual(response.text, reply_text)
        self.assertIsNone(response.poll_data)

    def test_perfect_images(self):
        reply_text = """Here are the images:
<images>
  <image path="assets/img1.png"/>
  <image path="assets/img2.jpg"/>
</images>
"""
        response = AgentResponse.from_string(reply_text)
        self.assertEqual(response.text.strip(), "Here are the images:")
        self.assertIsNotNone(response.image_paths)
        self.assertEqual(len(response.image_paths), 2)
        self.assertEqual(response.image_paths[0], "assets/img1.png")
        self.assertEqual(response.image_paths[1], "assets/img2.jpg")

    def test_images_fallback(self):
        reply_text = """Here are the images:
<images>
  <image path="assets/img1.png"/>
  <image path="assets/img2.jpg"/>
  <invalid>
</images>
"""
        response = AgentResponse.from_string(reply_text)
        self.assertEqual(response.text.strip(), "Here are the images:")
        self.assertIsNotNone(response.image_paths)
    def test_perfect_videos(self):
        reply_text = """Here are the videos:
<videos>
  <video path="assets/video1.mp4"/>
  <video path="assets/video2.webm"/>
</videos>
"""
        response = AgentResponse.from_string(reply_text)
        self.assertEqual(response.text.strip(), "Here are the videos:")
        self.assertIsNotNone(response.video_paths)
        self.assertEqual(len(response.video_paths), 2)
        self.assertEqual(response.video_paths[0], "assets/video1.mp4")
        self.assertEqual(response.video_paths[1], "assets/video2.webm")

    def test_videos_fallback(self):
        reply_text = """Here are the videos:
<videos>
  <video path="assets/video1.mp4"/>
  <video path="assets/video2.webm"/>
  <invalid>
</videos>
"""
        response = AgentResponse.from_string(reply_text)
        self.assertEqual(response.text.strip(), "Here are the videos:")
        self.assertIsNotNone(response.video_paths)
        self.assertEqual(len(response.video_paths), 2)
        self.assertEqual(response.video_paths[0], "assets/video1.mp4")
        self.assertEqual(response.video_paths[1], "assets/video2.webm")

    def test_combined_images_and_videos(self):
        reply_text = """Here is the package:
<images>
  <image path="assets/img1.png"/>
</images>
<videos>
  <video path="assets/video1.mp4"/>
</videos>
"""
        response = AgentResponse.from_string(reply_text)
        self.assertEqual(response.text.strip(), "Here is the package:")
        self.assertIsNotNone(response.image_paths)
        self.assertEqual(response.image_paths, ["assets/img1.png"])
        self.assertIsNotNone(response.video_paths)
        self.assertEqual(response.video_paths, ["assets/video1.mp4"])

    def test_perfect_system_memory_log(self):
        reply_text = """I have completed your task successfully!
<system_memory_log>
- [12:00:00] [MEMORY] Task: Generated chart. Status: Success. Decisions: Used matplotlib.
- [12:00:00] [CONTEXT] Evergreen: User prefers dark mode charts.
- [12:00:00] [FEEDBACK] Explicit: Ensure axes are always labeled.
</system_memory_log>
"""
        response = AgentResponse.from_string(reply_text)
        self.assertEqual(response.text.strip(), "I have completed your task successfully!")
        self.assertIsNotNone(response.system_memory_log)
        self.assertIn("[MEMORY] Task: Generated chart.", response.system_memory_log)
        self.assertIn("[CONTEXT] Evergreen: User prefers dark mode charts.", response.system_memory_log)
        self.assertIn("[FEEDBACK] Explicit: Ensure axes are always labeled.", response.system_memory_log)
        self.assertNotIn("<system_memory_log>", response.text)
        self.assertNotIn("</system_memory_log>", response.text)

    def test_combined_all_xml_blocks(self):
        reply_text = """Here is the complete report and options:
<poll allow_multiple="false">
  <question>Proceed?</question>
  <options>
    <option><text>Yes</text><emoji>✅</emoji><response>Yes</response></option>
  </options>
</poll>
<images>
  <image path="assets/report.png"/>
</images>
<videos>
  <video path="assets/summary.mp4"/>
</videos>
<system_memory_log>
- [12:00:00] [MEMORY] Task: Full report generated.
</system_memory_log>
"""
        response = AgentResponse.from_string(reply_text)
        self.assertEqual(response.text.strip(), "Here is the complete report and options:")
        self.assertIsNotNone(response.poll_data)
        self.assertIsNotNone(response.image_paths)
        self.assertIsNotNone(response.video_paths)
        self.assertIsNotNone(response.system_memory_log)
        self.assertEqual(response.poll_data["question"], "Proceed?")
        self.assertEqual(response.image_paths, ["assets/report.png"])
        self.assertEqual(response.video_paths, ["assets/summary.mp4"])
        self.assertEqual(response.system_memory_log, "- [12:00:00] [MEMORY] Task: Full report generated.")
        self.assertNotIn("<system_memory_log>", response.text)
        self.assertNotIn("<poll", response.text)
        self.assertNotIn("<images>", response.text)
        self.assertNotIn("<videos>", response.text)

    # --- <speech>: what a voice turn says aloud ----------------------------

    def test_no_speech_on_an_ordinary_reply(self):
        response = AgentResponse.from_string("You have two meetings today.")
        self.assertIsNone(response.speech)
        self.assertEqual(response.text, "You have two meetings today.")

    def test_speech_is_extracted_and_kept_out_of_the_text(self):
        reply = (
            "<speech>You have two meetings: standup at nine and the dentist at three.</speech>\n\n"
            "**Today**\n- 9:00 Standup\n- 15:00 Dentist"
        )
        response = AgentResponse.from_string(reply)
        self.assertEqual(response.speech, "You have two meetings: standup at nine and the dentist at three.")
        self.assertEqual(response.text, "**Today**\n- 9:00 Standup\n- 15:00 Dentist")

    def test_every_speech_block_is_kept_in_order(self):
        # Tokens accumulated across steps: the preamble before a tool call, then the answer.
        reply = "<speech>Checking your calendar.</speech><speech>You're free after four.</speech>You're free after 16:00."
        response = AgentResponse.from_string(reply)
        self.assertEqual(response.speech, "Checking your calendar. You're free after four.")
        self.assertEqual(response.text, "You're free after 16:00.")

    def test_an_unclosed_block_is_speech_and_stays_in_the_text(self):
        response = AgentResponse.from_string("<speech>The kitchen light is on.")
        self.assertEqual(response.speech, "The kitchen light is on.")
        self.assertEqual(response.text, "The kitchen light is on.")

    def test_a_reply_that_is_only_speech_has_empty_text(self):
        response = AgentResponse.from_string("<speech>Done, the lights are off.</speech>")
        self.assertEqual(response.speech, "Done, the lights are off.")
        self.assertEqual(response.text, "")

    def test_speech_beside_other_blocks(self):
        reply = (
            "<SPEECH>Want me to book the earlier slot?</SPEECH>Two slots are open.\n"
            '<poll allow_multiple="false"><question>Which slot?</question><options><option>'
            "<text>9:00</text><emoji>🌅</emoji><response>Book 9:00</response></option></options></poll>"
        )
        response = AgentResponse.from_string(reply)
        self.assertEqual(response.speech, "Want me to book the earlier slot?")
        self.assertEqual(response.text, "Two slots are open.")
        self.assertEqual(response.poll_data["question"], "Which slot?")
        self.assertIsNone(response.question, "a poll's question is not a marked one")

    def test_a_marked_question_is_stored_and_still_spoken_in_place(self):
        response = AgentResponse.from_string(
            "<speech>Yesterday the afternoon slipped away. <question>How's your energy, one to ten?</question>"
            "</speech>Good morning! Rate your energy 1-10."
        )
        self.assertEqual(response.speech, "Yesterday the afternoon slipped away. How's your energy, one to ten?")
        self.assertEqual(response.question, "How's your energy, one to ten?")
        self.assertEqual(response.text, "Good morning! Rate your energy 1-10.")

    def test_no_marked_question_no_question(self):
        response = AgentResponse.from_string("<speech>Want to go with today's workout?</speech>Workout below.")
        self.assertIsNone(response.question)

    def test_split_question_joins_several_and_tidies_whitespace(self):
        from core.channel.response_parser import split_question

        self.assertEqual(
            split_question("<question>Merge the\n notes?</question> And <QUESTION>keep tags?</QUESTION>"),
            ("Merge the\n notes? And keep tags?", "Merge the notes? keep tags?"),
        )
        self.assertEqual(split_question(None), (None, None))
        self.assertEqual(split_question("Plain."), ("Plain.", None))


class TestSwitchDirective(unittest.TestCase):
    """<switch to="..."/>: an agent handing the user to a teammate
    (core/prompts/templates/system/team.md)."""

    def test_the_directive_names_the_teammate_and_leaves_no_trace(self):
        response = AgentResponse.from_string(
            '<speech>Passing you to Grace.</speech>Grace runs project planning. <switch to="goal-setter"/>')
        self.assertEqual(response.switch_to, "goal-setter")
        self.assertEqual(response.text, "Grace runs project planning.")
        self.assertEqual(response.speech, "Passing you to Grace.")

    def test_forms_a_model_may_write(self):
        for raw in ("Over to Grace. <switch to='goal-setter'></switch>", 'Over to Grace.<SWITCH to="goal-setter" />',
                    'Over to Grace. <switch  to = "goal-setter">'):
            with self.subTest(raw=raw):
                response = AgentResponse.from_string(raw)
                self.assertEqual((response.switch_to, response.text), ("goal-setter", "Over to Grace."))

    def test_inside_speech_it_is_never_read_out(self):
        response = AgentResponse.from_string('<speech>Over to Grace. <switch to="goal-setter"/></speech>Done.')
        self.assertEqual((response.speech, response.switch_to), ("Over to Grace.", "goal-setter"))

    def test_the_last_one_counts_and_none_means_no_switch(self):
        self.assertEqual(AgentResponse.from_string('<switch to="ted"/> no, <switch to="grace"/>').switch_to, "grace")
        self.assertIsNone(AgentResponse.from_string("Talk to Grace about it.").switch_to)
        self.assertIsNone(AgentResponse.from_string('<switch to=""/>Hm.').switch_to)


class TestSwitchBrief(unittest.TestCase):
    """<switch to="...">brief</switch>: what the agent tells the teammate it
    hands the user to. It goes in the teammate's capsule, never to the user."""

    def test_the_self_closing_and_empty_forms_have_no_brief(self):
        for raw in ('Over to Elephant. <switch to="software-planner"/>',
                    'Over to Elephant. <switch to="software-planner"></switch>',
                    'Over to Elephant. <switch to="software-planner">  \n </switch>',
                    'Over to Elephant. <switch to="software-planner"/></switch>'):
            with self.subTest(raw=raw):
                response = AgentResponse.from_string(raw)
                self.assertEqual((response.switch_to, response.switch_brief, response.text),
                                 ("software-planner", None, "Over to Elephant."))

    def test_a_brief_is_kept_on_one_line_and_the_whole_element_leaves_the_text(self):
        response = AgentResponse.from_string(
            "Handing you to Elephant.\n\n"
            '<switch to="software-planner">\n  The user wants an inbox scanner.\n'
            "  Decided: a script schedule with a state file.\n</switch>\n\n"
            "<system_memory_log>\n- [17:09:00] [MEMORY] Handed the scanner to Elephant.\n</system_memory_log>")
        self.assertEqual(response.switch_to, "software-planner")
        self.assertEqual(response.switch_brief,
                         "The user wants an inbox scanner. Decided: a script schedule with a state file.")
        self.assertEqual(response.text, "Handing you to Elephant.")
        self.assertEqual(response.system_memory_log, "- [17:09:00] [MEMORY] Handed the scanner to Elephant.")

    def test_a_long_brief_is_clipped(self):
        from core.channel.response_parser import SWITCH_BRIEF_CHARS

        response = AgentResponse.from_string(f'Over. <switch to="x">{"word " * 400}</switch>')
        self.assertEqual(len(response.switch_brief), SWITCH_BRIEF_CHARS)
        self.assertTrue(response.switch_brief.endswith("…"))
        self.assertEqual(response.text, "Over.")

    def test_inside_speech_neither_the_tag_nor_the_brief_is_read_out(self):
        response = AgentResponse.from_string(
            '<speech>Over to Elephant. <switch to="software-planner">Spec the scanner.</switch></speech>Done.')
        self.assertEqual((response.speech, response.switch_to, response.switch_brief, response.text),
                         ("Over to Elephant.", "software-planner", "Spec the scanner.", "Done."))

    def test_a_bare_opener_never_takes_a_later_switch_in_as_its_brief(self):
        response = AgentResponse.from_string('<switch to="ted"> no, Grace: <switch to="goal-setter">Plan it.</switch>')
        self.assertEqual((response.switch_to, response.switch_brief), ("goal-setter", "Plan it."))
        self.assertEqual(response.text, "no, Grace:")


# The shape of a real reply (Aki, #agent-management) that explained the switch
# syntax in backticks, then ended with a poll and its real memory log. The prose
# is reworded here; the markup is as it came. Read as directives, the quoted
# <switch to="ID"/> switched the user to "ID", and the quoted
# <system_memory_log> opened a log that ran to the real one's closing tag: the
# reply stopped mid-sentence, and its paragraphs became memory lines.
EXPLAINED_SYNTAX = """<speech>
Right, the switch moved you without any context. Want me to put our plan in a file Elephant can read?
</speech>

You're right that Elephant began with nothing. Why it happened:

1. **The `<switch>` tag only redirects:** the instructions give me just `<switch to="ID"/>`. It moves your session to the teammate's channel and carries no payload.
2. **Each channel keeps its own context:** what we say in `#agent-management` never shows up in `#software-dev`.
3. **The memory log is slow:** I did add a `<system_memory_log>` about the inbox plan and `scan_wiki_inbox.py`, but that block feeds long-term memory later, not Elephant's prompt now.

### Getting the plan across
I can save the agreed spec as `pkm/inbox/william_script_spec.md`, and you can tell Elephant: *"Read `william_script_spec.md` and build it."*

<poll>
    <question>Save the spec for Elephant?</question>
    <options>
        <option>
            <text>Yes, save it</text>
            <emoji>📝</emoji>
            <response>Yes, save the spec where Elephant can read it.</response>
        </option>
        <option>
            <text>No, I'll explain</text>
            <emoji>🗣️</emoji>
            <response>No, I'll brief Elephant myself.</response>
        </option>
    </options>
</poll>

<system_memory_log>
- [17:10:00] [FEEDBACK] The user found that `<switch to="ID"/>` hands over no context; noted as a framework gap.
</system_memory_log>
"""


class TestCodeIsText(unittest.TestCase):
    """A control tag inside code -- an inline span or a fenced block -- is text:
    it stays in the reply as written, and directs nothing."""

    def test_the_reply_that_explained_the_syntax(self):
        response = AgentResponse.from_string(EXPLAINED_SYNTAX)
        self.assertIsNone(response.switch_to)
        self.assertEqual(response.system_memory_log, '- [17:10:00] [FEEDBACK] The user found that '
                                                     '`<switch to="ID"/>` hands over no context; noted as a framework gap.')
        self.assertEqual(response.speech, "Right, the switch moved you without any context. "
                                          "Want me to put our plan in a file Elephant can read?")
        self.assertEqual(response.poll_data["question"], "Save the spec for Elephant?")
        self.assertEqual(len(response.poll_data["options"]), 2)
        # The whole reply is there, its quoted tags as written.
        self.assertTrue(response.text.startswith("You're right that Elephant began with nothing."))
        for kept in ('the instructions give me just `<switch to="ID"/>`. It moves your session',
                     "I did add a `<system_memory_log>` about the inbox plan and `scan_wiki_inbox.py`, but",
                     "### Getting the plan across"):
            self.assertIn(kept, response.text)
        self.assertTrue(response.text.endswith('*"Read `william_script_spec.md` and build it."*'))
        for gone in ("<poll>", "<speech>", "[FEEDBACK]", "</system_memory_log>"):
            self.assertNotIn(gone, response.text)

    def test_a_switch_in_inline_code_is_text(self):
        for raw in ('The syntax is `<switch to="ID"/>`.', "The syntax is ``<switch to='ID'>brief</switch>``."):
            with self.subTest(raw=raw):
                response = AgentResponse.from_string(raw)
                self.assertEqual((response.switch_to, response.switch_brief, response.text), (None, None, raw))

    def test_tags_in_a_fenced_block_are_text(self):
        raw = ("Like this:\n```xml\n<switch to=\"software-planner\">brief</switch>\n<speech>Hi.</speech>\n"
               "<system_memory_log>- example</system_memory_log>\n```\nThat's all.")
        response = AgentResponse.from_string(raw)
        self.assertEqual((response.switch_to, response.speech, response.system_memory_log), (None, None, None))
        self.assertEqual(response.text, raw)

    def test_a_tag_after_the_code_still_counts(self):
        response = AgentResponse.from_string('Quote: `<switch to="ID"/>`.\n<switch to="goal-setter"/>')
        self.assertEqual((response.switch_to, response.text), ("goal-setter", 'Quote: `<switch to="ID"/>`.'))

    def test_the_memory_log_is_the_last_block_outside_code(self):
        response = AgentResponse.from_string(
            "Done.\n<system_memory_log>- first</system_memory_log>\nMore.\n<system_memory_log>- last</system_memory_log>")
        self.assertEqual(response.system_memory_log, "- last")
        self.assertEqual(response.text, "Done.\n\nMore.")

    def test_a_tag_named_in_passing_doesnt_swallow_the_reply(self):
        """Unquoted, the opener has no closer of its own: the log is the block at the end."""
        response = AgentResponse.from_string(
            "I wrote a <system_memory_log> earlier.\nStill visible.\n<system_memory_log>- real</system_memory_log>")
        self.assertEqual(response.system_memory_log, "- real")
        self.assertEqual(response.text, "I wrote a <system_memory_log> earlier.\nStill visible.")

    def test_speech_poll_images_and_videos_in_code_are_text(self):
        raw = ("Use `<speech>` for the ear, `<poll><question>Q?</question></poll>` for a vote, "
               "`<images><image path=\"a.png\"/></images>` and `<videos><video path=\"b.mp4\"/></videos>`.")
        response = AgentResponse.from_string(raw)
        self.assertEqual((response.speech, response.poll_data, response.image_paths, response.video_paths),
                         (None, None, None, None))
        self.assertEqual(response.text, raw)

    def test_an_unclosed_fence_or_a_stray_backtick_hides_nothing(self):
        for raw in ('```\nnever closed\n<switch to="goal-setter"/>', 'a ` b\n<switch to="goal-setter"/> ` c'):
            with self.subTest(raw=raw):
                self.assertEqual(AgentResponse.from_string(raw).switch_to, "goal-setter")

    def test_code_spans(self):
        from core.channel.response_parser import code_spans

        def spans(text):
            return [text[start:end] for start, end in code_spans(text)]

        self.assertEqual(spans("a `x` b ``y ` z`` c"), ["`x`", "``y ` z``"])
        self.assertEqual(spans("```one line``` and `two`"), ["```one line```", "`two`"])
        self.assertEqual(spans("~~~\n<poll>\n~~~~\nout `x`"), ["~~~\n<poll>\n~~~~", "`x`"])
        self.assertEqual(spans("````\n```\nstill code\n````\nout"), ["````\n```\nstill code\n````"])
        self.assertEqual(spans("`across\nlines` and ``` unclosed"), [])


if __name__ == '__main__':
    unittest.main()


