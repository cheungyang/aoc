import unittest
from core.voice.sentence_chunker import SentenceChunker

class TestSentenceChunker(unittest.TestCase):
    def setUp(self):
        self.chunker = SentenceChunker(min_chars=15, max_chars=100)

    def test_single_sentence_with_period(self):
        chunks = self.chunker.add_token("Hello world, this is a test sentence.")
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0], "Hello world, this is a test sentence.")
        self.assertEqual(self.chunker.flush(), [])

    def test_token_by_token_accumulation(self):
        tokens = ["This ", "is ", "the ", "first ", "sentence. ", "And ", "this ", "is ", "the ", "second ", "one!"]
        emitted = []
        for t in tokens:
            emitted.extend(self.chunker.add_token(t))
        emitted.extend(self.chunker.flush())

        self.assertEqual(len(emitted), 2)
        self.assertEqual(emitted[0], "This is the first sentence.")
        self.assertEqual(emitted[1], "And this is the second one!")

    def test_abbreviation_not_split(self):
        # "Dr." sits past min_chars, so only the ABBREVIATIONS lookup
        # (sentence_chunker.py:77) can stop it becoming a sentence boundary.
        text = "We had a meeting with Dr. Smith yesterday. It went well."
        chunks = self.chunker.add_token(text)
        # Should not split on "Dr." - the first boundary is the real full stop.
        self.assertEqual(chunks, ["We had a meeting with Dr. Smith yesterday."])
        self.assertEqual(self.chunker.flush(), ["It went well."])

    def test_flush_remaining_tokens(self):
        self.chunker.add_token("Unfinished trailing thought")
        flushed = self.chunker.flush()
        self.assertEqual(flushed, ["Unfinished trailing thought"])
        self.assertEqual(self.chunker.flush(), [])

    def test_cleans_xml_and_emojis_in_sentences(self):
        chunks = self.chunker.add_token("Here is the plan <poll><text>opt</text></poll> for today. ✨")
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0], "Here is the plan for today.")

    def test_split_into_sentences_returns_all_sentences(self):
        sentences = self.chunker.split_into_sentences(
            "This is the first sentence. And this is the second one!"
        )
        self.assertEqual(
            sentences,
            ["This is the first sentence.", "And this is the second one!"],
        )

    def test_split_into_sentences_keeps_a_trailing_fragment(self):
        # The streaming path withholds an incomplete tail waiting for more
        # tokens; this one must not, because the text has finished arriving.
        sentences = self.chunker.split_into_sentences(
            "A complete sentence here. Then a dangling tail"
        )
        self.assertEqual(sentences[-1], "Then a dangling tail")

    def test_split_into_sentences_handles_a_short_answer(self):
        # "Done." is below min_chars, so no split position is ever found. It
        # still has to be spoken -- this returned nothing before the method
        # existed, and the AttributeError was swallowed upstream.
        self.assertEqual(self.chunker.split_into_sentences("Done."), ["Done."])

    def test_split_into_sentences_is_empty_for_empty_text(self):
        self.assertEqual(self.chunker.split_into_sentences(""), [])
        self.assertEqual(self.chunker.split_into_sentences("   "), [])

    def test_split_into_sentences_does_not_disturb_the_stream_buffer(self):
        self.chunker.add_token("A partial thought still arriving")
        self.chunker.split_into_sentences("Something else entirely. Twice over.")
        self.assertEqual(self.chunker.flush(), ["A partial thought still arriving"])

if __name__ == "__main__":
    unittest.main()
