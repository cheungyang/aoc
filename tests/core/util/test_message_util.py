import unittest
import os
import random
import re
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from langchain_core.messages import (
    HumanMessage,
    AIMessage,
    ToolMessage,
    SystemMessage,
)
from core.util.message_util import (
    safe_chunks,
    estimate_message_tokens,
    estimate_total_tokens,
    find_safe_boundary,
)


FENCE_RUN = re.compile(r"`{3,}")
FENCE_LINE = re.compile(r"^\s*`{3,}[\w+#.-]*\s*$")
FENCE_TOKEN = re.compile(r"`{3,}[\w+#.-]*")
WORDS = ["alpha", "beta", "gamma", "delta", "`code`", "load", "plan", "set", "rep"]


def fences_balanced(chunk):
    return len(FENCE_RUN.findall(chunk)) % 2 == 0


def content_lines(text):
    """The text's non-blank lines, without fence lines."""
    return [l for l in text.split("\n") if l.strip() and not FENCE_LINE.match(l)]


def words(text):
    """The text's words, ignoring fences (with their language tags) and escapes."""
    return FENCE_TOKEN.sub(" ", text).replace("\\`", "`").split()


class TestSafeChunks(unittest.TestCase):

    def test_short_text_is_one_chunk(self):
        self.assertEqual(safe_chunks("Hello world"), ["Hello world"])

    def test_empty_or_none_is_no_chunks(self):
        self.assertEqual(safe_chunks(""), [])
        self.assertEqual(safe_chunks(None), [])

    def test_splits_at_line_boundaries(self):
        text = "\n".join(["a" * 40, "b" * 40, "c" * 40])
        self.assertEqual(safe_chunks(text, limit=100), ["a" * 40 + "\n" + "b" * 40, "c" * 40])

    def test_hard_splits_only_an_overlong_word(self):
        text = "a" * 3000
        chunks = safe_chunks(text, limit=1000)
        self.assertTrue(all(len(c) <= 1000 for c in chunks))
        self.assertEqual("".join(chunks), text)

    def test_default_limit_fits_discord_with_the_closing_fence(self):
        # Callers send with the default limit: every chunk, closing fence
        # included, must stay under Discord's 2,000-character cap.
        code = "\n".join(f"value_{i} = compute({i})  # step {i}" for i in range(400))
        chunks = safe_chunks(f"Results:\n```python\n{code}\n```")
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c) <= 1900 for c in chunks))
        self.assertTrue(all(fences_balanced(c) for c in chunks))

    def test_code_block_spanning_the_limit_stays_balanced(self):
        rng = random.Random(7)
        body = "\n".join(" ".join(rng.choice(WORDS) for _ in range(12)) for _ in range(40))
        code = "\n".join(f"x{i} = {i} * 2  # line {i}" for i in range(120))
        text = f"Intro paragraph\n\n```python\n{code}\n```\n\nAfter the block.\n{body}"
        chunks = safe_chunks(text, limit=500)
        self.assertTrue(all(len(c) <= 500 for c in chunks), max(len(c) for c in chunks))
        self.assertTrue(all(fences_balanced(c) for c in chunks))
        self.assertEqual(content_lines("\n".join(chunks)), content_lines(text))
        # The block reopens with its language in each message it continues into.
        self.assertTrue(all(c.startswith("```python\n") for c in chunks[1:4]))

    def test_unmatched_inline_backtick_is_escaped(self):
        self.assertEqual(
            safe_chunks("Use `ls` to list. Then a stray ` here."),
            ["Use `ls` to list. Then a stray \\` here."],
        )
        self.assertEqual(safe_chunks("Use `ls` and `cd`."), ["Use `ls` and `cd`."])

    def test_code_lines_are_left_as_written(self):
        text = "```\na ` b\n```"
        self.assertEqual(safe_chunks(text), [text])

    def test_streaming_preview_closes_an_open_block(self):
        text = 'Here:\n```json\n{"a": 1'
        self.assertEqual(safe_chunks(text, streaming=True), ['Here:\n```json\n{"a": 1\n```'])
        # The final message keeps an unclosed block as written.
        self.assertEqual(safe_chunks(text), [text])

    def test_streaming_preview_holds_back_a_bare_opener(self):
        # Closing it would show an empty block until the code arrives.
        self.assertEqual(safe_chunks("Here:\n```python", streaming=True), ["Here:"])
        self.assertEqual(safe_chunks("Here:\n```python"), ["Here:\n```python"])

    def test_prose_is_never_split_mid_word(self):
        rng = random.Random(7)
        vocab = WORDS[:4]
        prose = " ".join(rng.choice(vocab) for _ in range(2000))
        chunks = safe_chunks(prose, limit=300)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c), 300)
            self.assertTrue(all(w in vocab for w in c.split()), "split mid-word")

    def test_inline_fence_pair_split_with_its_line_stays_balanced(self):
        # The pieces of a split line become separate lines, so a ```pair```
        # straddling the split must not unbalance either message.
        for pad in range(1, 120):
            chunks = safe_chunks("w" * pad + " ```x y``` " + "z " * 100, limit=100)
            self.assertTrue(all(fences_balanced(c) for c in chunks), pad)
            self.assertTrue(all(len(c) <= 100 for c in chunks), pad)

    def test_no_empty_code_block_at_a_chunk_edge(self):
        # Discord renders an empty block as a box showing the language name.
        # A closing fence at the boundary must not open the next message with
        # one, and an opener at the boundary must not end this message with one.
        empty_end = re.compile(r"(^|\n)```[\w+#.-]*\n```$")
        empty_start = re.compile(r"^```[\w+#.-]*\n```(\n|$)")
        code = "\n".join(f"line {i:03d} of code" for i in range(30))
        prose = "\n".join(f"prose line number {i:03d}" for i in range(20))
        for limit in range(100, 260):
            for text in (f"```py\n{code}\n```\nafter", f"{prose}\n```py\n{code}\n```"):
                for chunk in safe_chunks(text, limit=limit):
                    self.assertFalse(empty_end.search(chunk), (limit, chunk))
                    self.assertFalse(empty_start.match(chunk), (limit, chunk))
                    self.assertTrue(fences_balanced(chunk), (limit, chunk))

    def test_overlong_language_tag_is_not_carried_over(self):
        text = "```" + "x" * 300 + "\n" + "code " * 200 + "\n```"
        chunks = safe_chunks(text, limit=200)
        self.assertTrue(all(len(c) <= 200 for c in chunks))
        self.assertTrue(all(fences_balanced(c) for c in chunks))
        self.assertTrue(all(c.startswith("```\n") for c in chunks[1:]))

    def test_limit_too_small_for_fences_raises(self):
        with self.assertRaises(ValueError):
            safe_chunks("hello world", limit=8)

    def test_fuzz_limits_balance_and_content(self):
        rng = random.Random(7)
        extras = ["```x```", "`"]  # inline fence pairs and stray backticks
        for trial in range(500):
            lines = []
            for _ in range(rng.randint(5, 80)):
                r = rng.random()
                if r < 0.08:
                    lines.append("```" + rng.choice(["", "py", "json"]))
                elif r < 0.12:  # long enough to be split into pieces
                    lines.append(" ".join(rng.choice(WORDS + extras) for _ in range(rng.randint(40, 160))))
                else:
                    lines.append(" ".join(rng.choice(WORDS + extras) for _ in range(rng.randint(0, 25))))
            text = "\n".join(lines)
            if len(FENCE_RUN.findall(text)) % 2:
                text += "\n```"
            limit = rng.choice([200, 400, 1900])
            prefix = text[:rng.randint(1, len(text))]
            with self.subTest(trial=trial, limit=limit):
                chunks = safe_chunks(text, limit=limit)
                self.assertTrue(all(len(c) <= limit for c in chunks))
                self.assertTrue(all(fences_balanced(c) for c in chunks))
                self.assertEqual(words("\n".join(chunks)), words(text))
                # A streaming preview of any prefix is balanced too.
                preview = safe_chunks(prefix, limit=limit, streaming=True)
                self.assertTrue(all(len(c) <= limit for c in preview))
                self.assertTrue(all(fences_balanced(c) for c in preview))


