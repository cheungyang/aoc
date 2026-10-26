"""safe_chunks (core/channel/chunks.py): Discord-sized messages, code fences intact."""
import random
import re
import unittest

from core.channel.chunks import safe_chunks


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


if __name__ == "__main__":
    unittest.main()