class TestMessageUtil(unittest.TestCase):

    def test_estimate_message_tokens_various_types(self):
        # None message
        self.assertEqual(estimate_message_tokens(None), 0)

        # Simple string HumanMessage
        msg1 = HumanMessage(content="Hello world")
        tokens1 = estimate_message_tokens(msg1)
        self.assertGreater(tokens1, 4)

        # Complex list content (text + image)
        msg2 = HumanMessage(content=[
            {"type": "text", "text": "Describe this image"},
            {"type": "image_url", "image_url": "data:image/png;base64,12345"}
        ])
        tokens2 = estimate_message_tokens(msg2)
        self.assertGreater(tokens2, 300)

        # AIMessage with tool_calls
        msg3 = AIMessage(
            content="I will search for flights.",
            tool_calls=[{"name": "seats_aero", "args": {"origin": "SFO", "dest": "NRT"}, "id": "call_123"}]
        )
        tokens3 = estimate_message_tokens(msg3)
        self.assertGreater(tokens3, 20)

        # ToolMessage
        msg4 = ToolMessage(content="Flight 101 available", name="seats_aero", tool_call_id="call_123")
        tokens4 = estimate_message_tokens(msg4)
        self.assertGreater(tokens4, 10)

    def test_estimate_total_tokens(self):
        messages = [
            HumanMessage(content="A" * 400),
            AIMessage(content="B" * 400),
        ]
        total = estimate_total_tokens(messages)
        self.assertGreaterEqual(total, 200)

    def test_find_safe_boundary_no_split_for_small_history(self):
        messages = [
            HumanMessage(content="Hello"),
            AIMessage(content="Hi there"),
        ]
        split_idx = find_safe_boundary(messages, window_messages=15)
        self.assertEqual(split_idx, 0)

    def test_find_safe_boundary_preserves_tool_call_pairs(self):
        messages = []
        for i in range(10):
            messages.append(HumanMessage(content=f"Turn {i} request"))
            messages.append(AIMessage(content=f"Turn {i} reply"))

        ai_tool = AIMessage(
            content="Running tools",
            tool_calls=[
                {"name": "tool_a", "args": {}, "id": "call_a"},
                {"name": "tool_b", "args": {}, "id": "call_b"}
            ]
        )
        t_msg_a = ToolMessage(content="Result A", name="tool_a", tool_call_id="call_a")
        t_msg_b = ToolMessage(content="Result B", name="tool_b", tool_call_id="call_b")
        ai_after = AIMessage(content="Final reply after tools")

        messages = messages[:14] + [ai_tool, t_msg_a, t_msg_b, ai_after] + messages[14:]

        split_idx = find_safe_boundary(messages, window_messages=8)
        recent = messages[split_idx:]

        self.assertNotIsInstance(recent[0], ToolMessage)

        for idx, m in enumerate(recent):
            if isinstance(m, ToolMessage):
                tc_id = getattr(m, "tool_call_id", None)
                preceding_ai = [
                    x for x in recent[:idx]
                    if isinstance(x, AIMessage) and getattr(x, "tool_calls", None) and any(
                        (tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)) == tc_id
                        for tc in x.tool_calls
                    )
                ]
                self.assertTrue(len(preceding_ai) > 0, f"Orphaned ToolMessage found: {tc_id}")

    def test_find_safe_boundary_always_starts_on_human_message(self):
        messages = [
            HumanMessage(content="Turn 0"),
            AIMessage(content="", tool_calls=[{"name": "t0", "args": {}, "id": "c0"}]),
            ToolMessage(content="r0", tool_call_id="c0", name="t0"),
            AIMessage(content="Turn 0 reply"),
            HumanMessage(content="Turn 1"),
            AIMessage(content="", tool_calls=[{"name": "t1_a", "args": {}, "id": "c1_a"}]),
            ToolMessage(content="r1_a", tool_call_id="c1_a", name="t1_a"),
            AIMessage(content="", tool_calls=[{"name": "t1_b", "args": {}, "id": "c1_b"}]),
            ToolMessage(content="r1_b", tool_call_id="c1_b", name="t1_b"),
            AIMessage(content="", tool_calls=[{"name": "t1_c", "args": {}, "id": "c1_c"}]),
            ToolMessage(content="r1_c", tool_call_id="c1_c", name="t1_c"),
            AIMessage(content="Turn 1 reply"),
            HumanMessage(content="Turn 2"),
            AIMessage(content="Turn 2 reply"),
        ]
        split_idx = find_safe_boundary(messages, window_messages=6)
        self.assertGreater(split_idx, 0)
        self.assertIsInstance(messages[split_idx], HumanMessage)
        self.assertEqual(messages[split_idx].content, "Turn 1")


if __name__ == "__main__":
    unittest.main()
